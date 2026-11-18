# Make your first LFMC map

This tutorial estimates live fuel moisture for a small area in Sardinia on
22 July 2021 using Sentinel-2. Start with the supplied configuration so that
installation, credentials and model access can be checked against an exercised
example. You can then change the satellite or area using the configuration guide.

Complete the [Conda installation](installation.md), obtain the three trusted
models and [configure CDSE and CDS access](accounts.md) first. Run every command
below from the repository directory in the activated environment.

For a form with file pickers, follow the [desktop GUI tutorial](gui.md).
It starts from a shapefile AOI and uses the same models, provider inputs and
quality rules. The command-line example below retains a simple bounding box
so that it can be compared with the original validation case.

## 1. Understand the request

The file `examples/sentinel2_2021.json` contains these settings:

```json
{
  "bounds": [8.75, 40.0, 8.77, 40.02],
  "day": "2021-07-22",
  "tile": "32TMK",
  "landcover_year": 2021,
  "landcover_policy": "fixed-year",
  "downscale": false,
  "models_dir": "../model-assets",
  "cache_dir": "../cache",
  "output": "../outputs/lfmc_20210722.tif"
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
lfmc-multisensor plan examples/sentinel2_2021.json
```

This command does not contact providers. It lists the required inputs, including
rainfall history back to 84 days before the analysis date. You do not need to
download that history yourself.

## 2. Check access and select the scene

```text
lfmc-multisensor access-check examples/sentinel2_2021.json
lfmc-multisensor discover examples/sentinel2_2021.json
```

The access check reports configuration presence without showing secrets. The
catalogue query then returns scenes matching the requested day, tile and area.
If exactly one scene matches, the run can select it. If several match, add a
`product_ids` list containing one of the returned IDs to your JSON file. Copy
the actual catalogue ID; do not substitute a filename. Multiple scenes are not
combined automatically.

Optional model preflight, in Python within the same Conda environment:

```python
from pathlib import Path
from lfmc_multisensor.models import load_published_bundle

for vegetation in ("Grass", "Shrub", "Tree"):
    path = Path("model-assets") / f"best_{vegetation}_gapfill_within_site.joblib"
    load_published_bundle(path, vegetation)
    print(vegetation, "model identity and contents checked")
```

This catches missing bundles, unexpected hashes and incompatible serialisation
before a large download. Do not run it on untrusted model files.

## 3. Download inputs and predict

```text
lfmc-multisensor run examples/sentinel2_2021.json
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

The example writes:

```text
outputs/
  lfmc_20210722.tif
  lfmc_20210722_qa.tif
  lfmc_20210722_vegetation.tif
  lfmc_20210722.json
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

output = Path("outputs/lfmc_20210722.tif")
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

The exercised Sentinel-2 example produced 8,701 valid pixels. Future catalogue
changes or a different selected scene may change that result; use the recorded
source identities when making a numerical comparison. A missing pixel is not a
zero-moisture observation. See the [output guide](outputs.md) for each QA code.

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

source = Path("examples/sentinel2_2021.json")
configuration = json.loads(source.read_text())
configuration["downscale"] = True
configuration["output"] = "../outputs/lfmc_20210722_downscaled.tif"
Path("examples/sentinel2_2021_downscaled.json").write_text(
    json.dumps(configuration, indent=2) + "\n"
)
```

```text
lfmc-multisensor run examples/sentinel2_2021_downscaled.json
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
