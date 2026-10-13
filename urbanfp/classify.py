"""Classification of raw OSM features and construction of physical footprints.

Input : data/interim/<city>/osm_features.parquet  (WGS84, original geometry + tags)
Output: data/interim/<city>/classified.parquet    (local equal-area CRS)

Each output row is one OSM feature with:
    fid, osm_type, osm_id, kind, cls, category, subcategory, basis, rule_tag,
    width_m, width_source, name, tags (JSON), source_wkb (original WGS84 geometry),
    geometry (physical footprint polygon in the city's local LAEA CRS)
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import dataclass

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from pyproj import CRS

from urbanfp.config import City, Classification, Paths
from urbanfp.osm_tags import parse_float, parse_int, parse_width

log = logging.getLogger(__name__)

TRACK_SPACING_M = 4.1   # centre distance of parallel 1520 mm tracks


def local_crs(boundary_wgs84: shapely.Geometry) -> CRS:
    """Lambert azimuthal equal-area projection centred on the city: exact areas, low distortion."""
    c = boundary_wgs84.centroid
    return CRS.from_proj4(f"+proj=laea +lat_0={c.y:.5f} +lon_0={c.x:.5f} +x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs")


@dataclass
class Width:
    value: float
    source: str  # tag | est_width | lanes | default


def line_width(group: str, tags: dict[str, str], widths: dict) -> Width:
    cfg = widths[group]
    max_w = float(cfg.get("max_width", 100))

    def clamp(w: float) -> float:
        return float(min(max(w, 0.5), max_w))

    if group == "highway":
        hw = tags.get("highway", "")
        w = parse_width(tags.get("width")) or parse_width(tags.get("width:carriageway"))
        src = "tag"
        if w is None:
            w = parse_width(tags.get("est_width"))
            src = "est_width"
        if w is None:
            lanes = parse_int(tags.get("lanes"))
            if lanes and 0 < lanes <= 12 and hw not in ("footway", "path", "cycleway", "steps", "bridleway", "pedestrian", "track"):
                lw = cfg["lane_width"].get(hw.removesuffix("_link"), cfg["lane_width"]["default"])
                w = lanes * float(lw)
                src = "lanes"
        if w is None:
            src = "default"
            svc = tags.get("service")
            if hw == "service" and svc in cfg.get("service_overrides", {}):
                w = float(cfg["service_overrides"][svc])
            else:
                two, one = cfg["defaults"].get(hw, [4.0, 3.5])
                oneway = tags.get("oneway") in ("yes", "1", "-1", "true") or hw in ("motorway",) and tags.get("oneway") != "no"
                w = float(one if oneway else two)
        # explicitly tagged sidewalks that are not mapped as separate ways
        sw = tags.get("sidewalk") or tags.get("sidewalk:both")
        n_sw = 0
        if sw in ("both", "yes"):
            n_sw = 2
        elif sw in ("left", "right"):
            n_sw = 1
        else:
            n_sw = sum(tags.get(f"sidewalk:{s}") == "yes" for s in ("left", "right"))
        if n_sw:
            w += n_sw * float(cfg.get("sidewalk_width", 2.0))
            src += "+sidewalk"
        return Width(clamp(w), src)

    key = {"railway": "railway", "waterway": "waterway", "aeroway": "aeroway"}[group]
    val = tags.get(key, "")
    w = parse_width(tags.get("width"))
    if w is not None:
        return Width(clamp(w), "tag")
    w = parse_width(tags.get("est_width"))
    if w is not None:
        return Width(clamp(w), "est_width")
    w = float(cfg["defaults"].get(val, next(iter(cfg["defaults"].values()))))
    if group == "railway":
        tracks = parse_int(tags.get("tracks"))
        if tracks and 1 < tracks <= 20:
            w += (tracks - 1) * TRACK_SPACING_M
            return Width(clamp(w), "default+tracks")
    return Width(clamp(w), "default")


def _polygonal(g: shapely.Geometry) -> shapely.Geometry | None:
    """Valid polygonal part of a geometry (drops stray lines/points from make_valid)."""
    if g is None or g.is_empty:
        return None
    if not g.is_valid:
        g = shapely.make_valid(g)
    if g.geom_type in ("Polygon", "MultiPolygon"):
        return g
    parts = [p for p in shapely.get_parts(g) if p.geom_type in ("Polygon", "MultiPolygon")]
    if not parts:
        return None
    return shapely.union_all(parts)


def classify_city(city: City, cls: Classification) -> dict:
    src = Paths.interim(city.id)
    raw = gpd.read_parquet(src / "osm_features.parquet")
    boundary = gpd.read_file(src / "boundary.geojson").geometry.iloc[0]
    crs = local_crs(boundary)
    log.info("[%s] classifying %d features", city.id, len(raw))

    # memoise classification on the subset of tags that rules can look at
    rule_keys: set[str] = set()
    for r in cls.rules + cls.secondary_rules:
        rule_keys |= set(r.match.spec) | set(r.exclude.spec)
    for m in cls.excludes:
        rule_keys |= set(m.spec)
    rule_keys = sorted(rule_keys)
    memo: dict[tuple, tuple] = {}

    report = {"input_features": int(len(raw)), "excluded": Counter(), "unmatched": Counter(),
              "unmatched_examples": {}, "basis": Counter(), "invalid_repaired": 0, "empty_footprint": 0}

    out_rows = []
    keep_idx = []
    for i, (osm_type, osm_id, kind, tag_json) in enumerate(zip(raw.osm_type, raw.osm_id, raw.kind, raw.tags)):
        tags = json.loads(tag_json)
        mk = (kind,) + tuple(tags.get(k) for k in rule_keys)
        res = memo.get(mk)
        if res is None:
            if cls.is_excluded(tags):
                res = ("excluded",)
            else:
                m = cls.classify(tags, kind)
                res = ("ok", m[0], m[1]) if m else ("unmatched",)
            memo[mk] = res
        if res[0] == "excluded":
            report["excluded"][_main_tag(tags)] += 1
            continue
        if res[0] == "unmatched":
            mt = _main_tag(tags)
            report["unmatched"][f"{kind}:{mt}"] += 1
            report["unmatched_examples"].setdefault(f"{kind}:{mt}", f"{osm_type}{osm_id}")
            continue
        rule, basis = res[1], res[2]
        report["basis"][basis] += 1
        width = None
        if rule.footprint == "buffer":
            width = line_width(rule.width_group, tags, cls.line_widths)
        cat, sub = rule.cls.split("/", 1)
        out_rows.append({
            "fid": f"{osm_type}{osm_id}",
            "osm_type": osm_type,
            "osm_id": int(osm_id),
            "kind": kind,
            "cls": rule.cls,
            "category": cat,
            "subcategory": sub,
            "basis": basis,
            "rule_tag": rule.describe(tags) if basis != "secondary_tag" else f"building={tags.get('building')}+{rule.describe(tags)}",
            "width_m": width.value if width else np.nan,
            "width_source": width.source if width else ("mapped_polygon" if kind == "area" else ""),
            "name": tags.get("name") or tags.get("name:uk") or "",
            "levels": parse_float(tags.get("building:levels")) if cat == "buildings" else np.nan,
            "tags": tag_json,
        })
        keep_idx.append(i)

    df = pd.DataFrame(out_rows)
    src_geom = raw.geometry.values[keep_idx]
    proj = gpd.GeoSeries(src_geom, crs="EPSG:4326").to_crs(crs).values

    is_buf = df["width_source"].ne("mapped_polygon").values & df["kind"].eq("line").values
    footprints = np.empty(len(df), dtype=object)
    if is_buf.any():
        footprints[is_buf] = shapely.buffer(np.asarray(proj[is_buf]), df["width_m"].values[is_buf] / 2.0, quad_segs=4)
    poly_idx = np.nonzero(~is_buf)[0]
    poly_geoms = np.asarray(proj[poly_idx])
    valid = shapely.is_valid(poly_geoms)
    report["invalid_repaired"] = int((~valid).sum())
    for j, g in zip(poly_idx, poly_geoms):
        footprints[j] = g
    for j in poly_idx[~valid]:
        footprints[j] = _polygonal(footprints[j])

    empty = np.array([g is None or g.is_empty for g in footprints])
    report["empty_footprint"] = int(empty.sum())
    df = df[~empty].reset_index(drop=True)
    footprints = footprints[~empty]
    src_wkb = shapely.to_wkb(np.asarray(src_geom)[~empty])

    gdf = gpd.GeoDataFrame(df, geometry=list(footprints), crs=crs)
    gdf["source_wkb"] = src_wkb
    gdf["footprint_area_m2"] = gdf.geometry.area
    gdf.to_parquet(src / "classified.parquet", compression="zstd")

    report = {
        **report,
        "classified_features": int(len(gdf)),
        "excluded": dict(report["excluded"].most_common()),
        "unmatched_top": dict(report["unmatched"].most_common(60)),
        "unmatched_total": int(sum(report["unmatched"].values())),
        "unmatched_examples": {k: report["unmatched_examples"][k] for k, _ in report["unmatched"].most_common(60)},
        "basis": dict(report["basis"]),
        "crs": crs.to_string(),
    }
    del report["unmatched"]
    (src / "classify_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("[%s] %d classified, %d unmatched, %d excluded", city.id, len(gdf),
             report["unmatched_total"], sum(report["excluded"].values()))
    return report


def _main_tag(tags: dict[str, str]) -> str:
    for k in ("building", "highway", "railway", "landuse", "natural", "leisure", "amenity", "waterway",
              "water", "aeroway", "man_made", "power", "military", "tourism", "public_transport"):
        if k in tags:
            return f"{k}={tags[k]}"
    return "other"
