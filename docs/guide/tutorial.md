# Make your first LFMC map

This tutorial estimates live fuel moisture near Paradise, California, from
the Sentinel-2 acquisition on 6 November 2018, two days before the Camp Fire.
The small area lies within the extent shown in the [paper's Appendix B](https://doi.org/10.3390/rs18213744).
The tutorial uses WorldCover 2021 for vegetation routing and leaves weather
downscaling off. The published Figure A3 uses a pre-fire Dynamic World composite
and terrain-adjusted weather, so expect differences in coverage and LFMC.

Complete the [Conda installation](installation.md) and [configure CDSE and CDS
access](accounts.md) first. The clone includes the three model bundles. Run every command
below from the repository directory in the activated environment.

To run the same configuration with file pickers, follow the [desktop GUI
tutorial](gui.md).

## 1. Understand the request

The file `examples/sentinel2_camp_fire_2018.json` contains these settings:

```json
{
  "bounds": [-121.66, 39.75, -121.62, 39.78],
  "day": "2018-11-06",
  "tile": "10TFK",
  "product_ids": ["4ffd9bb3-827b-4f8f-9cd1-bd81bdffe256"],
  "landcover_year": 2021,
  "landcover_policy": "fixed-year",
  "downscale": false,
  "models_dir": "../model-assets",
  "cache_dir": "../cache",
  "output": "../outputs/lfmc_camp_fire_20181106.tif"
}
```

`bounds` lists west, south, east and north in longitude/latitude degrees.
The tile identifies the Sentinel-2 MGRS tile without its leading `T`.
The date is the satellite acquisition and weather date. The selected 2021
WorldCover map determines which vegetation model is used at each output pixel.
It is a fixed snapshot: you may keep 2021 or select 2020 for another acquisition
year. To process a time series, add an inclusive `end_day`; the
[configuration guide](configuration.md) explains scene selection and dated outputs.
Downscaling is initially disabled.

Paths start from the directory containing the JSON file. Here, `../cache`
means the repository's `cache` directory, not a directory relative to your shell.

Inspect the acquisition plan:

```text
lfmc-multisensor plan examples/sentinel2_camp_fire_2018.json
```

This command does not contact providers. It lists the required inputs, including
rainfall history back to 84 days before the analysis date. You do not need to
download that history yourself.

## 2. Check access and select the scene

```text
lfmc-multisensor access-check examples/sentinel2_camp_fire_2018.json
lfmc-multisensor discover examples/sentinel2_camp_fire_2018.json
```

The access check reports configuration presence without showing secrets. The
catalogue query checks the pinned CDSE product for this date, tile and area.
When adapting the example to another area or date, remove the pinned
`product_ids` and run discovery again. If several scenes match, set
`product_ids` to the catalogue ID of the scene you want to run.

Optional model preflight, in Python within the same Conda environment:

```python
from pathlib import Path
from lfmc_multisensor.models import load_published_bundle

for vegetation in ("Grass", "Shrub", "Tree"):
    path = Path("model-assets") / f"best_{vegetation}_gapfill_within_site.joblib"
    load_published_bundle(path, vegetation)
    print(vegetation, "model identity and contents checked")
```

This extracts the bundles on first use and checks their hashes and contents
before a large download. Use only the trusted model bundles from the repository.

## 3. Download inputs and predict

```text
lfmc-multisensor run examples/sentinel2_camp_fire_2018.json
```

The workflow acquires the required satellite assets, prepares reflectance and
quality masks, obtains terrain and land cover, and builds the weather predictors.
It then selects the grass, shrub or tree model for eligible pixels and writes
the results. Sentinel-2 with CDSE S3 keys fetches the required AOI windows.
The first run also builds the weather cache, which can take longer than
prediction while requests wait in the CDS queue.

Keep the cache. If a download fails, rerun the same command with the same cache
folder. Verified inputs are reused and incomplete downloads restart at the
asset level. If the earlier attempt created output files, give the retry a
new output name. For interrupted date ranges, see [Cancel and recover](gui.md#cancel-and-recover).

## 4. Inspect the map and quality codes

The example writes the following files. The names retain the source scene,
processing baseline `N0500` and LFMC model version `v01`.

```text
outputs/
  lfmc_camp_fire_20181106_S2B_MSIL2A_20181106T185559_N0500_R113_T10TFK_20230709T125120_LFMC_v01.tif
  lfmc_camp_fire_20181106_S2B_MSIL2A_20181106T185559_N0500_R113_T10TFK_20230709T125120_LFMC_v01_qa.tif
  lfmc_camp_fire_20181106_S2B_MSIL2A_20181106T185559_N0500_R113_T10TFK_20230709T125120_LFMC_v01_vegetation.tif
  lfmc_camp_fire_20181106_S2B_MSIL2A_20181106T185559_N0500_R113_T10TFK_20230709T125120_LFMC_v01.json
```

The first GeoTIFF contains LFMC as percent dry mass. The QA raster explains
where predictions are valid or missing. The JSON file records source identities,
processing choices, model hashes and output hashes. Read these together when
interpreting or sharing a map.

Keep the vegetation companion with the other files to calculate
[AOI and vegetation time-series summaries](statistics.md) without the original
input cache. You can enable those summaries before mapping or analyse saved
outputs later.

```python
from pathlib import Path
import json
import numpy as np
import rasterio

output = Path("outputs") / (
    "lfmc_camp_fire_20181106_"
    "S2B_MSIL2A_20181106T185559_N0500_R113_T10TFK_20230709T125120_LFMC_v01.tif"
)
with rasterio.open(output) as source:
    lfmc = source.read(1, masked=True)
    print("Grid:", source.crs, source.res, source.shape)
with rasterio.open(output.with_name(output.stem + "_qa.tif")) as source:
    qa = source.read(1)

valid = (qa == 0) & ~np.ma.getmaskarray(lfmc)
print("Valid predictions:", int(valid.sum()))
if valid.any():
    print("Median LFMC (% dry mass):", float(np.median(lfmc.data[valid])))
provenance = json.loads(output.with_suffix(".json").read_text())
print("Vegetation counts:", provenance["classes"])
print("Weather mode:", provenance["weather_mode"])
```

The verified run produced **27,792 valid estimates out of 29,750 pixels
(93.4%)** on the 20 m grid. All pixels passed the optical screening. The
remaining 1,958 had no eligible vegetation model, as shown by code `1`
in the [QA raster](outputs.md#geotiff-products).

## 5. Try the optional weather adjustment

The appendix methodology adjusts weather using local terrain elevation and a
coarse reference elevation. The available reference is reconstructed from a
fixed historical weather-model field; its equivalence to the provider's exact terrain and
requested cycle has not been established. Keep original and adjusted results
as distinct runs.

Create a second configuration with a new output path:

```python
import json
from pathlib import Path

source = Path("examples/sentinel2_camp_fire_2018.json")
configuration = json.loads(source.read_text())
configuration["downscale"] = True
configuration["output"] = "../outputs/lfmc_camp_fire_20181106_downscaled.tif"
Path("examples/sentinel2_camp_fire_2018_downscaled.json").write_text(
    json.dumps(configuration, indent=2) + "\n"
)
```

```text
lfmc-multisensor run examples/sentinel2_camp_fire_2018_downscaled.json
```

This run automatically obtains the optional reference and reuses applicable
cached inputs. The unadjusted run does not require that reference. A finer
output grid or a changed prediction does not itself establish that the adjusted
result is more accurate.

## 6. Move to another satellite

The repository includes `landsat8_2021.json`, `landsat9_2021.json`,
`modis_2021.json`, `modis_aqua_2021.json`, `viirs_2021.json`,
`sentinel3a_2021.json` and `sentinel3b_2021.json` in `examples/`.
Their dates and sensor identifiers are deliberate. NASA products require
Earthdata access; Sentinel-3 requires CDSE S3 keys and a `.nc` output path.

For MODIS and VIIRS, retain `composite_policy: contains-analysis-date` and read
the selected interval in the plan. The weather date does not turn an eight-day
composite into a same-day satellite observation. For Sentinel-3, read the
two-dimensional latitude/longitude coordinates in the NetCDF output.

Use [configuration](configuration.md) and [outputs](outputs.md) before changing
the area, date, product or file format.
