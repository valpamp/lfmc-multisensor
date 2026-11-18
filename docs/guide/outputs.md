# Read the map, quality codes and provenance

LFMC is the mass of water in living vegetation expressed as a percentage of dry
mass. Values can exceed 100%. These outputs are model estimates; they do not
provide a separate uncertainty interval for each pixel.

Always inspect the quality information alongside LFMC. No-data means that the
workflow did not produce an eligible, complete prediction at that location.
It must not be interpreted as zero moisture.

The JSON request also records the optional cloud filter. Product provenance
retains the provider-reported `cloud_cover` percentage and `cloud_cover_source`;
a missing percentage is JSON `null`. These values describe the catalogue
scene/granule, rather than cloud coverage or valid-pixel coverage within the AOI.

New LFMC maps carry copyright, licence and usage restrictions as GeoTIFF tags
or global NetCDF attributes. See the repository's `OUTPUT_TERMS.md` for terms
on sharing maps and obtaining a commercial licence.

## GeoTIFF products

Sentinel-2, Landsat, MODIS and VIIRS write an LFMC raster, a QA raster, a dominant
vegetation-class raster and a JSON record. For an output named `map.tif`, the
companions are `map_qa.tif`, `map_vegetation.tif` and `map.json`. Keep all four
together for statistics without the original input cache. The LFMC band is
float32 with missing values stored as no-data.
Its CRS and transform describe the native output grid.

| QA value | Meaning | How to use it |
| --- | --- | --- |
| 0 | Valid LFMC estimate | Include in analyses of predictions |
| 1 | Land cover does not route to a grass, shrub or tree model, or routing support is invalid | Treat as unpredicted |
| 2 | Optical input is missing or rejected by product quality screening at a routed location | Treat as unpredicted |
| 3 | A routed location has incomplete required predictors | Inspect weather and terrain support |
| 4 | Pixel centre is outside the selected polygon AOI | Treat as unpredicted; the rectangular native grid is retained |
| 255 | Raster no-data marker | Treat as unpredicted |

Each pixel has one reported reason. For example, non-routed land cover is
recorded even when optical inputs are also missing. QA is not a confidence score
or a probability that a valid prediction is accurate.

## Sentinel-3 NetCDF

Sentinel-3 pixels lie on a curved two-dimensional geolocation grid. The `.nc`
output keeps that geometry rather than assigning a constant affine transform.
The companion `.json` file records provenance.

| Variable | Contents |
| --- | --- |
| `LFMC` | Moisture estimate, float32, percent dry mass |
| `QA` | Quality reason; codes 0-3 as above, plus 4 outside the requested bounding box or polygon AOI |
| `lat`, `lon` | Two-dimensional decoded source coordinates |
| `source_row`, `source_column` | Indices in the original source scene |
| `dominant_class` | Dominant WorldCover code over the nominal central support |
| `landcover_support_fraction` | Fraction of that support represented by valid land-cover inputs |

The native rectangular subset includes a two-cell halo. Halo centres outside
the requested bounds have QA code 4 and no LFMC prediction. The recorded support
fraction is diagnostic; no new training-suitability threshold is applied to it.

```python
import xarray as xr

with xr.open_dataset("outputs/sentinel3a_2021.nc") as data:
    valid_lfmc = data["LFMC"].where(data["QA"] == 0)
    print(valid_lfmc.count().item())
    print(data["lat"].shape, data["lon"].shape)
```

Use the latitude/longitude arrays when displaying or comparing native S3 data.
A regular-grid GeoTIFF export would require an explicit resampling choice and
is not implemented in this candidate.

## Model versions and filename labels

Every newly generated LFMC GeoTIFF and its QA companion store
`LFMC_MODEL_SET_VERSION`, `LFMC_MODEL_VERSION_GRASS`,
`LFMC_MODEL_VERSION_SHRUB` and `LFMC_MODEL_VERSION_TREE` as metadata tags.
The corresponding model SHA256 tags bind those versions to exact artefact
bytes. Native Sentinel-3 NetCDF stores the same fields as global attributes.
`LFMC_OUTPUT_LABEL` records the selected filename label.

Maps use `<label>_<source-product>_LFMC_v01.<extension>`. The original
Sentinel-2 and Sentinel-3 names retain their acquisition timestamps and baseline
tokens. Landsat uses the full USGS Level-2 identifier, including its processing
date when available from the source asset. MODIS and VIIRS preserve the native
granule identifier and append `Ayyyymmdd` for the analysis date, which can
differ from the start of the composite. QA, vegetation, and provenance
companions use the same map stem.

GeoTIFF tags and NetCDF global attributes include `LFMC_MODEL_MAP_VERSION`,
`LFMC_PAPER_DOI`, `LFMC_PAPER_URL`, and `LFMC_SOURCE_PRODUCT_NAME` for
maps created from a provider scene. Additional source tags record its catalogue
identifier and its native baseline or collection when identifiable.

The JSON provenance and time-series manifest include `model_provenance`, with
the model-set identity/version and each vegetation model's version and SHA256.
Existing `model_sha256` records are retained in per-map provenance. The current
set and all three models are **1.0.0**, assigned to the already published
weights without changing their contents. These versions are independent of
the package version. Existing old outputs are not modified retroactively.

```python
import rasterio

with rasterio.open("outputs/Forest reserve_<source-product>_LFMC_v01.tif") as data:
    print(data.tags()["LFMC_MODEL_SET_VERSION"])
    print(data.tags()["LFMC_MODEL_VERSION_TREE"])
```

The loader verifies each bundle's checksum against the published model manifest
before loading it.

## Time-series outputs

Setting an end date writes separate native maps for matching scenes. The
additional `<prefix>_timeseries.json` manifest lists
completed maps, dates, selected inputs and status. It is updated after each map,
so earlier completed results remain available if a later scene fails.

These files are a collection of native maps, rather than a regridded temporal
cube. Scene footprints, grids and valid pixels can differ. There is no temporal
interpolation, compositing across separate scenes or automatic gap filling.
WorldCover remains at the selected fixed reference year throughout the series.

The optional [AOI statistics](statistics.md) add tables and mean/median figures,
overall or by vegetation class. They can also be generated from saved outputs
without repeating inference. Spatial spread bands are distinct from the
optional exploratory intervals for paired-date vegetation contrasts.

## Provenance and comparisons

The JSON record identifies the optical source, date or composite interval,
land-cover policy, weather mode, model identities and checksums of inputs and
outputs. Preserve it when sharing a map. It allows another run to distinguish
a changed source scene, model or processing choice from a numerical discrepancy.

Output pixel spacing describes the satellite grid. Weather starts from the
native AgERA5 grid, and terrain starts from AW3D30. Interpolation and optional
weather adjustment do not turn those inputs into independent measurements at
every satellite pixel. Comparisons between sensors must also account for their
different footprints, dates, composite intervals and masks.
