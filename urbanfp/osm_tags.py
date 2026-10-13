"""OSM tagging semantics helpers (area vs. line interpretation of closed ways, value parsing)."""

from __future__ import annotations

import re

# Closed ways carrying any of these keys are polygons (unless area=no).
AREA_KEYS = {
    "building", "landuse", "leisure", "amenity", "water", "area:highway", "place",
    "military", "tourism", "shop", "office", "craft", "historic", "building:part",
}
LINEAR_NATURAL = {"coastline", "tree_row", "cliff", "ridge", "arete", "valley", "earth_bank", "gully", "dune"}
AREA_WATERWAY = {"riverbank", "dock", "boatyard"}
LINEAR_AEROWAY = {"runway", "taxiway", "parking_position", "holding_position", "jet_bridge"}
LINEAR_MAN_MADE = {"pipeline", "embankment", "cutline", "dyke", "goods_conveyor", "power_line"}
AREA_POWER = {"plant", "substation", "generator"}


def is_area_semantic(tags: dict[str, str]) -> bool:
    """Whether a CLOSED way with these tags represents an area (polygon)."""
    a = tags.get("area")
    if a == "no":
        return False
    if a == "yes":
        return True
    for k in AREA_KEYS:
        v = tags.get(k)
        if v is not None and v != "no":
            return True
    if "natural" in tags and tags["natural"] not in LINEAR_NATURAL:
        return True
    if tags.get("waterway") in AREA_WATERWAY:
        return True
    if "aeroway" in tags and tags["aeroway"] not in LINEAR_AEROWAY:
        return True
    if "man_made" in tags and tags["man_made"] not in LINEAR_MAN_MADE:
        return True
    if tags.get("power") in AREA_POWER:
        return True
    if tags.get("railway") == "platform" or tags.get("public_transport") == "platform":
        return True
    return False


_NUM = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*(m|meter|meters|metre|metres|м)?\s*$", re.IGNORECASE)
_FEET = re.compile(r"^\s*(\d+)\s*'\s*(?:(\d+)\s*\")?\s*$")


def parse_width(value: str | None) -> float | None:
    """Parse an OSM width/est_width value in metres. Returns None if unparseable."""
    if not value:
        return None
    value = value.split(";")[0]
    m = _NUM.match(value)
    if m:
        return float(m.group(1).replace(",", "."))
    m = _FEET.match(value)
    if m:
        feet = int(m.group(1)) + int(m.group(2) or 0) / 12
        return feet * 0.3048
    return None


def parse_int(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(float(value.split(";")[0].strip()))
    except ValueError:
        return None


def parse_float(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value.split(";")[0].strip().replace(",", "."))
    except ValueError:
        return None
