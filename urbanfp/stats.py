"""Area statistics and validation report for a processed city.

Outputs (data/processed/<city>/):
    stats.json        all statistics consumed by the web app / animation tooling
    validation.json   machine-readable checks
    validation.md     human-readable validation report
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import datetime, timezone

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely

from urbanfp.config import City, Classification, Paths

log = logging.getLogger(__name__)

KM2 = 1e6
# polygons larger than this in these categories are flagged as probable OSM tagging errors
SUSPICIOUS_LIMITS_M2 = {"roads": 500_000, "railways": 2_000_000, "parking": 300_000}


def _r(x: float, nd: int = 1) -> float:
    return float(round(float(x), nd))


def _mode_stats(cls: Classification, excl, feats, tiles, class_order, mode_area: float, mode_geom, line_feats, line_geoms) -> dict:
    """Statistics for one territory mode (a set of units). `excl`/`tiles` are already filtered to its units."""
    cls_area = {c: float(tiles[c].sum()) for c in class_order}
    cls_area["unclassified/no_data"] = float(tiles["unclassified/no_data"].sum())
    fp_area = {c: float(tiles["fp:" + c].sum()) for c in class_order}
    counts = excl[excl["cls"] != "unclassified/no_data"].groupby("cls")["fid"].nunique().to_dict()
    # a feature crossing unit borders has one piece per unit: sum per feature within the mode
    area_by_fid = excl.groupby("fid")["area_m2"].sum()

    categories = []
    for c in cls.categories:
        subs = []
        for sc in c["subcategories"]:
            cid = f"{c['id']}/{sc['id']}"
            a = cls_area.get(cid, 0.0)
            subs.append({"id": sc["id"], "label": sc["label"], "color": sc["color"],
                         "area_m2": _r(a), "pct": _r(100 * a / mode_area, 4),
                         "feature_count": int(counts.get(cid, 0))})
        a = sum(x["area_m2"] for x in subs)
        categories.append({"id": c["id"], "label": c["label"], "color": c["color"],
                           "area_m2": _r(a), "pct": _r(100 * a / mode_area, 4),
                           "feature_count": int(sum(x["feature_count"] for x in subs)),
                           "subcategories": subs})

    # ---------------- buildings
    b = feats[feats["category"] == "buildings"].merge(area_by_fid.rename("area_m2"), left_on="fid", right_index=True, how="inner")
    tags_b = b["tags"].map(json.loads)
    bval = Counter(t.get("building", "") for t in tags_b)
    with_levels = b["levels"].notna() & (b["levels"] > 0) & (b["levels"] < 200)
    buildings = {
        "count": int(len(b)),
        "footprint_m2": _r(b["area_m2"].sum()),
        "mean_footprint_m2": _r(b["area_m2"].mean() if len(b) else 0),
        "by_subcategory": {
            sub: {"count": int(len(g)), "area_m2": _r(g["area_m2"].sum()),
                  "mean_m2": _r(g["area_m2"].mean()), "median_m2": _r(g["area_m2"].median())}
            for sub, g in b.groupby("subcategory")
        },
        "classification_basis": {k: int(v) for k, v in b["basis"].value_counts().items()},
        "levels_tagged_pct": _r(100 * with_levels.mean() if len(b) else 0, 2),
        "levels_tagged_footprint_pct": _r(100 * b.loc[with_levels, "area_m2"].sum() / max(b["area_m2"].sum(), 1), 2),
        "floor_area_tagged_m2": _r((b.loc[with_levels, "area_m2"] * b.loc[with_levels, "levels"]).sum()),
        "top_building_values": dict(bval.most_common(25)),
    }

    # ---------------- line-based (estimated) footprints
    lf = line_feats.merge(area_by_fid.rename("area_m2"), left_on="fid", right_index=True, how="inner")
    est = {}
    for cat, g in lf.groupby("category"):
        by_src = g.groupby(g["width_source"].str.split("+").str[0])["area_m2"].sum()
        est[cat] = {
            "area_from_lines_m2": _r(g["area_m2"].sum()),
            "area_by_width_source_m2": {k: _r(v) for k, v in by_src.items()},
            "pct_area_default_width": _r(100 * by_src.get("default", 0) / max(g["area_m2"].sum(), 1), 2),
        }
    # network lengths, clipped to the mode's territory
    shapely.prepare(mode_geom)
    lengths_m = shapely.length(shapely.intersection(line_geoms, mode_geom))
    lt = line_feats.assign(length_m=lengths_m)
    lengths = {cat: {k: _r(v / 1000, 2) for k, v in g.groupby("type")["length_m"].sum().sort_values(ascending=False).items() if v > 0}
               for cat, g in lt.groupby("category")}

    overlap = []
    for c in class_order:
        if fp_area[c] <= 0:
            continue
        overlap.append({"cls": c, "footprint_m2": _r(fp_area[c]), "exclusive_m2": _r(cls_area[c]),
                        "hidden_by_higher_priority_m2": _r(fp_area[c] - cls_area[c]),
                        "hidden_pct": _r(100 * (fp_area[c] - cls_area[c]) / fp_area[c], 2)})
    return {
        "area": {"boundary_m2": _r(mode_area), "boundary_km2": _r(mode_area / KM2, 3)},
        "categories": categories,
        "buildings": buildings,
        "estimated_line_footprints": est,
        "network_length_km": lengths,
        "overlap": overlap,
        "_tile_total": float(tiles["tile_area"].sum()),
        "_sum_classes": float(sum(cls_area.values())),
        "_unclassified": cls_area["unclassified/no_data"],
    }


def compute_stats(city: City, cls: Classification) -> dict:
    interim, proc = Paths.interim(city.id), Paths.processed(city.id)
    excl = gpd.read_parquet(proc / "exclusive.parquet")
    feats = gpd.read_parquet(interim / "classified.parquet")
    tiles = pd.read_parquet(proc / "tile_stats.parquet")
    overlay_meta = json.loads((proc / "overlay.json").read_text())
    extract_meta = json.loads((interim / "extract.json").read_text())
    classify_rep = json.loads((interim / "classify_report.json").read_text(encoding="utf-8"))
    terr = json.loads((interim / "territories.json").read_text(encoding="utf-8"))
    units = gpd.read_parquet(interim / "units.parquet").to_crs(feats.crs)
    unit_geom = dict(zip(units["unit"], units.geometry.values))
    unit_area = overlay_meta["unit_area_m2"]
    class_order = overlay_meta["class_order"]

    line_feats = feats[feats["kind"] == "line"][["fid", "cls", "category", "width_source", "width_m", "tags", "source_wkb"]].copy()
    line_geoms = gpd.GeoSeries(shapely.from_wkb(line_feats["source_wkb"].values), crs="EPSG:4326").to_crs(feats.crs).values
    lt = line_feats["tags"].map(json.loads)
    line_feats["type"] = [t.get("highway") or t.get("railway") or t.get("waterway") or t.get("aeroway") for t in lt]

    # implausibly large polygons in categories that are normally narrow/small (likely OSM tagging errors)
    big = feats[(feats["kind"] == "area") & feats["category"].isin(SUSPICIOUS_LIMITS_M2) & (feats["subcategory"] != "airfield")]
    big = big[big["footprint_area_m2"] > big["category"].map(SUSPICIOUS_LIMITS_M2)]

    modes = {}
    for mode, spec in terr["modes"].items():
        us = [u for u in spec["units"] if u in unit_area]
        ex = excl[excl["unit"].isin(us)]
        ms = _mode_stats(cls, ex, feats, tiles[tiles["unit"].isin(us)], class_order,
                         sum(unit_area[u] for u in us), shapely.union_all([unit_geom[u] for u in us]), line_feats, line_geoms)
        fids = set(ex["fid"])
        suspicious = [{"fid": r.fid, "cls": r.cls, "rule_tag": r.rule_tag, "name": r.name,
                       "footprint_km2": round(r.footprint_area_m2 / KM2, 3)}
                      for r in big[big["fid"].isin(fids)].sort_values("footprint_area_m2", ascending=False).itertuples()]
        validation = _validate(city, cls, ms, ms["area"]["boundary_m2"], ms["_tile_total"], ms["_sum_classes"],
                               float(ex["area_m2"].sum()), ex, classify_rep, extract_meta, suspicious)
        for k in ("_tile_total", "_sum_classes", "_unclassified"):
            ms.pop(k)
        modes[mode] = {"status": spec["status"], "units": us, **ms, "validation": validation}

    city_mode = modes["city"]
    stats = {
        "schema_version": 2,
        "city": {"id": city.id, "name": city.name, "name_local": city.name_local, "country": city.country,
                 "osm_relation": city.boundary_relation, "notes": city.notes, "notes_uk": city.notes_uk,
                 "occupied": city.occupied},
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "osm_timestamp": extract_meta.get("source_osm_timestamp"),
        "source": city.source,
        "config_hash": cls.hash,
        # City mode at the top level (unchanged schema for consumers that ignore territory modes)
        **{k: city_mode[k] for k in ("area", "categories", "buildings", "estimated_line_footprints", "network_length_km", "overlap")},
        "modes": {m: {k: v for k, v in d.items() if k != "validation"} for m, d in modes.items()},
        "territories": {k: terr[k] for k in ("greater_drive_min", "suburbs_radius_km", "settlements_evaluated",
                                             "settlements_in_greater", "approx_outlines") if k in terr},
    }
    proc.joinpath("stats.json").write_text(json.dumps(stats, indent=2, ensure_ascii=False), encoding="utf-8")

    validation = {**modes["city"]["validation"],
                  "modes": {m: {"status": d["validation"]["status"], "checks": d["validation"]["checks"]} for m, d in modes.items()}}
    proc.joinpath("validation.json").write_text(json.dumps(validation, indent=2, ensure_ascii=False), encoding="utf-8")
    md = _validation_md(stats, modes["city"]["validation"])
    md += _territories_md(city, terr, modes, interim)
    proc.joinpath("validation.md").write_text(md, encoding="utf-8")
    for m, d in modes.items():
        log.info("[%s] %-8s %s: %.1f km², unclassified %.1f%%", city.id, m, d["status"], d["area"]["boundary_km2"],
                 next(c["pct"] for c in d["categories"] if c["id"] == "unclassified"))
    return stats


def _territories_md(city: City, terr: dict, modes: dict, interim) -> str:
    L = ["", "## Territory modes", "", "| Mode | Status | Units | km² | Validation |", "|---|---|---|---:|---|"]
    for m, d in modes.items():
        L.append(f"| {m} | {d['status']} | {'+'.join(d['units'])} | {d['area']['boundary_km2']:.2f} | {d['validation']['status']} |")
    p = interim / "settlements.geojson"
    if p.exists():
        s = gpd.read_file(p)
        L += ["", f"### Greater City settlements (drive time ≤ {terr['greater_drive_min']:g} min, "
                  f"{int(s['in_greater'].sum())} of {len(s)} included; * = approximate outline)", "",
              "| Settlement | Type | Drive min | Contiguous share | Built-up ha | In hromada | Included |", "|---|---|---:|---:|---:|---|---|"]
        s = s.sort_values("drive_min", na_position="last")
        for r in s[s["drive_min"].fillna(99) <= terr["greater_drive_min"] + 5].itertuples():
            L.append(f"| {r.name}{'*' if r.approx else ''} | {r.type} | {r.drive_min} | {r.contig_share} | {r.built_ha} | "
                     f"{'' if r.in_hromada is None else ('yes' if r.in_hromada else 'no')} | {'**yes**' if r.in_greater else 'no'} |")
    return "\n".join(L) + "\n"


def _check(checks, id_, ok, warn, message, value):
    status = "ok" if ok else ("warn" if warn else "fail")
    checks.append({"id": id_, "status": status, "message": message, "value": value})


def _validate(city, cls, stats, boundary_area, tile_total, sum_classes, piece_sum, excl, crep, emeta, suspicious):
    checks: list[dict] = []
    cat = {c["id"]: c for c in stats["categories"]}
    tot_cat = sum(c["area_m2"] for c in stats["categories"])

    err = abs(tot_cat - boundary_area) / boundary_area * 100
    _check(checks, "area_closure", err < 0.01, err < 0.1,
           f"Sum of categories {tot_cat / KM2:.4f} km² vs boundary {boundary_area / KM2:.4f} km² (diff {err:.5f}%)", err)
    err2 = abs(piece_sum - tile_total) / tile_total * 100
    _check(checks, "pieces_closure", err2 < 0.05, err2 < 0.5,
           f"Sum of exported per-feature pieces vs tiled area (diff {err2:.5f}%)", err2)

    # overlap test on a sample: exported pieces must not overlap each other
    sample = excl.sample(min(len(excl), 20000), random_state=1)
    tree = shapely.STRtree(sample.geometry.values)
    a, b = tree.query(sample.geometry.values, predicate="intersects")
    m = a < b
    inter = shapely.area(shapely.intersection(sample.geometry.values[a[m]], sample.geometry.values[b[m]])) if m.any() else np.array([0.0])
    ov = float(inter.sum())
    ov_pct = 100 * ov / max(float(sample["area_m2"].sum()), 1.0)
    _check(checks, "no_overlap_sample", ov_pct < 0.001, ov_pct < 0.01,
           f"Overlap between {len(sample)} sampled exported pieces: {ov:.1f} m² = {ov_pct:.6f}% of their area "
           f"(1 cm precision-grid rounding)", ov_pct)

    uncl = cat["unclassified"]["pct"]
    _check(checks, "unclassified_share", uncl < 15, uncl < 30,
           f"Unclassified (no OSM data) share of the city area: {uncl:.2f}%", uncl)

    b = stats["buildings"]
    unk_area = b["by_subcategory"].get("unknown", {}).get("area_m2", 0.0)
    unk_pct = 100 * unk_area / max(b["footprint_m2"], 1)
    _check(checks, "unknown_buildings", unk_pct < 25, unk_pct < 50,
           f"Buildings with unknown type: {unk_pct:.1f}% of building footprint area "
           f"({b['by_subcategory'].get('unknown', {}).get('count', 0)} buildings)", unk_pct)
    sec = b["classification_basis"].get("secondary_tag", 0)
    _check(checks, "secondary_tag_buildings", True, True,
           f"{sec} buildings classified from secondary tags on the same object (amenity/shop/building:use...)", sec)

    for c in ("roads", "railways"):
        e = stats["estimated_line_footprints"].get(c)
        if e:
            p = e["pct_area_default_width"]
            _check(checks, f"{c}_default_widths", True, True,
                   f"{c}: {e['area_from_lines_m2'] / KM2:.2f} km² estimated from buffered lines, "
                   f"{p:.1f}% of it using default widths (no width/lanes tag)", p)

    unm = crep.get("unmatched_total", 0)
    _check(checks, "unmatched_features", True, True,
           f"{unm} OSM features with relevant keys matched no rule (see unmatched_top)", unm)
    inv = crep.get("invalid_repaired", 0)
    _check(checks, "invalid_geometries", inv < 100, True, f"{inv} invalid polygons repaired with make_valid", inv)
    fails = emeta.get("pass_stats", {})
    nf = fails.get("area_geometry_failed", 0) + fails.get("line_geometry_failed", 0)
    _check(checks, "geometry_failures", nf < 100, True,
           f"{nf} geometries could not be built in the source extract (all cities in that pass)", nf)

    n_sus = len(suspicious)
    _check(checks, "suspicious_features", n_sus == 0, True,
           f"{n_sus} implausibly large road/rail/parking polygons (possible OSM tagging errors)"
           + (": " + ", ".join(f"{s['fid']} {s['rule_tag']} {s['footprint_km2']} km²" for s in suspicious[:5]) if n_sus else ""),
           n_sus)

    metrics = {
        "boundary_km2": boundary_area / KM2,
        "unclassified_pct": uncl,
        "unknown_building_area_pct": unk_pct,
        "building_count": b["count"],
        "levels_tagged_pct": b["levels_tagged_pct"],
    }
    return {
        "city": city.id,
        "status": "fail" if any(c["status"] == "fail" for c in checks) else ("warn" if any(c["status"] == "warn" for c in checks) else "ok"),
        "checks": checks,
        "metrics": metrics,
        "unmatched_top": crep.get("unmatched_top", {}),
        "unmatched_examples": crep.get("unmatched_examples", {}),
        "excluded": crep.get("excluded", {}),
        "suspicious_features": suspicious,
    }


def _validation_md(stats: dict, v: dict) -> str:
    icon = {"ok": "OK", "warn": "WARN", "fail": "FAIL"}
    L = [f"# Validation report — {stats['city']['name']}",
         "",
         f"* OSM data timestamp: `{stats['osm_timestamp']}`",
         f"* Generated: `{stats['generated_at']}`, config hash `{stats['config_hash']}`",
         f"* Administrative area: **{stats['area']['boundary_km2']:.2f} km²** (OSM relation {stats['city']['osm_relation']})",
         f"* Overall status: **{icon[v['status']]}**",
         "",
         "## Checks", "", "| Status | Check | Result |", "|---|---|---|"]
    for c in v["checks"]:
        L.append(f"| {icon[c['status']]} | `{c['id']}` | {c['message']} |")
    L += ["", "## Area breakdown", "", "| Category | km² | % | features |", "|---|---:|---:|---:|"]
    for c in stats["categories"]:
        L.append(f"| **{c['label']}** | {c['area_m2'] / KM2:.3f} | {c['pct']:.2f} | {c['feature_count']} |")
        for s in c["subcategories"]:
            if s["area_m2"] > 0:
                L.append(f"| &nbsp;&nbsp;{s['label']} | {s['area_m2'] / KM2:.3f} | {s['pct']:.2f} | {s['feature_count']} |")
    L += ["", "## Area hidden by higher-priority classes", "",
          "Footprint = union of the class's own polygons inside the boundary; exclusive = what remains after painting.", "",
          "| Class | footprint km² | exclusive km² | hidden % |", "|---|---:|---:|---:|"]
    for o in stats["overlap"]:
        L.append(f"| {o['cls']} | {o['footprint_m2'] / KM2:.3f} | {o['exclusive_m2'] / KM2:.3f} | {o['hidden_pct']:.1f} |")
    L += ["", "## Estimated (buffered line) footprints", ""]
    for c, e in stats["estimated_line_footprints"].items():
        srcs = ", ".join(f"{k}: {a / KM2:.3f} km²" for k, a in e["area_by_width_source_m2"].items())
        L.append(f"* **{c}**: {e['area_from_lines_m2'] / KM2:.3f} km² — {srcs}")
    L += ["", "## Buildings", "",
          f"* {stats['buildings']['count']} buildings, footprint {stats['buildings']['footprint_m2'] / KM2:.3f} km²",
          f"* building:levels tagged on {stats['buildings']['levels_tagged_pct']:.1f}% of buildings",
          f"* Classification basis: {stats['buildings']['classification_basis']}",
          "* Most common `building=*` values: " + ", ".join(f"`{k}` ({n})" for k, n in list(stats['buildings']['top_building_values'].items())[:15]),
          "", "## Unmatched OSM features (top)", "", "| kind:tag | count | example |", "|---|---:|---|"]
    for k, n in list(v["unmatched_top"].items())[:30]:
        L.append(f"| `{k}` | {n} | {v['unmatched_examples'].get(k, '')} |")
    if v.get("suspicious_features"):
        L += ["", "## Suspicious features (check in OSM)", "", "| feature | class | tag | name | footprint km² |", "|---|---|---|---|---:|"]
        for s in v["suspicious_features"][:30]:
            L.append(f"| {s['fid']} | {s['cls']} | `{s['rule_tag']}` | {s['name']} | {s['footprint_km2']} |")
    L += ["", "## Excluded features", "", "| tag | count |", "|---|---:|"]
    for k, n in list(v["excluded"].items())[:20]:
        L.append(f"| `{k}` | {n} |")
    return "\n".join(L) + "\n"
