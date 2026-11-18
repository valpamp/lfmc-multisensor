# Configure provider access

Each user supplies their own credentials. Keep them in environment variables or
the provider's external configuration file, outside this repository. Credentials
do not belong in JSON configurations, notebooks, provenance records or commits.

The first tutorial needs Copernicus Data Space access for Sentinel-2 and Climate
Data Store access for daily weather. Other products use the routes below.

| Input | Access needed | Configuration read by the package |
| --- | --- | --- |
| Sentinel-2 and Sentinel-3 | Copernicus Data Space Ecosystem (CDSE) account and S3 keys | `CDSE_S3_ACCESS_KEY`, `CDSE_S3_SECRET_KEY` |
| MODIS and VIIRS | NASA Earthdata account with applicable data access | External `.netrc`/`_netrc`, or `EARTHDATA_TOKEN`, or `EARTHDATA_USERNAME` and `EARTHDATA_PASSWORD` |
| AgERA5 weather, all products | Copernicus Climate Data Store (CDS) account and accepted dataset terms | External `.cdsapirc` or supported CDS client environment settings |
| Landsat 8/9 | Public Planetary Computer archive | No user credential in the exercised route |
| WorldCover and AW3D30 v4.1 | Public data services and applicable terms | No additional credential in the exercised region. The JAXA archive fallback can use optional `JAXA_USERNAME` and `JAXA_PASSWORD`. |
| Optional historical weather reference | Pinned public NCAR/GDEX source | No additional credential in the exercised route |

## Copernicus Data Space

1. Register or sign in to Copernicus Data Space and open the
   [S3 credentials manager](https://eodata-s3keysmanager.dataspace.copernicus.eu/panel/s3-credentials).
2. Choose **Add Credentials**, set an expiry date and confirm. Save the access
   key and secret key securely. The secret is shown once. [CDSE S3 instructions](https://documentation.dataspace.copernicus.eu/APIs/S3.html)
3. In the activated Windows Conda **Command Prompt**, run each line in turn and
   paste the corresponding key when prompted.

```bat
set /p "CDSE_S3_ACCESS_KEY=CDSE access key: "
set /p "CDSE_S3_SECRET_KEY=CDSE secret key: "
```

Launch the GUI or CLI from that prompt. These settings last until you close
the terminal. If you use PowerShell, set the same variables with
`$env:CDSE_S3_ACCESS_KEY = Read-Host "CDSE access key"` and
`$env:CDSE_S3_SECRET_KEY = Read-Host "CDSE secret key"`. Generate a new pair
when the keys expire.

Sentinel-2 uses S3 to read native AOI windows from reflectance and raster
masks. Sentinel-3 also uses S3 and downloads the complete files it needs
from the selected swath.

Sentinel-2 also has an OAuth implementation using `CDSE_ACCESS_TOKEN` or
`CDSE_USERNAME`/`CDSE_PASSWORD`; its download route has not received the same
live validation and downloads complete required SAFE files. Sentinel-3
currently requires the S3 keys. Prefer the exercised
S3 route for the tutorial.

## Climate Data Store

1. Register or sign in to CDS and copy the two configuration lines shown by the
   [CDS API setup page](https://cds.climate.copernicus.eu/how-to-api).
2. Save those lines in `%USERPROFILE%\.cdsapirc` on Windows. In Notepad, choose
   **All files** when saving and make sure the name is exactly `.cdsapirc`.
3. While signed in, open the
   [AgERA5 dataset](https://cds.climate.copernicus.eu/datasets/sis-agrometeorological-indicators?tab=overview),
   select **Download** and accept the terms at the bottom of the form.

Every satellite workflow needs CDS access. The Conda environment includes
the CDS client. LFMC requests the daily weather and rainfall history for you.

Provider request queues and processing affect download time. An account may be
configured correctly while a request is still waiting for completion.

## NASA Earthdata

1. Register or sign in at [Earthdata Login](https://urs.earthdata.nasa.gov/).
   If the data service asks you to authorise access, approve the relevant LP
   DAAC application. [NASA application authorisation](https://urs.earthdata.nasa.gov/documentation/for_users/how_to_preauth_app)
2. From your profile, choose **Generate Token** and copy the token when
   displayed. [NASA token instructions](https://urs.earthdata.nasa.gov/documentation/for_users/user_token)
3. In the activated Windows Conda Command Prompt, run the following and paste
   the token when prompted. Launch LFMC from the same terminal.

```bat
set /p "EARTHDATA_TOKEN=Earthdata token: "
```

In PowerShell, use `$env:EARTHDATA_TOKEN = Read-Host "Earthdata token"`.
You can also use a home-directory `.netrc` or `_netrc` with an
`urs.earthdata.nasa.gov` entry, or set both `EARTHDATA_USERNAME` and
`EARTHDATA_PASSWORD`. [NASA netrc instructions](https://urs.earthdata.nasa.gov/documentation/for_users/data_access/curl_and_wget)

## Check what the package can find

In your activated Conda environment:

```text
lfmc-multisensor access-check examples/sentinel2_2021.json
```

The command reports whether configuration is present, without returning secret
values. It does not authenticate against each provider or prove that terms have
been accepted. Public catalogue discovery can succeed even when an authenticated
download will fail. A download failure should be checked against the account,
keys or terms required for that particular input.

Next, [make the first map](tutorial.md).
