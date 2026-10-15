**English** · [Українська](README.md)

# Urban Footprint (Структура міст)

How much of a city's administrative territory is physically occupied by buildings, roads, railways,
parks, water, residential and industrial land? This project computes that **reproducibly** from
OpenStreetMap (OSM) data and shows it in a static web app (Ukrainian by default, with English available).

**Web app:** <https://victorhaydin.github.io/urban-footprint/>

Cities: **Kyiv, Kharkiv, Odesa, Dnipro, Donetsk, Zaporizhzhia, Lviv, Kryvyi Rih, Ivano-Frankivsk, Ternopil**. More can be added in
[`config/cities.yaml`](config/cities.yaml).

| Part | Where | What |
|---|---|---|
| Local processing pipeline | [`urbanfp/`](urbanfp), [`config/`](config) | Downloads OSM data, classifies features, builds a mutually exclusive area partition, validates it, exports datasets |
| Static web app | [`web/`](web) | Reads only precomputed files in `web/data/`; runs on GitHub Pages without a backend |

Documentation (in English):
* [docs/METHODOLOGY.md](docs/METHODOLOGY.md): classification, footprint estimation, overlap resolution, assumptions, limitations
* [docs/DATA_MODEL.md](docs/DATA_MODEL.md): every output file and its schema, plus how to use the feature-level data for animation

## Features

**Method**
* 13 mutually exclusive categories (buildings by type, roads, railways, parking, green space, water, residential land,
  industrial land, sports, cemeteries, agriculture, other land, unclassified) that add up to exactly 100% of the area.
* Actual building footprints; land-use categories exclude the buildings, roads, parking etc. inside them.
* Roads and railways mapped as lines are widened to an estimated physical width.
* Validation per city: area closure, overlaps, unclassified share, unknown building types, default widths,
  unmatched tags and suspicious features (likely OSM tagging errors).

**Web app**
* **Map:** category toggles, % / km², sub-categories as a share of their category or of the city, and
  click-to-inspect for every counted piece with a link to the OSM object.
* **Two cities side by side:** split-screen maps at the same ground scale (latitude-corrected), zooming
  together and panning independently. The sidebar compares the two cities category by category.
* **City comparison:** stacked bars (% or km²) for a chosen set of cities (multi-select filter). Click a category
  to focus on it and rank the cities by it. Breakdown by sub-category; collapsible data table.
* **Languages:** Ukrainian (default) and English, remembered in a cookie.
* **Mobile:** tiled rasters and vector tiles. Kyiv needs ~14 MB of GPU memory at city scale.
* Shareable links: city, second city, view and selected cities are kept in the URL.

---

## 1. Local setup

Requirements: Python ≥ 3.11 (tested with 3.13 on Windows 11), ~4 GB free RAM and ~3 GB disk.
All dependencies, including `osmium` (pyosmium), have binary wheels for Windows, Linux and macOS.

```bash
python -m venv .venv
# Windows:  .venv\Scripts\activate      Linux/macOS:  source .venv/bin/activate
pip install -e .
```

## 2. Processing

```bash
python -m urbanfp list                 # configured cities + which stages are done
python -m urbanfp run --all            # full pipeline for all cities (incremental)
python -m urbanfp run lviv             # one city
python -m urbanfp run --all --refresh  # re-download the latest OSM extract first
python -m urbanfp run kyiv --force     # ignore caches, recompute everything
python -m urbanfp run --all --from overlay   # restart from a given stage
python -m urbanfp serve                # preview the web app at http://localhost:8000
```

Each stage can also be run on its own: `download`, `extract`, `classify`, `overlay`, `stats`, `export`.

| Stage | Input → Output | Typical time |
|---|---|---|
| `download` | Geofabrik `europe/ukraine-latest.osm.pbf` (~0.9 GB) → `data/sources/` | ~5 min |
| `extract` | PBF → `data/interim/<city>/boundary.geojson`, `osm_features.parquet`. One pass covers all cities sharing a source file | ~12 min for any number of cities |
| `classify` | → `classified.parquet` (one physical footprint per OSM feature) | seconds |
| `overlay` | → `data/processed/<city>/exclusive.parquet` (mutually exclusive pieces; tiled, parallel) | 1–4 min per city |
| `stats` | → `stats.json`, `validation.json`, `validation.md` | < 1 min |
| `export` | → `web/data/<city>/…` (stats, boundary, overview raster tiles, vector tiles), `web/data/index.json`, `city_features.parquet` | ~1–2 min per city |

**Incremental processing:** a stage is skipped when its outputs are newer than its inputs and the configuration
hash is unchanged. Editing rules, priorities or widths in `config/classification.yaml` re-runs `classify` and the
stages after it; editing only labels or colours re-runs just `export`. `extract` re-runs only when the source
extract or the set of required OSM keys changes.

**Validation:** after every run, read `data/processed/<city>/validation.md`. The web app shows a summary too.
The `suspicious_features` check lists implausibly large road, rail or parking polygons with their OSM ids. For
example, the Dnipro Reservoir is tagged `highway=residential` in OSM. Such errors are best fixed in OSM itself.

### Adding a city

1. Find the OSM relation of its administrative boundary on openstreetmap.org, or with Nominatim:
   `https://nominatim.openstreetmap.org/search?q=Poltava&format=json`. Use the `osm_id` where `osm_type` is
   `relation`, and prefer the city itself over the whole hromada.
2. Add an entry to [`config/cities.yaml`](config/cities.yaml) with `name`, `name_local`, `boundary_relation`, `group` (`million` for the “largest cities” group; otherwise the city goes to “Other cities”) and,
   optionally, `notes` / `notes_uk`, which are shown on the Method & data page. For a city outside Ukraine,
   set `source` to the Geofabrik region, e.g. `geofabrik:europe/poland`.
3. Run `python -m urbanfp run <id>`. Compare the boundary area in the log with the official city area, and check
   `validation.md`. The web app picks the city up automatically from `web/data/index.json`.

### Changing categories or rules

Everything lives in [`config/classification.yaml`](config/classification.yaml): categories with English and
Ukrainian labels and colours, tag rules, painting priority, and road, rail and waterway width assumptions.
New categories appear in the web app automatically.

## 3. Web app

Preview locally with `python -m urbanfp serve`. The PMTiles files need an HTTP server with Range-request support,
which Python's built-in `http.server` lacks.

### Languages

* UI strings: [`web/i18n.js`](web/i18n.js) (`STRINGS.uk` / `STRINGS.en`, plus Ukrainian templates for validation messages)
* category names: `label` / `label_uk` in `config/classification.yaml`
* city names and notes: `name` / `name_local`, `notes` / `notes_uk` in `config/cities.yaml`
* Method & data page: [`web/about.uk.html`](web/about.uk.html) and [`web/about.html`](web/about.html); its city table is generated from the data

### Deploying to GitHub Pages

1. Run the pipeline locally and commit `web/`, including `web/data/`. Raw and intermediate data in `data/` are git-ignored.
2. In the repository settings, set **Pages → Build and deployment → Source** to **GitHub Actions** (once).
3. Push to `main`. [`.github/workflows/pages.yml`](.github/workflows/pages.yml) publishes the `web/` folder.

Notes:
* `features.pmtiles` (vector tiles, z14) and `overview/*.pmtiles` (raster pyramids per category, z6–13, lossless WebP)
  are read with HTTP range requests, which GitHub Pages supports. Only visible tiles are downloaded and decoded.
* GitHub rejects files over 100 MB. The largest file is Kyiv's `features.pmtiles` (~18 MB); all web data for
  7 cities is ~110 MB. GitHub Pages sites are limited to 1 GB, so for dozens of cities consider Git LFS or
  separate data hosting.
* The base map uses openstreetmap.org raster tiles, which is fine for light use. For heavy traffic, switch to your own
  tile provider in `web/app.js`.

## 4. Repository layout

```
config/            cities.yaml, classification.yaml
urbanfp/           pipeline (cli, sources, extract, classify, overlay, stats, export_web, serve)
web/               static app (index.html, app.js, i18n.js, style.css, about*.html, favicon) + data/ (generated)
docs/              methodology and data model
scripts/           make_favicon.py
tests/             unit tests (python -m pytest tests)
data/              (git-ignored) sources/, interim/, processed/, logs/
```

## Licence

Code: MIT (see [LICENSE](LICENSE)). Data: © OpenStreetMap contributors, ODbL. Derived datasets in `web/data` and `data/` are ODbL.
