"""Sea removal for coastal cities (`clip_to_land: true` in config/cities.yaml).

Some administrative boundaries extend into the sea (e.g. Berdiansk). OSM does not map the sea as polygons,
only the shore as `natural=coastline` ways, drawn with land on the left and sea on the right. The territory
units of such a city are cut along the coastline and the sea side is dropped, so city area and shares refer
to land. Inner harbours mapped as `natural=bay` areas stay inside (they lie on the land side of the
coastline and count as water, sub-category "bays & sea").

The coastline of a source extract is read once and cached next to the interim data.
"""
from __future__ import annotations

import logging
import time

import geopandas as gpd
import numpy as np
import osmium
import shapely

from urbanfp.config import City, Paths
from urbanfp.sources import pbf_path

log = logging.getLogger(__name__)

PROBE_M = 2.0  # distance of the land / sea probe points from the coastline
SEA_MAJORITY = 4  # sea-side probes must outnumber land-side probes this many times
FRAME_M = 3000  # margin of the rectangle in which the coastline is cut into land / sea faces


def coastline(city: City) -> gpd.GeoDataFrame:
    """All `natural=coastline` ways of the city's source extract (WGS84 lines), cached per extract file."""
    pbf = pbf_path(city.source_region)
    st = pbf.stat()
    cache = Paths.interim("_coastline") / f"{city.source_region.replace('/', '_')}.parquet"
    stamp = f"{st.st_size}-{int(st.st_mtime)}"
    if cache.exists():
        g = gpd.read_parquet(cache)
        if len(g) and g["stamp"].iloc[0] == stamp:
            return g
    t0 = time.time()
    wkb = osmium.geom.WKBFactory()
    lines = []
    fp = (osmium.FileProcessor(str(pbf), osmium.osm.NODE | osmium.osm.WAY)
          .with_locations("flex_mem")
          .with_filter(osmium.filter.EntityFilter(osmium.osm.WAY))
          .with_filter(osmium.filter.TagFilter(("natural", "coastline"))))
    for w in fp:
        try:
            lines.append(shapely.from_wkb(wkb.create_linestring(w)))
        except Exception:  # noqa: BLE001 - ways with missing nodes at the extract edge
            continue
    g = gpd.GeoDataFrame({"stamp": [stamp] * len(lines)}, geometry=lines, crs=4326)
    cache.parent.mkdir(parents=True, exist_ok=True)
    g.to_parquet(cache)
    log.info("coastline: %d ways from %s in %.0fs", len(g), pbf.name, time.time() - t0)
    return g


def _probes(line, land: list, sea: list) -> None:
    """Points PROBE_M left (land) and right (sea) of each segment midpoint of a coastline piece."""
    xy = np.asarray(line.coords)
    d = np.diff(xy, axis=0)
    n = np.hypot(d[:, 0], d[:, 1])
    ok = n > 0
    if not ok.any():
        return
    mid = (xy[:-1] + xy[1:])[ok] / 2
    left = np.column_stack([-d[ok, 1], d[ok, 0]]) / n[ok, None] * PROBE_M
    land.append(mid + left)
    sea.append(mid - left)


def sea_polygon(area, crs, city: City, keep_water=None):
    """Sea around polygon `area` (local CRS `crs`) as a polygon, minus `keep_water` (e.g. mapped bays), or None.

    The coastline is cut into faces within a rectangle around the area (a coastline crosses a rectangle edge to
    edge, so faces are well formed even where the shore runs along an irregular boundary). A face is sea when
    nearly all probe points inside it lie on the sea (right-hand) side of the shore."""
    x0, y0, x1, y1 = area.bounds
    frame = shapely.box(x0 - FRAME_M, y0 - FRAME_M, x1 + FRAME_M, y1 + FRAME_M)
    lines = coastline(city).to_crs(crs)
    lines = lines[lines.intersects(frame)]
    if lines.empty:
        return None
    inside = [g for g in shapely.get_parts(shapely.intersection(lines.geometry.values, frame))
              if not g.is_empty and g.geom_type == "LineString"]
    if not inside:
        return None
    faces = shapely.get_parts(shapely.polygonize([shapely.union_all([frame.boundary, *inside])]))
    land_pts, sea_pts = [], []
    for ln in inside:
        _probes(ln, land_pts, sea_pts)
    land_pts = shapely.points(np.vstack(land_pts))
    sea_pts = shapely.points(np.vstack(sea_pts))
    sea = [f for f in faces
           if np.count_nonzero(shapely.contains(f, sea_pts)) > SEA_MAJORITY * np.count_nonzero(shapely.contains(f, land_pts))]
    if not sea:
        return None
    sea = shapely.union_all(sea)
    if keep_water is not None and not keep_water.is_empty:
        sea = shapely.difference(sea, keep_water, grid_size=0.01)
    return None if sea.is_empty else sea
