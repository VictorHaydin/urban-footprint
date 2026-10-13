"""Loading of city definitions and classification rules from config/*.yaml."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
WEB_DIR = ROOT / "web"


# --------------------------------------------------------------------------- paths

class Paths:
    sources = DATA_DIR / "sources"

    @staticmethod
    def interim(city_id: str) -> Path:
        return DATA_DIR / "interim" / city_id

    @staticmethod
    def processed(city_id: str) -> Path:
        return DATA_DIR / "processed" / city_id

    @staticmethod
    def web_data() -> Path:
        return WEB_DIR / "data"

    @staticmethod
    def web_city(city_id: str) -> Path:
        return WEB_DIR / "data" / city_id


# --------------------------------------------------------------------------- cities

@dataclass
class City:
    id: str
    name: str
    name_local: str
    country: str
    boundary_relation: int
    source: str = "geofabrik:europe/ukraine"
    clip_to_land: bool = False
    notes: str = ""

    @property
    def source_region(self) -> str:
        kind, _, region = self.source.partition(":")
        if kind != "geofabrik":
            raise ValueError(f"Unsupported source {self.source!r} for city {self.id}")
        return region


def load_cities(path: Path | None = None) -> list[City]:
    data = yaml.safe_load((path or CONFIG_DIR / "cities.yaml").read_text(encoding="utf-8"))
    defaults = data.get("defaults", {})
    cities = [City(**{**defaults, **c}) for c in data["cities"]]
    ids = [c.id for c in cities]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate city ids in cities.yaml")
    return cities


def select_cities(names: list[str] | None, all_cities: bool) -> list[City]:
    cities = load_cities()
    if all_cities or not names:
        return cities
    by_id = {c.id: c for c in cities}
    unknown = [n for n in names if n not in by_id]
    if unknown:
        raise SystemExit(f"Unknown city id(s): {', '.join(unknown)}. Known: {', '.join(by_id)}")
    return [by_id[n] for n in names]


# --------------------------------------------------------------------------- tag matching

class TagMatcher:
    """Matches a tag dict against `{key: [values] | "*"}` (all keys must match)."""

    def __init__(self, spec: dict[str, Any] | None):
        self.spec: dict[str, set[str] | None] = {}
        for k, v in (spec or {}).items():
            if v == "*":
                self.spec[k] = None
            else:
                vals = v if isinstance(v, list) else [v]
                # YAML parses `yes` as True; normalise back to OSM strings
                self.spec[k] = {_yaml_str(x) for x in vals}

    def __bool__(self) -> bool:
        return bool(self.spec)

    def matches(self, tags: dict[str, str]) -> bool:
        for k, allowed in self.spec.items():
            v = tags.get(k)
            if v is None or v == "no":
                return False
            if allowed is not None and v not in allowed:
                return False
        return True

    def first_key(self) -> str:
        return next(iter(self.spec))


def _yaml_str(x: Any) -> str:
    if x is True:
        return "yes"
    if x is False:
        return "no"
    return str(x)


@dataclass
class Rule:
    cls: str
    match: TagMatcher
    exclude: TagMatcher
    geom: str | None          # "area" | "line" | None
    footprint: str            # "polygon" | "buffer"
    width_group: str | None
    basis: str

    def matches(self, tags: dict[str, str], geom_kind: str) -> bool:
        if self.geom and self.geom != geom_kind:
            return False
        if not self.match.matches(tags):
            return False
        if self.exclude and self.exclude.matches(tags):
            return False
        return True

    def describe(self, tags: dict[str, str]) -> str:
        """The primary tag that triggered this rule, e.g. 'building=apartments'."""
        k = self.match.first_key()
        return f"{k}={tags.get(k, '')}"


@dataclass
class Classification:
    raw: dict[str, Any]
    categories: list[dict[str, Any]]
    class_ids: list[str]                 # all "cat/sub" in display order
    priority: list[str]                  # all "cat/sub" in painting order
    rules: list[Rule]
    secondary_rules: list[Rule]
    secondary_enabled: bool
    excludes: list[TagMatcher]
    line_widths: dict[str, Any]
    hash: str = ""             # methodology only (rules, priority, widths): drives classify/overlay/stats
    display_hash: str = ""     # everything incl. labels and colours: drives export
    meta: dict[str, Any] = field(default_factory=dict)

    def category_of(self, cls: str) -> str:
        return cls.split("/", 1)[0]

    def color_of(self, cls: str) -> str:
        cat, sub = cls.split("/", 1)
        for c in self.categories:
            if c["id"] == cat:
                for s in c["subcategories"]:
                    if s["id"] == sub:
                        return s["color"]
                return c["color"]
        return "#999999"

    def is_excluded(self, tags: dict[str, str]) -> bool:
        return any(m.matches(tags) for m in self.excludes)

    def classify(self, tags: dict[str, str], geom_kind: str) -> tuple[Rule, str] | None:
        """Return (rule, basis) for the first matching rule, or None."""
        for rule in self.rules:
            if rule.matches(tags, geom_kind):
                if rule.basis == "building_unknown" and self.secondary_enabled:
                    for sec in self.secondary_rules:
                        if sec.match.matches(tags):
                            return sec, "secondary_tag"
                return rule, rule.basis
        return None


def _make_rule(r: dict[str, Any], default_basis: str = "primary_tag") -> Rule:
    geom = r.get("geom")
    footprint = r.get("footprint") or ("buffer" if geom == "line" else "polygon")
    return Rule(
        cls=r["class"],
        match=TagMatcher(r.get("match")),
        exclude=TagMatcher(r.get("not")),
        geom=geom,
        footprint=footprint,
        width_group=r.get("width_group"),
        basis=r.get("basis", default_basis),
    )


def load_classification(path: Path | None = None) -> Classification:
    path = path or CONFIG_DIR / "classification.yaml"
    text = path.read_text(encoding="utf-8")
    raw = yaml.safe_load(text)

    categories = raw["categories"]
    class_ids = [f"{c['id']}/{s['id']}" for c in categories for s in c["subcategories"]]

    priority: list[str] = []
    for entry in raw["priority"]:
        cat, sub = entry.split("/", 1)
        if sub == "*":
            expanded = [cid for cid in class_ids if cid.startswith(cat + "/")]
            if not expanded:
                raise ValueError(f"priority entry {entry!r}: unknown category")
        else:
            if entry not in class_ids:
                raise ValueError(f"priority entry {entry!r}: unknown class")
            expanded = [entry]
        priority.extend(c for c in expanded if c not in priority)
    missing = [c for c in class_ids if c not in priority]
    if missing:
        raise ValueError(f"Classes missing from priority list: {missing}")

    rules = [_make_rule(r) for r in raw["rules"]]
    for r in rules:
        if r.cls not in class_ids:
            raise ValueError(f"Rule references unknown class {r.cls!r}")
        if r.footprint == "buffer" and r.width_group not in raw["line_widths"]:
            raise ValueError(f"Rule for {r.cls} needs a valid width_group")

    sec = raw.get("building_secondary_tags", {}) or {}
    secondary_rules = [_make_rule(r, "secondary_tag") for r in sec.get("rules", [])]

    excludes = [TagMatcher(e) for e in raw.get("exclude", [])]

    def _h(obj) -> str:
        return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:16]

    # presentation fields (label*, color) don't affect any computed number
    method = dict(raw)
    method["categories"] = [
        {"id": c["id"], "subcategories": [s["id"] for s in c["subcategories"]]} for c in categories
    ]
    return Classification(
        raw=raw,
        categories=categories,
        class_ids=class_ids,
        priority=priority,
        rules=rules,
        secondary_rules=secondary_rules,
        secondary_enabled=bool(sec.get("enabled", False)),
        excludes=excludes,
        line_widths=raw["line_widths"],
        hash=_h(method),
        display_hash=_h(raw),
    )
