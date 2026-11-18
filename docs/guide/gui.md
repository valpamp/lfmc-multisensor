# Use the desktop interface

The desktop GUI provides file pickers and a form for the same inference workflow
used by the command line. Shapefile selection is the default way to define the
area of interest. You can also enter a longitude/latitude bounding box or
define a circle or square around a point.
Processing runs locally in your Conda environment. The documentation website
does not run models or collect provider credentials.

## Start the application

Complete installation, configure provider access and obtain the trusted model
bundles first. In an activated Conda terminal, run:

```text
lfmc-multisensor gui
```

Alternatively, use `lfmc-multisensor-gui` or
`python -m lfmc_multisensor.gui`. Use the current installed package so that it
includes the GUI and polygon dependencies. Tkinter/Tcl/Tk supplies the desktop
widgets; the provided Conda installation includes Tk. Other installations may
need the matching Conda `tk` package.

Select the folder containing the three model files and click **Check models**
before starting a download.

## 1. Choose the area

Select **Shapefile / polygon file** and browse to a `.shp` file. Keep its `.shx`,
`.dbf` and `.prj` components beside it with the same base name. The `.prj` must
declare the coordinate reference system. The loader does not guess a missing
CRS. WGS84 GeoJSON polygons are also accepted.

All polygon features are combined into one area. Interior holes and disconnected
parts are retained. A projected shapefile is reprojected to WGS84 for discovery
and masking. Invalid, empty or non-polygon features stop validation with a
message; they are not silently dropped or repaired.

The bounding rectangle limits acquisition and determines the native raster
window. A prediction is retained only when its pixel centre lies on or inside
the polygon union; hole interiors are excluded. The saved grid remains native,
with no-data outside the selected polygons. Input footprints are not reshaped
or resampled to make polygon boundaries look smoother.

To try the supplied example, load `examples/sentinel2_shapefile_2021.json` using
**Load configuration**. Its manually drawn Sardinia AOI includes a cut corner
and a small hole to demonstrate masking. It is not a field-site or fire-perimeter
dataset. Replace the models/cache/output paths for your installation.

For a simple rectangle, choose **Bounding box (WGS84)** and enter west, south,
east and north. Coordinates are longitude/latitude degrees. Original bounding-box
configurations continue to work.

To create an AOI on the fly, choose **Point and radius**. Enter **latitude**
and **longitude** in WGS84 decimal degrees, then **Radius (km)**. For example,
latitude `40.01`, longitude `8.76` and radius `0.75` define a circle extending
750 metres from the point. The value is a radius, not a diameter. The application
constructs a polygon from 720 boundary points at that distance on the WGS84
ellipsoid; it does not buffer longitude/latitude degrees as if they were metres.
Pixel centres inside this polygon are retained on the satellite's native grid.

The point and radius are stored directly in saved configurations, so no extra
shapefile or GeoJSON is needed. Reloading restores this selection. Zero,
negative or non-finite radii are rejected, as are circles crossing the
antimeridian or exceeding the existing geographic and one-degree area limits.
Reduce the radius if the plan reports an unsupported extent. A very small
circle may contain no pixel centres on a coarse satellite grid.

For a square, choose **Point and square** and enter its centre latitude,
longitude and **Side (km)**. A side of `1.5` creates a 1.5 by 1.5 km area;
this value is the full side length, not a radius or half-width. The square is
aligned east/north in a local metric projection centred on the point. Its
boundary is transformed to WGS84 for discovery and native pixel-centre masking.
The same geographic and area limits apply to the whole square. Both circle
and square settings are preserved when saving and loading a configuration.

## 2. Select a satellite, dates and WorldCover map

**Max cloud (%)** optionally limits discovery to scenes at or below a percentage
between 0 and 100. Leave it blank for an unrestricted search. **Include unknown
cloud cover** retains scenes without a usable catalogue percentage when a maximum
is set; otherwise those scenes are excluded. Without a maximum, all scenes remain
eligible, including those with missing percentages.

The scene selection window shows a **Cloud (%)** column, with **N/A** for missing
values. Click its heading to sort, or use **Apply list filter** to narrow the
returned scenes before selecting them. Percentages are displayed to two decimal
places; filtering uses the original precision and includes the maximum itself.
The chosen list filter is saved in the main form and applied again during Run.
Changing the main cloud settings clears previous scene selections. To recover
scenes excluded by the original search, widen its limit and search again.

Cloud percentages describe the provider's whole scene or granule, rather than
the selected AOI. They do not replace pixel quality masks. Sentinel-2 and
Sentinel-3 use the CDSE `cloudCover` attribute when available; Landsat uses STAC
`eo:cloud_cover`; MODIS/VIIRS use CMR `cloud_cover`. Some products, especially
composites, have no usable percentage and will show N/A. Missing, invalid or
ambiguous metadata is never interpreted as zero cloud.

These metadata definitions are documented by
[CDSE](https://documentation.dataspace.copernicus.eu/APIs/OData.html),
[STAC EO](https://github.com/stac-extensions/eo) and
[NASA CMR](https://cmr.earthdata.nasa.gov/search/site/docs/search/api.html).

Choose the product from the menu. **Landsat 8 / 9** searches both spacecraft.
Each selected Landsat scene uses its spacecraft's calibration. If a saved
configuration specifies just one Landsat spacecraft, the form displays that
restriction. Select **Landsat 8 / 9** again to search both.
**Sentinel-3 SYNERGY** works the same way for Sentinel-3A and Sentinel-3B.
**MODIS (Terra/Aqua)** searches both spacecraft. Scene IDs start with
`MOD09A1` for Terra and `MYD09A1` for Aqua. A saved spacecraft-specific
configuration keeps its original search scope. Select **MODIS (Terra/Aqua)**
again to search both.

Enter the actual acquisition date for Sentinel-2, Landsat or Sentinel-3.
For MODIS/VIIRS, enter the analysis
date and explicitly confirm use of the containing eight-day composite. Such a
composite can include observations after the analysis date.

Enter the date in **Single / start date (UTC)**. Leave **End date (optional)**
blank to search from 00:00 through 23:59 of that one day, including its final
seconds. Set an end date to search the complete inclusive interval. A range
run writes one native map per matching scene using each acquisition's own
weather date. End dates before the start date are rejected. Setting the same
start and end date enables a range run over all matching scenes on that day.

The **WorldCover map** defaults to **2021**, even for a request in 2023 or
another acquisition year. Select **2020** for the alternative snapshot. The
chosen map stays fixed across the time series; additional maps are future work.

For MODIS/VIIRS ranges, each overlapping eight-day granule is processed once.
The first composite uses the requested start date for weather; later composites
use their start dates. A final composite can include observations after the end
date. Keep the explicit composite confirmation selected when using these products.

The tile/WRS filter is optional. It can narrow discovery when you know the
desired grid, but you can search from the area and date without supplying one.
Sentinel-3 uses no tile filter. Each output remains a single scene/granule,
with areas no more than one degree across within 60 degrees south to 60 degrees
north. Polygon or date-range selection does not enable mosaicking. Actual
optical and weather availability still determine which dates can be processed.

## 3. Select models, cache and output

Choose the directory containing the three authorised model bundles, a cache
directory and a new output path. Most products write `.tif`; Sentinel-3 writes
`.nc` with two-dimensional coordinates. Switching products updates the expected
extension. Existing final outputs are protected from replacement.

**Filename label** sets the prefix before the source product ID and the
`_LFMC_v01` suffix. Selecting `Forest reserve.shp` defaults the label to
`Forest reserve`. The output selection sets the directory and extension.
The label follows changes to the selected file until you edit it. Your custom
label then stays in place; **Use AOI name** restores the automatic default.
Choosing or editing an output filename also updates the label. Saved
configurations preserve the chosen label. There is no required `test` prefix.

For a time series, choosing `lfmc.tif` also writes `lfmc_timeseries.json`. The
manifest updates as each scene completes; it links the individual maps and
their provenance.

Generated maps record the model-set version and the individual grass, shrub
and tree model versions. The current published weights are registered as
**1.0.0**; this labels the existing weights and does not retrain them. See
the [output metadata guide](outputs.md) for the tags and provenance fields.

Leave the optional weather adjustment unchecked for the default weather mode.
Selecting it applies the appendix methodology using the labelled historical
reference approximation. It does not change the satellite grid or establish
an improvement in field accuracy.

## 4. Inspect, discover and run

- **Plan** validates the form and explains the inputs without contacting providers.
  For a polygon, its CRS, bounds, feature count and file fingerprints identify
  the selected AOI.
- **Check access** reports the presence of external provider configuration. It
  does not prove that credentials or dataset terms are valid.
- **Check models** verifies bundle hashes and compatibility before a download.
- **Find scenes** queries the requested area/date. One matching scene is selected
  automatically. For a single date, choose one if several match. For a range,
  choose multiple scenes or use **Select all**. Leaving scene IDs empty in
  range mode processes all matches. No ranking or mosaic is applied.
- **Run** checks the models, downloads/prepares inputs and predicts. Progress
  messages and errors appear in the log while the form stays responsive.
  Sentinel-2 over CDSE S3 reports the MB transferred for native AOI windows.
  Its activity bar animates while files are read because the compressed bytes
  needed by each window are only known as the reads complete.
  For Sentinel-3 over CDSE S3, the progress bar and status show the current file and the MB
  transferred across the files still needed for that scene. Verified cached
  files are reused. The bar returns to its activity animation when processing
  continues after the download.

Sentinel-3 downloads the required geolocation, reflectance, QA, and manifest
files for the selected swath. Cropping to the AOI happens locally after
acquisition, so the first download can be sizeable even for a small circle.

Changing either date clears old scene selections. If you change the area or
satellite, find scenes again. A stale explicit
scene ID is rejected if it does not belong to the new catalogue request.

**Save configuration** writes a reusable JSON request without credentials.
**Load configuration** restores the form, including polygon, point/radius or
bounding-box mode.
The run's request and log are stored under the selected cache's `gui_requests`
directory. Outputs retain the normal QA and provenance companions.

## Cancel and recover

**Cancel** stops the local worker. Keep the same cache folder when retrying
a cancelled, interrupted or failed run. The next run reuses verified inputs
and restarts any incomplete download from the beginning of that asset.

- **One scene.** Correct the error and click **Run** again. Use a new output
  label if the earlier attempt created any output files.
- **Date range.** Open the previous `*_timeseries.json` in a text editor. Its
  `maps` list contains the completed scene IDs and outputs, even if the status
  still says `running` after a hard stop. Use **Find scenes** and select the
  remaining scenes, leave the cache folder unchanged, and choose a new output
  label. This starts a second series and preserves the first series. Running
  the original full selection again would also predict the completed scenes.
- **Only statistics failed.** Open **Statistics options / saved maps…** and
  use **Analyse saved outputs** with a fresh statistics label.

Some corrupt weather-cache entries require the manual check in
[troubleshooting](troubleshooting.md#an-optical-transfer-was-interrupted).

The GUI starts one task at a time and limits its worker's CPU/thread settings.
Provider requests can still wait in a remote queue. A native-library failure
in the worker is reported as a failed exit; it does not run inference inside
the GUI event loop.

The **Pampanoni et al. (2026)** link at the bottom right opens the paper's DOI
page in your default web browser. It also works with keyboard focus and Enter.
The footer remains visible while the form can scroll on smaller windows.

## AOI summaries and figures

Select **Calculate AOI statistics and figures after mapping** for automatic
summaries. **Statistics options / saved maps…** lets you choose median or mean,
vegetation groups and available/common pixel support. It also provides
**Analyse saved outputs**, which reads a completed series or map using the
current AOI and filename label. This analysis needs no provider access or model
loading. See the [statistics tutorial](statistics.md) for figures, coverage,
older-map compatibility and optional exploratory vegetation contrasts.
