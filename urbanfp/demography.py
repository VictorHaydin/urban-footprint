"""Population (demography) per city and territory mode, from State Statistics Service of Ukraine (Derzhstat) tables.

Sources live in data/demography/raw/ (committed), one workbook per reference date, named
`population_<YYYY-MM-DD>_<source>.xlsx`:
* derzhstat  Derzhstat estimate: regions, districts, hromadas and settlements with their KATOTTG code
             (Кодифікатор адміністративно-територіальних одиниць) and the de facto population;
* census     the 2001 census, one row per settlement with its 2001 name, type, oblast, raion and council,
             no codes. Rows get today's KATOTTG code by name (current or former, from OSM `old_name`) within
             the oblast; namesakes are told apart by the 2001 council and raion, which mostly map onto one
             hromada and one raion of today. Hromada totals are the sums of their settlements.
Parsed tables (same columns for every source) are written to data/demography/population_<year>.csv.

Matching to the map:
* City            the settlement row of the city ("місто Х"); its KATOTTG code is configured per city
                  (`katottg` in config/cities.yaml) or found by name within the city's hromada.
* Hromada         the hromada total ("Х міська ТГ, усього"): the code prefix shared with the city.
* Greater City    the city plus every settlement included in Greater City mode. Settlements are matched by
                  the OSM `katotth` tag (same KATOTTG code), else by name when it is unique within the oblast.
                  Unmatched settlements are listed, so the total is a lower bound.

Output: web/data/demography.json (all cities, all years).
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import numpy as np
import pandas as pd

from urbanfp.config import City, Paths, load_cities

log = logging.getLogger(__name__)

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "demography" / "raw"
OUT_DIR = RAW_DIR.parent
RAW_RE = re.compile(r"population_(\d{4})-(\d{2})-(\d{2})_(derzhstat|census)\.xlsx$")
SOURCES = {"derzhstat": "estimate", "census": "census"}

# settlement type prefixes in Derzhstat tables (spacing and Latin "c." vary between oblast sheets)
TYPE_RE = re.compile(r"^(місто|м\.|смт|с-ще|селище|с\.|c\.)\s*", re.IGNORECASE)
TYPE_KIND = {"місто": "city", "м.": "city", "смт": "smt", "с-ще": "settlement", "селище": "settlement",
             "с.": "village", "c.": "village"}
# compatible Derzhstat types for an OSM place type (smt are mapped as towns or villages in OSM)
OSM_TYPES = {"city": {"city"}, "town": {"city", "smt"}, "village": {"village", "settlement", "smt"},
             "hamlet": {"village", "settlement"}}


def _code_kind(code: str) -> str:
    """KATOTTG code UA OO RR HHH SSS DD CCCCC (oblast, raion, hromada, settlement, district, check digits)."""
    if code[4:14] == "0" * 10:
        return "oblast"
    if code[6:14] == "0" * 8:
        return "raion"
    if code[9:14] == "0" * 5:
        return "hromada"
    return "settlement"


def _split_name(raw: str) -> tuple[str, str]:
    name = re.sub(r"\s+", " ", raw.replace("*", "")).strip()
    m = TYPE_RE.match(name)
    if m:
        return name[m.end():].strip(), TYPE_KIND[m.group(1).lower()]
    return name, ""


def parse_derzhstat(path: Path) -> pd.DataFrame:
    """All rows with a KATOTTG code: code, kind, name, type, population (None for NA / C), oblast."""
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    rows = []
    for ws in wb.worksheets:
        if ws.title in ("Опис", "Україна"):
            continue
        for r in ws.iter_rows(min_row=3, values_only=True):
            name, code, value = (list(r) + [None, None, None])[:3]
            if not code or not name:
                continue
            code = str(code).strip()
            label = str(name).replace(", усього", "").strip()
            base, stype = _split_name(label)
            pop = int(value) if isinstance(value, (int, float)) else None  # "NA" / "C" -> unknown
            rows.append({"code": code, "kind": _code_kind(code), "name": base, "type": stype, "label": label,
                         "population": pop, "oblast": ws.title.strip()})
    df = pd.DataFrame(rows)
    df["population"] = df["population"].astype("Int64")
    return df


CENSUS_TYPES = {"місто": "city", "селище міського типу": "smt", "селище": "settlement", "село": "village"}
# today's type a 2001 type may have become (towns were promoted, villages became "селище" and back)
TYPE_NOW = {"city": {"city"}, "smt": {"smt", "city", "settlement", "village"},
            "settlement": {"settlement", "village", "smt"}, "village": {"village", "settlement", "smt"}}
# preferred today's types when namesakes remain after the council and raion votes
TYPE_ORDER = {"city": ["city"], "smt": ["smt", "city", "settlement", "village"],
              "settlement": ["settlement", "village", "smt"], "village": ["village", "settlement", "smt"]}
OSM_KIND = {"city": "city", "town": "city", "village": "village", "hamlet": "village"}
# (2001 name, 2001 council) -> today's name in the same oblast: renamings OSM has no `old_name` for,
# and settlements since merged into a city
CENSUS_RENAMES = {
    ("Іллічівськ", "Іллічівська міська рада"): "Чорноморськ",
    ("Кузнецовськ", "Кузнецовська міська рада"): "Вараш",
    ("Переяслав-Хмельницький", "Переяслав-Хмельницька міська рада"): "Переяслав",
    ("Новояворівське", "Новояворівська міська рада"): "Новояворівськ",
    ("Червонозаводське", "Червонозаводська міська рада"): "Заводське",
    ("Інгулець", "Інгулецька міська рада"): "Кривий Ріг",      # part of Kryvyi Rih since 2002
    ("Таромське", "Таромська селищна рада"): "Дніпро",         # part of Dnipro
    ("Красний Луч", "Краснолуцька міська рада"): "Хрустальний",  # not the village Mamusheve (old_name Красний Луч)
    ("Колхозне", "Орлинівська сільська рада"): "Колгоспне",       # Sevastopol
    ("Повортне", "Верхньосадівська сільська рада"): "Поворотне",  # typo in the census table
}


def _loose(s: str) -> str:
    """Spelling-insensitive key (є/е, і/и/ї, apostrophes, hyphens), for a second matching pass."""
    s = _norm(s).replace("’", "").replace("-", " ")
    return s.translate(str.maketrans("єїиі", "еііі"))


def parse_census(path: Path, current: pd.DataFrame) -> pd.DataFrame:
    """2001 census rows with today's KATOTTG code (see the module docstring); `current` is the latest Derzhstat
    table. Settlements merged since 2001 are summed into one code; rows left unmatched keep an empty code."""
    from urbanfp.agglomeration import coded_places
    raw = pd.read_excel(path, sheet_name=0).iloc[:, :6]
    raw.columns = ["name", "type", "population", "oblast", "raion", "council"]
    raw["type"] = raw["type"].map(CENSUS_TYPES).fillna("")
    raw["oblast"] = raw["oblast"].replace({"Севастополь (міськрада)": "м. Севастополь"})

    # candidates: settlements of today (Derzhstat rows; OSM points with a code, also for Crimea and Sevastopol)
    prefix = dict(zip(current["oblast"], current["code"].str[:4]))
    prefix.update({"Автономна Республіка Крим": "UA01", "м. Севастополь": "UA85"})
    cands: dict[tuple, dict] = {}  # (oblast prefix, name) -> {code: type}
    loose: dict[tuple, dict] = {}

    def add(code, names, typ, only_new=False, pre=None):
        pre = pre or code[:4]
        for n in re.split(r"\s*;\s*", names or ""):
            k = (pre, _norm(n))
            if n and not (only_new and k in cands):
                cands.setdefault(k, {}).setdefault(code, typ)
                loose.setdefault((pre, _loose(n)), {}).setdefault(code, typ)
    for code, name, typ, kind in zip(current["code"], current["name"], current["type"], current["kind"]):
        if kind == "settlement" or typ == "city":  # Kyiv is listed at region level
            add(code, name, typ)
    pts = coded_places(load_cities()[0].source_region)
    coded = pts[~pts["code"].str.startswith("OSM")]
    # a point inside a PREFIX_AREAS unit but coded elsewhere today (a Sevastopol village of 2001, in Bakhchysarai
    # raion since 2020) is a candidate of that unit only, so it never takes a namesake from the rest of Crimea
    pool = [a if a and a != c[:4] else None for c, a in zip(coded["code"], coded["area"])]
    for code, name, place, pre in zip(coded["code"], coded["name"], coded["place"], pool):
        add(code, name, OSM_KIND.get(place, ""), pre=pre)
    for code, old, place, pre in zip(coded["code"], coded["old_name"], coded["place"], pool):
        add(code, old, OSM_KIND.get(place, ""), only_new=True, pre=pre)  # a former name never shadows a current one
    # places OSM gives no code (most of Crimea): identified by their OSM point, never shadowing a coded one
    rest = pts[pts["code"].str.startswith("OSM")]
    for code, name, place, pre in zip(rest["code"], rest["name"], rest["place"], rest["prefix"]):
        add(code, name, OSM_KIND.get(place, ""), only_new=True, pre=pre)

    names = [CENSUS_RENAMES.get((n, c), n) for n, c in zip(raw["name"], raw["council"])]
    pref = raw["oblast"].map(prefix).fillna("")
    opts, types = [], []
    for o, n, typ in zip(pref, names, raw["type"]):
        c = cands.get((o, _norm(n))) or loose.get((o, _loose(n)), {})
        types.append(c)
        opts.append([k for k, t in c.items() if not t or t in TYPE_NOW.get(typ, {t})] or list(c))
    raw["code"] = [o[0] if len(o) == 1 else "" for o in opts]
    # namesakes: the hromada most settlements of the same 2001 council went to, else the raion of the old raion
    for level, col in ((9, "council"), (6, "raion")):
        done = raw[raw["code"] != ""]
        vote = (done.assign(unit=done["code"].str[:level]).groupby(["oblast", col])["unit"]
                .agg(lambda u: u.value_counts().index[0]).to_dict())
        for i in np.nonzero((raw["code"] == "").values)[0]:
            unit = vote.get((raw.at[i, "oblast"], raw.at[i, col]))
            hit = [k for k in opts[i] if unit and k[:level] == unit]
            if len(hit) == 1:
                raw.at[i, "code"] = hit[0]
    for i in np.nonzero((raw["code"] == "").values)[0]:  # still ambiguous: the closest type of today
        for t in TYPE_ORDER.get(raw.at[i, "type"], []):
            hit = [k for k in opts[i] if types[i].get(k) == t]
            if hit:
                if len(hit) == 1:
                    raw.at[i, "code"] = hit[0]
                break
    ok = raw["code"] != ""
    log.info("census %s: %d of %d settlements matched (%.1f%% of population)", path.name, ok.sum(), len(raw),
             100 * raw.loc[ok, "population"].sum() / raw["population"].sum())

    sett = (raw[ok].groupby("code").agg(name=("name", "first"), type=("type", "first"),
                                         population=("population", "sum"), oblast=("oblast", "first")).reset_index())
    sett["kind"] = "settlement"
    label = dict(zip(current["code"], current["label"]))
    sett["label"] = sett["code"].map(label).fillna(sett["name"])
    # totals of today's hromadas, as far as their settlements are matched
    hrom = current[current["kind"] == "hromada"][["code", "kind", "name", "type", "label", "oblast"]].copy()
    hrom["population"] = hrom["code"].str[:9].map(sett.groupby(sett["code"].str[:9])["population"].sum())
    hrom = hrom.dropna(subset=["population"])
    lost = raw[~ok].assign(kind="unmatched", label=raw.loc[~ok, "name"] + " (" + raw.loc[~ok, "council"] + ")")
    cols = ["code", "kind", "name", "type", "label", "population", "oblast"]
    df = pd.concat([sett[cols], hrom[cols], lost[cols]], ignore_index=True)
    df["population"] = df["population"].astype("Int64")
    return df


def available_years() -> dict[int, Path]:
    out = {}
    for p in sorted(RAW_DIR.glob("population_*.xlsx")):
        m = RAW_RE.search(p.name)
        if m:
            out[int(m.group(1))] = p
    return out


def year_source(path: Path) -> dict:
    m = RAW_RE.search(path.name)
    return {"kind": SOURCES[m.group(4)], "date": f"{m.group(1)}-{m.group(2)}-{m.group(3)}"}


def load_year(year: int, path: Path) -> pd.DataFrame:
    """Parsed table for `year`, cached as CSV next to the raw workbook."""
    csv = OUT_DIR / f"population_{year}.csv"
    if csv.exists() and csv.stat().st_mtime >= path.stat().st_mtime:
        df = pd.read_csv(csv, dtype={"code": str}, keep_default_na=False, na_values={"population": [""]})
        df["population"] = df["population"].astype("Int64")
        return df
    if year_source(path)["kind"] == "census":
        coded = {y: p for y, p in available_years().items() if year_source(p)["kind"] == "estimate"}
        if not coded:
            raise FileNotFoundError("Matching a census needs a Derzhstat workbook with KATOTTG codes")
        df = parse_census(path, load_year(max(coded), coded[max(coded)]))
    else:
        df = parse_derzhstat(path)
    df.to_csv(csv, index=False, encoding="utf-8")
    log.info("demography %d: %d rows parsed from %s", year, len(df), path.name)
    return df


# --------------------------------------------------------------------------- matching

def _norm(s: str) -> str:
    return re.sub(r"[’'ʼ`]", "’", (s or "").strip().lower()).replace("ё", "е")


def city_row(city: City, df: pd.DataFrame) -> pd.Series | None:
    if city.katottg:
        hit = df[df["code"] == city.katottg]
        return hit.iloc[0] if len(hit) else None
    cand = df[(df["kind"] == "settlement") & (df["type"] == "city") & (df["name"].map(_norm) == _norm(city.name_local))]
    if len(cand) == 1:
        return cand.iloc[0]
    if len(cand) > 1:  # same name in several oblasts: prefer the one whose hromada carries the city's name
        log.warning("[%s] ambiguous city name in demography table; set `katottg` in config/cities.yaml", city.id)
    return None


def _match_by_name(p: dict, by_name: dict, hromadas: set, raions: set, oblasts: set) -> str | None:
    """KATOTTG code of an OSM settlement by name: candidates of a compatible type, then the only one in the
    hromadas around the city, else in its raions, else in its oblasts."""
    cands = by_name.get(_norm(p.get("name")), [])
    ok = OSM_TYPES.get(p.get("type") or "", set())
    typed = [c for c in cands if not c[1] or not ok or c[1] in ok] or cands
    for pool in ([c for c in typed if c[0][:9] in hromadas], [c for c in typed if c[0][:6] in raions],
                 [c for c in typed if c[2] in oblasts]):
        if len(pool) == 1:
            return pool[0][0]
    return None


def _settlement_tags(city: City) -> dict[str, dict]:
    p = Paths.interim(city.id) / "settlements.parquet"
    if not p.exists():
        return {}
    s = pd.read_parquet(p, columns=["osm_type", "osm_id", "tags"])
    return {f"{t}{i}": json.loads(tags) for t, i, tags in zip(s["osm_type"], s["osm_id"], s["tags"])}


def _point_codes(city: City, feats: list) -> dict[str, str]:
    """KATOTTG codes from `place=*` points: OSM usually puts `katotth` on the settlement's point, not on its
    outline. For each settlement outline, the code of a same-named point inside it."""
    import geopandas as gpd
    import shapely
    p = Paths.interim(city.id) / "settlements.parquet"
    if not p.exists():
        return {}
    s = gpd.read_parquet(p)
    s = s[s["kind"] == "point"]
    t = s["tags"].map(json.loads)
    s = s.assign(code=t.map(lambda x: (x.get("katotth") or "").strip()), nm=t.map(lambda x: _norm(x.get("name:uk") or x.get("name"))))
    s = s[s["code"] != ""]
    out = {}
    if s.empty:
        return out
    tree = shapely.STRtree(s.geometry.values)
    for f in feats:
        pr = f["properties"]
        geom = shapely.geometry.shape(f["geometry"]).buffer(0.002)  # ~150 m: approximate outlines are tight
        idx = tree.query(geom, predicate="contains")
        hits = [s["code"].iloc[i] for i in idx if s["nm"].iloc[i] == _norm(pr.get("name"))]
        if len(set(hits)) == 1:
            out[pr.get("osm")] = hits[0]
    return out


def city_demography(city: City, df: pd.DataFrame, ref: pd.DataFrame | None = None) -> dict:
    """Population per territory mode for one city and one year. Settlements are identified in `ref` (the latest
    table with KATOTTG codes and today's names) and their population is read from `df` by code."""
    ref = df if ref is None else ref
    terr_p = Paths.interim(city.id) / "territories.json"
    terr = json.loads(terr_p.read_text(encoding="utf-8")) if terr_p.exists() else {"modes": {}}
    by_code = df[df["code"] != ""].drop_duplicates("code").set_index("code")
    rrow = city_row(city, ref)
    if rrow is not None and rrow["code"] in by_code.index:
        crow = by_code.loc[rrow["code"]].copy()
        crow["code"] = rrow["code"]
    else:
        crow, ref = city_row(city, df), df  # not in today's table (Crimea, Sevastopol): match within `df`
    if crow is None or pd.isna(crow["population"]):
        return {"available": False, "reason": "no_data" if crow is None else "na"}
    out = {"available": True, "city_code": crow["code"]}
    city_pop = int(crow["population"])
    modes = {"city": {"population": city_pop}}

    # hromada: the hromada total row (same oblast/raion/hromada code prefix)
    hromada_status = terr["modes"].get("hromada", {}).get("status", "not_computed")
    if hromada_status in ("ok", "same_as_city"):
        h = df[(df["kind"] == "hromada") & (df["code"].str[:9] == crow["code"][:9])]
        if len(h) and not pd.isna(h.iloc[0]["population"]):
            modes["hromada"] = {"population": int(h.iloc[0]["population"]), "name": h.iloc[0]["label"]}
    else:
        modes["hromada"] = {"population": city_pop}  # mode shows the city

    # greater city: city + included settlements
    sp = Paths.interim(city.id) / "settlements.geojson"
    if terr["modes"].get("greater", {}).get("status") == "ok" and sp.exists():
        feats = json.loads(sp.read_text(encoding="utf-8"))["features"]
        tags = _settlement_tags(city)
        pcodes = _point_codes(city, [f for f in feats if f["properties"].get("in_greater")])
        settl = ref[ref["kind"] == "settlement"]
        ref_kind = dict(zip(ref["code"], ref["kind"]))
        by_name: dict[str, list] = {}
        for n, c, t, o in zip(settl["name"].map(_norm), settl["code"], settl["type"], settl["oblast"]):
            by_name.setdefault(n, []).append((c, t, o))
        incl = [f["properties"] for f in feats if f["properties"].get("in_greater")]
        # raions of settlements matched by code: used to pick between namesakes
        code_of = lambda p: (tags.get(p.get("osm") or "", {}).get("katotth") or pcodes.get(p.get("osm")) or "").strip()  # noqa: E731
        coded = {code_of(p) for p in incl} - {""}
        hromadas = {crow["code"][:9]} | {c[:9] for c in coded}
        raions = {crow["code"][:6]} | {c[:6] for c in coded}
        oblast_of = dict(zip(settl["code"].str[:4], settl["oblast"]))
        oblasts = {crow["oblast"]} | {oblast_of[r[:4]] for r in raions if r[:4] in oblast_of}  # Kyiv: Kyiv Oblast
        total, items, unmatched, seen = city_pop, [], [], {crow["code"]}
        for p in incl:
            code, how = code_of(p), "katottg"
            if ref_kind.get(code) != "settlement":
                code, how = _match_by_name(p, by_name, hromadas, raions, oblasts), "name"
            if code is None or code in seen:
                if code is None:
                    unmatched.append(p.get("name") or "—")
                continue
            seen.add(code)
            pop = by_code.at[code, "population"] if code in by_code.index else None
            pop = None if pop is None or pd.isna(pop) else int(pop)
            items.append({"name": p.get("name"), "osm": p.get("osm"), "code": code, "population": pop, "match": how})
            total += pop or 0
        modes["greater"] = {"population": total, "settlements": items, "unmatched": unmatched}
    out["modes"] = modes
    return out


def build_demography() -> dict:
    years = available_years()
    if not years:
        raise FileNotFoundError(f"No Derzhstat workbooks in {RAW_DIR}")
    cities = [c for c in load_cities() if (Paths.web_city(c.id) / "stats.json").exists()]
    tables = {y: load_year(y, p) for y, p in sorted(years.items())}
    coded = [y for y, p in years.items() if year_source(p)["kind"] == "estimate"]
    ref = tables[max(coded)] if coded else None
    result = {"schema_version": 1, "years": sorted(years),
              "sources": {str(y): year_source(p) for y, p in sorted(years.items())}, "cities": {}}
    for year, df in tables.items():
        if year_source(years[year])["kind"] == "census":
            lost = df[df["kind"] == "unmatched"]
            result["sources"][str(year)]["unmatched"] = {"settlements": len(lost),
                                                        "population": int(lost["population"].sum())}
        for c in cities:
            d = city_demography(c, df, ref)
            result["cities"].setdefault(c.id, {})[str(year)] = d
            if d["available"]:
                m = d["modes"]
                g = m.get("greater", {})
                log.info("[%s] %d: city %s, hromada %s, greater %s (%d settlements, %d unmatched)", c.id, year,
                         f"{m['city']['population']:,}", f"{m.get('hromada', {}).get('population', 0):,}",
                         f"{g.get('population', 0):,}", len(g.get("settlements", [])), len(g.get("unmatched", [])))
            else:
                log.info("[%s] %d: no population data (%s)", c.id, year, d["reason"])
    out = Paths.web_data() / "demography.json"
    out.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    log.info("%s written (%d cities, years %s)", out, len(cities), sorted(years))
    return result
