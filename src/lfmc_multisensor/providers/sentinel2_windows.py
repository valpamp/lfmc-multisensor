"""Acquire native Sentinel-2 L2A windows from CDSE object storage."""

import hashlib
from io import BytesIO
import json
from math import ceil, floor
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from uuid import UUID

import rasterio
from rasterio.warp import transform_bounds
from rasterio.windows import Window, bounds as window_bounds, from_bounds

from ..naming import validate_label
from .cdse import s3_filesystem
from .http import ProviderError, file_sha256


BANDS = (('_B11_20m.jp2', 20), ('_B04_10m.jp2', 10), ('_B8A_20m.jp2', 20), ('_SCL_20m.jp2', 20))


def _asset_paths(objects):
    assets = []
    for suffix, resolution in (*BANDS, ('/MTD_MSIL2A.xml', None)):
        matches = [key for key in objects if key.endswith(suffix)]
        if len(matches) != 1:
            raise ProviderError(f'Expected a unique Sentinel-2 asset for {suffix}')
        assets.append((matches[0], resolution))
    masks = [key for key in objects if key.endswith('/MSK_CLASSI_B00.jp2')]
    if masks:
        resolution = 60
    else:
        masks = [key for key in objects if 'MSK_CLOUDS' in Path(key).name and key.endswith('.gml')]
        resolution = None
    if len(masks) != 1:
        raise ProviderError('Expected a unique native Sentinel-2 opaque/cirrus cloud mask')
    return assets + [(masks[0], resolution)]


def _extent(ds, bounds):
    if not ds.crs or ds.res != (20.0, 20.0) or ds.transform.b or ds.transform.d:
        raise ProviderError('Expected a north-up Sentinel-2 B11 grid at 20 m')
    native = transform_bounds('EPSG:4326', ds.crs, *bounds, densify_pts=21)
    window = from_bounds(*native, ds.transform)
    right = window.col_off + window.width
    bottom = window.row_off + window.height
    if min(window.col_off, window.row_off) < 0 or right > ds.width or bottom > ds.height:
        raise ProviderError('AOI extends outside the selected Sentinel-2 tile; select a covering scene')
    # Preserve the native 60 m QA lattice, with one cell of halo.
    left = max(0, 3 * (floor(window.col_off / 3) - 1))
    top = max(0, 3 * (floor(window.row_off / 3) - 1))
    right = min(ds.width, 3 * (ceil(right / 3) + 1))
    bottom = min(ds.height, 3 * (ceil(bottom / 3) + 1))
    return window_bounds(Window(left, top, right - left, bottom - top), ds.transform)


def _write_cutout(ds, extent, crs, resolution, output):
    if ds.crs != crs or ds.res != (resolution, resolution) or ds.transform.b or ds.transform.d:
        raise ProviderError('Unexpected projection or resolution for a Sentinel-2 asset')
    window = from_bounds(*extent, ds.transform)
    coordinates = (window.col_off, window.row_off, window.width, window.height)
    snapped = tuple(round(value) for value in coordinates)
    if any(abs(value - rounded) > 1e-6 for value, rounded in zip(coordinates, snapped)):
        raise ProviderError('Sentinel-2 asset does not align with the reference grid')
    left, top, width, height = snapped
    if min(left, top) < 0 or left + width > ds.width or top + height > ds.height:
        raise ProviderError('Sentinel-2 asset does not cover the requested window')
    window = Window(*snapped)
    pixels = ds.read(window=window)
    with rasterio.open(
        output, 'w', driver='GTiff', height=height, width=width, count=ds.count,
        dtype=ds.dtypes[0], crs=crs, transform=ds.window_transform(window),
        nodata=ds.nodata, compress='deflate',
    ) as dst:
        dst.write(pixels)
        dst.update_tags(native_grid_cutout='true')
        if resolution == 20 and all(value % 3 == 0 for value in snapped):
            dst.update_tags(native_60m_aligned='true')


def _verified_record(path, object_key, etag):
    receipt = path.with_suffix(path.suffix + '.receipt.json')
    if not path.is_file() or not receipt.is_file() or not etag:
        return None
    try:
        record = json.loads(receipt.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if isinstance(record, dict) and record.get('object') == object_key and record.get('etag') == etag and record.get('sha256') == file_sha256(path):
        return record
    return None


def _read_asset(fs, key, size, local, resolution, output, bounds, reference):
    if resolution is None:
        data = local.read_bytes() if local else fs.cat_file(key)
        if len(data) != size:
            raise ProviderError('Incomplete Sentinel-2 metadata or vector QA file')
        output.write_bytes(data)
        return reference, 0 if local else len(data)

    alias = Path(key).name
    received = 0
    last_update = monotonic()

    def opener(path, mode='rb'):
        if path == 'test':
            return BytesIO()
        if path != alias:
            raise FileNotFoundError(path)
        stream = fs.open(key, mode, size=size, block_size=128 * 1024, cache_type='blockcache')
        fetch = stream.cache.fetcher

        def count_range(start, end):
            nonlocal received, last_update
            data = fetch(start, end)
            received += len(data)
            now = monotonic()
            if now - last_update >= 2:
                print(f'CDSE: {received/1e6:.1f} MB transferred for the {alias} window.', flush=True)
                last_update = now
            return data

        stream.cache.fetcher = count_range
        return stream

    with rasterio.open(local) if local else rasterio.open(alias, opener=opener) as ds:
        if reference is None:
            reference = ds.crs, _extent(ds, bounds)
        crs, extent = reference
        _write_cutout(ds, extent, crs, resolution, output)
    return reference, received


def acquire_s2_windows(product, bounds, cache):
    """Cache the required native rasters around an AOI plus small SAFE metadata."""
    prefix = product['S3Path'].lstrip('/')
    product_name = validate_label(product['Name'])
    product_id = str(UUID(product['Id']))
    if not prefix.startswith('eodata/Sentinel-2/') or not prefix.endswith('/' + product_name):
        raise ProviderError('Unexpected CDSE Sentinel-2 object prefix')
    cache = Path(cache)
    fs = s3_filesystem()
    print('CDSE: locating Sentinel-2 assets for AOI window reads.', flush=True)
    try:
        objects = fs.find(prefix, detail=True)
        assets = _asset_paths(objects)
        identity = [(key, objects[key].get('ETag'), objects[key]['size']) for key, _ in assets]
        key = hashlib.sha256(json.dumps((bounds, identity)).encode()).hexdigest()[:20]
        folder = cache / 'windows' / product_id / key
        folder.mkdir(parents=True, exist_ok=True)
        reference = None
        entries = {}
        for (key, resolution), (_, etag, size) in zip(assets, identity):
            relative = Path(key.removeprefix(prefix + '/'))
            if not key.startswith(prefix + '/') or relative.is_absolute() or '..' in relative.parts:
                raise ProviderError('Invalid Sentinel-2 SAFE asset path')
            basename = relative.with_suffix('.tif').name if resolution else relative.name
            target = folder / basename
            record = _verified_record(target, key, etag)
            if record is None:
                full_file = cache / product_id / product_name / relative
                local = full_file if _verified_record(full_file, key, etag) else None
                activity = 'Cropping cached' if local else 'Reading AOI window from' if resolution else 'Downloading'
                print(f'CDSE: {activity} {relative.name}.', flush=True)
                # A private staging directory prevents simultaneous runs or a
                # cancelled transfer from exposing partial rasters to preparation.
                with TemporaryDirectory(prefix='s2-', dir=folder.parent) as scratch:
                    staged = Path(scratch) / basename
                    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN='EMPTY_DIR'):
                        reference, received = _read_asset(fs, key, size, local, resolution, staged, bounds, reference)
                    record = dict(object=key, etag=etag, sha256=file_sha256(staged), stored_bytes=staged.stat().st_size,
                                  source_bytes=size, downloaded_bytes=received)
                    staged_receipt = staged.with_suffix(staged.suffix + '.receipt.json')
                    staged_receipt.write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
                    staged.replace(target)
                    staged_receipt.replace(target.with_suffix(target.suffix + '.receipt.json'))
                entries[basename] = record
            else:
                print(f'CDSE: reusing verified {basename}.', flush=True)
                entries[basename] = dict(record, downloaded_bytes=0)
                if reference is None:
                    with rasterio.open(target) as ds:
                        reference = ds.crs, ds.bounds
        transferred = sum(entry['downloaded_bytes'] for entry in entries.values())
        full_size = sum(entry['source_bytes'] for entry in entries.values())
        print(f'CDSE: Sentinel-2 windows ready. Downloaded {transferred/1e6:.1f} MB; source assets total {full_size/1e6:.1f} MB.', flush=True)
        return folder, dict(method='CDSE S3 native AOI windows', downloaded_bytes=transferred, source_bytes=full_size, assets=entries)
    except ProviderError:
        raise
    except Exception as exc:
        raise ProviderError(f'Sentinel-2 windowed S3 acquisition failed ({type(exc).__name__}); check access and retry') from None
