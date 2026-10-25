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
| Horlivka | 3862199 | Temporarily occupied since 2014. Relation tagged admin_level 10. OSM boundary ≈ 183.4 km² |
| Khmelnytskyi | 1792913 | City proper. OSM boundary ≈ 98.3 km² |
| Kropyvnytskyi | 2825228 | City proper. OSM boundary ≈ 96.4 km² |
| Melitopol | 2318317 | Temporarily occupied since 2022. City boundary extends ~3.5 km² beyond the hromada in OSM. OSM boundary ≈ 53.4 km² |
| Berdiansk | 1280581 | Temporarily occupied since 2022. On the Sea of Azov. OSM boundary ≈ 109.6 km², of which ≈ 24.3 km² is sea (removed): land ≈ 85.4 km² |

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

## 6. Assumptions, data gaps and known inaccuracies

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
