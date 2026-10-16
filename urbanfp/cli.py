"""Command line interface.

    python -m urbanfp list
    python -m urbanfp serve     [--port 8000]         local preview of web/ (supports Range requests)
    python -m urbanfp download  [--refresh]           [CITY ... | --all]
    python -m urbanfp run       [--force] [--refresh] [CITY ... | --all]
    python -m urbanfp extract | classify | overlay | stats | export   [CITY ... | --all] [--force]

`run` executes all stages and skips those whose outputs are newer than their inputs and
whose configuration has not changed (incremental processing).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

from urbanfp.config import City, Classification, Paths, load_classification, select_cities

log = logging.getLogger("urbanfp")

STAGES = ["extract", "territories", "classify", "overlay", "stats", "export"]


def _stage_files(city: City) -> dict[str, tuple[list[Path], list[Path]]]:
    i, p, w = Paths.interim(city.id), Paths.processed(city.id), Paths.web_city(city.id)
    return {
        "territories": ([i / "osm_features.parquet", i / "boundary.geojson"], [i / "units.parquet", i / "territories.json"]),
        "classify": ([i / "osm_features.parquet"], [i / "classified.parquet"]),
        "overlay": ([i / "classified.parquet", i / "units.parquet"], [p / "exclusive.parquet", p / "tile_stats.parquet"]),
        "stats": ([p / "exclusive.parquet"], [p / "stats.json", p / "validation.json"]),
        "export": ([p / "stats.json"], [w / "stats.json", w / "features.pmtiles"]),
    }


def _state_file(city: City) -> Path:
    return Paths.processed(city.id) / "state.json"


def _stage_hash(stage: str, cls: Classification) -> str:
    return cls.display_hash if stage == "export" else cls.hash


def _needs_run(city: City, stage: str, cls: Classification) -> bool:
    inputs, outputs = _stage_files(city)[stage]
    if not all(o.exists() for o in outputs):
        return True
    if any(i.exists() and i.stat().st_mtime > min(o.stat().st_mtime for o in outputs) for i in inputs):
        return True
    sf = _state_file(city)
    state = json.loads(sf.read_text()) if sf.exists() else {}
    return state.get(stage, {}).get("config_hash") != _stage_hash(stage, cls)


def _mark_done(city: City, stage: str, cls: Classification) -> None:
    sf = _state_file(city)
    sf.parent.mkdir(parents=True, exist_ok=True)
    state = json.loads(sf.read_text()) if sf.exists() else {}
    state[stage] = {"config_hash": _stage_hash(stage, cls), "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    sf.write_text(json.dumps(state, indent=2))


def run_stage(stage: str, cities: list[City], cls: Classification, force: bool) -> None:
    if stage == "extract":
        from urbanfp.extract import run_extract
        run_extract(cities, cls, force=force)
        return
    for city in cities:
        if not force and not _needs_run(city, stage, cls):
            log.info("[%s] %s up to date, skipping", city.id, stage)
            continue
        t0 = time.time()
        if stage == "territories":
            from urbanfp.territories import build_territories
            build_territories(city)
        elif stage == "classify":
            from urbanfp.classify import classify_city
            classify_city(city, cls)
        elif stage == "overlay":
            from urbanfp.overlay import run_overlay
            run_overlay(city, cls)
        elif stage == "stats":
            from urbanfp.stats import compute_stats
            compute_stats(city, cls)
        elif stage == "export":
            from urbanfp.export_web import export_city
            export_city(city, cls)
        _mark_done(city, stage, cls)
        log.info("[%s] %s finished in %.0fs", city.id, stage, time.time() - t0)
    if stage == "export":
        from urbanfp.export_web import export_index
        export_index(cls)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="urbanfp", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="list configured cities and their processing state")
    sp = sub.add_parser("serve", help="preview the web app locally (with HTTP Range support)")
    sp.add_argument("--port", type=int, default=8000)
    for name in ["download", "run", *STAGES]:
        p = sub.add_parser(name)
        p.add_argument("cities", nargs="*", help="city ids (default: all)")
        p.add_argument("--all", action="store_true", help="process all configured cities")
        p.add_argument("--force", action="store_true", help="re-run even if outputs are up to date")
        if name in ("download", "run"):
            p.add_argument("--refresh", action="store_true", help="re-download the OSM source extract")
        if name == "run":
            p.add_argument("--from", dest="from_stage", choices=STAGES, help="start at this stage")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    for noisy in ("pyogrio", "fiona", "urllib3", "rasterio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if args.cmd == "list":
        from urbanfp.config import load_cities
        for c in load_cities():
            sf = _state_file(c)
            state = json.loads(sf.read_text()) if sf.exists() else {}
            print(f"{c.id:12s} {c.name:15s} relation {c.boundary_relation:<10d} source {c.source:28s} "
                  f"stages done: {', '.join(state) or '-'}")
        return

    if args.cmd == "serve":
        from urbanfp.serve import serve
        serve(args.port)
        return

    cities = select_cities(args.cities, args.all)
    cls = load_classification()

    if args.cmd == "download":
        from urbanfp.sources import ensure_extract
        for region in sorted({c.source_region for c in cities}):
            ensure_extract(region, refresh=args.refresh)
        return

    if args.cmd == "run":
        if args.refresh:
            from urbanfp.sources import ensure_extract
            for region in sorted({c.source_region for c in cities}):
                ensure_extract(region, refresh=True)
        stages = STAGES[STAGES.index(args.from_stage):] if args.from_stage else STAGES
        for stage in stages:
            run_stage(stage, cities, cls, force=args.force)
        return

    run_stage(args.cmd, cities, cls, force=args.force)


if __name__ == "__main__":
    main()
