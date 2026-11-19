# Install and check the environment

Use Conda to provide Python and the geospatial libraries together. On Windows,
activating the environment also sets the paths needed by GDAL and other native
libraries. Calling an environment's `python.exe` directly does not provide the
same setup.

## Clone the repository

Install [Git](https://git-scm.com/downloads). In the parent folder where you
want to keep the project, run:

```text
git clone https://github.com/valpamp/lfmc-multisensor.git
cd lfmc-multisensor
```

The public clone includes the three compressed model bundles in
`model-assets/`, about 170 MiB in total. Standard Git is sufficient.

## Tested Windows installation

1. Install [Miniforge for Windows x86_64](https://github.com/conda-forge/miniforge#windows)
   if Conda is unfamiliar or absent. An existing Conda installation also works.
2. Open **Miniforge Prompt**
   from the Windows Start menu, or open the prompt for your existing Conda
   installation. Change to the cloned folder. For example, in that prompt
   run `cd /d "C:\path\to\lfmc-multisensor"`.
3. Run these commands in that prompt.

```text
conda create -n lfmc-multisensor --file environment-win64.lock.txt
conda activate lfmc-multisensor
python -m pip install --require-hashes --no-deps -r requirements-statistics-win64.lock.txt
python -m pip install --no-deps --no-build-isolation .
python -m pip check
```

The explicit Conda file fixes the tested Windows package builds. The supplemental
pip file supplies the HDF4 reader used by Terra/Aqua MODIS and the polygon AOI
readers used by the GUI and shapefile configurations, plus Matplotlib and its
plotting dependencies for AOI statistics. The statistics lock includes the
workflow lock; CLI installations without figures can use
`requirements-workflow-win64.lock.txt` instead. Installing this
repository adds the `lfmc-multisensor` command. The lock includes build tools, so
the installation can use them without creating a separate build environment.

Check which environment is running:

```text
python -c "import sys, numpy, rasterio; print(sys.prefix); print(numpy.__version__); print(rasterio.__version__)"
python -m lfmc_multisensor.cli plan examples/sentinel2_camp_fire_2018.json
```

The printed prefix should identify the environment you just activated. The plan
command does not download data. If a terminal cannot retain activation, use
Conda to launch each command:

```text
conda run --no-capture-output -n lfmc-multisensor python -m lfmc_multisensor.cli plan examples/sentinel2_camp_fire_2018.json
```

## Supply the published models

The checkout includes all three LFMC model bundles from model set **1.0.0**:

```text
model-assets/
  best_Grass_gapfill_within_site.joblib.bz2
  best_Shrub_gapfill_within_site.joblib.bz2
  best_Tree_gapfill_within_site.joblib.bz2
```

The example configurations already refer to this directory. To use another
location, change `models_dir` in the configuration. Paths are resolved relative
to the configuration file, including when you run the CLI from another directory.

In the GUI, select `model-assets` and click **Check models** before running
the first map. Both **Check models** and a CLI run automatically extract and
verify each bundle on first use. Allow about 600 MiB of extra space in the
model directory for the extracted `.joblib` files. Subsequent runs reuse
these files and verify them against the published SHA-256 checksums. The
Windows environment above includes the required scikit-learn **1.9.0**.

Only load files from a trusted source. Joblib/Python model serialisation can
execute code; a model file is not an inert data table.

## Desktop interface

After installation and provider setup, run `lfmc-multisensor gui` from the
activated Conda terminal. The [GUI tutorial](gui.md) walks through shapefile
selection, scene discovery and prediction. Tkinter supplies the native widgets;
the tested Windows Conda lock includes Tcl/Tk. A different Conda installation
may need its matching `tk` package.

On subsequent launches, open the Conda prompt, return to the repository folder,
and run `conda activate lfmc-multisensor` before launching the GUI.

The installation above was tested on Windows x64. Linux and macOS have yet to
be independently tested and require platform-specific dependencies.

Next, [configure provider accounts](accounts.md).
