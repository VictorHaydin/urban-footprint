"""Extraction of city boundaries and raw OSM features from a country PBF extract.

Two streaming passes with pyosmium (libosmium does multipolygon assembly):

1. boundaries:  assemble the administrative boundary relations of the requested cities.
2. features:    for all requested cities at once, collect every way / multipolygon that
                carries one of the keys used by the classification rules and intersects the
                city boundary. Original geometry (WGS84) and ALL original tags are kept.

Outputs per city (data/interim/<city>/):
    boundary.geojson      administrative boundary (WGS84)
    osm_features.parquet  GeoParquet: osm_type, osm_id, kind (area|line), tags (JSON), geometry
    extract.json          provenance (source file, OSM timestamp, counts, failures)
"""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from pathlib import Path

import geopandas as gpd
import numpy as np
import osmium
import pandas as pd
import shapely

from urbanfp.config import City, Classification, Paths
from urbanfp.osm_tags import is_area_semantic
from urbanfp.sources import ensure_extract, source_info

log = logging.getLogger(__name__)

EXTRACT_VERSION = 1
# Keys needed beyond the ones referenced by rules (for exclusions / context).
EXTRA_KEYS = {"area:highway"}
# Keys that make a (non-closed or non-area) way relevant as a line feature.
LINE_KEYS = {"highway", "railway", "waterway", "aeroway"}
BATCH = 50_000


def relevant_keys(cls: Classification) -> list[str]:
    keys = set(EXTRA_KEYS)
    for r in cls.rules:
        keys.update(r.match.spec.keys())
    return sorted(keys)


# --------------------------------------------------------------------------- boundaries

def extract_boundaries(pbf: Path, cities: list[City]) -> dict[str, shapely.Geometry]:
    rel_ids = {c.boundary_relation for c in cities}
    area_ids = {2 * r + 1 for r in rel_ids}  # libosmium area id for relations
    wkb = osmium.geom.WKBFactory()
    found: dict[int, shapely.Geometry] = {}
    t0 = time.time()
    fp = (osmium.FileProcessor(str(pbf))
          .with_locations("flex_mem")
          .with_areas(osmium.filter.IdFilter(rel_ids))
          .with_filter(osmium.filter.EntityFilter(osmium.osm.AREA))
          .with_filter(osmium.filter.IdFilter(area_ids)))
    for obj in fp:
        if obj.from_way():
            continue
        found[obj.orig_id()] = shapely.from_wkb(wkb.create_multipolygon(obj))
    log.info("Boundary pass done in %.0fs", time.time() - t0)
    out = {}
    for c in cities:
        g = found.get(c.boundary_relation)
        if g is None or g.is_empty:
            raise RuntimeError(f"Boundary relation {c.boundary_relation} for {c.id} not found "
                               f"or could not be assembled (broken multipolygon?)")
        out[c.id] = shapely.make_valid(g)
    return out


# --------------------------------------------------------------------------- features

class _CityBucket:
    def __init__(self, city: City, boundary: shapely.Geometry):
        self.city = city
        self.boundary = boundary
        shapely.prepare(boundary)
        minx, miny, maxx, maxy = boundary.bounds
        self.bbox = (minx, miny, maxx, maxy)
        self.rows: list[tuple] = []
        self.geoms: list[shapely.Geometry] = []


def extract_features(pbf: Path, buckets: list[_CityBucket], keys: list[str]) -> dict:
    """Single pass over `pbf` collecting features for all buckets."""
    # Hot loop over every relevant object of the whole country: keep the per-object Python
    # work minimal. Geometry is built in C++ (WKB), the bbox test uses shapely's scalar bounds,
    # and tags are only converted for objects that fall into a city bbox.
    wkbf = osmium.geom.WKBFactory()
    stats: dict[str, int] = defaultdict(int)
    pending_meta: list[tuple] = []
    pending_geom: list = []
    gminx = min(b.bbox[0] for b in buckets)
    gminy = min(b.bbox[1] for b in buckets)
    gmaxx = max(b.bbox[2] for b in buckets)
    gmaxy = max(b.bbox[3] for b in buckets)
    city_bboxes = [b.bbox for b in buckets]
    from_wkb = shapely.from_wkb
    line_keys = tuple(LINE_KEYS)

    def flush():
        if not pending_geom:
            return
        geoms = np.array(pending_geom, dtype=object)
        bounds = shapely.bounds(geoms)
        for b in buckets:
            bx = b.bbox
            mask = ((bounds[:, 0] <= bx[2]) & (bounds[:, 2] >= bx[0])
                    & (bounds[:, 1] <= bx[3]) & (bounds[:, 3] >= bx[1]))
            idx = np.nonzero(mask)[0]
            if len(idx) == 0:
                continue
            hit = shapely.intersects(b.boundary, geoms[idx])
            for j in idx[hit]:
                b.rows.append(pending_meta[j])
                b.geoms.append(geoms[j])
        pending_meta.clear()
        pending_geom.clear()

    t0 = time.time()
    n = 0
    fp = (osmium.FileProcessor(str(pbf))
          .with_locations("flex_mem")
          .with_areas(osmium.filter.KeyFilter(*keys))
          .with_filter(osmium.filter.EntityFilter(osmium.osm.WAY | osmium.osm.AREA))
          .with_filter(osmium.filter.KeyFilter(*keys)))
    for obj in fp:
        n += 1
        if n % 2_000_000 == 0:
            log.info("  ... %d objects scanned, %d kept (%.0fs)", n, stats["areas"] + stats["lines"], time.time() - t0)
        is_area = obj.is_area()
        if not is_area:
            ot = obj.tags
            if not any(k in ot for k in line_keys):
                continue
        try:
            g = from_wkb(wkbf.create_multipolygon(obj) if is_area else wkbf.create_linestring(obj))
        except (RuntimeError, osmium.InvalidLocationError):
            stats["area_geometry_failed" if is_area else "line_geometry_failed"] += 1
            continue
        minx, miny, maxx, maxy = g.bounds
        if minx > gmaxx or maxx < gminx or miny > gmaxy or maxy < gminy:
            continue
        for bx in city_bboxes:
            if not (minx > bx[2] or maxx < bx[0] or miny > bx[3] or maxy < bx[1]):
                break
        else:
            continue
        tags = dict(obj.tags)
        if is_area:
            from_way = obj.from_way()
            if from_way and not is_area_semantic(tags):
                continue
            pending_meta.append(("w" if from_way else "r", obj.orig_id(), "area", json.dumps(tags, ensure_ascii=False)))
            stats["areas"] += 1
        else:
            if obj.is_closed() and is_area_semantic(tags):
                continue  # handled as area
            pending_meta.append(("w", obj.id, "line", json.dumps(tags, ensure_ascii=False)))
            stats["lines"] += 1
        pending_geom.append(g)
        if len(pending_geom) >= BATCH:
            flush()
    flush()
    stats["objects_scanned"] = n
    log.info("Feature pass done in %.0fs: %s", time.time() - t0, dict(stats))
    return dict(stats)


# --------------------------------------------------------------------------- driver

def _manifest(city: City, info: dict, keys: list[str]) -> dict:
    return {
        "extract_version": EXTRACT_VERSION,
        "source": city.source,
        "source_osm_timestamp": info.get("osm_timestamp"),
        "source_size_bytes": info.get("size_bytes"),
        "boundary_relation": city.boundary_relation,
        "keys": keys,
    }


def is_up_to_date(city: City, cls: Classification) -> bool:
    d = Paths.interim(city.id)
    mf = d / "extract.json"
    if not (mf.exists() and (d / "osm_features.parquet").exists() and (d / "boundary.geojson").exists()):
        return False
    try:
        info = source_info(city.source_region)
    except FileNotFoundError:
        return True  # nothing newer available locally
    old = json.loads(mf.read_text())
    want = _manifest(city, info, relevant_keys(cls))
    return all(old.get(k) == v for k, v in want.items())


def run_extract(cities: list[City], cls: Classification, force: bool = False) -> None:
    keys = relevant_keys(cls)
    by_region: dict[str, list[City]] = defaultdict(list)
    for c in cities:
        if force or not is_up_to_date(c, cls):
            by_region[c.source_region].append(c)
        else:
            log.info("[%s] extract up to date, skipping", c.id)

    for region, group in by_region.items():
        pbf = ensure_extract(region)
        info = source_info(region)
        log.info("Extracting %s from %s (OSM timestamp %s)", [c.id for c in group], pbf.name, info.get("osm_timestamp"))
        boundaries = extract_boundaries(pbf, group)
        buckets = [_CityBucket(c, boundaries[c.id]) for c in group]
        stats = extract_features(pbf, buckets, keys)

        for b in buckets:
            out = Paths.interim(b.city.id)
            out.mkdir(parents=True, exist_ok=True)
            gpd.GeoDataFrame({"name": [b.city.name], "osm_relation": [b.city.boundary_relation]},
                             geometry=[b.boundary], crs="EPSG:4326").to_file(out / "boundary.geojson", driver="GeoJSON")
            df = pd.DataFrame(b.rows, columns=["osm_type", "osm_id", "kind", "tags"])
            gdf = gpd.GeoDataFrame(df, geometry=b.geoms, crs="EPSG:4326")
            gdf = gdf.drop_duplicates(subset=["osm_type", "osm_id", "kind"]).reset_index(drop=True)
            gdf.to_parquet(out / "osm_features.parquet", compression="zstd")
            manifest = _manifest(b.city, info, keys)
            manifest.update({
                "feature_count": int(len(gdf)),
                "area_features": int((gdf["kind"] == "area").sum()),
                "line_features": int((gdf["kind"] == "line").sum()),
                "pass_stats": stats,
            })
            (out / "extract.json").write_text(json.dumps(manifest, indent=2))
            log.info("[%s] %d features written", b.city.id, len(gdf))
