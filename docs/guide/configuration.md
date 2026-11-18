# Choose the area, date and satellite

A JSON configuration makes the inference choices explicit and keeps credentials
separate from the request. Begin with the example for your satellite. Paths are
resolved relative to that JSON file.

Use relative paths or explicit absolute paths; `~` is not expanded. In Windows
JSON paths, use forward slashes or escape each backslash as `\\`.

## Shared settings

| Setting | Meaning |
| --- | --- |
| `aoi_path` | Polygon shapefile, or WGS84 GeoJSON; the primary file-based AOI option |
| `aoi_circle` | Alternative point/radius object with `latitude`, `longitude` and `radius_km` |
| `aoi_square` | Alternative point/square object with `latitude`, `longitude` and full `side_km` |
| `bounds` | Alternative `[west, south, east, north]` in WGS84 longitude/latitude degrees |
| `day` | `YYYY-MM-DD`; single/start date, beginning at 00:00 UTC |
| `end_day` | Optional inclusive end date; omit or leave empty for a single day |
| `max_cloud_cover` | Optional inclusive maximum catalogue cloud percentage, 0–100; `null` (default) means no limit |
| `include_unknown_cloud` | With a maximum set, retain scenes whose percentage is unavailable; defaults to `false` |
| `product` | Optical product identifier; omitted only for the default `s2_l2a` |
| `sensor` | Explicit spacecraft/domain where required; see below |
| `landcover_year` | Defaults to 2021; 2020 is also available, independently of acquisition year |
| `landcover_policy` | Defaults to `fixed-year`; older `same-year-annual` requests are migrated to this policy |
| `models_dir` | Directory holding the three trusted published bundles |
| `cache_dir` | Directory for reusable source inputs and prepared files |
| `output` | Directory, default filename label, and extension (`.tif` for affine grids or `.nc` for Sentinel-3) |
| `output_label` | Optional editable filename prefix; defaults to the polygon file's stem, otherwise the output's existing stem |
| `statistics` | Generate AOI statistics and figures after mapping; defaults to `false` |
| `statistics_by_vegetation` | Also summarise Grass, Shrub and Tree; defaults to `true` |
| `statistics_centre` | Figure line: `median` (default) or `mean`; the CSV contains both |
| `statistics_support` | `available` (default) or `common` valid native pixels |
| `statistics_comparisons` | Optional exploratory paired-date intervals; defaults to `false`; requires common support and vegetation groups |
| `statistics_block_length` | Temporal block length in dates; default 3 |
| `statistics_source` | Saved series manifest or LFMC output to analyse with the `statistics` command |
| `downscale` | Optional weather adjustment; defaults to `false` |
| `product_ids` | One selected scene for a single day; optional subset of scenes in range mode, otherwise all matches |

The initial adapters accept non-crossing areas no more than one degree across,
within 60 degrees south to 60 degrees north. Each map handles one scene or granule;
a time series processes the matching scenes sequentially as separate maps.
For affine products, an AOI crossing the selected source grid is rejected.
Sentinel-3 records the native centres inside the requested area; the selected
scene may cover only part of it. There is no automatic multi-scene mosaic.

Set **one of `aoi_path`, `aoi_circle`, `aoi_square` or `bounds`**. Shapefile components must share
a base name and include `.shp`, `.shx`, `.dbf` and `.prj`. All polygon features
are unioned, reprojected from the declared CRS and applied as a pixel-centre
mask. Holes remain excluded. Download bounds are derived automatically; they
do not replace the polygon mask. File and geometry hashes distinguish AOIs
with different boundaries even when they share the same bounding rectangle.

```json
"aoi_path": "aoi/sardinia.shp"
```

See `examples/sentinel2_shapefile_2021.json` and the desktop GUI guide.

For a circular AOI without an input polygon file, use:

```json
"aoi_circle": {"latitude": 40.01, "longitude": 8.76, "radius_km": 0.75}
```

This constructs a WGS84 geodesic circle polygon with 720 boundary vertices.
Its derived bounds limit acquisition and its polygon masks native pixel
centres. The point, radius, construction method and geometry hash are retained
in provenance. The full circle must satisfy the same geographic/area limits;
it does not enable crossing the antimeridian or mosaicking scenes. See
`examples/sentinel2_point_radius_2021.json`.

For a centred square, use:

```json
"aoi_square": {"latitude": 40.01, "longitude": 8.76, "side_km": 1.5}
```

This defines the full side length in kilometres, aligned east/north in a local
azimuthal equidistant projection. Densified edges are transformed to WGS84.
See `examples/sentinel2_square_2021.json`.

For polygon-file AOIs, the default output label comes from the filename stem.
An `aoi_path` ending in `Forest reserve.shp` and `output: "maps/test.tif"`
produce `maps/Forest reserve_<source-product>_LFMC_v01.tif`. Set
`output_label: "My study"` to change the prefix. Use a non-empty portable
filename component without path separators or reserved characters. The GUI
shows the output prefix while the form is edited. Scene discovery supplies
the source product identifier. Per-map provenance lists the actual filenames.

WorldCover 2021 is the default for all acquisition years, including 2023.
Set `landcover_year: 2020` to use the alternative snapshot. The selected map
stays fixed throughout a time series; it may not represent land-cover changes
after its reference year. No other land-cover maps are implemented yet.

## Single days and inclusive date ranges

With only `day`, discovery searches that complete UTC day. With `end_day`,
it searches from 00:00 of `day` through all of the final minute of `end_day`,
including seconds and fractional seconds. Where the catalogue supports it,
the implementation uses midnight of the following day as an exclusive upper
bound. End dates before the start date are rejected.

```json
"day": "2023-07-01",
"end_day": "2023-07-31",
"landcover_year": 2021
```

Range-mode `run` processes all matching scenes, or the subset in `product_ids`.
Sentinel-2, Landsat and Sentinel-3 use each scene's acquisition date for weather
and calendar features. Multiple scenes on the same date remain separate maps;
no gap filling, nearby-date substitution or mosaicking is applied. See
`examples/sentinel2_timeseries_2023.json`.

## Product-specific settings

| Product | `product` | `sensor` | `tile` | Extra choice |
| --- | --- | --- | --- | --- |
| Sentinel-2 L2A | `s2_l2a` | Read from selected SAFE | Optional MGRS, e.g. `32TMK`, without leading `T` | Exact UTC acquisition date |
| Landsat 8 / 9 | `landsat_c2l2` | `L8/L9` for both, `L8` or `L9` for one | Optional six-digit WRS path/row, e.g. `193032` | Exact UTC acquisition date |
| MODIS Terra/Aqua | `modis` | `Terra/Aqua MODIS` for both, `Terra MODIS` for Terra | Optional sinusoidal tile, e.g. `h18v04` | Explicit composite policy |
| Suomi NPP VIIRS | `viirs` | `Suomi NPP VIIRS` | Same optional convention | Explicit composite policy |
| Sentinel-3 | `s3syn300` | `S3A/S3B` for both, `S3A` or `S3B` for one | Empty | NTC collection 002, `.nc` output |

Only calibrated spacecraft domains are accepted. NOAA VIIRS spacecraft and
other products are not substituted for the published Suomi NPP domain.

Existing Aqua-only configurations use `modis_aqua` with `Aqua MODIS`.
The grouped MODIS setting preserves the Terra or Aqua calibration and source
granule ID in each map.

## Eight-day composites

MODIS and VIIRS require:

```json
"composite_policy": "contains-analysis-date"
```

The selected interval is anchored at day 1 of the year and contains `day`.
For example, 22 July 2021 selects 20 through 27 July, with an exclusive end of
28 July. Weather and day-of-year predictors use the configured analysis date.
Reflectance may originate from another day within the composite, including a
later day. This is a retrospective retrieval and depends on completed processing.
The adapter does not replace the configured weather date with a per-pixel
observation date.

A MODIS/VIIRS range finds the eight-day composites overlapping the requested
days, including the composite containing the start date. It processes each
matching granule once. The first composite uses the requested start date as
its weather/analysis date; later composites use their start dates. This also
applies across calendar years. The final composite can contain observations
after the requested end date, so the explicit composite confirmation remains
required.

## Scene selection and repeated runs

`discover` returns the current catalogue candidates. For a single date with
several matches, insert one returned ID in `product_ids`. In range mode, omit
IDs to process all matching scenes, or provide several IDs to choose a subset.
The workflow does not choose the first candidate, search a nearby date or switch
spacecraft when a scene is missing.

For `output: "maps/lfmc.tif"`, a range writes names such as
`lfmc_<source-product>_LFMC_v01.tif`, normal QA/provenance companions and
`lfmc_timeseries.json`. Sentinel-3 uses `.nc` for each native map. The manifest
records the requested interval, fixed WorldCover year and completed maps. It
updates after each map and records a failure without discarding earlier results.
An interrupted worker can leave its manifest marked `running`; completed maps
remain valid. To retry, choose a new output prefix and reuse the cache, or
request only the unfinished scene IDs with a new prefix. Existing final outputs
and manifests are protected from replacement.

Choose another output filename for a repeated run or a different weather mode.
Keep the same cache when inputs can be reused. A cached request does not remove
the need for valid provider configuration or guarantee that the complete run
will work offline.
