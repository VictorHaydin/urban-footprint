"""Road traffic of the cities from area traffic reports (hourly series and a tile grid, CSV).

Input, one folder per city id: data/traffic/<city id>/ with two CSV files of one report period:

* the hourly series of the whole report area (`Region index, Region label, Time, Speed [kmh], Free flow speed
  [kmh], Congestion level [%], Travel time per 10 km [min]`): one row per hour. Congestion level = free flow
  speed / speed − 1 (the extra travel time), travel time per 10 km = 600 / speed;
* `*_tiles.csv`: the same metrics for every grid cell with roads (`region, tile_level, lat, lon, lat_min, lat_max,
  lon_min, lon_max, congestion_level_pct, speed_kmh, free_flow_speed_kmh, travel_time_per_10km_min`), one value
  per cell for the whole period. The cell values are coarse classes (speed in 20 km/h steps, congestion in 10 %
  steps, travel time in 5 min steps), set per metric, so the metrics of one cell need not agree; travel time 255
  means no value. Every cell value gets a random correction of up to ±5 (percentage points for congestion, % of
  the value for the others; jitter_cells).

The raw reports are licensed data and are never committed or published (data/traffic/ is git-ignored): after a
city is processed its CSV files are deleted (unless keep=True), and the cities already in web/data/traffic.json
whose reports are gone are kept as they are, so new reports can be added one city at a time.

Output:
* web/data/traffic.json: per city the report period, averages (all days, weekdays, weekends), the average hourly
  profile of weekdays and weekends and the weekday peak hour;
* web/data/<city id>/traffic_tiles.json: the cells as [lat_min, lat_max, lon_min, lon_max, congestion %, speed,
  free flow speed, travel time per 10 km or null].
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import numpy as np
import pandas as pd

from urbanfp.config import DATA_DIR, Paths, load_cities

log = logging.getLogger(__name__)

SOURCE_DIR = DATA_DIR / "traffic"
# cities whose traffic is not shown: frontline cities, where the traffic may be military movements
EXCLUDED = {"kherson"}
HOURLY_COLS = {
    "Speed [kmh]": "speed", "Free flow speed [kmh]": "ff",
    "Congestion level [%]": "cong", "Travel time per 10 km [min]": "tt",
}
TILE_COLS = ["congestion_level_pct", "speed_kmh", "free_flow_speed_kmh", "travel_time_per_10km_min"]
TT_NO_VALUE = 255
METRICS = ["cong", "speed", "ff", "tt"]


def _means(df: pd.DataFrame) -> dict:
    return {m: round(float(df[m].mean()), 1) for m in METRICS}


def _profile(df: pd.DataFrame) -> dict:
    g = df.groupby(df.Time.dt.hour)[METRICS].mean().reindex(range(24))
    return {m: [None if pd.isna(v) else round(float(v), 1) for v in g[m]] for m in METRICS}


def _hourly(path: Path) -> dict:
    df = pd.read_csv(path, encoding="utf-8-sig", parse_dates=["Time"]).rename(columns=HOURLY_COLS)
    regions = df["Region label"].unique()
    if len(regions) > 1:  # several report regions: the city as their plain average per hour
        log.warning("%s: %d regions (%s), averaged per hour", path.name, len(regions), ", ".join(map(str, regions)))
        df = df.groupby("Time", as_index=False)[METRICS].mean()
    df = df.sort_values("Time")
    wd = df.Time.dt.dayofweek < 5
    wdp = _profile(df[wd])
    peak = max(range(24), key=lambda h: wdp["cong"][h] if wdp["cong"][h] is not None else -1)
    return {
        "period": [df.Time.min().strftime("%Y-%m-%d"), df.Time.max().strftime("%Y-%m-%d")],
        "hours": int(len(df)),
        "mean": {"all": _means(df), "weekday": _means(df[wd]), "weekend": _means(df[~wd])},
        "profile": {"weekday": wdp, "weekend": _profile(df[~wd])},
        "peak": {"hour": peak, "cong": wdp["cong"][peak], "speed": wdp["speed"][peak]},
    }


# Random correction of every cell value: (step, probability); the sign is drawn separately for nonzero steps.
# Congestion moves by the step in percentage points, the other metrics by the step in % of the value.
JITTER = [(0, 0.20), (1, 0.40), (2, 0.20), (3, 0.10), (4, 0.06), (5, 0.04)]


def jitter_cells(cells: list, rng: np.random.Generator | None = None) -> list:
    """Coarsen the cell values with a random correction of up to ±5 (see JITTER), each value independently; zero
    values are kept."""
    rng = rng or np.random.default_rng()
    steps = np.array([s for s, _ in JITTER])
    probs = np.array([p for _, p in JITTER])
    for c in cells:
        for i in (4, 5, 6, 7):  # congestion %, speed, free-flow speed, travel time
            if not c[i]:  # no value or zero (e.g. no congestion): left as it is
                continue
            k = int(rng.choice(steps, p=probs)) * (1 if rng.random() < 0.5 else -1)
            v = c[i] + k if i == 4 else round(c[i] * (1 + k / 100))
            c[i] = max(int(v), 0 if i == 4 else 1)
    return cells


def _tiles(path: Path) -> dict:
    df = pd.read_csv(path, encoding="utf-8-sig")
    cells = []
    for r in df.itertuples(index=False):
        tt = int(r.travel_time_per_10km_min)
        cells.append([round(r.lat_min, 6), round(r.lat_max, 6), round(r.lon_min, 6), round(r.lon_max, 6),
                      int(r.congestion_level_pct), int(r.speed_kmh), int(r.free_flow_speed_kmh),
                      None if tt == TT_NO_VALUE else tt])
    m = re.search(r"(\d{4}-\d{2}-\d{2})_(\d{4}-\d{2}-\d{2})_tiles", path.name)
    return {
        "period": [m.group(1), m.group(2)] if m else None,
        "bounds": [round(float(df.lon_min.min()), 5), round(float(df.lat_min.min()), 5),
                   round(float(df.lon_max.max()), 5), round(float(df.lat_max.max()), 5)],
        "cells": jitter_cells(cells),
    }


def build_traffic(src: Path = SOURCE_DIR, keep: bool = False) -> Path:
    """Read every city folder of `src`, update web/data/traffic.json and the tile files and delete the processed
    reports (unless `keep`)."""
    known = {c.id for c in load_cities()}
    dest = Paths.web_data() / "traffic.json"
    out = json.loads(dest.read_text(encoding="utf-8"))["cities"] if dest.exists() else {}
    for cid in EXCLUDED:
        out.pop(cid, None)
    done = []
    for folder in sorted(p for p in src.iterdir() if p.is_dir()) if src.exists() else []:
        if folder.name in EXCLUDED:
            log.warning("%s: excluded (frontline city), reports deleted", folder)
            if not keep:
                for p in folder.glob("*.csv"):
                    p.unlink()
            continue
        if folder.name not in known:
            log.warning("%s: not a configured city id, skipped", folder)
            continue
        csvs = sorted(folder.glob("*.csv"))
        tiles = [p for p in csvs if p.stem.endswith("_tiles")]
        hourly = [p for p in csvs if not p.stem.endswith("_tiles")]
        if len(hourly) != 1 or len(tiles) > 1:
            log.warning("%s: expected one hourly CSV and at most one *_tiles.csv, found %d and %d — skipped",
                        folder, len(hourly), len(tiles))
            continue
        city = _hourly(hourly[0])
        if tiles:
            t = _tiles(tiles[0])
            tdest = Paths.web_city(folder.name) / "traffic_tiles.json"
            tdest.parent.mkdir(parents=True, exist_ok=True)
            tdest.write_text(json.dumps({"period": t["period"], "bounds": t["bounds"], "cells": t["cells"]},
                                       separators=(",", ":")), encoding="utf-8")
            city["tiles"] = {"n": len(t["cells"]), "period": t["period"], "bounds": t["bounds"]}
        out[folder.name] = city
        done.append(folder)
        log.info("%s: %s - %s, %d hours, mean congestion %.1f%%, %s tiles", folder.name, *city["period"],
                 city["hours"], city["mean"]["all"]["cong"], city.get("tiles", {}).get("n", "no"))
    out = dict(sorted(out.items()))
    dest.write_text(json.dumps({"cities": out}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    log.info("%s: %d cities (%d updated)", dest, len(out), len(done))
    if not keep:  # the raw reports are not kept (licensed data)
        for folder in done:
            for p in folder.glob("*.csv"):
                p.unlink()
            if not any(folder.iterdir()):
                folder.rmdir()
            log.info("%s: reports deleted", folder)
    return dest
