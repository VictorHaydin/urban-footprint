# Territory modes: City · Hromada · Greater City (design)

Status: implemented on branch `feature/suburbs` (pipeline stage `territories`, web app switch); data rebuilt for
all configured cities. A city without `territories: true` shows the city boundary in the new modes.

The app currently measures each city inside its own administrative boundary. This feature adds a **global
setting** with three territory modes:

| Mode | UA | Territory |
|---|---|---|
| City | Місто | the city's own OSM boundary (current behaviour) |
| Hromada | Громада | the urban territorial community (міська територіальна громада) the city belongs to |
| Greater City | Місто з передмістями | the city plus surrounding settlements within 10 min drive of it (rule below) |

## Hromada mode

* Territory = the OSM boundary of the city's hromada (`admin_level=7`), configured per city as
  `hromada_relation` in `config/cities.yaml`.
* **Kyiv** has no hromada level (a city with special status is itself the community), so its hromada
  territory equals the city.
* Some hromadas consist of the city only (e.g. Kharkiv): the result equals City mode.
* **Occupied cities** (`occupied: true` in the config; currently Donetsk) always use the city boundary in
  this mode, even where OSM has a hromada boundary. They stay selectable; the UI marks them
  («окуповане — показано межі міста»).

## Greater City mode

### Rule

A settlement is included if the **typical free-flow car drive time from it to the nearest built-up part of
the city proper is at most D = 10 minutes**.

* *Typical* = median over road points inside the settlement's built-up area (a typical resident, not the
  luckiest house).
* *Built-up part of the city* = any road point inside the city boundary next to buildings or urban land use.
  The destination can be anywhere in the city (a workplace at the edge counts as much as the centre).
* **Non-transitive:** time is measured to the city proper only, never via other suburbs. A village near an
  included town but far from the city stays out.
* Drive times: OSM road network, speed = `maxspeed` tag if present (capped at 1.3× the class default), else a
  default per road class (motorway 100, trunk 80, primary 60, secondary 50, tertiary 45, unclassified 35,
  residential 25, service 15, track 15 km/h…). No traffic, so rush-hour times are longer; documented as such.
* What is added is the settlement's **whole territory** (its outline), not just its built-up area.
* There is no requirement of built-up continuity: the drive-time limit alone decides. (Continuity with the
  city's built-up area is still computed and shown, as context.)

### Settlements: where outlines come from

Settlement mapping in OSM is inconsistent across oblasts, so a settlement is any of:

1. an `admin_level=9` boundary (most of Kyiv and Lviv oblasts);
2. an `admin_level=8` boundary with `place=*` (towns in Kharkiv oblast);
3. a `place=city|town|village|hamlet` area without boundary tags (e.g. Zubra, Merefa, Hostomel);
4. **only a `place=*` point** (44% of settlements within 15 km of Lviv, e.g. Zavadiv, Zashkiv): an
   **approximate outline** is derived from built-up land — the cluster of buildings/urban land use (200 m gap
   rule, grown by 100 m) nearest to the point, within 2 km of it, split between neighbouring point-only
   settlements by proximity, never overlapping official outlines or the city. Marked `approx` in the data and
   in the UI. These outlines are tighter than official ones (no surrounding fields), i.e. they under- rather
   than over-count area.

Duplicates (a boundary and a `place` area for the same settlement) are resolved by preferring the boundary,
then the larger polygon; a polygon lying ≥ 50% inside an already kept one is dropped.

Settlements may lie in other hromadas or oblasts.

### Prototype results (OSM 2026-10-12, D = 10)

| City | Settlements in | Examples in | Examples out |
|---|---|---|---|
| Lviv | 63 | Vynnyky, Sokilnyky, Zymna Voda, Dubliany, Zubra, Briukhovychi, Obroshyne (6.9 min), Pidhirne* | Zashkiv* 11.4, Kulykiv 11.5, Pustomyty 14.7, Zavadiv* 14.9 |
| Kyiv | 64 | Brovary, Vyshneve, Vyshhorod, Irpin (6.0), Hostomel, Bucha (9.1), Skybyn, Kapitanivka | Boryspil 11.2, Hlevakha 11.9, Boiarka 13.5, Vorzel 15.6 |
| Kharkiv | 45 | Pisochyn, Vysokyi, Pokotylivka, Bezliudivka, Rohan, Korotych | Derhachi 10.6, Berezivka 10.8, Merefa 17.9 |

\* point-only settlement with an approximate outline. Zavadiv is close as the crow flies but separated from
Lviv by the Holosko/Briukhovychi forests (13 km by road).

Alternatives considered and rejected:

* **Built-up continuity only (200 m gap rule):** matched the first examples, but chains along roads (ribbon
  development) and cannot bridge forests (Irpin, Bucha).
* **Density grid (Degree of Urbanisation style):** misses low-density suburbs (Zymna Voda).
* **Continuity or town status, with a time cap:** excluded close villages in gaps (Skybyn, Obroshyne).

## Data architecture

* Each city's territory is split into up to four disjoint **units** (`urbanfp/territories.py`):
  A = the city, B = land in both the hromada and Greater City, C = hromada only, D = Greater City only.
  City = A, Hromada = A+B+C, Greater City = A+B+D. Overlay, statistics and map data are computed per unit,
  so every mode is an exact sum of units and nothing is stored twice.
* Extraction (cities with `territories: true`) keeps all features in the region = city + hromada +
  `suburbs_radius_km` (default 25 km), plus settlement outlines and `place=*` points (a fast node-only pass).
* `stats.json` keeps City-mode figures at the top level and every mode under `modes`; each mode has its own
  validation. Vector tiles carry the unit (`u`); overview rasters are stored per unit
  (`overview/<unit>/<category>.pmtiles`); `territories.geojson` has the outline of each mode.
* The web app shows the selected territory's outline and **hides everything outside it** (no fading).
* Settlement-level results (drive time, continuity share, built-up area, approx flag, inclusion per mode) are
  exported for the Method & data page and validation.

## UI

* A global three-way setting «Місто / Громада / Місто з передмістями» ("City / Hromada / Greater City") below
  the city selectors on the map page and below the filters on the comparison page; kept in the URL and
  remembered in the browser.
* Occupied cities stay selectable in every mode; in Hromada mode they show the note that the city boundary is
  used.
* The map popup / sidebar shows which settlements are included in Greater City mode, with drive times.

## Open points

* Measure drive time to the city proper (current) or to its continuous built-up area (would include, e.g.,
  villages behind Briukhovychi)? Default: city proper.
* Public-transport times would be more meaningful than car times, but no Ukraine-wide timetable data exists.
