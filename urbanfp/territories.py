"""Territory modes: City · Hromada · Greater City (design: docs/SUBURBS.md).

For every city this stage defines up to four disjoint *units* (local equal-area CRS):

    A  the city itself
    B  land in both the hromada and Greater City (outside the city)
    C  land in the hromada only
    D  land in Greater City only

    City = A      Hromada = A + B + C      Greater City = A + B + D

Overlay, statistics and map data are computed per unit, so each mode is an exact sum of units and nothing
is stored twice.

Greater City rule: a settlement is included if the median free-flow car drive time from road points in
its built-up area to the nearest built-up part of the city proper is <= GREATER_DRIVE_MIN minutes
(non-transitive: time is never measured via other settlements). Settlements are polygons (admin_level 8/9
boundaries or place=* areas) or, where OSM only has a place=* point, an approximate outline derived from
the built-up land around the point.

Outputs (data/interim/<city>/):
    units.parquet         unit id (A-D) + geometry, local CRS
    settlements.geojson   evaluated settlements with metrics (WGS84, simplified)
    territories.json      modes -> units, status, parameters, summary
"""

from __future__ import annotations

import json
import logging
import re
import time

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from rasterio import features as rio_features
from rasterio.features import shapes as rio_shapes
from rasterio.transform import from_origin
from scipy import ndimage
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import dijkstra

from urbanfp.classify import local_crs
from urbanfp.config import City, Paths

log = logging.getLogger(__name__)

TERRITORIES_VERSION = 1
GREATER_DRIVE_MIN = 10.0          # drive-time limit D (minutes)
RES = 10.0                        # analysis grid (m)
GAP_M = 200                       # built-up continuity: gaps up to 200 m
POINT_OUTLINE_MAX_M = 2000        # approximate outlines: at most this far from the place point
POINT_SNAP_M = 500                # ... and only if built-up land lies within this distance
MIN_UNIT_PART_M2 = 5_000          # drop slivers from boundary mismatches
PLACES = {"city", "town", "village", "hamlet"}
URBAN_LANDUSE = {"residential", "industrial", "commercial", "retail", "garages", "construction"}
# free-flow car speeds by road class (km/h); maxspeed tags are used when present (capped at 1.3x)
SPEED = {"motorway": 100, "trunk": 80, "primary": 60, "secondary": 50, "tertiary": 45, "motorway_link": 50,
         "trunk_link": 45, "primary_link": 40, "secondary_link": 35, "tertiary_link": 30, "unclassified": 35,
         "residential": 25, "living_street": 10, "service": 15, "road": 30, "track": 15}
MODES = ("city", "hromada", "greater")


def _clean(g, min_part=MIN_UNIT_PART_M2):
    if g is None or g.is_empty:
        return None
    g = shapely.make_valid(g)
    parts = [p for p in shapely.get_parts(g) if p.geom_type == "Polygon" and p.area >= min_part]
    return shapely.multipolygons(parts) if parts else None


def compose_units(A, H=None, G=None) -> dict:
    """Disjoint territory units from city A, hromada H and Greater City area G (any may overlap):
    A city · B = (H ∩ G) − A · C = H − G − A · D = G − H − A. Slivers below MIN_UNIT_PART_M2 are dropped."""
    units = {"A": A}
    if H is not None:
        h_rest = shapely.difference(H, A, grid_size=0.01)
        if G is not None:
            units["B"] = shapely.intersection(h_rest, G, grid_size=0.01)
            units["C"] = shapely.difference(h_rest, G, grid_size=0.01)
        else:
            units["C"] = h_rest
    if G is not None:
        rest = shapely.difference(G, A, grid_size=0.01)
        units["D"] = shapely.difference(rest, H, grid_size=0.01) if H is not None else rest
    units = {k: (v if k == "A" else _clean(v)) for k, v in units.items()}
    return {k: v for k, v in units.items() if v is not None and not v.is_empty}


def _load(city: City):
    d = Paths.interim(city.id)
    bnd = gpd.read_file(d / "boundary.geojson").geometry.iloc[0]
    crs = local_crs(bnd)
    city_p = gpd.GeoSeries([bnd], crs=4326).to_crs(crs).iloc[0]
    hromada_p = None
    if city.hromada_relation and (d / "hromada.geojson").exists():
        hromada_p = gpd.read_file(d / "hromada.geojson").to_crs(crs).geometry.iloc[0]
    return d, crs, shapely.make_valid(city_p), (shapely.make_valid(hromada_p) if hromada_p is not None else None)


# --------------------------------------------------------------------------- settlements

def _select_settlements(sett_raw: gpd.GeoDataFrame, city_p, radius_m: float) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Polygons (deduplicated) and place points within the radius, outside the city."""
    tags = sett_raw["tags"].map(json.loads)
    # Ukrainian name first: in Crimea and parts of the occupied east the plain `name` tag is Russian
    sett_raw = sett_raw.assign(t=tags.values, name=tags.map(lambda t: t.get("name:uk") or t.get("name", "")).values,
                               place=tags.map(lambda t: t.get("place") or "").values)
    polys = sett_raw[sett_raw["kind"] == "area"]
    # settlements only: place=city|town|village|hamlet, or an untagged admin_level 9 boundary. Excludes city
    # districts such as Makiivka's boroughs (admin_level 8, place=borough).
    polys = polys[polys["place"].isin(PLACES) | (polys["t"].map(lambda t: t.get("admin_level") == "9") & (polys["place"] == ""))]
    pts = sett_raw[(sett_raw["kind"] == "point") & sett_raw["place"].isin(PLACES)]
    polys = polys[(polys.geometry.distance(city_p) < radius_m) & ~polys.geometry.representative_point().within(city_p)]
    polys = polys[polys.geometry.area > 0]
    # the same settlement may be mapped twice (boundary + place area): prefer the boundary, then the larger
    # polygon; drop polygons lying >= 50% inside one already kept
    polys = polys.assign(_pref=polys["t"].map(lambda t: 0 if t.get("boundary") == "administrative" else 1),
                         _area=polys.geometry.area).sort_values(["_pref", "_area"], ascending=[True, False])
    kept, tree_geoms = [], []
    for idx, g in zip(polys.index, polys.geometry.values):
        if any(shapely.intersection(g, k).area >= 0.5 * g.area for k in tree_geoms if k.intersects(g)):
            continue
        kept.append(idx)
        tree_geoms.append(g)
    polys = polys.loc[kept]
    # type of a boundary without place tag: take it from a place area / point of the same name
    by_name = {n: p for n, p in zip(sett_raw["name"], sett_raw["place"]) if p in PLACES}
    polys = polys.assign(place=[p if p in PLACES else by_name.get(n, "?") for p, n in zip(polys["place"], polys["name"])])
    pts = pts[(pts.geometry.distance(city_p) < radius_m) & ~pts.geometry.within(city_p)]
    return polys.reset_index(drop=True), pts.reset_index(drop=True)


# --------------------------------------------------------------------------- driver

def build_territories(city: City) -> dict:
    t0 = time.time()
    d, crs, city_p, hromada_p = _load(city)
    if hromada_p is not None and city.hromada_formal_only:
        hromada_status = "occupied"
    elif hromada_p is None:
        hromada_status = "no_hromada"
    else:
        hromada_status = "ok"

    settlements_out = gpd.GeoDataFrame(columns=["name", "type", "approx", "built_ha", "contig_share", "drive_min",
                                                "in_hromada", "in_greater", "geometry"], geometry="geometry", crs=crs)
    greater_g = None
    if city.territories:
        settlements_out, greater_g = _greater_city(city, d, crs, city_p, hromada_p if hromada_status == "ok" else None)

    units = compose_units(city_p, hromada_p if hromada_status == "ok" else None, greater_g)

    modes = {
        "city": {"units": ["A"], "status": "ok"},
        "hromada": {"units": [u for u in ("A", "B", "C") if u in units], "status": hromada_status},
        "greater": {"units": [u for u in ("A", "B", "D") if u in units],
                    "status": "ok" if city.territories else "not_computed"},
    }
    if hromada_status == "ok" and modes["hromada"]["units"] == ["A"]:
        modes["hromada"]["status"] = "same_as_city"
    if not city.territories:
        modes["hromada"]["status"] = "not_computed" if hromada_status == "ok" else hromada_status
        modes["hromada"]["units"] = ["A"]
    for m in modes.values():
        m["area_km2"] = round(sum(units[u].area for u in m["units"]) / 1e6, 3)

    gpd.GeoDataFrame({"unit": list(units)}, geometry=list(units.values()), crs=crs).to_parquet(d / "units.parquet")
    (d / "settlements.geojson").unlink(missing_ok=True)
    if len(settlements_out):
        out_s = settlements_out.copy()
        out_s["geometry"] = out_s.geometry.simplify(20)
        out_s.to_crs(4326).to_file(d / "settlements.geojson", driver="GeoJSON", COORDINATE_PRECISION=5)
    info = {
        "version": TERRITORIES_VERSION,
        "greater_drive_min": GREATER_DRIVE_MIN,
        "suburbs_radius_km": city.suburbs_radius_km,
        "modes": modes,
        "units_km2": {k: round(v.area / 1e6, 3) for k, v in units.items()},
        "settlements_evaluated": int(len(settlements_out)),
        "settlements_in_greater": int(settlements_out["in_greater"].sum()) if len(settlements_out) else 0,
        "approx_outlines": int(settlements_out["approx"].sum()) if len(settlements_out) else 0,
        "seconds": round(time.time() - t0, 1),
    }
    (d / "territories.json").write_text(json.dumps(info, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("[%s] territories: %s", city.id, {m: (v["status"], v["area_km2"]) for m, v in modes.items()})
    return info


def _greater_city(city: City, d, crs, city_p, hromada_p):
    """Evaluate settlements around the city; returns (settlements gdf, union of included outlines)."""
    radius = city.suburbs_radius_km * 1000
    raw = gpd.read_parquet(d / "osm_features.parquet")
    tags = raw["tags"].map(json.loads)
    is_area = (raw["kind"] == "area").values
    sett_raw = gpd.read_parquet(d / "settlements.parquet").to_crs(crs)
    polys, pts = _select_settlements(sett_raw, city_p, radius)

    # ---- built-up raster (buildings + urban land use)
    fab_mask = is_area & tags.map(lambda t: ("building" in t and t.get("building") != "no")
                                  or t.get("landuse") in URBAN_LANDUSE).values
    fabric = raw[fab_mask].to_crs(crs).geometry.values
    minx, miny, maxx, maxy = shapely.buffer(city_p, radius + 5000).bounds
    W, H = int((maxx - minx) / RES), int((maxy - miny) / RES)
    T = from_origin(minx, maxy, RES, RES)
    fab = rio_features.rasterize(((g, 1) for g in fabric), out_shape=(H, W), transform=T, fill=0, dtype="uint8").astype(bool)
    near_fab = ndimage.binary_dilation(fab, iterations=5)  # within ~50 m of built-up land
    cityr = rio_features.rasterize([(city_p, 1)], out_shape=(H, W), transform=T, fill=0, dtype="uint8").astype(bool)
    r = int(GAP_M / 2 / RES)
    yy, xx = np.ogrid[-r:r + 1, -r:r + 1]
    grown = ndimage.binary_dilation(fab, structure=(xx * xx + yy * yy <= r * r))
    lab, _ = ndimage.label(grown, structure=np.ones((3, 3)))
    gap_cluster = np.isin(lab, np.unique(lab[cityr & (lab > 0)]))
    del grown
    polyr = rio_features.rasterize(((g, i + 1) for i, g in enumerate(polys.geometry.values)),
                                   out_shape=(H, W), transform=T, fill=0, dtype="int32")

    # ---- point-only settlements: approximate outline from built-up land around the point
    pc = ((pts.geometry.x - minx) / RES).astype(int).values
    pr = ((maxy - pts.geometry.y) / RES).astype(int).values
    ok = (pc >= 0) & (pc < W) & (pr >= 0) & (pr < H)
    pts, pc, pr = pts[ok].reset_index(drop=True), pc[ok], pr[ok]
    only = polyr[pr, pc] == 0
    pts, pc, pr = pts[only].reset_index(drop=True), pc[only], pr[only]
    R2, RS = int(POINT_OUTLINE_MAX_M / RES), int(POINT_SNAP_M / RES)
    approx_rows = []
    for k, nd in pts.iterrows():
        c0, r0 = pc[k], pr[k]
        x0, x1, y0, y1 = max(c0 - R2, 0), min(c0 + R2 + 1, W), max(r0 - R2, 0), min(r0 + R2 + 1, H)
        win = lab[y0:y1, x0:x1]
        yy_, xx_ = np.mgrid[y0:y1, x0:x1]
        dist = np.hypot(xx_ - c0, yy_ - r0)
        near = (dist <= RS) & (win > 0)
        if not near.any():
            continue
        comp = np.bincount(win[near]).argmax()
        m = (win == comp) & (dist <= R2) & (polyr[y0:y1, x0:x1] == 0) & ~cityr[y0:y1, x0:x1]
        for j in range(len(pts)):
            if j != k and abs(pc[j] - c0) < 2 * R2 and abs(pr[j] - r0) < 2 * R2:
                m &= dist <= np.hypot(xx_ - pc[j], yy_ - pr[j])
        if m.sum() * RES * RES < 5000:
            continue
        tr = from_origin(minx + x0 * RES, maxy - y0 * RES, RES, RES)
        geom = shapely.union_all([shapely.geometry.shape(g) for g, v in rio_shapes(m.astype("uint8"), mask=m, transform=tr) if v == 1])
        approx_rows.append({"name": nd["name"], "place": nd["place"], "approx": True, "osm": f"n{nd['osm_id']}", "geometry": geom})
    polys = polys.assign(approx=False, osm=polys["osm_type"] + polys["osm_id"].astype(str))
    sett = pd.concat([polys[["name", "place", "approx", "osm", "geometry"]],
                      gpd.GeoDataFrame(approx_rows, geometry="geometry", crs=crs) if approx_rows else None],
                     ignore_index=True)
    sett = gpd.GeoDataFrame(sett, geometry="geometry", crs=crs)
    settr = rio_features.rasterize(((g, i + 1) for i, g in enumerate(sett.geometry.values)),
                                   out_shape=(H, W), transform=T, fill=0, dtype="int32")
    del lab, polyr

    # ---- road graph and drive times to the city's built-up area
    rd_mask = (raw["kind"] == "line").values & tags.map(
        lambda t: t.get("highway") in SPEED and t.get("access") not in ("no", "private")).values
    rd = raw[rd_mask].to_crs(crs)
    key_to_id, coords_all, U, V, Wt = {}, [], [], [], []

    def vid(p):
        key = (round(p[0], 2), round(p[1], 2))  # shared OSM nodes coincide exactly
        i = key_to_id.get(key)
        if i is None:
            i = key_to_id[key] = len(coords_all)
            coords_all.append(p)
        return i

    for g, t in zip(rd.geometry.values, tags[rd_mask].values):
        mm = re.match(r"^\s*(\d+)", t.get("maxspeed", "") or "")
        sp = min(float(mm.group(1)) if mm else SPEED[t["highway"]], SPEED[t["highway"]] * 1.3) / 3.6
        c = np.asarray(g.coords)
        ids = [vid(tuple(p)) for p in c]
        U += ids[:-1]
        V += ids[1:]
        Wt += list(np.hypot(np.diff(c[:, 0]), np.diff(c[:, 1])) / sp)
    coords = np.array(coords_all)
    n = len(coords)
    G = coo_matrix((Wt + Wt, (U + V, V + U)), shape=(n, n)).tocsr()
    col = ((coords[:, 0] - minx) / RES).astype(int)
    row = ((maxy - coords[:, 1]) / RES).astype(int)
    inside = (col >= 0) & (col < W) & (row >= 0) & (row < H)
    colc, rowc = np.clip(col, 0, W - 1), np.clip(row, 0, H - 1)
    src = np.nonzero(inside & cityr[rowc, colc] & near_fab[rowc, colc])[0]
    t_sec = dijkstra(G, directed=False, indices=src, min_only=True)
    log.info("[%s] road graph %d vertices, %d in the city's built-up area", city.id, n, len(src))

    # ---- metrics per settlement
    vert_sett = np.where(inside, settr[rowc, colc], 0)
    vert_ok = near_fab[rowc, colc] & inside
    rows = []
    for i, s in sett.iterrows():
        m = settr == i + 1
        built = float((fab & m).sum()) * RES * RES
        contig = float((fab & m & gap_cluster).sum()) * RES * RES / built if built else 0.0
        tt = t_sec[(vert_sett == i + 1) & vert_ok]
        tt = tt[np.isfinite(tt)]
        drive = float(np.median(tt) / 60) if len(tt) else None
        rows.append({
            "name": s["name"], "type": s["place"], "approx": bool(s["approx"]), "osm": s["osm"],
            "built_ha": round(built / 1e4, 1), "contig_share": round(contig, 2),
            "drive_min": round(drive, 1) if drive is not None else None,
            "dist_km": round(s.geometry.distance(city_p) / 1000, 1),
            "in_hromada": (bool(s.geometry.representative_point().within(hromada_p)) if hromada_p is not None else None),
            "in_greater": drive is not None and drive <= GREATER_DRIVE_MIN,
        })
    res = gpd.GeoDataFrame(pd.DataFrame(rows), geometry=sett.geometry.values, crs=crs)
    inc = res[res["in_greater"]]
    greater = shapely.union_all(list(inc.geometry.values) + [city_p], grid_size=0.01) if len(inc) else None
    log.info("[%s] %d settlements evaluated (%d approximate outlines), %d within %g min", city.id, len(res),
             int(res["approx"].sum()), len(inc), GREATER_DRIVE_MIN)
    return res, greater
