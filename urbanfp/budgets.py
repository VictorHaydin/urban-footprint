"""Annual budgets of every hromada from OpenBudget (Ministry of Finance, https://openbudget.gov.ua).

For each hromada of the latest Derzhstat table (KATOTTG code) the budget code is looked up in the OpenBudget
budget dictionary (`BUDG`, which carries the KATOTTG code), and two reports are read per year through the public
API (https://api.openbudget.gov.ua/swagger-ui.html), both cumulative, of which only the last quarter (= the year)
is kept, all funds together (FUND_TYP = T):

* INCOMES, by revenue code: actual revenue (FAKT_AMT). Codes 1… taxes, 2… non-tax, 3… capital, 4… transfers,
  5… earmarked funds; only detailed codes are reported, groups are summed by code prefix.
* EXPENSES by programme (classificationType=PROGRAM): actual spending (FAKT_AMT) per programme, with its functional
  code (COD_CONS_MB_FK) and economic code (COD_CONS_EK; rows with 0000 are the programme totals, the other rows
  their economic breakdown). The FUNCTIONAL report is not used: its "actual" column repeats the plan.

Output: data/budgets/budgets_<years>.csv, one row per hromada and year, amounts in UAH (columns below).
API responses are cached in data/budgets/cache/, so an interrupted run resumes where it stopped.
`export_budgets` writes web/data/budgets.json for the web app: the budget of each configured city's hromada
(Kyiv: the city budget), by year.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

from urbanfp.config import DATA_DIR, Paths, load_cities
from urbanfp.demography import available_years, load_year

log = logging.getLogger(__name__)

API = "https://api.openbudget.gov.ua"
BUDGET_DIR = DATA_DIR / "budgets"
CACHE = BUDGET_DIR / "cache"
YEARS = (2021, 2025)
KYIV = "UA80000000000093317"
WORKERS = 2          # more parallel requests make the API answer 503
RETRIES = 6

# revenue groups: column -> code prefixes (revenue classification)
INCOME_GROUPS = {
    "inc_total": ("",),
    "inc_own": ("1", "2", "3"),            # own revenue: taxes, non-tax, capital
    "inc_tax": ("1",),
    "inc_pit": ("1101",),                  # personal income tax (ПДФО)
    "inc_single_tax": ("180504",),         # single tax (єдиний податок)
    "inc_property": ("1801",),             # property tax: real estate, land, rent of land
    "inc_excise": ("14",),                 # excise duties (fuel, tobacco, alcohol retail)
    "inc_nontax": ("2",),
    "inc_capital": ("3",),
    "inc_transfers": ("4",),               # official transfers
    "inc_dotations": ("4102",),            # basic / additional / reverse-compensating dotations
    "inc_subventions": ("4103", "4104", "4105"),
    "inc_edu_subvention": ("41033900",),   # education subvention from the state budget
    "inc_funds": ("5",),                   # earmarked funds
}
# spending by functional classification: column -> code prefixes (first digits of COD_CONS_MB_FK)
EXPENSE_GROUPS = {
    "exp_total": ("",),
    "exp_admin": ("011", "012", "013", "014", "015", "016"),  # government and administration (01 without debt/transfers)
    "exp_debt": ("017",),                  # debt service
    "exp_transfers": ("018",),             # transfers to other budgets, incl. the reverse dotation
    "exp_defence": ("02",),
    "exp_order": ("03",),                  # public order, safety
    "exp_economy": ("04",),                # economic activity: roads, transport, agriculture
    "exp_environment": ("05",),
    "exp_housing": ("06",),                # housing and communal services
    "exp_health": ("07",),
    "exp_culture": ("08",),                # culture and sport
    "exp_education": ("09",),
    "exp_social": ("10",),                 # social protection
}
ECONOMIC_GROUPS = {"exp_current": ("2",), "exp_capital": ("3",)}  # by economic code of the breakdown rows


# --------------------------------------------------------------------------- API

def _get(url: str) -> str:
    err = None
    for attempt in range(RETRIES):
        if attempt:
            time.sleep(5 * attempt)  # back off: the API answers 503 when it is busy
        try:
            with urllib.request.urlopen(url, timeout=180) as r:
                return r.read().decode("utf-8", "replace")
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            err = e
    raise RuntimeError(f"{url}: {err}")


def budget_codes() -> pd.DataFrame:
    """Every budget code a KATOTTG unit has had (OpenBudget dictionary BUDG): katottg, codebudg, namebudg, begin,
    end. Codes change over the years (Kryvyi Rih 0457800000 until 2022, 0457810000 since 2023)."""
    p = BUDGET_DIR / "budget_codes.csv"
    if p.exists():
        return pd.read_csv(p, dtype=str, keep_default_na=False)
    log.info("downloading the OpenBudget budget dictionary (~180 MB)…")
    rows = json.loads(_get(f"{API}/items/BUDG"))
    df = pd.DataFrame([{"katottg": r.get("katottg") or "", "codebudg": r.get("codebudg") or "",
                        "namebudg": r.get("namebudg") or "", "begin": r.get("beginDate") or "",
                        "end": r.get("endDate") or ""} for r in rows if r.get("katottg") and r.get("codebudg")])
    df = df.drop_duplicates()
    BUDGET_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(p, index=False, encoding="utf-8")
    return df


def codes_for_year(codes: pd.DataFrame, year: int) -> dict[str, list[str]]:
    """katottg -> budget codes to try for `year`: records valid that year first (10-digit codes before the
    11-digit ones of the transition), then every other code the unit has had, newest first."""
    out = {}
    for kat, g in codes.groupby("katottg"):
        def rank(r):
            valid = (not r.begin or r.begin[:4] <= str(year)) and (not r.end or r.end[:4] >= str(year))
            return (0 if valid else 1, len(r.codebudg) != 10, -int((r.begin or "0")[:4] or 0))
        seen = []
        for r in sorted(g.itertuples(), key=rank):
            if r.codebudg not in seen:
                seen.append(r.codebudg)
        out[kat] = seen
    return out


def _fetch_year(code: str, year: int, item: str) -> list[dict]:
    """Last-quarter rows of one report, all funds together (FUND_TYP T); cached as a small CSV."""
    f = CACHE / str(year) / f"{code}_{item}.csv"
    if f.exists():
        return list(csv.DictReader(f.open(encoding="utf-8")))
    q = "budgetItem=INCOMES" if item == "inc" else "budgetItem=EXPENSES&classificationType=PROGRAM"
    txt = _get(f"{API}/api/public/localBudgetData?budgetCode={code}&{q}&period=QUARTER&year={year}")
    rows = list(csv.DictReader(io.StringIO(txt), delimiter=";"))
    last = f"12.{year}"
    keep = [r for r in rows if r.get("REP_PERIOD") == last and r.get("FUND_TYP") == "T"]
    cols = (["COD_INCO", "FAKT_AMT"] if item == "inc" else ["COD_CONS_MB_PK", "COD_CONS_MB_FK", "COD_CONS_EK", "FAKT_AMT"])
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as out:
        w = csv.DictWriter(out, fieldnames=cols + ["periods"])
        w.writeheader()
        periods = ",".join(sorted({r.get("REP_PERIOD", "") for r in rows}))
        for r in keep:
            w.writerow({**{c: r.get(c, "") for c in cols}, "periods": periods})
        if not keep:  # no data for the year: remember the periods seen (empty when none)
            w.writerow({**{c: "" for c in cols}, "periods": periods})
    tmp.replace(f)
    return list(csv.DictReader(f.open(encoding="utf-8")))


def _amount(r) -> float:
    try:
        return float(r.get("FAKT_AMT") or 0)
    except ValueError:
        return 0.0


def summarise(codes: list[str], year: int) -> dict:
    """Annual summary from the first of `codes` that has income data for the year."""
    for code in codes:
        inc = [r for r in _fetch_year(code, year, "inc") if r.get("COD_INCO")]
        if inc:
            break
    else:
        code = codes[0]
    exp = [r for r in _fetch_year(code, year, "exp") if r.get("COD_CONS_MB_FK")]
    out = {"budget_code": code, "has_income": bool(inc), "has_expenses": bool(exp)}
    for col, prefixes in INCOME_GROUPS.items():
        out[col] = round(sum(_amount(r) for r in inc if r["COD_INCO"].startswith(prefixes)), 2)
    totals = [r for r in exp if r.get("COD_CONS_EK") == "0000"]       # programme totals
    detail = [r for r in exp if r.get("COD_CONS_EK") not in ("0000", "")]
    for col, prefixes in EXPENSE_GROUPS.items():
        out[col] = round(sum(_amount(r) for r in totals if r["COD_CONS_MB_FK"].startswith(prefixes)), 2)
    for col, prefixes in ECONOMIC_GROUPS.items():
        out[col] = round(sum(_amount(r) for r in detail if r["COD_CONS_EK"].startswith(prefixes)), 2)
    return out


# --------------------------------------------------------------------------- run

class Progress:
    """One status line: done / total, percent, elapsed, ETA, errors (also logged every 2%)."""

    def __init__(self, total: int, label: str):
        self.total, self.label, self.done, self.errors = total, label, 0, 0
        self.t0 = time.time()
        self.lock = threading.Lock()
        self.last_logged = -1

    def step(self, ok: bool):
        with self.lock:
            self.done += 1
            self.errors += 0 if ok else 1
            el = time.time() - self.t0
            eta = el / self.done * (self.total - self.done)
            line = (f"{self.label}: {self.done}/{self.total} ({100 * self.done / self.total:.1f}%), "
                    f"{_hms(el)} elapsed, ~{_hms(eta)} left, {self.errors} errors")
            if sys.stderr.isatty():
                print("\r" + line + " " * 4, end="" if self.done < self.total else "\n", file=sys.stderr, flush=True)
            pct = int(50 * self.done / self.total)  # every 2%
            if pct != self.last_logged or self.done == self.total:
                self.last_logged = pct
                if not sys.stderr.isatty():
                    log.info(line)


def _hms(s: float) -> str:
    s = int(s)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


def build_budgets(years=YEARS, workers: int = WORKERS) -> Path:
    tables = {y: load_year(y, p) for y, p in available_years().items()}
    latest = tables[max(tables)]
    # hromadas, plus Kyiv (a city at region level, with no hromada row)
    units = latest[(latest["kind"] == "hromada") | (latest["code"] == KYIV)]
    hrom = units[["code", "name", "oblast", "population"]].drop_duplicates("code")
    codes = budget_codes()
    per_year = {y: codes_for_year(codes, y) for y in years}
    hrom = hrom.assign(budget_code=hrom["code"].map(lambda k: ",".join(per_year[years[-1]].get(k, [])[:1])))
    log.info("%d hromadas (and Kyiv), %d with an OpenBudget budget code; years %s", len(hrom),
             (hrom["budget_code"] != "").sum(), list(years))
    jobs = [(r.code, tuple(per_year[y][r.code]), y) for r in hrom.itertuples() if r.code in per_year[years[0]] for y in years]
    prog = Progress(len(jobs), "budgets")
    results, failed = {}, []

    def work(job):
        katottg, bcode, year = job
        return job, summarise(bcode, year)

    with ThreadPoolExecutor(workers) as ex:
        futs = [ex.submit(work, j) for j in jobs]
        for fut in as_completed(futs):
            try:
                (katottg, bcode, year), res = fut.result()
                results[(katottg, year)] = res
                prog.step(True)
            except Exception as e:  # noqa: BLE001 - keep going, report at the end
                failed.append(str(e))
                prog.step(False)
    rows = []
    for r in hrom.itertuples():
        for y in years:
            res = results.get((r.code, y))
            rows.append({"katottg": r.code, "name": r.name, "oblast": r.oblast, "population_2022": r.population,
                         "budget_code": (res or {}).get("budget_code", r.budget_code), "year": y,
                         "status": "ok" if res and res["has_income"] else ("no_data" if res else ("no_budget" if not r.budget_code else "error")),
                         **{k: v for k, v in (res or {}).items() if not k.startswith("has_") and k != "budget_code"}})
    df = pd.DataFrame(rows)
    out = BUDGET_DIR / f"budgets_{'_'.join(map(str, years))}.csv"
    df.to_csv(out, index=False, encoding="utf-8")
    log.info("%s: %d rows; %s", out, len(df), df.groupby(["year", "status"]).size().to_dict())
    if failed:
        log.warning("%d requests failed (rerun to retry, cached results are kept); first: %s", len(failed), failed[0])
    export_budgets(out)
    return out


# --------------------------------------------------------------------------- web export

def export_budgets(csv_path: Path | None = None) -> Path:
    """web/data/budgets.json: per configured city, the budget of its hromada (the hromada whose KATOTTG code shares
    the city's oblast/raion/hromada prefix; Kyiv: the city) for every year of the CSV. Years of an occupied
    hromada from its occupation on are flagged `occupied` (budgets of administrations in exile, or nothing)."""
    csv_path = csv_path or max(BUDGET_DIR.glob("budgets_*.csv"), key=lambda f: f.stat().st_mtime)
    df = pd.read_csv(csv_path, dtype={"katottg": str, "budget_code": str})
    demo = json.loads((Paths.web_data() / "demography.json").read_text(encoding="utf-8"))
    by_prefix = {k[:11]: g for k, g in df.groupby(df["katottg"])}
    amounts = [c for c in df.columns if c.startswith(("inc_", "exp_"))]
    years = sorted(int(y) for y in df["year"].unique())
    cities = {}
    for c in load_cities():
        code = next((v.get("city_code") for v in demo["cities"].get(c.id, {}).values() if v.get("city_code")), None)
        if not code:
            continue
        g = df[df["katottg"] == code] if code == KYIV else by_prefix.get(code[:11])
        if g is None or g.empty:
            continue
        first = g.iloc[0]
        entry = {"katottg": first["katottg"], "name": first["name"], "budget_code": first["budget_code"],
                 "population": int(first["population_2022"]), "years": {}}
        for _, r in g.iterrows():
            y = int(r["year"])
            if r["status"] != "ok":
                continue
            occupied = bool(c.occupied_since and c.occupied_since < y) or (r["exp_total"] or 0) <= 0
            entry["years"][str(y)] = {"occupied": True} if occupied else {k: round(float(r[k])) for k in amounts}
        cities[c.id] = entry
    out = {"schema_version": 1, "years": years, "population_date": "2022-01-01",
           "source": "OpenBudget (Ministry of Finance of Ukraine), https://openbudget.gov.ua", "cities": cities}
    path = Paths.web_data() / "budgets.json"
    path.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    log.info("%s: %d cities, years %s", path, len(cities), years)
    return path
