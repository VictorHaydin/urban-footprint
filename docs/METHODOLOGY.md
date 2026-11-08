# Methodology

Goal: for each city, partition the **administrative territory** into mutually exclusive categories of
*physical* land cover/use, so that

```
Σ category areas = administrative area   (exactly)
```

and every number can be traced back to individual OSM objects.

## 1. Data source

| Choice | Reason |
|---|---|
| **OpenStreetMap**, via the Geofabrik country extract (`europe/ukraine-latest.osm.pbf`, updated daily) | Open (ODbL), detailed building footprints and land use, consistent tagging across cities. A pinned file with a known timestamp gives reproducible results. One pass serves all cities in the country |
| Not Overpass API | City-scale queries (Kyiv: > 300k objects) routinely time out on public Overpass servers, and results depend on when the query was run |
| Processed with **pyosmium / libosmium** | libosmium assembles multipolygons robustly, the same way as the OSM rendering stack |

The OSM data timestamp is recorded in `extract.json`, `stats.json` and shown in the web app.

### Alternative / supplementary sources (considered)

| Source | Assessment |
|---|---|
| Mapbox Streets vector tiles | Derived from OSM anyway (plus some proprietary data). The terms of service forbid bulk extraction or caching for analysis, so it isn't usable as a data source. |
| Microsoft Global ML Building Footprints / Overture Maps buildings | Machine-detected footprints (CDLA / ODbL). Useful to **measure OSM building completeness** or fill gaps. Typeless, so they would land in "unknown". Good candidate for a future validation layer |
| ESA WorldCover 10 m (CC-BY 4.0) | Satellite land cover (tree, grass, built-up, water…). Can't separate buildings from roads, but could characterise the "unclassified" remainder (e.g. "x % of unclassified land is tree-covered") |
| Copernicus Urban Atlas | High quality, but covers EU functional urban areas only; Ukraine isn't included |

The pipeline is source-pluggable at the `extract` stage. A supplementary source would be added as an extra
stage that writes features in the same `classified.parquet` schema with its own `basis`.

## 2. Territory

The city is the OSM administrative boundary relation configured in `config/cities.yaml`:

| City | Relation | Notes |
|---|---|---|
| Kyiv | 421866 | City with special status. Includes large forests and Dnipro floodplains |
| Lviv | 2032280 | City proper, not the larger urban hromada |
| Odesa | 1413934 | City proper. Boundary follows the coastline; ~0.04 km² of sea removed (≈ 1.1 km² in Greater City) |
| Kharkiv | 3154746 | City proper |
| Dnipro | 1017311 | City proper |
| Zaporizhzhia | 1418311 | City proper. OSM boundary ≈ 314 km² vs official ≈ 331 km² |
| Donetsk | 1413957 | Temporarily occupied since 2014; OSM mapping may be less complete or current. OSM boundary ≈ 401 km² vs official ≈ 358 km² |
| Kryvyi Rih | 1821193 | City proper, ~60 km long along the iron-ore basin. OSM boundary ≈ 462 km² vs official ≈ 430 km² |
| Ivano-Frankivsk | 2362670 | OSM settlement boundary (≈ 41 km², includes Uhornyky). Mykytyntsi and Khryplyn, incorporated into the city in 2006, are separate settlements in OSM and are not included; official area ≈ 84 km² |
| Ternopil | 3058686 | City proper. OSM boundary ≈ 58.6 km² (official ≈ 59 km²) |
| Chernivtsi | 1742393 | City proper, on both banks of the Prut. OSM boundary ≈ 151.5 km² (official ≈ 153 km²) |
| Lutsk | 1951964 | City proper. OSM boundary ≈ 40.7 km² |
| Zhytomyr | 2692156 | City proper. OSM boundary ≈ 60.5 km² |
| Poltava | 1641691 | City proper. OSM boundary ≈ 105.3 km² |
| Sumy | 3678531 | City proper. OSM boundary ≈ 105.7 km² |
| Vinnytsia | 361818 | City proper. OSM boundary ≈ 113.2 km² |
| Uzhhorod | 2692232 | City; the hromada is the city only. OSM boundary ≈ 37.6 km² |
| Cherkasy | 2825507 | City (relation tagged admin_level 10). OSM boundary ≈ 79.2 km² |
| Rivne | 448930 | City proper. OSM boundary ≈ 58.2 km² |
| Mykolaiv | 11622860 | Mykolaiv Oblast (south). City; the hromada is the city only. Includes river water. OSM boundary ≈ 251.1 km² |
| Bila Tserkva | 2069683 | City proper, Kyiv Oblast. OSM boundary ≈ 68.0 km² |
| Kherson | 2175078 | City proper. Occupied March–November 2022; the left bank opposite remains occupied and has no road link to the city in OSM. OSM boundary ≈ 77.9 km² |
| Kovel | 2814537 | City proper, Volyn Oblast. OSM boundary ≈ 46.9 km² |
| Mukachevo | 2283083 | City proper, Zakarpattia Oblast. OSM boundary ≈ 28.2 km² |
| Kamianske | 2238215 | City proper, Dnipropetrovsk Oblast. OSM boundary ≈ 130.2 km² |
| Mariupol | 1748539 | Temporarily occupied since 2022; heavily destroyed, OSM may not reflect the current state. Relation tagged admin_level 10. OSM boundary ≈ 192.5 km², land ≈ 184.3 km² (sea removed) |
| Luhansk | 2171253 | Temporarily occupied since 2014. Relation tagged admin_level 10. OSM boundary ≈ 256.2 km² |
| Kramatorsk | 3936275 | City proper, Donetsk Oblast. OSM boundary ≈ 59.7 km² |
| Lysychansk | 3678529 | Temporarily occupied since 2022. OSM boundary ≈ 52.5 km² |
| Sevastopol | 3030976 | Temporarily occupied since 2014. City with special status: the city proper is used, not the whole special-status territory (≈ 3,100 km² incl. sea); no hromada level. Includes Sevastopol Bay (counted as water, bays & sea). OSM boundary ≈ 154.7 km², land and bays ≈ 152.8 km² (open sea removed) |
| Simferopol | 3030295 | Temporarily occupied since 2014; capital of the Autonomous Republic of Crimea; no hromada level. City outline is the OSM `place=city` relation. No population data from Derzhstat; 2001 census only. |
| Horlivka | 3862199 | Temporarily occupied since 2014. Relation tagged admin_level 10. OSM boundary ≈ 183.4 km² |
| Khmelnytskyi | 1792913 | City proper. OSM boundary ≈ 98.3 km² |
| Kropyvnytskyi | 2825228 | City proper. OSM boundary ≈ 96.4 km² |
| Melitopol | 2318317 | Temporarily occupied since 2022. City boundary extends ~3.5 km² beyond the hromada in OSM. OSM boundary ≈ 53.4 km² |
| Berdiansk | 1280581 | Temporarily occupied since 2022. On the Sea of Azov. OSM boundary ≈ 109.6 km², of which ≈ 24.3 km² is sea (removed): land ≈ 85.4 km² |
| Kremenchuk | 2320579 | City proper, Poltava Oblast, on both banks of the Dnipro. OSM boundary ≈ 95.4 km² |
| Uman | 3058556 | City proper, Cherkasy Oblast. OSM boundary ≈ 44.6 km² |
| Chernihiv | 1952636 | City proper. City boundary extends ~2 km² beyond the hromada in OSM. OSM boundary ≈ 83.9 km² |
| Makiivka | 2224013 | Temporarily occupied since 2014; OSM mapping may be less complete or current. Donetsk Oblast. OSM boundary ≈ 180.9 km² |
| Kerch | 2865045 | Temporarily occupied since 2014; OSM mapping may be less complete or current. Autonomous Republic of Crimea. No hromada level; OSM has no KATOTTG codes here. OSM boundary ≈ 108.4 km² |
| Nikopol | 2318332 | City proper. Dnipropetrovsk Oblast. OSM boundary ≈ 44.3 km² |
| Sloviansk | 3940400 | City proper. Donetsk Oblast. OSM boundary ≈ 71.0 km² |
| Siverskodonetsk | 4089182 | Temporarily occupied since 2022; OSM mapping may be less complete or current. Luhansk Oblast. OSM boundary ≈ 93.8 km² |
| Alchevsk | 2383892 | Temporarily occupied since 2014; OSM mapping may be less complete or current. Luhansk Oblast. OSM boundary ≈ 40.0 km² |
| Pavlohrad | 1746879 | City proper. Dnipropetrovsk Oblast. OSM boundary ≈ 60.0 km² |
| Yevpatoria | 2175059 | Temporarily occupied since 2014; OSM mapping may be less complete or current. Autonomous Republic of Crimea. No hromada level; OSM has no KATOTTG codes here. OSM boundary ≈ 43.0 km² |
| Yenakiieve | 3936114 | Temporarily occupied since 2014; OSM mapping may be less complete or current. Donetsk Oblast. OSM boundary ≈ 49.3 km² |
| Kamianets-Podilskyi | 2202756 | City proper. Khmelnytskyi Oblast. OSM boundary ≈ 28.0 km² |
| Kostiantynivka | 3940146 | City proper. Donetsk Oblast. OSM boundary ≈ 61.0 km² |
| Khrustalnyi | 2910703 | Temporarily occupied since 2014; OSM mapping may be less complete or current. Luhansk Oblast. OSM boundary ≈ 46.0 km² |
| Oleksandriia | 2320570 | City proper. Kirovohrad Oblast. OSM boundary ≈ 55.5 km² |
| Konotop | 2822403 | City proper. Sumy Oblast. OSM boundary ≈ 43.9 km² |
| Kadiivka | 2521434 | Temporarily occupied since 2014; OSM mapping may be less complete or current. Luhansk Oblast. OSM boundary ≈ 57.5 km² |
| Berdychiv | 2069556 | City proper. Zhytomyr Oblast. OSM boundary ≈ 35.5 km² |
| Shostka | 2776723 | City proper. Sumy Oblast. OSM boundary ≈ 43.7 km² |
| Brovary | 1929810 | City proper. Kyiv Oblast. OSM boundary ≈ 33.8 km² |
| Izmail | 17731704 | City proper. Odesa Oblast. OSM boundary ≈ 38.0 km² |
| Bakhmut | 3313703 | Temporarily occupied since 2022; OSM mapping may be less complete or current. Donetsk Oblast. OSM boundary ≈ 37.2 km² |
| Yalta | 3005461 | Temporarily occupied since 2014; OSM mapping may be less complete or current. Autonomous Republic of Crimea. No hromada level; OSM has no KATOTTG codes here. OSM boundary ≈ 14.9 km² |
| Drohobych | 2122758 | City proper. Lviv Oblast. OSM boundary ≈ 32.3 km² |
| Nizhyn | 5162189 | City proper. Chernihiv Oblast. OSM boundary ≈ 43.5 km² |
| Feodosia | 2997809 | Temporarily occupied since 2014; OSM mapping may be less complete or current. Autonomous Republic of Crimea. No hromada level; OSM has no KATOTTG codes here. OSM boundary ≈ 37.8 km² |
| Dovzhansk | 2878463 | Temporarily occupied since 2014; OSM mapping may be less complete or current. Luhansk Oblast. OSM boundary ≈ 42.2 km² |
| Samar | 3505757 | City proper. Dnipropetrovsk Oblast. OSM boundary ≈ 36.1 km² |
| Chystiakove | 2263374 | Temporarily occupied since 2014; OSM mapping may be less complete or current. Donetsk Oblast. OSM boundary ≈ 63.6 km² |
| Sheptytskyi | 2819236 | City proper. Lviv Oblast. OSM boundary ≈ 15.4 km² |
| Pervomaisk | 2318311 | City proper. Mykolaiv Oblast. OSM boundary ≈ 42.6 km² |
| Smila | 9245942 | City proper. Cherkasy Oblast. OSM boundary ≈ 35.7 km² |
| Pokrovsk | 2506125 | City proper. Donetsk Oblast. OSM boundary ≈ 31.2 km² |
| Kalush | 2190422 | City proper. Ivano-Frankivsk Oblast. OSM boundary ≈ 67.1 km² |
| Korosten | 2296668 | City proper. Zhytomyr Oblast. OSM boundary ≈ 43.2 km² |
| Rubizhne | 2521435 | Temporarily occupied since 2022; OSM mapping may be less complete or current. Luhansk Oblast. OSM boundary ≈ 34.7 km² |
| Pryluky | 18281344 | City proper. Chernihiv Oblast. OSM boundary ≈ 42.1 km² |
| Druzhkivka | 2900261 | City proper. Donetsk Oblast. OSM boundary ≈ 33.5 km² |
| Khartsyzk | 2228485 | Temporarily occupied since 2014; OSM mapping may be less complete or current. Donetsk Oblast. OSM boundary ≈ 20.5 km² |
| Lozova | 2580458 | City proper. Kharkiv Oblast. OSM boundary ≈ 22.8 km² |
| Antratsyt | 2383939 | Temporarily occupied since 2014; OSM mapping may be less complete or current. Luhansk Oblast. OSM boundary ≈ 34.3 km² |
| Stryi | 7986302 | City proper. Lviv Oblast. OSM boundary ≈ 17.1 km² |
| Kolomyia | 2697552 | City proper. Ivano-Frankivsk Oblast. OSM boundary ≈ 40.6 km² |
| Shakhtarsk | 2381773 | Temporarily occupied since 2014; OSM mapping may be less complete or current. Donetsk Oblast. OSM boundary ≈ 40.6 km² |
| Snizhne | 2068203 | Temporarily occupied since 2014; OSM mapping may be less complete or current. Donetsk Oblast. OSM boundary ≈ 46.3 km² |
| Sorokyne | 4116163 | Temporarily occupied since 2014; OSM mapping may be less complete or current. Luhansk Oblast. OSM boundary ≈ 38.7 km² |
| Zviahel | 2313420 | City proper. Zhytomyr Oblast. OSM boundary ≈ 28.0 km² |
| Enerhodar | 1989799 | Temporarily occupied since 2022; OSM mapping may be less complete or current. Zaporizhzhia Oblast. OSM boundary ≈ 18.0 km² |
| Izium | 11179426 | City proper. Kharkiv Oblast. OSM boundary ≈ 47.6 km² |
| Myrnohrad | 2910227 | City proper. Donetsk Oblast. OSM boundary ≈ 22.9 km² |
| Brianka | 4716256 | Temporarily occupied since 2014; OSM mapping may be less complete or current. Luhansk Oblast. OSM boundary ≈ 42.1 km² |
| Chornomorsk | 3606771 | City proper. Odesa Oblast. OSM boundary ≈ 7.0 km² |
| Boryspil | 1934961 | City proper. Kyiv Oblast. OSM boundary ≈ 38.1 km² |
| Novovolynsk | 1951965 | City proper. Volyn Oblast. OSM boundary ≈ 13.4 km² |
| Rovenky | 2878522 | Temporarily occupied since 2014; OSM mapping may be less complete or current. Luhansk Oblast. OSM boundary ≈ 42.1 km² |
| Zhovti Vody | 2693667 | City proper. Dnipropetrovsk Oblast. OSM boundary ≈ 35.4 km² |
| Lubny | 18281343 | City proper. Poltava Oblast. OSM boundary ≈ 31.5 km² |
| Nova Kakhovka | 2516278 | Temporarily occupied since 2022; OSM mapping may be less complete or current. Kherson Oblast. OSM boundary ≈ 22.4 km² |
| Fastiv | 2533207 | City proper. Kyiv Oblast. OSM boundary ≈ 50.9 km² |
| Bilhorod-Dnistrovskyi | 3200259 | City proper. Odesa Oblast. OSM boundary ≈ 19.6 km² |
| Horishni Plavni | 4097647 | City proper. Poltava Oblast. OSM boundary ≈ 114.1 km² |
| Romny | 2696092 | City proper. Sumy Oblast. OSM boundary ≈ 32.2 km² |
| Okhtyrka | 3589658 | City proper. Sumy Oblast. OSM boundary ≈ 33.5 km² |
| Svitlovodsk | 18298365 | City proper. Kirovohrad Oblast. OSM boundary ≈ 23.2 km² |

Areas are computed in a city-centred **Lambert Azimuthal Equal-Area** projection, so area error from the
projection is zero.

## 3. Classification

Each OSM way or multipolygon is matched against an ordered list of tag rules
(`config/classification.yaml`, first match wins) and gets **one** class `category/subcategory`. Rules
specify whether they apply to areas or lines. Closed ways are interpreted as areas or lines following
standard OSM semantics (`urbanfp/osm_tags.py`). For example, a closed `highway=footway` is a line unless
`area=yes`.

Categories (display order): buildings, roads, railways, parking, green space, water, industrial land,
residential land, industrial land, sports & recreation, cemeteries, agriculture, other mapped land, unclassified.

### Buildings

* **Actual footprints**: only `building=*` outlines. Residential or commercial *districts* (`landuse=*`) are
  never counted as buildings.
* Typed by the `building=*` value: detached houses, apartments, commercial/office/retail, industrial/warehouse,
  public/institutional, other (garages, sheds, roofs…).
* `building=residential` means residential, type unspecified. It is kept as its own sub-category rather than
  guessed to be apartments or houses.
* `building=yes` is **unknown**. It is typed only from explicit tags on the *same* OSM object
  (`building:use`, `amenity=school`, `shop=*`, `office=*`…). Those buildings are counted separately as
  `basis = secondary_tag`, and this step can be switched off (`building_secondary_tags.enabled`). Buildings
  are never typed from their surroundings (e.g. "inside landuse=industrial → industrial").
* Excluded: `building:part` (parts of buildings already outlined), underground buildings
  (`location=underground`, negative `layer`).

### Physical footprint of line features

Roads, railways, waterways and runways are usually mapped as centre-lines. Their surface is estimated by
buffering the line by half its width (round joins):

1. `width` tag (or `width:carriageway`) → `width_source = tag`
2. `est_width` → `est_width`
3. highways: `lanes × lane width` (3.75 m motorway/trunk, 3.5 m primary, 3.25 m secondary/tertiary, 3.0 m other) → `lanes`
4. default per type, with separate values for one-way carriageways (dual carriageways are mapped as two
   one-way ways) → `default`

Plus:
* `sidewalk=both|left|right` on the road adds 2 m per side (`+sidewalk`). Separately mapped sidewalks
  (`footway=sidewalk`) are buffered footways. Untagged sidewalks are **not** guessed.
* Railways: 4.5 m per track (sleepers + ballast shoulder), `tracks=n` adds 4.1 m (track spacing) per extra
  track. Tram 3 m. Surface sections of the metro are included; tunnels are excluded.
* Mapped surface polygons (`area:highway=*`, pedestrian areas, other `highway=*` polygons only with
  `area=yes`, platforms, runway/apron polygons) are used as is. Requiring `area=yes` guards against
  mis-tagged polygons. Example: the Dnipro Reservoir relation (r21217234) is tagged `highway=residential`,
  and without this guard it counted as 48 km² of streets in Dnipro. The validation check
  `suspicious_features` flags any road polygon over 50 ha, rail polygon over 200 ha or parking polygon over
  30 ha for review. Where they coincide with a buffered line, the union is counted once.

Main defaults (m, two-way / one-way): motorway 15/8 · trunk 14/7.5 · primary 12/7 · secondary 10/6.5 ·
tertiary 8/5.5 · residential & unclassified 6/4 · service 4.5/3.5 (parking aisle 5.5, driveway 3) ·
track 3 · footway 2 · path 1.5 · cycleway 2 · pedestrian street 5.

The share of road and rail area that depends on default widths is reported per city as a data-quality metric.

**Bridges** count as transport surface (top-down view). **Tunnels**, culverts and underground objects are
excluded (`exclude` in the config).

## 4. Overlap resolution: physical cover before functional land use

OSM polygons overlap constantly: buildings sit inside `landuse=residential`, a pond inside a park, a park
inside `landuse=residential`, a forest overlapping a nature reserve, and so on. To get mutually exclusive
categories, classes are **painted in priority order**. Each class receives only the area that no
higher-priority class has taken:

```
buildings → railway tracks → metro (surface) → tram → platforms → roads → parking
→ piers/structures → water → sports → green space → cemeteries → agriculture
→ industrial land → railway land → residential land → other land use → (remainder) unclassified
```

The principle is precise physical objects first, then land **cover** (water, sports surfaces, vegetation),
then land **use** polygons last. Consequences:

* A building in an industrial zone counts only as a building. "Industrial land" is the part of industrial
  areas not covered by buildings, roads, rails, parking, water or mapped vegetation.
* A forest inside a residential area counts as green. A playground inside a park counts as sports & recreation.
* "Residential land" is the remainder of `landuse=residential` polygons: courtyards, unmapped lawns and
  garden plots. It is its own top-level category because it is one of the largest shares in every city.
* "Other mapped land" is the remainder of commercial, institutional, military, construction, airport and
  garage-cooperative land.

Within one class, features are processed smallest-first, and each one only keeps area not already taken by
an earlier feature of the same class. Per-feature pieces therefore never overlap either, which the
animation dataset needs.

For each class, the validation report lists its full footprint, its exclusive area, and how much was
hidden by higher-priority classes.

### Implementation

`urbanfp/overlay.py`:

* The boundary is cut into 2 km × 2 km tiles, processed in parallel.
* GEOS overlay runs on a 1 cm precision grid (`grid_size=0.01`), which avoids topology errors and
  slivers.
* Pieces of features cut by tile edges are dissolved back together.
* Area accounting is per tile, so the closure `Σ classes + unclassified = tile area` holds by construction.

## 5. Validation

`data/processed/<city>/validation.md` reports on every run:

| Check | Meaning |
|---|---|
| `area_closure` | Σ categories vs boundary area (should be < 0.01 %) |
| `pieces_closure` | Σ exported per-feature pieces vs tiled area |
| `no_overlap_sample` | pairwise intersection area of a 20k-piece sample (should be ~0) |
| `unclassified_share` | share of the area with no classified OSM feature |
| `unknown_buildings` | share of building footprint with unknown type |
| `roads_default_widths`, `railways_default_widths` | share of line-derived area that used default widths |
| `unmatched_features` | OSM objects with relevant keys that no rule matched (listed by tag, with example ids) |
| `invalid_geometries`, `geometry_failures` | repaired or failed geometries |

## 6. Population

Sources (the year switch in the app chooses one):
* **2022**: State Statistics Service of Ukraine (Держстат), *de facto population (estimate)* by region,
  district, hromada and settlement on 1 January, with KATOTTG codes (`population_2022-01-01_derzhstat.xlsx`).
* **2001**: the All-Ukrainian census of 5 December 2001, de facto population of every settlement, with its 2001
  name, type, oblast, raion and council but no codes (`population_2001-12-05_census.xlsx`, compiled from the
  Pop-stat regional tables of the census results).

The original workbooks are kept in `data/demography/raw/`; `python -m urbanfp demography` parses them
(`data/demography/population_<year>.csv`) and writes `web/data/demography.json`.

**Census matching.** Census rows are given today's KATOTTG code by name within the oblast, with candidates from
the Derzhstat table and from OSM `place` points (`katotth`, current name and `old_name`, which covers most
decommunisation renamings). Namesakes are told apart by the 2001 council (the hromada most other settlements of
the same council went to), then by the 2001 raion, then by the type closest to the 2001 one; a second pass
ignores spelling variants (є/е, і/и). A few renamings OSM does not record and settlements merged into a city
since 2001 (Inhulets → Kryvyi Rih, Taromske → Dnipro) are listed in `CENSUS_RENAMES`. Settlements merged into
one are summed. Where OSM has no code (most of Crimea), a place point is used instead, identified by its OSM id
(`OSM<id>`) and assigned to the oblast of the nearest coded point; it is only used when no coded settlement has
that name. Points inside Sevastopol's OSM boundary (relation 1574364, `PREFIX_AREAS` in `agglomeration.py`)
belong to Sevastopol whatever their code: the villages of its 2001 city council are coded in Bakhchysarai raion
since the 2020 reform or have no code, and the nearest coded points are Crimean. 29,030 of 29,962 settlements
(98.9% of the population) are matched; the unmatched are mostly small
villages that no longer exist or have been renamed. Hromada totals for 2001
are sums of their matched settlements; the city row is the city's own census figure (the 2001 city, which
may differ from today's boundary).

* **City**: the settlement row of the city ("місто Х"), found by name or by `katottg` in the config (Kyiv,
  Mykolaiv).
* **Hromada**: the hromada total. Where Hromada mode shows the city (no hromada level; occupied before 2020),
  the city's population is used.
* **Greater City**: the city plus every included settlement. Settlements are matched by KATOTTG code from the
  OSM `katotth` tag (on the settlement outline, or on its `place` point inside it), otherwise by name (same
  type, unique in the surrounding hromadas, raions or oblasts). On 2022 data 837 of 856 included settlements
  are matched (833 by code); the rest are mostly unnamed places or not registered as settlements, so the
  total is a slight lower bound.
* **Density** = population / territory area (land, incl. forests, water and fields), so it depends on where
  boundaries are drawn.
* Known gaps: Crimea and Sevastopol are not covered by Derzhstat (Simferopol and Sevastopol have 2001 census data only). For territories occupied in 2014 figures
  are estimates. Where the OSM city boundary differs from the official one (Ivano-Frankivsk excludes
  Mykytyntsi, Khryplyn and Uhornyky, which are officially part of the city), the population refers to the
  official city and the density is over-estimated.

### Hromada budgets (OpenBudget)

`python -m urbanfp budgets` reads the annual budget of every hromada (and Kyiv) for 2021 and 2025 from the public
OpenBudget API of the Ministry of Finance (`api.openbudget.gov.ua`). The budget code of a hromada comes from the
budget dictionary `BUDG`, which carries the KATOTTG code; codes change over the years (Kryvyi Rih, Poltava,
Kropyvnytskyi in 2023, Kherson in 2026), so every code a unit has had is tried. Only the last quarter of the
cumulative reports is used (= the year), all funds together:

* revenue (`INCOMES`): actual amounts by revenue code, grouped by code prefix — PIT `1101`, single tax `180504`,
  property and land `1801`, own revenue `1`–`3`, transfers `4`, earmarked funds `5`;
* spending (`EXPENSES`, programme classification): actual amounts of the programme totals (economic code `0000`)
  grouped by functional code — `09` education, `07` health, `10` social, `06` housing, `04` economy, `08` culture,
  `011`–`016` administration, `018` transfers to other budgets, the rest as other. The functional report itself is
  not used: its "actual" column repeats the plan.

Output: `data/budgets/budgets_2021_2025.csv` (one row per hromada and year) and `web/data/budgets.json` (the
hromada of each configured city). Per-resident figures use the population on 1 January 2022. Years of hromadas
occupied before them are flagged (budgets of administrations in exile, or nothing); OpenBudget has no data for
Crimea and Sevastopol. API responses are cached in `data/budgets/cache/` (a full run takes ~3.5 h, 4 parallel
requests; more make the API answer 503).

On the Агломерації and region modelling pages the revenue of every hromada (`web/data/budgets_hromadas.json`,
keyed by the 9-character KATOTTG hromada prefix) is shared out among its settlements by population on 1 January
2022 and summed per agglomeration or region: the centre (the city's own share), the joined settlements, and the
total; settlements outside any hromada with data (Crimea, Sevastopol) or without population are listed as unknown.
Occupied hromadas are counted as reported (administrations in exile since 2022, negligible since 2014).

### Agglomerations: city population with surroundings by drive time ("Агломерації" page)

`python -m urbanfp agglomeration` builds a national car road graph from the OSM extract (all `highway` classes
except service roads, tracks and paths; same free-flow speeds as Greater City mode) and locates every
settlement of every year (Derzhstat, and census rows matched as above) at the OSM `place` point with the
same KATOTTG code (28,580 of 28,657 Derzhstat settlements; 100% of the population). For every configured city it computes the drive time from any road inside the city boundary to
every settlement within 6 h; the Агломерації page uses up to 3 h, the region modelling page the whole range.
A settlement point is joined to the nearest vertex of the main (largest connected) road network, at most 3 km
away, and that straight distance is driven at 20 km/h (`LAST_MILE_KMH`) and added to the drive time. Snapping to
any vertex used to catch stray pieces (yards, car parks, footway-bounded service roads: Kotsiubynske, 17.6k, sat
on one 80 m from the network) and villages linked only by field tracks, which the graph leaves out; 116
settlements were reached from no city, now 11 (all over 3 km from a road, and Snake Island).

Centres are all 101 cities of over 50,000 people in the 2001 census. A city needs only its extracted OSM boundary
to be a centre: a city configured but not yet processed further (`map_only` in `agglomeration.json`) appears on the
Агломерації page only, named from `config/cities.yaml` and linked to its census / Derzhstat row by `katottg` or by name.

The graph is the **intact road network**, as before the full-scale war, consistent with the free-flow
assumption:
* roads mapped as destroyed or out of use are kept: lifecycle tags (`ruined:`, `destroyed:`, `damaged:`,
  `disused:highway=*`, also with `highway=destroyed`) and destroyed bridges retagged `highway=proposed` +
  `destroyed`/`ruins` (the M-18 bridge south of Zaporizhzhia, the Antonivskyi bridge in Kherson);
* `access=no` is ignored on every road of the graph except service roads (front-line closures and checkpoints,
  also on village roads around Horlivka and Stanytsia Luhanska); `access=private` still excludes minor roads;
* along the front line OSM roads are cut by moving way ends a few metres apart without a shared node. Every
  dead end is joined to the nearest road vertex ahead of it within 30 m, or within 150 m of another main road
  for main-road dead ends (cut dual carriageways), driven at 20 km/h. This also closes ordinary mapping gaps.
Without this, Zaporizhzhia → Melitopol took 307 min (detour via Huliaipole) instead of 96 min, and Kherson →
Melitopol 401 min instead of 167 min. The Kakhovka dam road (mapped as proposed, without destroyed tags) is
not restored.

The page joins settlements for any threshold from 0 (city only) to 180 min in 5 min steps (slider; buttons for 0, 10, 30, 60, 90, 120, 150 and 180 min):
* centres are taken from the largest city (own population) down; a centre within the threshold of an active
  centre more than twice its size becomes inactive and joins it (the fastest reached one; e.g. Kamianske →
  Dnipro at 20 min). Centres of comparable size (ratio ≤ 2, e.g. Dnipro with Zaporizhzhia and Kryvyi Rih) both stay
  active and compete for the settlements between them;
* every other settlement joins the active centre it is reached from fastest, if within the threshold.
* the map outlines each agglomeration with a concave hull: its settlements and the (simplified) boundaries of
  its cities, the centre and the absorbed ones, are triangulated (Delaunay) and triangles with a side over 15 km
  are dropped; only the contiguous part around the centre is drawn, so detached members (an absorbed city far away)
  do not stretch the outline, and holes are not shown;
* the two rules are independent: an absorbed centre's surroundings do not follow it but join the nearest
  active centre, so an agglomeration can have outlying islands. At 3 h Mukachevo joins Lviv (176 min; Lviv is
  more than twice its size, Uzhhorod at 27 min is not), while Svaliava, Berehove, Irshava and Khust, closest to
  Mukachevo, join Uzhhorod, which they reach faster than Lviv.

At 10 min the result is close to, but not the same as, Greater City mode: there the drive time is the median
over a settlement's built-up road points and the whole settlement territory is added; here it is the time to
the settlement point. No traffic, ferries or border crossings are modelled; destroyed bridges may still be in
OSM.

## 7. Assumptions, data gaps and known inaccuracies

* **Mapping completeness differs between cities.** A lower "green" share can mean less detailed mapping.
  Check the `unclassified` share and the "residential land" and "other mapped land" remainders before comparing. Kyiv, Lviv and Odesa
  all have near-complete building coverage in OSM, but land-cover detail inside blocks (lawns, trees, yards)
  varies.
* **Road widths are estimates** wherever `width`/`lanes` are missing, which is most residential streets.
  An error of ±1 m on residential streets changes road area by roughly ±15 %. Sidewalks are counted only
  where tagged or separately mapped, so road area is probably **underestimated** in dense centres with
  wide untagged sidewalks.
* **Trams** embedded in streets are counted as railway (3 m strip), taking that area from the road.
* **Building footprints** are roof outlines as mapped. Canopies (`building=roof`) are included under
  "other".
* **Water**: rivers mapped only as centre-lines get a default width (river 10 m, stream 2 m). Large rivers
  (Dnipro) are mapped as polygons.
* **Sea**: some administrative boundaries extend into the sea (Berdiansk, Mariupol, Sevastopol, slightly
  Odesa). OSM maps the sea only as the shore line (`natural=coastline`, land on the left), so for cities
  with `clip_to_land: true` the territory is cut along the coastline and **open sea is removed**: city area
  and shares refer to land. The coastline is cut into land and sea faces within a rectangle around the
  territory; a face is sea when nearly all probe points next to the shore inside it lie on the sea side.
  **Bays mapped as `natural=bay` areas stay** in the territory and count as water, sub-category
  *bays & sea* (e.g. Sevastopol Bay). Piers and breakwaters out at sea are removed with it (< 0.1 km²).
  The administrative area including the sea is not reported.
* **Agricultural land, wetlands, allotments**: assigned by tags; allotments ("dacha" cooperatives) are
  counted as green space, sub-category *allotments*. Change this in the config if needed.
* **Temporal consistency**: all cities in one run use the same OSM snapshot.
* The `other/other_landuse` catch-all collects any `landuse=*` value not otherwise covered. See the
  validation report for which values occur.
