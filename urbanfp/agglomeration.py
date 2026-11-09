"""City populations with their surroundings by drive time ("Агломерації" page).

For every configured city (an urban centre) the free-flow car drive time to every settlement of Ukraine
within MAX_MIN minutes (6 h) is computed on a national OSM road graph. The Агломерації page joins settlements to
centres for a chosen threshold up to 3 h; the region modelling page uses the whole range:

* centres are taken from the largest (population of the city itself) down; a centre within the threshold
  of an active centre more than twice its size becomes inactive and joins it; centres of comparable size
  both stay active;
* every settlement joins the active centre it is reached from fastest, if within the threshold.

Inputs: the Geofabrik extract (roads, `place=*` points with `katotth` = KATOTTG code), Derzhstat tables
(data/demography) and the city boundaries (data/interim/<city>/boundary.geojson).
Output: web/data/agglomeration.json. Run: python -m urbanfp agglomeration
"""
from __future__ import annotations

import json
import logging
from array import array
import re
import time

import geopandas as gpd
import numpy as np
import osmium
import pandas as pd
import shapely
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import connected_components, dijkstra
from scipy.spatial import cKDTree

from urbanfp.config import Paths, load_cities
from urbanfp.demography import available_years, city_row, load_year
from urbanfp.sources import pbf_path
from urbanfp.territories import SPEED

log = logging.getLogger(__name__)

AGGLOMERATION_VERSION = 5
OUTLINE_TOLERANCE_DEG = 0.002  # city outline simplification for the map (~200 m)
THRESHOLDS_MIN = [0, 10, 30, 60, 90, 120, 150, 180]  # buttons; the slider covers every 5 min
MAX_MIN = 360  # 6 h: region modelling assigns settlements to few, distant centres; agglomerations use up to 3 h
# national graph: no service roads, tracks or paths (they matter for the last metres only)
GRAPH_HIGHWAYS = {k: v for k, v in SPEED.items() if k not in ("service", "track")}
# The graph is the intact (pre-war) network: roads mapped as destroyed or out of use (blown-up bridges such as
# the M-18 bridge south of Zaporizhzhia; destroyed bridges retagged highway=proposed + destroyed/ruins, such
# as the Antonivskyi bridge in Kherson) are kept, and access=no bans (front-line closures and checkpoints, also
# on village roads around Horlivka and Stanytsia Luhanska) are ignored except on service roads;
# access=private still excludes a minor road.
WAR_PREFIXES = ("ruined", "destroyed", "damaged", "disused")
MAIN_ROADS = {"motorway", "trunk", "primary", "secondary", "tertiary", "motorway_link", "trunk_link",
              "primary_link", "secondary_link", "tertiary_link"}
# Dead ends of roads are joined to the nearest vertex of another road within this distance: along the front
# line OSM roads are deliberately cut by moving way ends a few metres apart (no shared node), and ordinary
# mapping slips leave similar gaps elsewhere.
HEAL_GAP_M = 30
HEAL_MAIN_GAP_M = 150      # main roads (edges of HEAL_MAIN_KMH and faster): dead end to another main road
HEAL_MAIN_KMH = 45
HEAL_SPEED_KMH = 20
SNAP_MAX_M = 3000          # a settlement further than this from the main road network is left unreached
# Settlements are joined to the nearest vertex of the main (largest connected) road network, never to a stray
# piece (a yard, car park or road not joined to anything: Kotsiubynske snapped to one 80 m from the network) or
# to a village whose only links are field tracks, which the graph leaves out. The straight distance to that
# vertex is driven at LAST_MILE_KMH (track speed) and added to the drive time.
LAST_MILE_KMH = 20
EARTH_M_PER_DEG = 111_320.0


def _cache_dir():
    d = Paths.interim("_national")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _stamp(pbf) -> str:
    st = pbf.stat()
    return f"{st.st_size}-{int(st.st_mtime)}-v{AGGLOMERATION_VERSION}"


def road_graph(region: str):
    """Undirected car road graph of the extract: (lon, lat) per vertex and a CSR matrix of travel seconds."""
    pbf = pbf_path(region)
    cache = _cache_dir() / f"roads_{region.replace('/', '_')}.npz"
    if cache.exists():
        z = np.load(cache, allow_pickle=False)
        if str(z["stamp"]) == _stamp(pbf):
            g = csr_matrix((z["data"], z["indices"], z["indptr"]), shape=(len(z["lon"]),) * 2)
            return z["lon"], z["lat"], _heal(z["lon"], z["lat"], g)
    t0 = time.time()
    refs, lons, lats, way_start, way_speed = array('q'), array('d'), array('d'), [], []
    fp = (osmium.FileProcessor(str(pbf), osmium.osm.NODE | osmium.osm.WAY)
          .with_locations("flex_mem")
          .with_filter(osmium.filter.EntityFilter(osmium.osm.WAY))
          .with_filter(osmium.filter.KeyFilter("highway", *[f"{p}:highway" for p in WAR_PREFIXES])))
    for w in fp:
        hw = w.tags.get("highway")
        if hw not in GRAPH_HIGHWAYS:  # missing, or a lifecycle value such as highway=destroyed + destroyed:highway=trunk
            hw = next((w.tags.get(f"{p}:highway") for p in WAR_PREFIXES if f"{p}:highway" in w.tags), hw)
        if hw == "proposed" and any(w.tags.get(k) not in (None, "no") for k in ("destroyed", "ruins", "damage")):
            hw = w.tags.get("proposed")
        if hw not in GRAPH_HIGHWAYS or w.tags.get("area") == "yes":
            continue
        if w.tags.get("access") == "private" and hw not in MAIN_ROADS or w.tags.get("access") == "no" and hw == "service":
            continue
        mm = re.match(r"^\s*(\d+)", w.tags.get("maxspeed", "") or "")
        sp = min(float(mm.group(1)) if mm else GRAPH_HIGHWAYS[hw], GRAPH_HIGHWAYS[hw] * 1.3)
        way_start.append(len(refs))
        way_speed.append(sp)
        for n in w.nodes:
            if not n.location.valid():
                continue
            refs.append(n.ref)
            lons.append(n.location.lon)
            lats.append(n.location.lat)
    log.info("road graph: %d ways, %d node refs read in %.0fs", len(way_start), len(refs), time.time() - t0)
    refs = np.frombuffer(refs, dtype=np.int64)
    lons = np.frombuffer(lons)
    lats = np.frombuffer(lats)
    way_start = np.asarray(way_start + [len(refs)])
    way_speed = np.asarray(way_speed)
    # consecutive node pairs within each way
    same_way = np.ones(len(refs) - 1, dtype=bool)
    same_way[way_start[1:-1] - 1] = False
    seg_way = np.repeat(np.arange(len(way_speed)), np.diff(way_start))[:-1]
    a = np.nonzero(same_way)[0]
    b = a + 1
    uniq, inv = np.unique(refs, return_inverse=True)
    vlon = np.zeros(len(uniq))
    vlat = np.zeros(len(uniq))
    vlon[inv] = lons
    vlat[inv] = lats
    dx = np.radians(lons[b] - lons[a]) * np.cos(np.radians((lats[a] + lats[b]) / 2))
    dy = np.radians(lats[b] - lats[a])
    dist = 6_371_000 * np.hypot(dx, dy)
    sec = dist / (way_speed[seg_way[a]] / 3.6)
    u, v = inv[a], inv[b]
    keep = u != v
    n = len(uniq)
    g = csr_matrix((np.r_[sec[keep], sec[keep]], (np.r_[u[keep], v[keep]], np.r_[v[keep], u[keep]])), shape=(n, n))
    np.savez(cache, stamp=_stamp(pbf), lon=vlon, lat=vlat, data=g.data, indices=g.indices, indptr=g.indptr)
    log.info("road graph: %d vertices, %d edges, built in %.0fs", n, g.nnz // 2, time.time() - t0)
    return vlon, vlat, _heal(vlon, vlat, g)


def _heal(lon, lat, g):
    """Join every dead end (vertex of degree 1) to the nearest vertex other than its own neighbour: within
    HEAL_GAP_M of any road, or within HEAL_MAIN_GAP_M of a main road for main-road dead ends (cut dual
    carriageways leave gaps of ~50 m), and ahead of the road's direction at the dead end (so the two cut
    carriageways are not just joined to each other). Links are driven at HEAL_SPEED_KMH."""
    t0 = time.time()
    xy = _xy(lon, lat)
    # speed of the fastest edge at each vertex, to tell main roads apart
    coo = g.tocoo()
    kmh = np.hypot(*(xy[coo.row] - xy[coo.col]).T) / np.maximum(coo.data, 1e-9) * 3.6
    vmax = np.zeros(len(lon))
    np.maximum.at(vmax, coo.row, kmh)
    ends = np.nonzero(np.diff(g.indptr) == 1)[0]
    nb = g.indices[g.indptr[ends]]  # the only neighbour of each dead end
    found = []
    main = vmax >= HEAL_MAIN_KMH - 1
    for pool, cand, gap in ((np.arange(len(lon)), ends, HEAL_GAP_M), (np.nonzero(main)[0], main[ends], HEAL_MAIN_GAP_M)):
        sel = np.arange(len(ends)) if cand is ends else np.nonzero(cand)[0]
        e = ends[sel]
        d, j = cKDTree(xy[pool]).query(xy[e], k=8, distance_upper_bound=gap)
        j = np.where(j < len(pool), pool[np.minimum(j, len(pool) - 1)], -1)
        # nearest candidate other than the vertex itself and its neighbour, ahead of the road
        heading = xy[e] - xy[nb[sel]]
        heading /= np.maximum(np.linalg.norm(heading, axis=1), 1e-9)[:, None]
        to = xy[j] - xy[e][:, None, :]
        ahead = (to * heading[:, None, :]).sum(axis=2) > 0.3 * np.maximum(np.linalg.norm(to, axis=2), 1e-9)
        bad = (j == e[:, None]) | (j == nb[sel][:, None]) | ~np.isfinite(d) | ~ahead
        d = np.where(bad, np.inf, d)
        col = d.argmin(axis=1)
        rows = np.arange(len(sel))
        ok = np.isfinite(d[rows, col])
        found.append((ends[sel][ok], j[rows, col][ok], d[rows, col][ok]))
    src = np.concatenate([f[0] for f in found]); dst = np.concatenate([f[1] for f in found])
    dist = np.concatenate([f[2] for f in found])
    a, b = np.minimum(src, dst), np.maximum(src, dst)
    pairs, first = np.unique(np.c_[a, b], axis=0, return_index=True)  # one link per pair of vertices
    sec = np.maximum(dist[first], 1) / (HEAL_SPEED_KMH / 3.6)
    links = csr_matrix((np.r_[sec, sec], (np.r_[pairs[:, 0], pairs[:, 1]], np.r_[pairs[:, 1], pairs[:, 0]])),
                       shape=g.shape)
    log.info("road graph: %d dead ends joined to a nearby road (%.0fs)", len(pairs), time.time() - t0)
    return (g + links).tocsr()


def place_points(region: str) -> pd.DataFrame:
    """`place=city|town|village|hamlet` points of the extract with their KATOTTG code (OSM `katotth`), type and
    names (current and former, for matching older statistics)."""
    pbf = pbf_path(region)
    cache = _cache_dir() / f"places_v2_{region.replace('/', '_')}.parquet"
    if cache.exists():
        df = pd.read_parquet(cache)
        if len(df) and df["stamp"].iloc[0] == _stamp(pbf):
            return df
    rows = []
    fp = (osmium.FileProcessor(str(pbf), osmium.osm.NODE)
          .with_filter(osmium.filter.TagFilter(*[("place", p) for p in ("city", "town", "village", "hamlet")])))
    for nd in fp:
        t = nd.tags
        rows.append({"osm": nd.id, "katotth": (t.get("katotth") or "").strip(), "place": t.get("place"),
                     "name": t.get("name:uk") or t.get("name") or "", "old_name": t.get("old_name:uk") or t.get("old_name") or "",
                     "lon": nd.location.lon, "lat": nd.location.lat, "stamp": _stamp(pbf)})
    df = pd.DataFrame(rows)
    df.to_parquet(cache)
    return df


# Oblast-level units whose settlements are coded elsewhere today: the villages of the Sevastopol city council of
# 2001 belong to hromadas of Bakhchysarai raion (UA01) since the 2020 reform or have no code in OSM, so the census
# rows of Sevastopol found no candidates. Points inside the unit's OSM boundary are flagged (`area`), and
# uncoded ones take its prefix instead of the nearest coded point's.
PREFIX_AREAS = {"UA85": 1574364}  # Sevastopol, city with special status (admin_level 4, ISO 3166-2 UA-40)


def prefix_areas(region: str) -> dict[str, shapely.Geometry]:
    """Boundaries of PREFIX_AREAS (cached per extract)."""
    from urbanfp.extract import extract_boundaries
    pbf = pbf_path(region)
    cache = _cache_dir() / f"prefix_areas_{region.replace('/', '_')}.geojson"
    if cache.exists():
        g = gpd.read_file(cache)
        if len(g) and g["stamp"].iloc[0] == _stamp(pbf) and set(g["prefix"]) == set(PREFIX_AREAS):
            return dict(zip(g["prefix"], g.geometry))
    found = extract_boundaries(pbf, [], set(PREFIX_AREAS.values()))
    areas = {pre: found[rel] for pre, rel in PREFIX_AREAS.items() if rel in found}
    gpd.GeoDataFrame({"prefix": list(areas), "stamp": _stamp(pbf)}, geometry=list(areas.values()),
                     crs="EPSG:4326").to_file(cache, driver="GeoJSON")
    return areas


def coded_places(region: str) -> pd.DataFrame:
    """Place points with a settlement `code`: the KATOTTG code, or `OSM<node id>` where OSM has none (most of
    Crimea), and the oblast `prefix` (UAxx) of the code, else of the PREFIX_AREAS unit the point lies in, else of
    the nearest coded point."""
    pts = place_points(region).copy()
    valid = pts["katotth"].str.match(r"^UA\d{17}$")
    pts["code"] = np.where(valid, pts["katotth"], "OSM" + pts["osm"].astype(str))
    pts["prefix"] = pts["katotth"].str[:4]
    if (~valid).any() and valid.any():
        _, near = cKDTree(_xy(pts.loc[valid, "lon"], pts.loc[valid, "lat"])).query(_xy(pts.loc[~valid, "lon"], pts.loc[~valid, "lat"]))
        pts.loc[~valid, "prefix"] = pts.loc[valid, "prefix"].values[near]
    pts["area"] = ""  # PREFIX_AREAS unit the point lies in, whatever its code
    for pre, area in prefix_areas(region).items():
        inside = shapely.contains_xy(area, pts["lon"].values, pts["lat"].values)
        pts.loc[inside, "area"] = pre
        pts.loc[inside & ~valid, "prefix"] = pre
    return pts.drop_duplicates("code")


def _xy(lon, lat, lat0=48.5):
    """Equirectangular metres (good enough for snapping within a few km)."""
    return np.column_stack([np.asarray(lon) * EARTH_M_PER_DEG * np.cos(np.radians(lat0)), np.asarray(lat) * EARTH_M_PER_DEG])


def build_agglomeration() -> dict:
    t0 = time.time()
    years = available_years()
    if not years:
        raise FileNotFoundError("No Derzhstat workbooks in data/demography/raw")
    # every city with an extracted boundary; those not yet processed further appear on the Агломерації page only
    cities = [c for c in load_cities() if (Paths.interim(c.id) / "boundary.geojson").exists()]
    published = {c.id for c in cities if (Paths.web_city(c.id) / "stats.json").exists()}
    region = cities[0].source_region
    demo = json.loads((Paths.web_data() / "demography.json").read_text(encoding="utf-8"))

    # ---- settlements: Derzhstat rows (all years) located by the OSM place point with the same KATOTTG code
    tables = {y: load_year(y, p) for y, p in sorted(years.items())}
    latest = tables[max(tables)]
    city_codes = {v.get("city_code") for d in demo["cities"].values() for v in d.values() if v.get("available")}
    # settlements of every year (latest names first), plus cities listed at region level (Kyiv)
    sett = pd.concat([df[(df["kind"] == "settlement") | df["code"].isin(city_codes)]
                      for _, df in sorted(tables.items(), reverse=True)])
    sett = sett[sett["code"] != ""].drop_duplicates("code")[["code", "name", "type", "oblast"]].copy()
    pts = coded_places(region)
    sett = sett.merge(pts[["code", "lon", "lat"]], on="code", how="inner")
    for y, df in tables.items():
        sett[f"pop{y}"] = sett["code"].map(dict(zip(df["code"], df["population"])))
    sett = sett.reset_index(drop=True)
    log.info("settlements located: %d of %d", len(sett), int((latest["kind"] == "settlement").sum()))

    # ---- road graph and snapping
    vlon, vlat, g = road_graph(region)
    _, comp = connected_components(g, directed=False)
    main_v = np.nonzero(comp == np.bincount(comp).argmax())[0]
    d_snap, j = cKDTree(_xy(vlon[main_v], vlat[main_v])).query(_xy(sett["lon"], sett["lat"]))
    v_snap = main_v[j]
    reachable = d_snap <= SNAP_MAX_M
    last_mile = d_snap / (LAST_MILE_KMH / 3.6)  # seconds
    log.info("settlements joined to the main road network (%d vertices of %d): %d within %d m, median %.0f m",
             len(main_v), len(vlon), int(reachable.sum()), SNAP_MAX_M, float(np.median(d_snap)))

    # ---- drive times from every city (all road vertices inside its boundary)
    reach, centres = {}, []
    code_index = {c: i for i, c in enumerate(sett["code"])}
    for c in cities:
        bnd = gpd.read_file(Paths.interim(c.id) / "boundary.geojson").geometry.iloc[0]
        x0, y0, x1, y1 = bnd.bounds
        cand = np.nonzero((vlon >= x0) & (vlon <= x1) & (vlat >= y0) & (vlat <= y1))[0]
        src = cand[shapely.contains_xy(bnd, vlon[cand], vlat[cand])]
        d = demo["cities"].get(c.id, {})
        code = next((v.get("city_code") for v in d.values() if v.get("available")), None)
        if code is None:  # not in demography.json yet: the city's row in the latest table that has it
            code = next((row["code"] for _, df in sorted(tables.items(), reverse=True)
                         if (row := city_row(c, df)) is not None and row["code"] in code_index), None)
        if c.id in published:
            ctr = shapely.Point(*json.loads((Paths.web_city(c.id) / "stats.json").read_text(encoding="utf-8"))["map"]["center"])
        else:
            ctr = bnd.representative_point()
        # simplified outline (exterior rings, ~200 m), drawn into the agglomeration outline on the map
        simple = bnd.simplify(OUTLINE_TOLERANCE_DEG)
        rings = [g.exterior for g in getattr(simple, "geoms", [simple]) if not g.is_empty]
        outline = [[round(x, 4), round(y, 4)] for r in rings for x, y in r.coords[:-1]]
        centres.append({"id": c.id, "name": c.name, "name_local": c.name_local, "map_only": c.id not in published,
                        "code": code, "settlement": code_index.get(code),
                        "lon": round(ctr.x, 5), "lat": round(ctr.y, 5), "outline": outline})
        if not len(src):
            log.warning("[%s] no road vertices inside the boundary", c.id)
            reach[c.id] = []
            continue
        t1 = time.time()
        tsec = dijkstra(g, directed=False, indices=src, min_only=True, limit=MAX_MIN * 60)
        ts = tsec[v_snap] + last_mile
        inside = shapely.contains_xy(bnd, sett["lon"].values, sett["lat"].values)
        ts = np.where(inside, 0.0, ts)  # settlements within the city boundary
        ok = np.nonzero((reachable | inside) & np.isfinite(ts) & (ts <= MAX_MIN * 60))[0]
        reach[c.id] = [[int(i), int(round(ts[i] / 6))] for i in ok]  # tenths of a minute
        log.info("[%s] %d road vertices in the city, %d settlements within %d min (%.0fs)",
                 c.id, len(src), len(ok), MAX_MIN, time.time() - t1)

    years_sorted = sorted(tables)
    out = {
        "schema_version": 1, "version": AGGLOMERATION_VERSION, "thresholds_min": THRESHOLDS_MIN,
        "max_min": MAX_MIN, "years": years_sorted,
        "osm_timestamp": json.loads((Paths.web_city(cities[0].id) / "stats.json").read_text(encoding="utf-8")).get("osm_timestamp"),  # Kyiv
        "settlements": {
            "code": sett["code"].tolist(), "name": sett["name"].tolist(),
            "lon": sett["lon"].round(4).tolist(), "lat": sett["lat"].round(4).tolist(),
            "pop": {str(y): [None if pd.isna(v) else int(v) for v in sett[f"pop{y}"]] for y in years_sorted},
        },
        "centres": centres,
        "reach": reach,
    }
    p = Paths.web_data() / "agglomeration.json"
    p.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    log.info("%s: %d settlements, %d centres, %.1f MB, %.0fs total", p, len(sett), len(centres),
             p.stat().st_size / 1e6, time.time() - t0)
    build_town_reach()
    return out


# --------------------------------------------------------------------------- towns as region centres

TOWN_MIN_POP = 20_000   # 2001 census: settlements this large may be chosen as region centres
UNREACHED = 0xFFFF


def build_town_reach(min_pop: int = TOWN_MIN_POP) -> None:
    """Drive times from every settlement of at least `min_pop` (2001 census) that is not a configured city to
    every settlement, for the region modelling page: web/data/towns.json (the towns, by settlement index of
    agglomeration.json) and web/data/reach/<settlement code>.bin, one little-endian uint16 per settlement (tenths
    of a minute, 0xFFFF beyond MAX_MIN or unreached). A town has no boundary: times are from its own point (joined
    to the road network like any settlement, with its last mile). Files are loaded only for the towns chosen."""
    t0 = time.time()
    data = json.loads((Paths.web_data() / "agglomeration.json").read_text(encoding="utf-8"))
    S = data["settlements"]
    n = len(S["code"])
    cities = {c["settlement"] for c in data["centres"] if c["settlement"] is not None}
    p01 = S["pop"].get("2001") or []
    towns = [i for i, v in enumerate(p01) if v and v >= min_pop and i not in cities]
    lon, lat = np.asarray(S["lon"]), np.asarray(S["lat"])
    vlon, vlat, g = road_graph(load_cities()[0].source_region)
    _, comp = connected_components(g, directed=False)
    main_v = np.nonzero(comp == np.bincount(comp).argmax())[0]
    d_snap, j = cKDTree(_xy(vlon[main_v], vlat[main_v])).query(_xy(lon, lat))
    v_snap = main_v[j]
    reachable = d_snap <= SNAP_MAX_M
    last_mile = d_snap / (LAST_MILE_KMH / 3.6)
    out_dir = Paths.web_data() / "reach"
    out_dir.mkdir(parents=True, exist_ok=True)
    for f in out_dir.glob("*.bin"):
        f.unlink()
    index = []
    for k, i in enumerate(towns):
        tsec = dijkstra(g, directed=False, indices=int(v_snap[i]), limit=MAX_MIN * 60)
        ts = tsec[v_snap] + last_mile + last_mile[i]
        ts[i] = 0.0
        tenths = np.where(reachable & np.isfinite(ts) & (ts <= MAX_MIN * 60), np.round(ts / 6), UNREACHED)
        tenths.astype("<u2").tofile(out_dir / f"{S['code'][i]}.bin")
        index.append({"id": S["code"][i], "settlement": i, "name": S["name"][i], "pop2001": int(p01[i])})
        if (k + 1) % 20 == 0 or k + 1 == len(towns):
            log.info("town drive times: %d/%d (%.0fs)", k + 1, len(towns), time.time() - t0)
    (Paths.web_data() / "towns.json").write_text(json.dumps(
        {"schema_version": 1, "min_pop_2001": min_pop, "settlements": n, "max_min": MAX_MIN, "towns": index},
        ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    log.info("web/data/towns.json: %d towns of %d+ (2001), reach files %.1f MB, %.0fs", len(index), min_pop,
             sum(f.stat().st_size for f in out_dir.glob("*.bin")) / 1e6, time.time() - t0)
