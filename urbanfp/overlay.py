"""Mutually exclusive land-cover partition by priority "painting".

The administrative area is split into square tiles (parallel processing, bounded memory).
Within each tile, classes are processed in priority order:

    exclusive(class) = (union of class footprints ∩ tile) − (everything painted before)

and the remainder of the tile becomes `unclassified/no_data`. Inside a class, features are
ordered by footprint area (smallest first) and each one only keeps the part not already
taken by an earlier feature of the same class, so even per-feature pieces never overlap.

By construction   Σ exclusive areas + unclassified = tile area,   summed over tiles = city area.

Pieces of the same feature from different tiles are dissolved back into one geometry.

Output (data/processed/<city>/exclusive.parquet), local LAEA CRS:
    fid, cls, category, subcategory, area_m2, geometry
plus per-tile area accounting used for validation (tile_stats.parquet).
"""

from __future__ import annotations

import json
import logging
import os
import time
from concurrent.futures import ProcessPoolExecutor

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely

from urbanfp.classify import local_crs
from urbanfp.config import City, Classification, Paths

log = logging.getLogger(__name__)

GRID = 0.01          # 1 cm precision grid for overlay operations (robustness, no slivers)
TILE_SIZE_M = 2000.0


def _clean(g):
    """Keep only polygonal parts (overlay ops may emit degenerate lines/points)."""
    if g is None or g.is_empty:
        return None
    t = g.geom_type
    if t in ("Polygon", "MultiPolygon"):
        return g
    parts = [p for p in shapely.get_parts(g) if p.geom_type == "Polygon" and not p.is_empty]
    return shapely.multipolygons(parts) if parts else None


_EMPTY = shapely.Polygon()


def _polygonal_array(geoms: np.ndarray) -> np.ndarray:
    """Vectorised _clean(): replace non-polygonal results by their polygonal parts (or empty)."""
    tid = shapely.get_type_id(geoms)
    bad = ~np.isin(tid, (3, 6)) & ~shapely.is_empty(geoms)
    if bad.any():
        geoms = geoms.copy()
        for i in np.nonzero(bad)[0]:
            g = _clean(geoms[i])
            geoms[i] = g if g is not None else _EMPTY
    return geoms


def _poly(g):
    g = _clean(g)
    return g if g is not None else _EMPTY


def _paint_tile(args):
    tile_id, tile_geom, class_order, feat_idx, feat_cls, feat_geoms = args
    pieces_idx: list[int] = []      # feature index (-1 for unclassified)
    pieces_cls: list[int] = []
    pieces_geom: list = []
    footprint_area = np.zeros(len(class_order))
    exclusive_area = np.zeros(len(class_order))

    covered = None
    if len(feat_idx):
        clipped = shapely.intersection(feat_geoms, tile_geom, grid_size=GRID)
        clipped = _polygonal_array(clipped)
    else:
        clipped = np.array([], dtype=object)
    for ci in range(len(class_order)):
        sel = np.nonzero(feat_cls == ci)[0]
        if len(sel) == 0:
            continue
        geoms = clipped[sel]
        ok = ~shapely.is_empty(geoms) & (shapely.area(geoms) > 0)
        sel, geoms = sel[ok], geoms[ok]
        if len(sel) == 0:
            continue
        union_c = _poly(shapely.union_all(geoms, grid_size=GRID))
        footprint_area[ci] = union_c.area
        excl = union_c if covered is None else _poly(shapely.difference(union_c, covered, grid_size=GRID))
        covered = union_c if covered is None else _poly(shapely.union(covered, union_c, grid_size=GRID))
        if excl.is_empty or excl.area <= 0:
            continue
        exclusive_area[ci] = excl.area

        # per-feature exclusive pieces
        order = np.argsort(shapely.area(geoms), kind="stable")
        geoms = geoms[order]
        sel = sel[order]
        if covered is not union_c:  # something of higher priority was there
            parts = _polygonal_array(shapely.intersection(geoms, excl, grid_size=GRID))
        else:
            parts = geoms.copy()
        # remove within-class overlaps (earlier = smaller feature wins)
        if len(parts) > 1:
            tree = shapely.STRtree(geoms)
            a, b = tree.query(geoms, predicate="intersects")
            m = b < a
            a, b = a[m], b[m]
            if len(a):
                touch = shapely.touches(geoms[a], geoms[b])
                a, b = a[~touch], b[~touch]
            if len(a):
                order_a = np.argsort(a, kind="stable")
                a, b = a[order_a], b[order_a]
                splits = np.nonzero(np.diff(a))[0] + 1
                for grp_a, grp_b in zip(np.split(a, splits), np.split(b, splits)):
                    i = grp_a[0]
                    if parts[i] is None or parts[i].is_empty:
                        continue
                    earlier = [parts[j] for j in grp_b if parts[j] is not None and not parts[j].is_empty]
                    if earlier:
                        parts[i] = _poly(shapely.difference(parts[i], _poly(shapely.union_all(earlier, grid_size=GRID)), grid_size=GRID))
        for k, g in enumerate(parts):
            g = _clean(g)
            if g is None or g.area <= 0:
                continue
            pieces_idx.append(int(feat_idx[sel[k]]))
            pieces_cls.append(ci)
            pieces_geom.append(g)

    rest = tile_geom if covered is None else shapely.difference(tile_geom, covered, grid_size=GRID)
    rest = _clean(rest)
    unclassified_area = rest.area if rest is not None else 0.0
    if rest is not None and unclassified_area > 0:
        pieces_idx.append(-1)
        pieces_cls.append(-1)
        pieces_geom.append(rest)
    return {
        "tile_id": tile_id,
        "tile_area": tile_geom.area,
        "footprint_area": footprint_area,
        "exclusive_area": exclusive_area,
        "unclassified_area": unclassified_area,
        "pieces_idx": pieces_idx,
        "pieces_cls": pieces_cls,
        "pieces_wkb": shapely.to_wkb(np.array(pieces_geom, dtype=object)) if pieces_geom else [],
    }


def _tiles(boundary, size: float):
    minx, miny, maxx, maxy = boundary.bounds
    xs = np.arange(np.floor(minx / size) * size, maxx, size)
    ys = np.arange(np.floor(miny / size) * size, maxy, size)
    boxes, ids = [], []
    for ix, x in enumerate(xs):
        for iy, y in enumerate(ys):
            boxes.append(shapely.box(x, y, x + size, y + size))
            ids.append(f"{ix}_{iy}")
    boxes = np.array(boxes, dtype=object)
    shapely.prepare(boundary)
    hit = shapely.intersects(boundary, boxes)
    boxes, ids = boxes[hit], [i for i, h in zip(ids, hit) if h]
    geoms = shapely.intersection(boxes, boundary, grid_size=GRID)
    return ids, geoms


def run_overlay(city: City, cls: Classification, workers: int | None = None) -> None:
    t0 = time.time()
    src = Paths.interim(city.id)
    out = Paths.processed(city.id)
    out.mkdir(parents=True, exist_ok=True)

    feats = gpd.read_parquet(src / "classified.parquet", columns=["fid", "cls", "geometry"])
    boundary_wgs = gpd.read_file(src / "boundary.geojson").geometry.iloc[0]
    crs = local_crs(boundary_wgs)
    if feats.crs is None or not feats.crs.equals(crs):
        feats = feats.to_crs(crs)
    boundary = gpd.GeoSeries([boundary_wgs], crs="EPSG:4326").to_crs(crs).iloc[0]
    boundary = shapely.make_valid(boundary)

    class_order = cls.priority[:-1] if cls.priority[-1].startswith("unclassified/") else cls.priority
    code = {c: i for i, c in enumerate(class_order)}
    feat_cls = feats["cls"].map(code).to_numpy()
    if np.isnan(feat_cls.astype(float)).any():
        raise ValueError("Features with classes missing from priority order")
    feat_cls = feat_cls.astype(np.int32)
    geoms = feats.geometry.values.to_numpy() if hasattr(feats.geometry.values, "to_numpy") else np.asarray(feats.geometry.values)
    geoms = np.asarray(geoms, dtype=object)

    tile_ids, tile_geoms = _tiles(boundary, TILE_SIZE_M)
    tree = shapely.STRtree(geoms)
    tile_idx, feat_idx = tree.query(tile_geoms, predicate="intersects")
    order = np.argsort(tile_idx, kind="stable")
    tile_idx, feat_idx = tile_idx[order], feat_idx[order]
    bounds = np.searchsorted(tile_idx, np.arange(len(tile_geoms) + 1))
    log.info("[%s] overlay: %d features, %d tiles of %.0f m", city.id, len(feats), len(tile_geoms), TILE_SIZE_M)

    def tasks():
        for t in range(len(tile_geoms)):
            fi = feat_idx[bounds[t]:bounds[t + 1]]
            yield (tile_ids[t], tile_geoms[t], class_order, fi, feat_cls[fi], geoms[fi])

    workers = workers or max(1, (os.cpu_count() or 2) - 1)
    results = []
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for k, r in enumerate(ex.map(_paint_tile, tasks(), chunksize=2)):
            results.append(r)
            if (k + 1) % 50 == 0:
                log.info("  ... %d / %d tiles (%.0fs)", k + 1, len(tile_geoms), time.time() - t0)

    # ---------------- assemble pieces
    p_idx = np.concatenate([np.asarray(r["pieces_idx"], dtype=np.int64) for r in results])
    p_cls = np.concatenate([np.asarray(r["pieces_cls"], dtype=np.int64) for r in results])
    p_tile = np.concatenate([np.full(len(r["pieces_idx"]), r["tile_id"], dtype=object) for r in results])
    p_geom = shapely.from_wkb(np.concatenate([np.asarray(r["pieces_wkb"], dtype=object) for r in results if len(r["pieces_wkb"])]))

    fids = feats["fid"].to_numpy()
    all_cls = np.array(class_order + ["unclassified/no_data"], dtype=object)
    df = pd.DataFrame({
        "fid": np.where(p_idx >= 0, fids[np.maximum(p_idx, 0)], "u" + p_tile.astype(str)),
        "cls": all_cls[p_cls],
    })
    df["geometry"] = p_geom
    # dissolve features split across tiles
    dup = df["fid"].duplicated(keep=False)
    single = df[~dup]
    multi = df[dup]
    if len(multi):
        merged = (multi.groupby(["fid", "cls"], sort=False)["geometry"]
                  .agg(lambda s: shapely.union_all(np.asarray(s.values, dtype=object), grid_size=GRID))
                  .reset_index())
        df = pd.concat([single, merged], ignore_index=True)
    gdf = gpd.GeoDataFrame(df, geometry="geometry", crs=crs)
    gdf["category"] = gdf["cls"].str.split("/").str[0]
    gdf["subcategory"] = gdf["cls"].str.split("/").str[1]
    gdf["area_m2"] = gdf.geometry.area
    gdf = gdf[["fid", "cls", "category", "subcategory", "area_m2", "geometry"]]
    gdf.to_parquet(out / "exclusive.parquet", compression="zstd")

    # ---------------- per-tile accounting
    rows = []
    for r in results:
        row = {"tile_id": r["tile_id"], "tile_area": r["tile_area"], "unclassified/no_data": r["unclassified_area"]}
        for i, c in enumerate(class_order):
            row[c] = r["exclusive_area"][i]
            row["fp:" + c] = r["footprint_area"][i]
        rows.append(row)
    pd.DataFrame(rows).to_parquet(out / "tile_stats.parquet")
    (out / "overlay.json").write_text(json.dumps({
        "tile_size_m": TILE_SIZE_M, "grid_size_m": GRID, "tiles": len(results),
        "boundary_area_m2": boundary.area, "crs": crs.to_string(),
        "class_order": class_order, "seconds": round(time.time() - t0, 1),
    }, indent=2))
    log.info("[%s] overlay done in %.0fs: %d exclusive pieces", city.id, time.time() - t0, len(gdf))
