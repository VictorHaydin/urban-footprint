"""City populations with their surroundings by drive time ("Агломерації" page).

For every configured city (an urban centre) the free-flow car drive time to every settlement of Ukraine
within MAX_MIN minutes (3 h) is computed on a national OSM road graph. The web page then joins settlements to
centres for a chosen threshold (city only, 10, 20, 30, 45 min, 1, 1.5, 2, 2.5, 3 h):

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
from scipy.sparse.csgraph import dijkstra
from scipy.spatial import cKDTree

from urbanfp.config import Paths, load_cities
from urbanfp.demography import available_years, load_year
from urbanfp.sources import pbf_path
from urbanfp.territories import SPEED

log = logging.getLogger(__name__)

AGGLOMERATION_VERSION = 4
THRESHOLDS_MIN = [0, 10, 20, 30, 45, 60, 90, 120, 150, 180]
MAX_MIN = 180
# national graph: no service roads, tracks or paths (they matter for the last metres only)
GRAPH_HIGHWAYS = {k: v for k, v in SPEED.items() if k not in ("service", "track")}
# The graph is the intact (pre-war) network: roads mapped as destroyed or out of use (blown-up bridges such as
# the M-18 bridge south of Zaporizhzhia; destroyed bridges retagged highway=proposed + destroyed/ruins, such
# as the Antonivskyi bridge in Kherson) are kept, and access bans on main roads (front-line closures,
# checkpoints) are ignored; on minor roads access=no/private still excludes the way.
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
SNAP_MAX_M = 3000          # a settlement further than this from any road is left unreached
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
        if hw not in MAIN_ROADS and w.tags.get("access") in ("no", "private"):
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


def coded_places(region: str) -> pd.DataFrame:
    """Place points with a settlement `code`: the KATOTTG code, or `OSM<node id>` where OSM has none (most of
    Crimea), and the oblast `prefix` (UAxx) of the code or of the nearest coded point."""
    pts = place_points(region).copy()
    valid = pts["katotth"].str.match(r"^UA\d{17}$")
    pts["code"] = np.where(valid, pts["katotth"], "OSM" + pts["osm"].astype(str))
    pts["prefix"] = pts["katotth"].str[:4]
    if (~valid).any() and valid.any():
        _, near = cKDTree(_xy(pts.loc[valid, "lon"], pts.loc[valid, "lat"])).query(_xy(pts.loc[~valid, "lon"], pts.loc[~valid, "lat"]))
        pts.loc[~valid, "prefix"] = pts.loc[valid, "prefix"].values[near]
    return pts.drop_duplicates("code")


def _xy(lon, lat, lat0=48.5):
    """Equirectangular metres (good enough for snapping within a few km)."""
    return np.column_stack([np.asarray(lon) * EARTH_M_PER_DEG * np.cos(np.radians(lat0)), np.asarray(lat) * EARTH_M_PER_DEG])


def build_agglomeration() -> dict:
    t0 = time.time()
    years = available_years()
    if not years:
        raise FileNotFoundError("No Derzhstat workbooks in data/demography/raw")
    cities = [c for c in load_cities() if (Paths.interim(c.id) / "boundary.geojson").exists()]
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
    tree = cKDTree(_xy(vlon, vlat))
    d_snap, v_snap = tree.query(_xy(sett["lon"], sett["lat"]))
    reachable = d_snap <= SNAP_MAX_M

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
        ctr = shapely.Point(*json.loads((Paths.web_city(c.id) / "stats.json").read_text(encoding="utf-8"))["map"]["center"])
        centres.append({"id": c.id, "code": code, "settlement": code_index.get(code),
                        "lon": round(ctr.x, 5), "lat": round(ctr.y, 5)})
        if not len(src):
            log.warning("[%s] no road vertices inside the boundary", c.id)
            reach[c.id] = []
            continue
        t1 = time.time()
        tsec = dijkstra(g, directed=False, indices=src, min_only=True, limit=MAX_MIN * 60)
        ts = tsec[v_snap]
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
        "osm_timestamp": json.loads((Paths.web_city(cities[0].id) / "stats.json").read_text(encoding="utf-8")).get("osm_timestamp"),
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
    return out
