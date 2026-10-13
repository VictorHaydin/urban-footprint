# Data model

All geometry in `data/interim/<city>/classified.parquet` and `data/processed/` is stored in a **city-local
Lambert Azimuthal Equal-Area projection** (`+proj=laea` centred on the boundary centroid, metres). This projection
keeps areas exact and distortion negligible at city scale. Web outputs use WGS84 (EPSG:4326) or Web Mercator.
The CRS is stored in every GeoParquet file. Use `geopandas.read_parquet` and `.to_crs(...)` as needed.

Identifiers: `fid` = OSM type letter + OSM id (`w123456` = way, `r98765` = relation). Unclassified
remainder pieces have `fid = u<tile>`.

## Pipeline stages and files

```
data/sources/geofabrik/<region>-latest.osm.pbf   + .meta.json (URL, size, OSM timestamp)
data/interim/<city>/
    boundary.geojson          administrative boundary, WGS84
    osm_features.parquet      raw features: osm_type, osm_id, kind (area|line), tags (JSON, ALL original tags),
                              geometry (original OSM geometry, WGS84; lines stay lines)
    extract.json              provenance: source, OSM timestamp, keys extracted, counts
    classified.parquet        one row per classified OSM feature (see below)
    classify_report.json      unmatched / excluded tag statistics
data/processed/<city>/
    exclusive.parquet         mutually exclusive partition of the city (see below)
    tile_stats.parquet        per-tile area accounting (footprint and exclusive area per class)
    overlay.json              overlay parameters
    stats.json                statistics (also published to the web app)
    validation.json / .md     validation checks
    city_features.parquet     exclusive pieces joined with all OSM attributes (animation / analysis dataset)
    state.json                incremental-processing state (config hash per stage)
web/data/
    index.json                cities + category definitions (label / label_uk, colours, order)
    <city>/stats.json         statistics + validation summary + map georeferencing
    <city>/boundary.geojson   simplified boundary
    <city>/overview/<cat>.png coverage rasters (Web Mercator grid, alpha = share of pixel covered)
    <city>/features.pmtiles   vector tiles z14, layer "features"
```

### `classified.parquet`

One row per OSM feature that matched a rule. The feature's full **physical footprint** is kept, before
overlap resolution and before clipping to the boundary.

| column | meaning |
|---|---|
| `fid`, `osm_type`, `osm_id` | OSM identity |
| `kind` | `area` (polygon / multipolygon) or `line` |
| `cls`, `category`, `subcategory` | assigned class, e.g. `buildings/apartments` |
| `basis` | `primary_tag`, `secondary_tag` (building typed via amenity/shop/building:use… on the same object), or `building_unknown` |
| `rule_tag` | the tag that triggered the rule, e.g. `highway=residential` |
| `width_m`, `width_source` | for buffered lines: width used and where it came from (`tag`, `est_width`, `lanes`, `default`, `default+tracks`, each optionally `+sidewalk`); `mapped_polygon` for areas |
| `name`, `levels` | convenience copies of `name` and `building:levels` |
| `tags` | JSON of all original OSM tags |
| `source_wkb` | original OSM geometry (WGS84 WKB). Lines are still lines here |
| `footprint_area_m2` | area of the full footprint |
| `geometry` | physical footprint polygon (local CRS) |

### `exclusive.parquet` / `city_features.parquet`

The mutually exclusive partition. Every square metre of the administrative area belongs to exactly one row.

| column | meaning |
|---|---|
| `fid` | feature id (`u…` for unclassified remainder) |
| `cls`, `category`, `subcategory` | class |
| `area_m2` | exclusive area of this piece |
| `geometry` | exclusive geometry: the footprint clipped to the boundary, minus higher-priority classes, minus earlier (smaller) features of the same class |

`city_features.parquet` adds every column of `classified.parquet` except the footprint geometry, plus
`lon`/`lat` of a representative point.

Invariants (checked in `validation.md`):
* `Σ area_m2 = boundary area` (up to floating-point error, ≪ 0.01 %)
* pieces don't overlap (sampled test)
* `Σ area_m2 by category = stats.json` category areas

### `stats.json`

```jsonc
{
  "city": {"id", "name", "name_local", "country", "osm_relation", "notes"},
  "osm_timestamp": "...", "generated_at": "...", "config_hash": "...",
  "area": {"boundary_m2", "boundary_km2"},
  "categories": [{"id", "label", "color", "area_m2", "pct", "feature_count",
                  "subcategories": [{"id", "label", "color", "area_m2", "pct", "feature_count"}]}],
  "buildings": {"count", "footprint_m2", "mean_footprint_m2", "by_subcategory", "classification_basis",
                "levels_tagged_pct", "levels_tagged_footprint_pct", "floor_area_tagged_m2", "top_building_values"},
  "estimated_line_footprints": {"roads": {"area_from_lines_m2", "area_by_width_source_m2", "pct_area_default_width"}, ...},
  "network_length_km": {"roads": {"residential": 1234.5, ...}, "railways": {...}, ...},
  "overlap": [{"cls", "footprint_m2", "exclusive_m2", "hidden_by_higher_priority_m2", "hidden_pct"}],
  // web copy only:
  "validation": {"status", "checks"},
  "map": {"bounds", "center", "overview": {...}, "vector_tiles": {...}}
}
```

`pct` is always relative to the administrative area.

### Vector tiles (`features.pmtiles`)

Layer `features`, zoom 14 only (overzoomed beyond; 4096 extent ≈ 0.4 m precision). Each feature is one exclusive piece with properties:
`c` category, `s` subcategory, `id` fid, `a` exclusive area (m², whole feature), `n` name, `t` rule tag, `w` buffer width.

## Using the data for animation

The planned animation, where a city decomposes into its elements that then regroup into stacked bars, needs
individual shapes. Use `data/processed/<city>/city_features.parquet`:

* every piece is non-overlapping and carries its category, so pieces can be animated independently and
  regrouped by `category` / `subcategory`
* geometry is in metres (equal-area), so areas are visually comparable when cities are laid out side by side
  at the same scale
* `area_m2` sums exactly to the bar lengths in `stats.json`
* `source_wkb` / `tags` in `classified.parquet` give the original OSM object if an animation should show,
  e.g., a road's centre-line before it is widened

Example: export one category to GeoJSON for a JS animation tool:

```python
import geopandas as gpd
g = gpd.read_parquet("data/processed/lviv/city_features.parquet")
g[g.category == "buildings"][["fid", "subcategory", "area_m2", "geometry"]] \
    .to_crs(4326).to_file("lviv_buildings.geojson", driver="GeoJSON")
```
