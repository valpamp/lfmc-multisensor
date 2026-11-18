# What the workflow estimates and what has been tested

The package applies the models from the
[published multisensor LFMC study](https://doi.org/10.3390/rs18213744).
For each eligible satellite location, it prepares reflectance, weather, terrain
and seasonal predictors. Annual land cover selects the grass, shrub or tree
model. The package puts the predictors in the order expected by that trained
Random Forest, applies its stored scaling, and converts the model output back
to LFMC as percent dry mass.

## Reflectance and vegetation models

Product-specific screening removes missing or unsuitable optical observations.
Reflectance is kept at the product's native support. Sentinel-2 uses B8A and B11
at 20 m and averages red B4 from 10 m. MODIS and VIIRS keep their actual
sinusoidal spacing. Sentinel-3 retains its per-pixel geolocation.

Before calculating spectral indices, each vegetation model applies its own
stored calibration for the selected non-reference sensor and spacecraft.
Sentinel-2 is the reference product and follows its reference preprocessing.
The package does not fit new calibration coefficients or train a new forest
during inference. Missing calibration domains stop the run.

The indices, seasonal terms, ordered predictors, scaler and inverse target
transformation follow the stored published bundles. Predictions require all
of a model's inputs to be finite. LFMC outputs are not clipped to an arbitrary
percentage range.

## Weather and optional elevation adjustment

AgERA5 version 2 supplies daily weather on its native 0.1-degree grid. Its daily
labels follow the provider's local-day convention. The package associates those
labels with the configured analysis date, preserving the study's date convention;
it does not reconstruct hourly weather at satellite overpass time. See the
[provider description](https://cds.climate.copernicus.eu/datasets/sis-agrometeorological-indicators?tab=overview).
Predictors
include daily conditions and rainfall totals over the preceding 3, 7, 28 and
84 days. Those antecedent totals exclude the analysis day; the daily rainfall
predictor uses that day itself. Missing required history is not replaced by zero.

By default, weather is interpolated to the satellite centres without the
appendix elevation adjustment. Setting `downscale: true` applies the appendix
constant-relative-humidity lapse methodology using local AW3D30 elevation and
a reconstructed reference for the weather grid. That reference comes from a
fixed historical field of the European Centre for Medium-Range Weather Forecasts'
high-resolution (HRES) model. The reference is explicitly
approximate: equivalence to the weather provider's exact terrain and requested
date/cycle has not been verified. Both modes retain separate provenance.

Terrain derivatives are calculated on the AW3D30 source grid before sampling.
The optional adjustment changes weather predictors; it does not increase the
resolution of the optical observation or establish a measured accuracy gain.

## Land cover and quality screening

Direct workflows use WorldCover 2021 by default, with 2020 selectable. The map
year is independent of the acquisition year and remains fixed across a time
series. Changes in land cover after that snapshot are not reconstructed.
The dominant category is selected among all map classes; only tree, shrub and
grass categories route to a model. Training-site neighbourhood and suitability
filters are not imposed on arbitrary inference locations.

Sentinel-3 aggregates class fractions over nominal 300 m central rectangles in
local UTM coordinates. Its audited metadata-based QA excludes cloud, snow and
specified retrieval failures. This screening is stricter than the older
appendix example's mask. The historical replay preserves its original mask;
it is not a claim that the current direct QA reproduces every historical pixel.
Historical Dynamic World probabilities are not acquired by these direct workflows.

## Evidence available for this candidate

Three checks answer different questions:

1. **Saved-case replay:** can extracted inference reproduce the saved appendix
   outputs and masks for the checked Sentinel-2 and native Sentinel-3 cases?
2. **Real-input reference comparison:** do newly prepared inputs produce the same
   predictors and predictions through the package and the trusted research helpers?
3. **Installed-package exercise:** does a built, non-editable package work outside
   the research source tree, with the recorded environment and provider access?

The exercised Sardinia examples include all eight sensor/spacecraft paths in
both weather modes. Their original and adjusted runs produced these valid-pixel
counts:

| Example | Acquisition or analysis date | Valid pixels in each mode |
| --- | --- | --- |
| Sentinel-2 | 2021-07-22 | 8,701 |
| Landsat 8 | 2021-07-15 | 3,047 |
| Landsat 9 | 2021-11-18 | 3,047 |
| Terra MODIS | 2021-07-22, composite 20-27 July | 16 |
| Aqua MODIS | 2021-07-22, same composite | 6 |
| Suomi NPP VIIRS | 2021-07-22, same composite | 16 |
| Sentinel-3A | 2021-07-22 | 42 |
| Sentinel-3B | 2021-07-21 | 49 |

The combined Windows check matched the baseline LFMC values and valid masks
for all eight paths in both weather modes. Each original-mode path started
with an empty provider cache. The downscaled paths reused the same
checksum-verified historical weather reference. Later checks used cached
inputs to verify polygon masking, the GUI and date-range processing.

Reproducing software outputs is distinct from validating LFMC against independent
field measurements. The small examples do not establish general geographic/date
coverage, every processing baseline or operational real-time availability.
Linux/macOS, additional land-cover maps, mosaics, and scientific accuracy of the
optional weather adjustment remain outside the demonstrated scope.
