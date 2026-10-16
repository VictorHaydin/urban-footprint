"""Export of the precomputed datasets consumed by the static web app (web/data/).

Per city (web/data/<city>/):
    stats.json            statistics (copy of processed stats + validation summary + overview georef)
    boundary.geojson      simplified administrative boundary (WGS84)
    overview/<unit>/<cat>.pmtiles  per-unit, per-category coverage raster pyramid (lossless WebP tiles, z6–13)
    territories.geojson   outline of each territory mode (city / hromada / greater)
    settlements.geojson   (territorial cities) evaluated Greater City settlements with drive times
    features.pmtiles      vector tiles (layer "features") with every exclusive feature piece

Global:
    web/data/index.json   list of cities + category definitions (labels, colors, order)

Also writes data/processed/<city>/city_features.parquet: the full feature-level dataset
(exclusive pieces joined with OSM attributes/tags) intended for the animation tooling.
"""

from __future__ import annotations

import gzip
import io
import json
import logging
import math
import os
import shutil
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone

import geopandas as gpd
import mapbox_vector_tile
import numpy as np
import pandas as pd
import shapely
from PIL import Image
from pmtiles.tile import Compression, TileType, zxy_to_tileid
from pmtiles.writer import Writer
from rasterio import features as rio_features
from rasterio.transform import from_origin

from urbanfp.config import City, Classification, Paths, load_cities, load_city_groups

log = logging.getLogger(__name__)

WEB_MERC_HALF = 20037508.342789244
VT_MINZOOM = 14
VT_MAXZOOM = 14
VT_EXTENT = 4096
VT_BUFFER = 16           # tile units of overlap to avoid seams
VT_MIN_AREA = 4.0        # drop pieces smaller than this (tile units²) at the given zoom
OVERVIEW_MINZOOM = 6     # overview raster pyramid: zoom range, tile size and supersampling
OVERVIEW_MAXZOOM = 13    # z13 with 512 px tiles ≈ 6 m/px at Ukrainian latitudes
OVERVIEW_TILE = 512
OVERVIEW_SS = 4



# --------------------------------------------------------------------------- feature dataset

def build_city_features(city: City) -> gpd.GeoDataFrame:
    interim, proc = Paths.interim(city.id), Paths.processed(city.id)
    excl = gpd.read_parquet(proc / "exclusive.parquet")
    attrs = pd.read_parquet(interim / "classified.parquet",
                            columns=["fid", "osm_type", "osm_id", "kind", "basis", "rule_tag", "width_m",
                                     "width_source", "name", "levels", "tags", "footprint_area_m2"])
    gdf = excl.merge(attrs, on="fid", how="left")
    gdf = gpd.GeoDataFrame(gdf, geometry="geometry", crs=excl.crs)
    cent = gdf.geometry.representative_point().to_crs("EPSG:4326")
    gdf["lon"] = cent.x.round(6)
    gdf["lat"] = cent.y.round(6)
    gdf.to_parquet(proc / "city_features.parquet", compression="zstd")
    return gdf


# --------------------------------------------------------------------------- overview rasters
#
# Low-zoom view of the city: per-category coverage rasters (alpha = share of the pixel covered by
# that category, so edges and small buildings are anti-aliased). Exported as one raster PMTiles
# pyramid per category so browsers only load and decode the tiles on screen. A single full-size
# image per category needed ~70 MB of GPU memory each and crashed mobile Safari.

def _overview_tile(args):
    z, x, y, wkbs, codes, colors = args
    minx, miny, maxx, maxy = _tile_bounds(z, x, y)
    px = OVERVIEW_TILE * OVERVIEW_SS
    transform = from_origin(minx, maxy, (maxx - minx) / px, (maxy - miny) / px)
    geoms = shapely.from_wkb(wkbs)
    grid = rio_features.rasterize(zip(geoms, codes.tolist()), out_shape=(px, px), transform=transform,
                                  fill=0, dtype="uint8")
    blocks = grid.reshape(OVERVIEW_TILE, OVERVIEW_SS, OVERVIEW_TILE, OVERVIEW_SS)
    out = {}
    for code in np.unique(grid):
        if code == 0:
            continue
        alpha = np.round((blocks == code).sum(axis=(1, 3)) * (255.0 / OVERVIEW_SS ** 2)).astype(np.uint8)
        if not alpha.any():
            continue
        img = np.empty((OVERVIEW_TILE, OVERVIEW_TILE, 4), dtype=np.uint8)
        img[..., :3] = colors[code - 1]
        img[..., 3] = alpha
        buf = io.BytesIO()
        # lossless WebP: same pixels as PNG at ~45% of the size; supported by all current browsers
        Image.fromarray(img, "RGBA").save(buf, format="WEBP", lossless=True, method=4)
        out[int(code)] = buf.getvalue()
    return z, x, y, out


def export_overview(gdf3857: gpd.GeoDataFrame, cls: Classification, out_dir, bounds_ll, center) -> dict:
    cat_ids = [c["id"] for c in cls.categories]
    colors = np.array([[int(c["color"][k:k + 2], 16) for k in (1, 3, 5)] for c in cls.categories], dtype=np.uint8)
    codes_all = gdf3857["category"].map({c: i + 1 for i, c in enumerate(cat_ids)}).astype("uint8").to_numpy()
    geoms = np.asarray(gdf3857.geometry.values, dtype=object)
    tree = shapely.STRtree(geoms)
    minx, miny, maxx, maxy = gdf3857.total_bounds

    def tasks():
        for z in range(OVERVIEW_MINZOOM, OVERVIEW_MAXZOOM + 1):
            size = 2 * WEB_MERC_HALF / (1 << z)
            x0, x1 = int((minx + WEB_MERC_HALF) // size), int((maxx + WEB_MERC_HALF) // size)
            y0, y1 = int((WEB_MERC_HALF - maxy) // size), int((WEB_MERC_HALF - miny) // size)
            for x in range(x0, x1 + 1):
                for y in range(y0, y1 + 1):
                    b = _tile_bounds(z, x, y)
                    idx = tree.query(shapely.box(*b))
                    if len(idx) == 0:
                        continue
                    pad = size * 0.01
                    clipped = shapely.clip_by_rect(geoms[idx], b[0] - pad, b[1] - pad, b[2] + pad, b[3] + pad)
                    keep = ~shapely.is_empty(clipped)
                    if not keep.any():
                        continue
                    yield z, x, y, shapely.to_wkb(clipped[keep]), codes_all[idx][keep], colors

    per_cat: dict[int, dict[int, bytes]] = {}
    t0 = time.time()
    with ProcessPoolExecutor(max_workers=max(1, (os.cpu_count() or 2) - 1)) as ex:
        for z, x, y, out in ex.map(_overview_tile, tasks(), chunksize=2):
            tid = zxy_to_tileid(z, x, y)
            for code, data in out.items():
                per_cat.setdefault(code, {})[tid] = data
    log.info("  overview tiles rendered in %.0fs", time.time() - t0)

    out_dir.mkdir(parents=True, exist_ok=True)
    files, total = {}, 0
    for code, tiles in sorted(per_cat.items()):
        cat = cat_ids[code - 1]
        path = out_dir / f"{cat}.pmtiles"
        with open(path, "wb") as f:
            w = Writer(f)
            for tid in sorted(tiles):
                w.write_tile(tid, tiles[tid])
            w.finalize({
                "tile_type": TileType.WEBP, "tile_compression": Compression.NONE,
                "min_zoom": OVERVIEW_MINZOOM, "max_zoom": OVERVIEW_MAXZOOM,
                "min_lon_e7": int(bounds_ll[0] * 1e7), "min_lat_e7": int(bounds_ll[1] * 1e7),
                "max_lon_e7": int(bounds_ll[2] * 1e7), "max_lat_e7": int(bounds_ll[3] * 1e7),
                "center_zoom": OVERVIEW_MINZOOM + 3,
                "center_lon_e7": int(center[0] * 1e7), "center_lat_e7": int(center[1] * 1e7),
            }, {"name": cat, "attribution": "© OpenStreetMap contributors (ODbL)"})
        files[cat] = f"overview/{cat}.pmtiles"
        total += path.stat().st_size
    res_m = 2 * WEB_MERC_HALF / (OVERVIEW_TILE * (1 << OVERVIEW_MAXZOOM)) * math.cos(math.radians(center[1]))
    return {"format": "pmtiles", "files": files, "minzoom": OVERVIEW_MINZOOM, "maxzoom": OVERVIEW_MAXZOOM,
            "tile_size": OVERVIEW_TILE, "pixel_size_m": round(res_m, 2), "total_bytes": total,
            "tiles": sum(len(t) for t in per_cat.values())}


# --------------------------------------------------------------------------- vector tiles

def _tile_bounds(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    size = 2 * WEB_MERC_HALF / (1 << z)
    minx = -WEB_MERC_HALF + x * size
    maxy = WEB_MERC_HALF - y * size
    return minx, maxy - size, minx + size, maxy


def _encode_tile(args):
    z, x, y, wkbs, props = args
    minx, miny, maxx, maxy = _tile_bounds(z, x, y)
    size = maxx - minx
    unit = size / VT_EXTENT
    buf = VT_BUFFER * unit
    geoms = shapely.from_wkb(wkbs)
    geoms = shapely.clip_by_rect(geoms, minx - buf, miny - buf, maxx + buf, maxy + buf)
    geoms = shapely.simplify(geoms, unit * 0.5, preserve_topology=True)
    # to tile coordinates (y down)
    geoms = shapely.transform(geoms, lambda c: np.column_stack(((c[:, 0] - minx) / unit, (maxy - c[:, 1]) / unit)))
    areas = shapely.area(geoms)
    feats = []
    for g, a, p in zip(geoms, areas, props):
        if g is None or g.is_empty or a < VT_MIN_AREA:
            continue
        if g.geom_type not in ("Polygon", "MultiPolygon"):
            parts = [q for q in shapely.get_parts(g) if q.geom_type == "Polygon"]
            if not parts:
                continue
            g = shapely.multipolygons(parts)
        feats.append({"geometry": g, "properties": p})
    if not feats:
        return z, x, y, None
    data = mapbox_vector_tile.encode(
        [{"name": "features", "features": feats}],
        default_options={"y_coord_down": True, "extents": VT_EXTENT,
                         "on_invalid_geometry": mapbox_vector_tile.encoder.on_invalid_geometry_make_valid})
    return z, x, y, gzip.compress(data, mtime=0)


def export_pmtiles(gdf3857: gpd.GeoDataFrame, path, bounds_ll, center) -> dict:
    geoms = np.asarray(gdf3857.geometry.values, dtype=object)
    props = []
    for r in gdf3857.itertuples(index=False):
        p = {"c": r.category, "s": r.subcategory, "id": r.fid, "a": int(round(r.area_m2)), "u": r.unit}
        if isinstance(r.name, str) and r.name:
            p["n"] = r.name
        if isinstance(r.rule_tag, str) and r.rule_tag:
            p["t"] = r.rule_tag
        if isinstance(r.width_m, float) and not math.isnan(r.width_m):
            p["w"] = round(r.width_m, 1)
        props.append(p)
    tree = shapely.STRtree(geoms)
    wkb_all = shapely.to_wkb(geoms)
    minx, miny, maxx, maxy = gdf3857.total_bounds

    def tasks():
        for z in range(VT_MINZOOM, VT_MAXZOOM + 1):
            size = 2 * WEB_MERC_HALF / (1 << z)
            x0, x1 = int((minx + WEB_MERC_HALF) // size), int((maxx + WEB_MERC_HALF) // size)
            y0, y1 = int((WEB_MERC_HALF - maxy) // size), int((WEB_MERC_HALF - miny) // size)
            for x in range(x0, x1 + 1):
                for y in range(y0, y1 + 1):
                    b = _tile_bounds(z, x, y)
                    idx = tree.query(shapely.box(*b))
                    if len(idx) == 0:
                        continue
                    idx.sort()
                    yield z, x, y, wkb_all[idx], [props[i] for i in idx]

    results = {}
    sizes = {}
    t0 = time.time()
    with ProcessPoolExecutor(max_workers=max(1, (os.cpu_count() or 2) - 1)) as ex:
        for z, x, y, data in ex.map(_encode_tile, tasks(), chunksize=4):
            if data is not None:
                results[zxy_to_tileid(z, x, y)] = data
                sizes.setdefault(z, []).append(len(data))
    log.info("  encoded %d tiles in %.0fs", len(results), time.time() - t0)

    tmp = path.with_suffix(".pmtiles.tmp")
    with open(tmp, "wb") as f:
        w = Writer(f)
        for tid in sorted(results):
            w.write_tile(tid, results[tid])
        header = {
            "tile_type": TileType.MVT, "tile_compression": Compression.GZIP,
            "min_zoom": VT_MINZOOM, "max_zoom": VT_MAXZOOM,
            "min_lon_e7": int(bounds_ll[0] * 1e7), "min_lat_e7": int(bounds_ll[1] * 1e7),
            "max_lon_e7": int(bounds_ll[2] * 1e7), "max_lat_e7": int(bounds_ll[3] * 1e7),
            "center_zoom": VT_MINZOOM, "center_lon_e7": int(center[0] * 1e7), "center_lat_e7": int(center[1] * 1e7),
        }
        meta = {"vector_layers": [{"id": "features", "minzoom": VT_MINZOOM, "maxzoom": VT_MAXZOOM,
                                   "fields": {"c": "String", "s": "String", "id": "String", "a": "Number",
                                              "n": "String", "t": "String", "w": "Number"}}],
                "attribution": "© OpenStreetMap contributors (ODbL)"}
        w.finalize(header, meta)
    os.replace(tmp, path)
    return {z: {"tiles": len(s), "max_kb": round(max(s) / 1024, 1), "mean_kb": round(sum(s) / len(s) / 1024, 1)}
            for z, s in sorted(sizes.items())}


# --------------------------------------------------------------------------- driver

def export_city(city: City, cls: Classification) -> None:
    t0 = time.time()
    proc, interim, web = Paths.processed(city.id), Paths.interim(city.id), Paths.web_city(city.id)
    web.mkdir(parents=True, exist_ok=True)

    gdf = build_city_features(city)
    log.info("[%s] city_features.parquet: %d pieces", city.id, len(gdf))

    boundary = gpd.read_file(interim / "boundary.geojson")
    b = boundary.geometry.iloc[0]
    center = (b.centroid.x, b.centroid.y)
    bsimple = boundary.copy()
    bsimple["geometry"] = shapely.set_precision(bsimple.geometry.simplify(0.00002), 1e-6)
    bsimple.to_file(web / "boundary.geojson", driver="GeoJSON", COORDINATE_PRECISION=6)

    g3857 = gdf[["fid", "unit", "category", "subcategory", "area_m2", "name", "rule_tag", "width_m", "geometry"]].to_crs("EPSG:3857")

    # ---- territory modes: outlines + bounds per mode, settlement table
    terr = json.loads((interim / "territories.json").read_text(encoding="utf-8"))
    units = gpd.read_parquet(interim / "units.parquet")
    ugeom = dict(zip(units["unit"], units.geometry.values))
    mode_rows, mode_bounds = [], {}
    for m, spec in terr["modes"].items():
        g = shapely.union_all([ugeom[u] for u in spec["units"] if u in ugeom], grid_size=0.01)
        gw = gpd.GeoSeries([g], crs=units.crs).simplify(15).to_crs(4326).iloc[0]
        mode_rows.append({"mode": m, "status": spec["status"], "geometry": gw})
        mode_bounds[m] = [round(v, 6) for v in gw.bounds]
    gpd.GeoDataFrame(mode_rows, geometry="geometry", crs=4326).to_file(
        web / "territories.geojson", driver="GeoJSON", COORDINATE_PRECISION=5)
    (web / "settlements.geojson").unlink(missing_ok=True)
    if (interim / "settlements.geojson").exists():
        shutil.copyfile(interim / "settlements.geojson", web / "settlements.geojson")
    region_b = units.to_crs(4326).total_bounds

    if (web / "overview").exists():
        shutil.rmtree(web / "overview")
    overview = {"format": "pmtiles", "files": {}, "minzoom": OVERVIEW_MINZOOM, "maxzoom": OVERVIEW_MAXZOOM,
                "tile_size": OVERVIEW_TILE, "tiles": 0, "total_bytes": 0}
    for unit in units["unit"]:
        part = g3857[g3857["unit"] == unit]
        if not len(part):
            continue
        ov = export_overview(part, cls, web / "overview" / unit, region_b, center)
        overview["files"][unit] = {k: f"overview/{unit}/{v.split('/', 1)[1]}" for k, v in ov["files"].items()}
        overview["tiles"] += ov["tiles"]
        overview["total_bytes"] += ov["total_bytes"]
        overview["pixel_size_m"] = ov["pixel_size_m"]
    log.info("[%s] overview pyramids (units %s): %d tiles, %.1f MB", city.id, list(overview["files"]),
             overview["tiles"], overview["total_bytes"] / 1e6)

    tile_stats = export_pmtiles(g3857, web / "features.pmtiles", region_b, center)
    log.info("[%s] features.pmtiles %.1f MB, tile sizes %s", city.id,
             (web / "features.pmtiles").stat().st_size / 1e6, tile_stats)

    stats = json.loads((proc / "stats.json").read_text(encoding="utf-8"))
    validation = json.loads((proc / "validation.json").read_text(encoding="utf-8"))
    stats["validation"] = {"status": validation["status"], "checks": validation["checks"]}
    for m, v in (validation.get("modes") or {}).items():
        if m in stats.get("modes", {}):
            stats["modes"][m]["validation"] = v
    stats["map"] = {
        "bounds": [round(v, 6) for v in b.bounds],
        "mode_bounds": mode_bounds,
        "territories": "territories.geojson",
        "settlements": "settlements.geojson" if (web / "settlements.geojson").exists() else None,
        "center": [round(center[0], 6), round(center[1], 6)],
        "overview": overview,
        "vector_tiles": {"file": "features.pmtiles", "minzoom": VT_MINZOOM, "maxzoom": VT_MAXZOOM,
                         "layer": "features", "tile_stats": tile_stats},
    }
    (web / "stats.json").write_text(json.dumps(stats, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    log.info("[%s] web export done in %.0fs", city.id, time.time() - t0)


def export_index(cls: Classification) -> None:
    cities = []
    for c in load_cities():
        p = Paths.web_city(c.id) / "stats.json"
        if not p.exists():
            continue
        s = json.loads(p.read_text(encoding="utf-8"))
        cities.append({"id": c.id, "name": c.name, "name_local": c.name_local, "country": c.country,
                       "area_km2": s["area"]["boundary_km2"], "osm_timestamp": s["osm_timestamp"],
                       "center": s["map"]["center"], "bounds": s["map"]["bounds"],
                       "osm_relation": c.boundary_relation, "notes": c.notes, "notes_uk": c.notes_uk,
                       "group": c.group, "occupied": c.occupied,
                       "modes": {m: {"status": d.get("status"), "area_km2": d["area"]["boundary_km2"]}
                                 for m, d in (s.get("modes") or {}).items()}})
    index = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "config_hash": cls.hash,
        "categories": cls.categories,
        "city_groups": load_city_groups(),
        "cities": cities,
        "attribution": "Map data © OpenStreetMap contributors, available under the Open Database License (ODbL).",
    }
    Paths.web_data().mkdir(parents=True, exist_ok=True)
    (Paths.web_data() / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    log.info("web/data/index.json: %d cities", len(cities))
