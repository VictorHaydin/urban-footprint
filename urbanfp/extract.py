"""Extraction of city boundaries and raw OSM features from a country PBF extract.

Two streaming passes with pyosmium (libosmium does multipolygon assembly):

1. boundaries:  assemble the administrative boundary relations of the requested cities.
2. features:    for all requested cities at once, collect every way / multipolygon that
                carries one of the keys used by the classification rules and intersects the
                city boundary. Original geometry (WGS84) and ALL original tags are kept.

For cities with `territories: true` the extraction covers the wider **region** (city + hromada +
`suburbs_radius_km` around the city) and additionally keeps settlement outlines and points
(docs/SUBURBS.md). A third, node-only pass collects `place=*` points.

Outputs per city (data/interim/<city>/):
    boundary.geojson      administrative boundary of the city (WGS84)
    region.geojson        area covered by the extraction (= the city unless territorial)
    hromada.geojson       hromada boundary (cities with a hromada_relation)
    osm_features.parquet  GeoParquet: osm_type, osm_id, kind (area|line), tags (JSON), geometry
    settlements.parquet   (territorial) settlement polygons (kind=area) and place points (kind=point)
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

EXTRACT_VERSION = 2
# Keys needed for territory modes (settlement outlines / points), territorial cities only.
TERRITORY_KEYS = {"boundary", "place"}
PLACES = {"city", "town", "village", "hamlet"}
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


def keys_for(city: City, cls: Classification) -> list[str]:
    keys = set(relevant_keys(cls))
    if city.territories:
        keys |= TERRITORY_KEYS
    return sorted(keys)


def is_settlement_polygon(tags: dict) -> bool:
    """Settlement outline: place=* area, or an admin_level 8/9 boundary (tagging differs by oblast)."""
    if tags.get("place") in PLACES:
        return (tags.get("admin_level") or "99") not in ("2", "3", "4", "5", "6", "7")
    # admin_level 8 without a settlement place tag is a district (e.g. place=borough), not a settlement;
    # it is still extracted (cheap) but rejected in territories._select_settlements
    return tags.get("boundary") == "administrative" and tags.get("admin_level") in ("8", "9")


# --------------------------------------------------------------------------- boundaries

def extract_boundaries(pbf: Path, cities: list[City], extra_rel_ids: set[int] = frozenset()) -> dict:
    """City boundaries by city id, plus any `extra_rel_ids` (e.g. hromadas) by relation id."""
    rel_ids = {c.boundary_relation for c in cities} | set(extra_rel_ids)
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
    for r in extra_rel_ids:
        g = found.get(r)
        if g is None or g.is_empty:
            log.warning("Relation %s not found or could not be assembled", r)
            continue
        out[r] = shapely.make_valid(g)
    return out


# --------------------------------------------------------------------------- features

class _CityBucket:
    def __init__(self, city: City, boundary: shapely.Geometry, territorial: bool = False):
        self.city = city
        self.boundary = boundary  # area to collect (the region for territorial cities)
        self.territorial = territorial
        self.sett_rows: list[tuple] = []
        self.sett_geoms: list[shapely.Geometry] = []
        shapely.prepare(boundary)
        minx, miny, maxx, maxy = boundary.bounds
        self.bbox = (minx, miny, maxx, maxy)
        self.rows: list[tuple] = []
        self.geoms: list[shapely.Geometry] = []


def extract_features(pbf: Path, buckets: list[_CityBucket], keys: list[str], feature_keys: list[str] | None = None) -> dict:
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
    feature_keys = set(feature_keys or keys)  # objects with only boundary/place tags are not features
    any_territorial = any(b.territorial for b in buckets)

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
                meta, is_feat, is_sett = pending_meta[j]
                if is_feat:
                    b.rows.append(meta)
                    b.geoms.append(geoms[j])
                if is_sett and b.territorial:
                    b.sett_rows.append(meta)
                    b.sett_geoms.append(geoms[j])
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
        is_feat = not feature_keys.isdisjoint(tags)
        if is_area:
            from_way = obj.from_way()
            is_sett = any_territorial and is_settlement_polygon(tags)
            if from_way and not is_area_semantic(tags) and not is_sett:
                continue
            if not (is_feat or is_sett):
                continue
            meta = ("w" if from_way else "r", obj.orig_id(), "area", json.dumps(tags, ensure_ascii=False))
            pending_meta.append((meta, is_feat, is_sett))
            stats["areas"] += 1
        else:
            if obj.is_closed() and is_area_semantic(tags):
                continue  # handled as area
            if not is_feat:
                continue
            pending_meta.append((("w", obj.id, "line", json.dumps(tags, ensure_ascii=False)), True, False))
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
        "territories": city.territories,
        "hromada_relation": city.hromada_relation,
        "suburbs_radius_km": city.suburbs_radius_km if city.territories else None,
    }


def region_for(city: City, boundary: shapely.Geometry, hromada: shapely.Geometry | None) -> shapely.Geometry:
    """Extraction area: the city, or for territorial cities city + hromada + radius around the city."""
    if not city.territories:
        return boundary
    from urbanfp.classify import local_crs
    crs = local_crs(boundary)
    buf = gpd.GeoSeries([boundary], crs="EPSG:4326").to_crs(crs).buffer(city.suburbs_radius_km * 1000).to_crs(4326).iloc[0]
    parts = [boundary, buf] + ([hromada] if hromada is not None else [])
    return shapely.make_valid(shapely.union_all(parts))


def extract_place_points(pbf: Path, buckets: list[_CityBucket]) -> int:
    """Node-only pass: place=city|town|village|hamlet points inside territorial regions."""
    terr = [b for b in buckets if b.territorial]
    if not terr:
        return 0
    t0, n = time.time(), 0
    for node in osmium.FileProcessor(str(pbf), osmium.osm.NODE).with_filter(osmium.filter.KeyFilter("place")):
        if node.tags.get("place") not in PLACES:
            continue
        pt = shapely.Point(node.location.lon, node.location.lat)
        for b in terr:
            if b.boundary.contains(pt):
                b.sett_rows.append(("n", node.id, "point", json.dumps(dict(node.tags), ensure_ascii=False)))
                b.sett_geoms.append(pt)
                n += 1
    log.info("Place-point pass done in %.0fs: %d points", time.time() - t0, n)
    return n


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
    if city.territories and not (d / "settlements.parquet").exists():
        return False
    want = _manifest(city, info, keys_for(city, cls))
    return all(old.get(k) == v for k, v in want.items())


def run_extract(cities: list[City], cls: Classification, force: bool = False) -> None:
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
        hromada_ids = {c.hromada_relation for c in group if c.hromada_relation}
        boundaries = extract_boundaries(pbf, group, hromada_ids)
        buckets = []
        for c in group:
            hromada = boundaries.get(c.hromada_relation) if (c.territories and c.hromada_relation) else None
            buckets.append(_CityBucket(c, region_for(c, boundaries[c.id], hromada), territorial=c.territories))
        keys = sorted(set().union(*(keys_for(c, cls) for c in group)))
        stats = extract_features(pbf, buckets, keys, feature_keys=relevant_keys(cls))
        stats["place_points"] = extract_place_points(pbf, buckets)

        for b in buckets:
            out = Paths.interim(b.city.id)
            out.mkdir(parents=True, exist_ok=True)
            gpd.GeoDataFrame({"name": [b.city.name], "osm_relation": [b.city.boundary_relation]},
                             geometry=[boundaries[b.city.id]], crs="EPSG:4326").to_file(out / "boundary.geojson", driver="GeoJSON")
            gpd.GeoDataFrame({"name": [b.city.name]}, geometry=[b.boundary], crs="EPSG:4326").to_file(out / "region.geojson", driver="GeoJSON")
            hrom = boundaries.get(b.city.hromada_relation) if b.city.hromada_relation else None
            (out / "hromada.geojson").unlink(missing_ok=True)
            if hrom is not None:
                gpd.GeoDataFrame({"osm_relation": [b.city.hromada_relation]}, geometry=[hrom],
                                 crs="EPSG:4326").to_file(out / "hromada.geojson", driver="GeoJSON")
            if b.territorial:
                sdf = pd.DataFrame(b.sett_rows, columns=["osm_type", "osm_id", "kind", "tags"])
                sg = gpd.GeoDataFrame(sdf, geometry=b.sett_geoms, crs="EPSG:4326")
                sg.drop_duplicates(subset=["osm_type", "osm_id", "kind"]).reset_index(drop=True).to_parquet(
                    out / "settlements.parquet", compression="zstd")
            df = pd.DataFrame(b.rows, columns=["osm_type", "osm_id", "kind", "tags"])
            gdf = gpd.GeoDataFrame(df, geometry=b.geoms, crs="EPSG:4326")
            gdf = gdf.drop_duplicates(subset=["osm_type", "osm_id", "kind"]).reset_index(drop=True)
            gdf.to_parquet(out / "osm_features.parquet", compression="zstd")
            manifest = _manifest(b.city, info, keys_for(b.city, cls))
            manifest.update({
                "feature_count": int(len(gdf)),
                "area_features": int((gdf["kind"] == "area").sum()),
                "line_features": int((gdf["kind"] == "line").sum()),
                "settlement_objects": len(b.sett_rows),
                "pass_stats": stats,
            })
            (out / "extract.json").write_text(json.dumps(manifest, indent=2))
            log.info("[%s] %d features written", b.city.id, len(gdf))
