# Troubleshooting

Start with the failing command and its configuration. Preserve the provenance
and relevant error text, but remove credentials and authentication tokens before
sharing diagnostics.

## Python or GDAL fails on Windows

Open a Conda-capable terminal and activate the environment before running Python,
pip, tests or the CLI. A full path to `python.exe` does not by itself activate
the environment's native-library paths.

```text
conda activate lfmc-multisensor
python -c "import sys; print(sys.prefix)"
python -m pip check
```

Alternatively, use `conda run --no-capture-output -n lfmc-multisensor` before
the Python command. Check that the prefix is the intended environment. If a
native process fails, preserve the exit code and retry one small check before
starting more jobs. Avoid running several large validation jobs while diagnosing
an unstable environment.

The explicit Windows lock includes the JPEG2000 driver needed by Sentinel-2.
Installing an arbitrary rasterio/GDAL combination into an existing environment
can leave that driver or its DLL dependencies unavailable.

## Credentials appear configured but downloading fails

`access-check` reports configuration presence. Check the keys and expiry
dates for CDSE (Sentinel-2 and Sentinel-3), the Earthdata account and token or
login for MODIS/VIIRS, and your CDS token and AgERA5 terms for every workflow.
Public scene discovery can succeed before an authenticated download fails.
See the [provider account guide](accounts.md) for the setup steps.

## The desktop window does not open

Launch `lfmc-multisensor gui` from the activated Conda terminal to see startup
errors. Confirm that the current package is installed in that environment.
If Tkinter is unavailable, install the matching Conda `tk` package there. The
GUI uses a local desktop window and needs an interactive graphical session.

## A shapefile is rejected or the mask looks unexpected

Keep `.shp`, `.shx`, `.dbf` and `.prj` files together with the same base name.
The CRS must be declared in `.prj`; use GIS software to identify and assign
the correct source CRS before retrying. Assigning WGS84 to projected coordinates
does not reproject them. Empty, invalid or non-polygon features require correction
in the source dataset. The application does not repair or discard them silently.

All features are combined, including disconnected parts, with holes retained.
Pixels are selected by their native centres, so coarse satellite pixels do not
trace a boundary exactly. QA code 4 marks centres outside the polygon union.
The polygon's bounding rectangle must still satisfy the current area and
latitude limits. If an AOI component changes after loading, reload the request.

## No matching scene, or several matches

Use `discover` with the same JSON configuration. Confirm date, area, tile and
spacecraft. No nearby-date or alternate-sensor search is performed. When there
are multiple matches on a single date, select one returned ID in `product_ids`.
For a range, leave IDs empty to process all matches, or select a subset.
Separate scenes remain separate maps.

## Date or land-cover policy is rejected

The current code defaults to WorldCover 2021 for any acquisition year and also
accepts WorldCover 2020. If a 2023 request still reports a same-year restriction,
update the installed package in the active Conda environment and restart the GUI.
The current JSON policy is `fixed-year`; old saved `same-year-annual` requests
are migrated on loading. Check that an optional end date is not before the
start date. MODIS/VIIRS still require the explicit containing-composite policy.

## Weather is unavailable or still downloading

AgERA5 needs the analysis day's variables and complete antecedent rainfall
history. Provider queues can delay an accepted request. Missing history stops
preparation; it is not replaced with zero rainfall or another weather product.
If a request is rejected, inspect the dataset's actual availability and account
terms before changing the configuration.

## An optical transfer was interrupted

Retry with the same cache folder. Completed assets with matching receipts
can be reused, and incomplete assets restart from zero. Leave `.part` files
for the downloader to handle. If you have an existing series manifest, see
[Cancel and recover](gui.md#cancel-and-recover) to retry just the unfinished scenes.

Automatic recovery is not complete for every corrupt CDS archive or extracted
daily NetCDF. Preserve the error and identify the affected cache entry before
repairing it. Clearing an entire cache can unnecessarily repeat large downloads.

## The output already exists

Choose a new output label or filename. An interrupted date-range attempt can
leave a `*_timeseries.json` manifest that reserves the label even before the
first map is finished. Keep that manifest for recovery and retain each
completed map's JSON provenance alongside it.

## There are few or no valid predictions

Inspect QA counts first. Cloud/snow/retrieval screening, non-routed land cover,
missing optical values or incomplete weather/terrain predictors can each reduce
coverage. Only dominant grass, shrub and tree categories select a model.
An all-cloud scene can legitimately have no LFMC estimates. Do not replace
no-data with zero or relax QA simply to obtain a complete-looking map.

## A model is missing, has the wrong hash or cannot be loaded

Use the authorised published bundles, preserve their original filenames and
check `models_dir` relative to the JSON file. The loader requires scikit-learn
1.9.0 and the known artefact hashes. Do not bypass these checks or load an
untrusted replacement to make a run proceed.

## Sentinel-3 will not write a GeoTIFF

Use a `.nc` output path. The native grid needs two-dimensional latitude and
longitude. Read the NetCDF variables as described in the output guide. A
regular-grid export requires a separate, explicit resampling implementation.
