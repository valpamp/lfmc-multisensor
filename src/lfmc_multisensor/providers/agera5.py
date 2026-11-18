"""AgERA5 v2 daily inputs for the existing 15 LFMC weather predictors.

Native 0.1-degree grids only. Daily labels are already local-day labels.
CDS requests are split by calendar month; credentials come from cdsapi config.
"""
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from hashlib import sha256
import json
import math
from pathlib import Path
import zipfile

import numpy as np
import rasterio
from rasterio.transform import from_origin
import requests
import xarray as xr

from ..weather import WEATHER_FEATURE_VERSION, WEATHER_SOURCE
from ..features import vpd_from_tmean as compute_vpd_from_tmean

DATASET = 'sis-agrometeorological-indicators'
VERSION = '2_0'
TIF_SUBDIR = 'agera5_v2_tifs'
NC_SUBDIR = 'agera5_v2_nc_cache'
FILE_SUFFIX = 'AGERA5_v2_wx.tif'
REQUIRED_OUTPUT_BANDS = (
    'precip_24h', 'precip_3d', 'precip_1w', 'precip_4w', 'precip_12w',
    'RH_06h', 'RH_09h', 'RH_12h', 'RH_15h', 'Tmax', 'Tmean',
    'dewpoint', 'VP', 'wind', 'VPD',
)
# feature: CDS variable, selector key/value, NetCDF variable, units
SPECS = {
    'precip_24h': ('precipitation_flux', None, None, 'Precipitation_Flux', 'mm d-1'),
    'Tmax': ('2m_temperature', 'statistic', '24_hour_maximum', 'Temperature_Air_2m_Max_24h', 'K'),
    'Tmean': ('2m_temperature', 'statistic', '24_hour_mean', 'Temperature_Air_2m_Mean_24h', 'K'),
    'dewpoint': ('2m_dewpoint_temperature', 'statistic', '24_hour_mean', 'Dew_Point_Temperature_2m_Mean_24h', 'K'),
    'VP': ('vapour_pressure', 'statistic', '24_hour_mean', 'Vapour_Pressure_Mean_24h', 'hPa'),
    'wind': ('10m_wind_speed', 'statistic', '24_hour_mean', 'Wind_Speed_10m_Mean_24h', 'm s-1'),
    **{f'RH_{h}h': ('2m_relative_humidity', 'time', f'{h}_00', f'Relative_Humidity_2m_{h}h', '%') for h in ('06', '09', '12', '15')},
}
WINDOWS = {'precip_3d': 3, 'precip_1w': 7, 'precip_4w': 28, 'precip_12w': 84}


def filename(aoi_name, day):
    return f'{aoi_name}_{day}_{FILE_SUFFIX}'


def required_dates(image_dates):
    days = {date.fromisoformat(str(d)) for d in image_dates}
    rain = {d - timedelta(days=k) for d in days for k in range(85)}
    return {name: rain if name == 'precip_24h' else days for name in SPECS}


def fetch_constraints():
    """Discover the current constraints URL; never assume a fixed publication lag."""
    url = f'https://cds.climate.copernicus.eu/api/catalogue/v1/collections/{DATASET}'
    response = requests.get(url, timeout=60)
    response.raise_for_status()
    links = [link['href'] for link in response.json()['links'] if link['rel'] == 'constraints']
    if len(links) != 1:
        raise RuntimeError('Cannot discover AgERA5 availability constraints')
    response = requests.get(links[0], timeout=60)
    response.raise_for_status()
    return response.json()


def available_dates(image_dates, constraints=None):
    """Intersect all requested selectors AND each preceding rainfall day."""
    if not image_dates:
        return []
    constraints = fetch_constraints() if constraints is None else constraints
    need = required_dates(image_dates)
    available = {}
    for feature, (variable, key, value, _, _) in SPECS.items():
        rows = [r for r in constraints if VERSION in r.get('version', [])
                and variable in r.get('variable', [])
                and (key is None or value in r.get(key, []))]
        available[feature] = {d for d in need[feature] if any(
            f'{d.year:04d}' in r.get('year', []) and f'{d.month:02d}' in r.get('month', [])
            and f'{d.day:02d}' in r.get('day', []) for r in rows)}
    return sorted(str(d) for d in need['Tmean'] if all(
        d in available[f] for f in SPECS if f != 'precip_24h') and all(
        d - timedelta(days=k) in available['precip_24h'] for k in range(85)))


def read_daily(path, feature, day):
    """Validate CF time, variable, units and grid; retain missing pixels."""
    with xr.open_dataset(path) as ds:
        name, units = SPECS[feature][3:]
        if name not in ds or ds[name].attrs.get('units') != units:
            raise ValueError(f'{path}: expected {name} in {units}')
        da = ds[name]
        renames = {k: v for k, v in [('latitude', 'lat'), ('longitude', 'lon')] if k in da.dims}
        da = da.rename(renames)
        if set(da.dims) != {'time', 'lat', 'lon'} or da.sizes['time'] != 1:
            raise ValueError(f'{path}: expected one daily lat/lon grid')
        actual = np.datetime64(da.time.values[0], 'D')
        if actual != np.datetime64(str(day)):
            raise ValueError(f'{path}: unexpected date {actual}, wanted {day}')
        da = da.isel(time=0).sortby('lat', ascending=False).sortby('lon').transpose('lat', 'lon')
        lat, lon = np.round(da.lat.values, 6), np.round(da.lon.values, 6)
        if len(lat) < 2 or len(lon) < 2 or not np.allclose(np.diff(lat), -.1) or not np.allclose(np.diff(lon), .1):
            raise ValueError(f'{path}: expected regular 0.1 degree grid')
        return da.values.astype(np.float32), lat, lon


def plan_requests(needed, area):
    """Only request missing dates; keep year/month products unambiguous."""
    groups = defaultdict(lambda: defaultdict(set))
    for feature, days in needed.items():
        for day in days:
            groups[(SPECS[feature][0], day.year, day.month)][feature].add(day)
    result = []
    # Group selectors only when they have identical missing dates.
    for (variable, year, month), features in sorted(groups.items()):
        by_days = defaultdict(list)
        for feature, days in features.items():
            by_days[tuple(sorted(days))].append(feature)
        for days, names in by_days.items():
            request = dict(variable=variable, version=VERSION, year=[str(year)],
                           month=[f'{month:02d}'], day=[f'{d.day:02d}' for d in days], area=list(area))
            for feature in names:
                _, key, value, _, _ = SPECS[feature]
                if key:
                    request.setdefault(key, []).append(value)
            result.append((request, names, days))
    return result


def download_request(spec, cache, api_key='', overwrite=False):
    import cdsapi
    request, features, days = spec
    token = sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
    archive = cache / 'requests' / (token + '.zip')
    archive.parent.mkdir(parents=True, exist_ok=True)
    if overwrite or not archive.exists():
        print(f"[AgERA5] {request['variable']} {request['year'][0]}-{request['month'][0]}: {len(days)} days", flush=True)
        partial = archive.with_suffix('.part')
        client = cdsapi.Client(timeout=120, quiet=True, **({'key': api_key} if api_key else {}))
        client.retrieve(DATASET, request).download(str(partial))
        with zipfile.ZipFile(partial) as z:
            if z.testzip() is not None:
                raise ValueError('Corrupt AgERA5 archive')
        partial.replace(archive)
        archive.with_suffix('.json').write_text(json.dumps(request, indent=2), encoding='utf-8')
    return spec, archive


def install_archive(spec, archive, cache):
    """Validate downloads serially (NetCDF/HDF5 readers are not thread safe)."""
    request, features, days = spec
    expected = {(feature, day) for feature in features for day in days}
    found = set()
    staged = []
    stage = cache / 'requests' / (archive.stem + '_daily')
    stage.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as z:
        for member in z.namelist():
            if not member.endswith('.nc'):
                continue
            path = stage / Path(member).name
            path.write_bytes(z.read(member))
            with xr.open_dataset(path) as ds:
                names = [feature for feature in features if SPECS[feature][3] in ds]
                if len(names) != 1 or 'time' not in ds or ds.sizes['time'] != 1:
                    raise ValueError(f'{member}: unexpected AgERA5 contents')
                feature = names[0]
                day = date.fromisoformat(str(np.datetime64(ds.time.values[0], 'D')))
            if (feature, day) not in expected or (feature, day) in found:
                raise ValueError(f'{member}: unexpected or duplicate daily field')
            read_daily(path, feature, day)
            found.add((feature, day))
            staged.append((path, feature, day))
    if found != expected:
        raise ValueError(f'{archive}: missing {len(expected - found)} daily fields')
    # Publish only a completely validated request. Filenames are controlled here.
    for path, feature, day in staged:
        target = cache / feature / (str(day) + '.nc')
        target.parent.mkdir(exist_ok=True)
        path.replace(target)


def rainfall_windows(days, read_rain, output_dates):
    """Streaming complete preceding-day sums; NaNs expire with their window."""
    queues = {n: deque() for n in WINDOWS.values()}
    totals, counts = {}, {}
    previous = None
    wanted = set(output_dates)
    for day in sorted(days):
        if previous is None or day != previous + timedelta(days=1):
            for q in queues.values():
                q.clear()
            totals, counts = {}, {}
        rain = read_rain(day)
        if day in wanted:
            bands = {'precip_24h': rain}
            for name, n in WINDOWS.items():
                if len(queues[n]) != n:
                    raise ValueError(f'{day}: missing antecedent rainfall days')
                bands[name] = np.where(counts[n] == n, totals[n], np.nan).astype(np.float32)
            yield day, bands
        valid = np.isfinite(rain)
        clean = np.where(valid, rain, 0).astype(np.float64)
        for n, q in queues.items():
            if n not in totals:
                totals[n] = np.zeros(rain.shape, dtype=np.float64)
                counts[n] = np.zeros(rain.shape, dtype=np.int16)
            if len(q) == n:
                old, old_valid = q.popleft()
                totals[n] -= old
                counts[n] -= old_valid
            q.append((clean, valid))
            totals[n] += clean
            counts[n] += valid
        previous = day


def weather_tif_is_complete(path, require_all_finite=False, *, expected_date=None, expected_bounds=None):
    try:
        with rasterio.open(path) as src:
            import re
            tagged_day=src.tags().get('WEATHER_DATE','')
            date.fromisoformat(tagged_day)
            match=re.search(r'_(\d{4}-\d{2}-\d{2})_',Path(path).name)
            wanted=expected_date or (match.group(1) if match else None)
            if wanted is not None and tagged_day!=str(wanted):return False
            if expected_bounds is not None:
                w,s,e,n=map(float,expected_bounds)
                if not (src.bounds.left<=w and src.bounds.bottom<=s and src.bounds.right>=e and src.bounds.top>=n):return False
            if (src.tags().get('LFMC_WEATHER_FEATURE_VERSION') != WEATHER_FEATURE_VERSION
                    or src.tags().get('LFMC_WEATHER_SOURCE') != WEATHER_SOURCE
                    or src.descriptions != REQUIRED_OUTPUT_BANDS or src.crs != rasterio.crs.CRS.from_epsg(4326)
                    or not np.allclose(src.res, (.1, .1))):
                return False
            for band in src.read(masked=True):
                valid = np.isfinite(band.filled(np.nan))
                if not valid.any() or (require_all_finite and not valid.all()):
                    return False
        return True
    except (OSError, ValueError, rasterio.errors.RasterioError):
        return False


def prepare(aoi_shapefile, image_dates, output_dir, aoi_name='', api_key='',
            overwrite=False, overwrite_nc=False, ref_tif=None, max_workers=2,
            aoi_bbox=None, nc_cache_dir=None):
    """Download and build exactly the requested dates, failing on unavailable inputs."""
    if ref_tif is not None:
        raise ValueError('AgERA5 weather must remain on its native grid')
    days = sorted({str(date.fromisoformat(str(d))) for d in image_dates})
    if not days:
        return {}
    aoi_name = aoi_name or Path(aoi_shapefile).stem
    if Path(aoi_name).name != aoi_name or aoi_name in ('.', '..'):
        raise ValueError('Invalid AOI name')
    out_dir = Path(output_dir) / TIF_SUBDIR / aoi_name
    out_dir.mkdir(parents=True, exist_ok=True)
    result = {d: str(out_dir / filename(aoi_name, d)) for d in days}
    if aoi_shapefile:
        import geopandas as gpd
        frame = gpd.read_file(aoi_shapefile)
        if frame.crs is None:
            raise ValueError('AOI shapefile has no CRS')
        bounds = frame.to_crs(4326).total_bounds
    elif aoi_bbox is not None:
        bounds = aoi_bbox
    else:
        if not overwrite and not overwrite_nc and all(weather_tif_is_complete(result[d],expected_date=d) for d in days):
            return result  # Explicit cache-only call: no new geographic request.
        raise ValueError('Supply aoi_shapefile or aoi_bbox')
    w, s, e, n = map(float, bounds)
    if not (-180 <= w < e <= 180 and -90 <= s < n <= 90):
        raise ValueError('Invalid WGS84 bounds (dateline crossing needs separate areas)')
    missing = [d for d in days if overwrite or overwrite_nc or not weather_tif_is_complete(
        result[d],expected_date=d,expected_bounds=(w,s,e,n))]
    if not missing:return result
    allowed = set(available_dates(missing))
    if set(missing)-allowed:
        raise ValueError(f'AgERA5 required daily inputs unavailable for: {sorted(set(missing)-allowed)}')
    area = [min(90., math.ceil((n + .2)*10)/10), max(-180., math.floor((w - .2)*10)/10),
            max(-90., math.floor((s - .2)*10)/10), min(180., math.ceil((e + .2)*10)/10)]
    namespace = sha256(json.dumps([DATASET, VERSION, area]).encode()).hexdigest()[:20]
    cache = Path(nc_cache_dir or Path(output_dir) / NC_SUBDIR) / namespace
    cache.mkdir(parents=True, exist_ok=True)
    needed = required_dates(missing)
    uncached = {f: {d for d in ds if overwrite_nc or not (cache / f / (str(d) + '.nc')).exists()} for f, ds in needed.items()}
    planned = plan_requests(uncached, area)
    with ThreadPoolExecutor(max_workers=min(4, max(1, int(max_workers)))) as pool:
        # Keep all NetCDF inspection in this thread, outside the download workers.
        for spec, archive in pool.map(lambda spec: download_request(spec, cache, api_key, overwrite_nc), planned):
            install_archive(spec, archive, cache)
    grid = None
    def read(feature, day):
        nonlocal grid
        arr, lat, lon = read_daily(cache / feature / (str(day) + '.nc'), feature, day)
        if grid is None:
            grid = lat, lon
            if not (lon[0] - .05 <= w and lon[-1] + .05 >= e and lat[-1] - .05 <= s and lat[0] + .05 >= n):
                raise ValueError('AgERA5 grid does not cover the AOI')
        elif not (np.array_equal(lat, grid[0]) and np.array_equal(lon, grid[1])):
            raise ValueError(f'{feature} {day}: inconsistent AgERA5 grid')
        return arr
    for day, bands in rainfall_windows(needed['precip_24h'], lambda d: read('precip_24h', d), {date.fromisoformat(d) for d in missing}):
        for feature in SPECS:
            if feature != 'precip_24h':
                bands[feature] = read(feature, day)
        bands['VPD'] = compute_vpd_from_tmean(bands['Tmean'], bands['VP'])
        if any(not np.isfinite(bands[f]).any() for f in REQUIRED_OUTPUT_BANDS):
            raise ValueError(f'{day}: a required weather band is entirely missing')
        lat, lon = grid
        path = Path(result[str(day)])
        partial = path.with_suffix('.tmp.tif')
        with rasterio.open(partial, 'w', driver='GTiff', height=len(lat), width=len(lon),
                           count=15, dtype='float32', crs='EPSG:4326', nodata=np.nan,
                           transform=from_origin(lon[0] - .05, lat[0] + .05, .1, .1),
                           compress='deflate', predictor=3) as dst:
            for i, feature in enumerate(REQUIRED_OUTPUT_BANDS, 1):
                dst.write(bands[feature].astype(np.float32), i)
                dst.set_band_description(i, feature)
            dst.update_tags(LFMC_WEATHER_FEATURE_VERSION=WEATHER_FEATURE_VERSION,
                            LFMC_WEATHER_SOURCE=WEATHER_SOURCE, CDS_DATASET=DATASET,
                            CDS_VERSION=VERSION, WEATHER_DATE=str(day),
                            PRECIP_WINDOWS='preceding 3/7/28/84 local days; sample day excluded',
                            CDS_AREA=json.dumps(area))
        if not weather_tif_is_complete(partial,expected_date=str(day),expected_bounds=(w,s,e,n)):
            raise RuntimeError(f'Invalid AgERA5 output: {partial}')
        partial.replace(path)
        print(f'[AgERA5] Built {path.name}', flush=True)
    return result
