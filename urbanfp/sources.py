"""Download and bookkeeping of OSM source extracts (Geofabrik PBF files)."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import osmium
import requests

from urbanfp.config import Paths

log = logging.getLogger(__name__)

GEOFABRIK_URL = "https://download.geofabrik.de/{region}-latest.osm.pbf"
USER_AGENT = "UrbanFootprintComparer/0.1 (+https://github.com/; OSM land-use research)"


def pbf_path(region: str) -> Path:
    return Paths.sources / "geofabrik" / f"{region}-latest.osm.pbf"


def meta_path(region: str) -> Path:
    return pbf_path(region).with_suffix(".meta.json")


def read_pbf_timestamp(path: Path) -> str | None:
    """OSM data timestamp from the PBF header (replication timestamp)."""
    try:
        reader = osmium.io.Reader(str(path), osmium.osm.osm_entity_bits.NOTHING)
        header = reader.header()
        reader.close()
        ts = header.get("osmosis_replication_timestamp") or header.get("timestamp")
        return ts or None
    except Exception as exc:  # pragma: no cover - diagnostics only
        log.warning("Could not read PBF header of %s: %s", path, exc)
        return None


def source_info(region: str) -> dict:
    p = meta_path(region)
    if p.exists():
        return json.loads(p.read_text())
    pbf = pbf_path(region)
    if pbf.exists():
        info = {
            "region": region,
            "url": GEOFABRIK_URL.format(region=region),
            "file": str(pbf.relative_to(Paths.sources.parent.parent)),
            "size_bytes": pbf.stat().st_size,
            "osm_timestamp": read_pbf_timestamp(pbf),
            "downloaded_at": datetime.fromtimestamp(pbf.stat().st_mtime, timezone.utc).isoformat(),
        }
        p.write_text(json.dumps(info, indent=2))
        return info
    raise FileNotFoundError(f"No source extract for {region}; run `urbanfp download` first")


def ensure_extract(region: str, refresh: bool = False, max_age_days: float | None = None) -> Path:
    """Make sure the Geofabrik extract for `region` exists locally.

    Re-downloads when `refresh` is set, or when the local copy is older than
    `max_age_days` (by OSM data timestamp)."""
    pbf = pbf_path(region)
    if pbf.exists() and not refresh:
        if max_age_days is None:
            return pbf
        ts = source_info(region).get("osm_timestamp")
        if ts:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(ts.replace("Z", "+00:00"))).days
            if age <= max_age_days:
                return pbf
            log.info("Extract %s is %d days old, refreshing", region, age)
        else:
            return pbf

    url = GEOFABRIK_URL.format(region=region)
    pbf.parent.mkdir(parents=True, exist_ok=True)
    part = pbf.with_suffix(".pbf.part")
    log.info("Downloading %s", url)
    t0 = time.time()
    with requests.get(url, stream=True, timeout=120, headers={"User-Agent": USER_AGENT}) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        done = 0
        last = 0.0
        with open(part, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                f.write(chunk)
                done += len(chunk)
                if time.time() - last > 10:
                    last = time.time()
                    pct = f"{100 * done / total:.0f}%" if total else ""
                    log.info("  %s: %.0f MB %s", region, done / 1e6, pct)
    if total and done != total:
        raise IOError(f"Incomplete download of {url}: {done} of {total} bytes")
    part.replace(pbf)
    meta_path(region).unlink(missing_ok=True)
    info = source_info(region)
    log.info("Downloaded %s (%.0f MB, OSM timestamp %s) in %.0fs",
             region, done / 1e6, info.get("osm_timestamp"), time.time() - t0)
    return pbf
