"""Core invariants: run with `python -m pytest tests` (or `python tests/test_core.py`)."""

import numpy as np
import shapely

from urbanfp.classify import line_width
from urbanfp.config import load_classification
from urbanfp.osm_tags import is_area_semantic, parse_width
from urbanfp.overlay import _paint_tile

CLS = load_classification()


def _paint(features, tile):
    order = ["buildings/apartments", "roads/minor", "green/park", "industrial/industrial"]
    cls_idx = np.array([order.index(c) for c, _ in features], dtype=np.int32)
    geoms = np.array([g for _, g in features], dtype=object)
    return order, _paint_tile(("t", tile, order, np.arange(len(features)), cls_idx, geoms))


def test_priority_partition_closes_and_does_not_double_count():
    tile = shapely.box(0, 0, 100, 100)
    feats = [
        ("industrial/industrial", shapely.box(0, 0, 60, 100)),   # land use
        ("buildings/apartments", shapely.box(10, 10, 30, 30)),   # building inside industrial zone
        ("roads/minor", shapely.box(0, 45, 100, 55)),            # road crossing everything
        ("green/park", shapely.box(50, 0, 100, 40)),             # park overlapping industrial
        ("green/park", shapely.box(70, 10, 80, 20)),             # nested park (same class)
    ]
    order, r = _paint(feats, tile)
    ex = dict(zip(order, r["exclusive_area"]))
    assert ex["buildings/apartments"] == 400
    assert ex["roads/minor"] == 1000
    assert ex["green/park"] == 50 * 40                       # union of both parks, once
    assert ex["industrial/industrial"] == 60 * 100 - 400 - 600 - 10 * 40
    total = sum(r["exclusive_area"]) + r["unclassified_area"]
    assert abs(total - 10000) < 1e-6
    pieces = shapely.from_wkb(np.asarray(r["pieces_wkb"], dtype=object))
    # pieces are pairwise disjoint and sum to the tile
    assert abs(sum(p.area for p in pieces) - 10000) < 1e-6
    for i in range(len(pieces)):
        for j in range(i + 1, len(pieces)):
            assert shapely.intersection(pieces[i], pieces[j]).area < 1e-6


def test_area_semantics():
    assert is_area_semantic({"building": "yes"})
    assert not is_area_semantic({"highway": "footway"})
    assert is_area_semantic({"highway": "pedestrian", "area": "yes"})
    assert not is_area_semantic({"natural": "tree_row"})
    assert is_area_semantic({"natural": "water"})


def test_widths():
    assert parse_width("7.5") == 7.5
    assert parse_width("7,5 m") == 7.5
    assert parse_width("abc") is None
    w = line_width("highway", {"highway": "residential"}, CLS.line_widths)
    assert (w.value, w.source) == (6.0, "default")
    w = line_width("highway", {"highway": "primary", "lanes": "4"}, CLS.line_widths)
    assert (w.value, w.source) == (14.0, "lanes")
    w = line_width("highway", {"highway": "tertiary", "width": "9", "sidewalk": "both"}, CLS.line_widths)
    assert (w.value, w.source) == (13.0, "tag+sidewalk")


def test_classification_rules():
    assert CLS.classify({"building": "yes"}, "area")[0].cls == "buildings/unknown"
    assert CLS.classify({"building": "house"}, "area")[0].cls == "buildings/detached_house"
    assert CLS.classify({"building": "yes", "shop": "supermarket"}, "area")[0].cls == "buildings/commercial"
    assert CLS.classify({"highway": "pedestrian", "area": "yes"}, "area")[0].cls == "roads/pedestrian"
    assert CLS.classify({"highway": "residential"}, "line")[0].cls == "roads/minor"
    # mis-tagged polygon without area=yes must not become road surface
    m = CLS.classify({"highway": "residential", "name": "Дніпровське водосховище"}, "area")
    assert m is None or not m[0].cls.startswith("roads/")
    assert CLS.classify({"highway": "service", "area": "yes"}, "area")[0].cls == "roads/minor"
    assert CLS.classify({"landuse": "residential"}, "area")[0].cls == "residential/yards"
    assert CLS.is_excluded({"railway": "subway", "tunnel": "yes"})
    assert CLS.is_excluded({"building:part": "yes"})


def test_labels_do_not_change_method_hash(tmp_path):
    from pathlib import Path
    import yaml
    src = Path(__file__).resolve().parent.parent / "config" / "classification.yaml"
    raw = yaml.safe_load(src.read_text(encoding="utf-8"))
    raw["categories"][0]["label_uk"] = "змінено"
    raw["categories"][0]["color"] = "#000000"
    p = tmp_path / "c.yaml"
    p.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    changed = load_classification(p)
    assert changed.hash == CLS.hash                  # classify/overlay/stats stay cached
    assert changed.display_hash != CLS.display_hash  # export re-runs


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
