# Urban Footprint — city land-use analysis from OpenStreetMap

Ukrainian product name: **Структура міст** (the web app opens in Ukrainian by default).

How much of a city's administrative territory is physically occupied by buildings, roads, railways,
parks, water, industrial land and so on? This project computes that **reproducibly** from
OpenStreetMap (OSM) data and shows it in a static web app.

Current cities: **Kyiv, Kharkiv, Odesa, Dnipro, Donetsk, Zaporizhzhia, Lviv** (add more in [`config/cities.yaml`](config/cities.yaml)).

It has two parts:

| Part | Where | What |
|---|---|---|
| Local processing pipeline | [`urbanfp/`](urbanfp), [`config/`](config) | Downloads OSM data, classifies features, builds a mutually exclusive area partition, validates it, and exports datasets |
| Static web app | [`web/`](web) | Map with toggleable categories and per-city statistics, plus a split-screen mode for two cities at the same ground scale. Comparison of selected cities (multi-select filter) as stacked bars: click a category on a bar or in the legend to line up and rank that category across cities. Reads only precomputed files in `web/data/` and runs on GitHub Pages |

Documentation:
* [docs/METHODOLOGY.md](docs/METHODOLOGY.md): classification, footprint estimation, overlap resolution, assumptions, limitations
* [docs/DATA_MODEL.md](docs/DATA_MODEL.md): every output file and its schema, plus how to use the feature-level data for animation

---

## 1. Local setup

Requirements: Python ≥ 3.11 (tested with 3.13 on Windows 11), about 4 GB RAM free, and about 3 GB of disk.
All dependencies, including `osmium` (pyosmium), have binary wheels for Windows, Linux and macOS.

```bash
python -m venv .venv
# Windows:  .venv\Scripts\activate      Linux/macOS:  source .venv/bin/activate
pip install -e .
```

## 2. Processing commands

```bash
python -m urbanfp list                 # configured cities + which stages are done
python -m urbanfp run --all            # full pipeline for all cities (incremental)
python -m urbanfp run lviv             # one city
python -m urbanfp run --all --refresh  # re-download the latest OSM extract first
python -m urbanfp run kyiv --force     # ignore caches, recompute everything
python -m urbanfp run --all --from overlay   # restart from a given stage
```

Each stage can also be run on its own: `download`, `extract`, `classify`, `overlay`, `stats`, `export`.

| Stage | Input → Output | Typical time (Kyiv) |
|---|---|---|
| `download` | Geofabrik `europe/ukraine-latest.osm.pbf` (~0.9 GB) → `data/sources/` | 5 min |
| `extract` | PBF → `data/interim/<city>/boundary.geojson`, `osm_features.parquet`. One pass handles all cities that share a source file | 15–25 min for all cities |
| `classify` | → `classified.parquet` (one physical footprint per OSM feature) | 1 min |
| `overlay` | → `data/processed/<city>/exclusive.parquet` (mutually exclusive pieces) | a few min (parallel) |
| `stats` | → `stats.json`, `validation.json`, `validation.md` | < 1 min |
| `export` | → `web/data/<city>/…`, `web/data/index.json`, `city_features.parquet` | a few min |

**Incremental processing:** a stage is skipped when its outputs are newer than its inputs and the
classification config hash is unchanged. If you edit rules, priorities or widths in
`config/classification.yaml`, `run` recomputes `classify` and the stages after it. If you only edit labels
or colours, it re-runs just `export`. The expensive `extract` stage re-runs only when the source extract
or the set of required OSM keys changes.

**Validation:** read `data/processed/<city>/validation.md` after every run. It checks that the area
sums close, that exported pieces don't overlap, the unclassified share, unknown building types,
how much area relies on default road widths, unmatched OSM tags, and invalid geometries. The
web app shows a summary of these checks.

### Adding a city

1. Find the OSM relation of its administrative boundary. Search the city on openstreetmap.org, or
   use Nominatim: `https://nominatim.openstreetmap.org/search?q=Kharkiv&format=json` and read
   `osm_id` where `osm_type` is `relation`.
2. Add an entry to `config/cities.yaml`. For a city outside Ukraine, set `source` to the matching
   Geofabrik region, e.g. `geofabrik:europe/poland`.
3. `python -m urbanfp run <id>`. The web app picks it up automatically from `web/data/index.json`.

### Changing categories or rules

Everything lives in [`config/classification.yaml`](config/classification.yaml): categories and colours,
tag rules, painting priority, and road, rail and waterway width assumptions. New categories appear
in the web app automatically.

## 3. Web app

Preview locally. You need an HTTP server **with Range-request support** for the PMTiles files.
Python's built-in `http.server` doesn't support it, so use the bundled one:

```bash
python -m urbanfp serve
```

Then open <http://localhost:8000>.

### Languages

The interface is in **Ukrainian by default**, with English available. Users switch with the УКР / ENG button
in the top bar, and the choice is saved in a `lang` cookie for one year.

* UI strings: `web/i18n.js` (`STRINGS.uk` / `STRINGS.en`, plus Ukrainian templates for validation messages)
* category names: `label` / `label_uk` in `config/classification.yaml`, exported to `web/data/index.json`
* methodology page: `web/about.html` (EN) and `web/about.uk.html` (UA)
* city names: `name` (EN) / `name_local` (UA) in `config/cities.yaml`

Changing labels or colours only re-runs the `export` stage. The cached computations use a methodology-only
config hash.

### Deploying to GitHub Pages

1. Run the pipeline locally and commit `web/` (including `web/data/`). Raw and intermediate data in
   `data/` are git-ignored.
2. In the GitHub repository, go to **Settings → Pages → Build and deployment → Source** and choose
   **GitHub Actions**.
3. Push to `main`. [`.github/workflows/pages.yml`](.github/workflows/pages.yml) publishes the
   `web/` folder.

Notes:
* `features.pmtiles` and `overview/*.pmtiles` files are read with HTTP range requests, which GitHub Pages
  supports. Overview rasters are tiled pyramids so phones only decode the tiles on screen (Kyiv needs ~14 MB
  of GPU memory at city scale instead of ~870 MB with full-size images).
* GitHub rejects files over 100 MB. The largest file is a city's `features.pmtiles`; check its size
  in the export log. If a future city goes over the limit, lower `VT_MAXZOOM` in
  `urbanfp/export_web.py` or track `*.pmtiles` with Git LFS. The workflow checks out LFS files.
* The base map uses openstreetmap.org raster tiles, which is fine for light use. For heavy traffic,
  switch to a tile provider of your own in `web/app.js`.

## 4. Repository layout

```
config/            cities.yaml, classification.yaml
urbanfp/           pipeline package (cli, sources, extract, classify, overlay, stats, export_web)
web/               static app (index.html, app.js, style.css, about.html) + data/ (generated)
docs/              methodology and data model
data/              (git-ignored) sources/, interim/, processed/
```

## Licence

Code: MIT. Data: © OpenStreetMap contributors, ODbL. Derived datasets in `web/data` and `data/` are ODbL.
