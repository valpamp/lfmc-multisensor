"""Automatic WorldCover and ALOS AW3D30 inputs, with explicit source versions."""
from functools import lru_cache
import gzip
from hashlib import sha256
from importlib.resources import files
from io import BytesIO
import json
import math
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urljoin
import zipfile

import aiohttp
import fsspec
import numpy as np
from pyproj import Geod
import rasterio
from rasterio.merge import merge
from rasterio.windows import from_bounds
from rasterio.warp import reproject,Resampling,transform_bounds
import requests

from .http import ProviderError, file_sha256, request

WC_CODES=[10,20,30,40,50,60,70,80,90,95,100]


def tile_name(lat,lon):
    return f"{'N' if lat>=0 else 'S'}{abs(lat):02d}{'E' if lon>=0 else 'W'}{abs(lon):03d}"


def worldcover(bounds,year,target,output,cache):
    if year not in (2020,2021): raise ValueError("WorldCover year must be 2020 or 2021")
    version='v100' if year==2020 else 'v200';cache=Path(cache);cache.mkdir(parents=True,exist_ok=True)
    # Cover complete snapped output pixels, not only the unsnapped user AOI.
    from rasterio.transform import array_bounds
    bounds=transform_bounds(target['crs'],'EPSG:4326',*array_bounds(target['height'],target['width'],target['transform']),densify_pts=21)
    w,s,e,n=bounds;files=[];urls=[]
    for lat in range(math.floor(s/3)*3,math.ceil(n/3)*3,3):
        for lon in range(math.floor(w/3)*3,math.ceil(e/3)*3,3):
            name=f"ESA_WorldCover_10m_{year}_{version}_{tile_name(lat,lon)}_Map.tif"
            url=f"https://esa-worldcover.s3.eu-central-1.amazonaws.com/{version}/{year}/map/{name}"
            local=cache/name
            # Materialize only the necessary native-grid range-read crop, not a global tile.
            local=local.with_name(sha256(repr(tuple(bounds)).encode()).hexdigest()[:16]+'_'+name)
            receipt=local.with_suffix('.receipt.json')
            cached=False
            if local.exists() and receipt.exists():
                record=json.loads(receipt.read_text())
                cached=record.get('url')==url and record.get('sha256')==file_sha256(local)
            if not cached:
                fs=fsspec.filesystem('http',skip_instance_cache=True)
                def opener(path,mode='rb'):
                    if not str(path).endswith(name): raise FileNotFoundError
                    return fs.open(url,mode,block_size=1024*1024)
                with rasterio.open(name,opener=opener) as ds:
                    win=from_bounds(max(w,lon),max(s,lat),min(e,lon+3),min(n,lat+3),ds.transform)
                    c0=math.floor(win.col_off);r0=math.floor(win.row_off);c1=math.ceil(win.col_off+win.width);r1=math.ceil(win.row_off+win.height)
                    win=rasterio.windows.Window(c0,r0,c1-c0,r1-r0);arr=ds.read(1,window=win);profile=ds.profile.copy()
                    profile.update(width=arr.shape[1],height=arr.shape[0],transform=ds.window_transform(win),driver='GTiff')
                    partial=local.with_suffix('.part.tif')
                    with rasterio.open(partial,'w',**profile) as dst: dst.write(arr,1);dst.update_tags(source_url=url,source_version=version)
                    partial.replace(local)
                    receipt.write_text(json.dumps(dict(url=url,sha256=file_sha256(local)))+'\n')
            files.append(local);urls.append(url)
    from contextlib import ExitStack
    with ExitStack() as stack:
        datasets=[stack.enter_context(rasterio.open(p)) for p in files]
        values,tf=merge(datasets);crs=datasets[0].crs
    profile=target.copy();shape=(profile['height'],profile['width']);fractions=[]
    for code in WC_CODES:
        source=np.where(values[0]!=0,(values[0]==code).astype(float),np.nan)
        out=np.full(shape,np.nan)
        reproject(source,out,src_transform=tf,src_crs=crs,src_nodata=np.nan,dst_transform=profile['transform'],dst_crs=profile['crs'],dst_nodata=np.nan,resampling=Resampling.average)
        fractions.append(out)
    profile.update(driver='GTiff',count=len(WC_CODES),dtype='float32',nodata=np.nan,compress='deflate')
    with rasterio.open(output,'w',**profile) as dst:
        dst.write(np.stack(fractions).astype('float32'))
        for i,code in enumerate(WC_CODES,1): dst.set_band_description(i,f'wc_{code}')
        dst.update_tags(year=str(year),policy='fixed-year',routing='dominant area-weighted category; no training suitability mask')
    return dict(source='ESA WorldCover',year=year,version=version,policy='fixed-year',urls=urls,crops={p.name:file_sha256(p) for p in files})


AW3D30_COLLECTION = "JAXA.EORC_ALOS.PRISM_AW3D30.v4.1_global"
AW3D30_COG_ROOT = (
    f"https://s3.ap-northeast-1.wasabisys.com/je-pds/cog/v1/{AW3D30_COLLECTION}/2024-04/3/"
)
TERRAIN_HALO = .002


def _aw3d30_tile(lat, lon):
    return f"{'N' if lat >= 0 else 'S'}{abs(lat):03d}{'E' if lon >= 0 else 'W'}{abs(lon):03d}"


@lru_cache(maxsize=1)
def _aw3d30_v41_tiles():
    # April 2024 release membership from JAXA's public tile-version index.
    resource = files("lfmc_multisensor").joinpath("aw3d30_v41_tiles.txt.gz")
    return frozenset(gzip.decompress(resource.read_bytes()).decode("ascii").splitlines())


def _verified_terrain(path, **source):
    receipt = path.with_suffix(".receipt.json")
    try:
        record = json.loads(receipt.read_text(encoding="utf-8"))
        return (
            path.is_file()
            and all(record.get(key) == value for key, value in source.items())
            and record.get("sha256") == file_sha256(path)
        )
    except (FileNotFoundError, ValueError):
        return False


def _cache_terrain(target, write, **source):
    with TemporaryDirectory(dir=target.parent) as folder:
        tmp = Path(folder) / target.name
        write(tmp)
        receipt = tmp.with_suffix(".receipt.json")
        receipt.write_text(json.dumps(dict(source, sha256=file_sha256(tmp))) + "\n", encoding="utf-8")
        tmp.replace(target)
        receipt.replace(target.with_suffix(".receipt.json"))


def _aw3d30_cog(session, fs, lat, lon, extent, cache):
    def edge(value, positive, negative, digits):
        return f"{positive if value >= 0 else negative}{abs(value):0{digits}d}.00"

    longitude = f"{edge(lon, 'E', 'W', 3)}-{edge(lon + 1, 'E', 'W', 3)}"
    latitude = f"{edge(lat, 'N', 'S', 2)}-{edge(lat + 1, 'N', 'S', 2)}"
    item_url = f"{AW3D30_COG_ROOT}{longitude}/{latitude}.json"
    tile = _aw3d30_tile(lat, lon)
    bounds_key = sha256(repr(extent).encode("ascii")).hexdigest()[:20]
    target = cache / f"AW3D30_v4.1_{tile}_{bounds_key}.tif"
    if _verified_terrain(target, item=item_url, bounds=list(extent), complete_window=True):
        return target

    bbox = [lon, lat, lon + 1, lat + 1]
    try:
        with request(session, "GET", item_url) as response:
            item = response.json()
        asset = item["assets"]["DSM"]
        url = urljoin(item_url, asset["href"])
        if (
            item["bbox"] != bbox or item["collection"] != AW3D30_COLLECTION
            or asset["je:rasters"]["dn2value"] != {"slope": 1.0, "offset": 0.0}
            or not url.startswith(f"{AW3D30_COG_ROOT}{longitude}/")
        ):
            raise ValueError("Unexpected AW3D30 Earth API asset")

        def opener(path, mode="rb"):
            if path == "test":
                return BytesIO()
            if path != target.name:
                raise FileNotFoundError(path)
            return fs.open(url, mode, block_size=128 * 1024, cache_type="blockcache")

        with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR"):
            with rasterio.open(target.name, opener=opener) as src:
                if (
                    src.crs is None or src.crs.to_epsg() != 4326 or src.shape != (3600, 3600)
                    or src.count != 1 or src.dtypes[0] != "int16" or src.nodata != -9999
                    or not src.transform.is_rectilinear or src.transform.a <= 0 or src.transform.e >= 0
                    or not np.allclose(src.res, (1 / 3600, 1 / 3600), rtol=0, atol=1e-12)
                    or not np.allclose(src.bounds, bbox, rtol=0, atol=1e-10)
                ):
                    raise ValueError("Unexpected AW3D30 Earth API grid")
                win = from_bounds(*extent, src.transform)
                c0 = max(0, math.floor(win.col_off) - 1)
                r0 = max(0, math.floor(win.row_off) - 1)
                c1 = min(src.width, math.ceil(win.col_off + win.width) + 1)
                r1 = min(src.height, math.ceil(win.row_off + win.height) + 1)
                window = rasterio.windows.Window(c0, r0, c1 - c0, r1 - r0)
                pixels = src.read(1, window=window)
                # The Earth API can mask valid coastal elevations in the archive.
                if np.any(pixels == src.nodata):
                    raise ValueError("Earth API window contains missing elevations; use the release archive")
                profile = dict(
                    driver="GTiff", count=1, dtype=src.dtypes[0], nodata=src.nodata,
                    crs=src.crs, transform=src.window_transform(window),
                    width=pixels.shape[1], height=pixels.shape[0], compress="deflate",
                )
    except (aiohttp.ClientError, OSError, KeyError, ValueError, RuntimeError, rasterio.errors.RasterioError) as exc:
        raise ProviderError(f"AW3D30 Earth API could not supply the native DSM for {tile}: {exc}") from exc

    def write(path):
        with rasterio.open(path, "w", **profile) as dst:
            dst.write(pixels, 1)
            dst.update_tags(source_url=url, source_version="v4.1")

    _cache_terrain(target, write, url=url, item=item_url, bounds=extent, complete_window=True)
    return target


def _aw3d30_archive_url(lat, lon):
    south = lat // 5 * 5
    west = lon // 5 * 5
    block = f"{_aw3d30_tile(south, west)}_{_aw3d30_tile(south + 5, west + 5)}"
    return f"https://www.eorc.jaxa.jp/ALOS/aw3d30/data/release_v2404/{block}.zip"


def _aw3d30_archive(fs, url, target):
    with fs.open(url, "rb", block_size=1024 * 1024) as archive, zipfile.ZipFile(archive) as archive_zip:
        members = [entry for entry in archive_zip.namelist() if Path(entry).name == target.name]
        if len(members) != 1:
            raise ProviderError(f"AW3D30 archive has no unique DSM tile: {target.name}")
        member = archive_zip.getinfo(members[0])
        data = archive_zip.read(member)
    _cache_terrain(target, lambda path: path.write_bytes(data), url=url, member=member.filename, zip_crc=member.CRC)
    return target


def aw3d30(bounds, cache):
    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    w, s, e, n = bounds
    left, bottom, right, top = w - TERRAIN_HALO, s - TERRAIN_HALO, e + TERRAIN_HALO, n + TERRAIN_HALO
    tiles = [
        (lat, lon, _aw3d30_tile(lat, lon))
        for lat in range(math.floor(bottom), math.ceil(top))
        for lon in range(math.floor(left), math.ceil(right))
    ]
    versions = _aw3d30_v41_tiles()
    for _, _, tile in tiles:
        if tile not in versions:
            raise ProviderError(f"AW3D30 v4.1 unavailable for {tile}")

    user = os.environ.get("JAXA_USERNAME")
    password = os.environ.get("JAXA_PASSWORD")
    result = []
    fs = fsspec.filesystem("http", skip_instance_cache=True)
    with requests.Session() as session:
        for lat, lon, tile in tiles:
            url = _aw3d30_archive_url(lat, lon)
            target = cache / f"ALPSMLC30_{tile}_DSM.tif"
            if _verified_terrain(target, url=url):
                result.append(target)
                continue
            extent = (max(left, lon), max(bottom, lat), min(right, lon + 1), min(top, lat + 1))
            try:
                path = _aw3d30_cog(session, fs, lat, lon, extent, cache)
            except ProviderError as cog_error:
                auth = {"auth": aiohttp.BasicAuth(user, password)} if user and password else {}
                archive_fs = fsspec.filesystem("http", skip_instance_cache=True, **auth) if auth else fs
                try:
                    path = _aw3d30_archive(archive_fs, url, target)
                except (aiohttp.ClientError, OSError, RuntimeError, zipfile.BadZipFile) as archive_error:
                    raise ProviderError(
                        f"{cog_error}. JAXA's release archive is also unavailable for {tile}."
                    ) from archive_error
            result.append(path)
    return result


def terrain_derivatives(elevation,transform):
    """Four-neighbour percent slope/aspect on an AW3D geographic grid.

    Authalic-sphere neighbour distances; source borders stay no-data. This
    metric was identified against frozen terrain, not assumed from WGS84 CRS.
    """
    z=np.asarray(elevation,float);h,w=z.shape;row=np.arange(1,h-1);lat=transform.f+(row+.5)*transform.e
    geod=Geod(a=6371007.180918,b=6371007.180918);lon=np.full(lat.shape,transform.c+transform.a*.5)
    _,_,dx=geod.inv(lon-transform.a,lat,lon+transform.a,lat)
    _,_,dy=geod.inv(lon,lat+transform.e,lon,lat-transform.e)
    gx=(z[1:-1,2:]-z[1:-1,:-2])/np.abs(dx[:,None]);gy=(z[:-2,1:-1]-z[2:,1:-1])/np.abs(dy[:,None])
    # The saved workflow used float32 slope degrees before tan(degrees*pi/180).
    angle=np.degrees(np.arctan(np.hypot(gx,gy))).astype('float32').astype(float)
    slope=np.full(z.shape,np.nan);aspect=slope.copy();slope[1:-1,1:-1]=np.tan(angle*np.pi/180)*100
    aspect[1:-1,1:-1]=np.where((gx==0)&(gy==0),0,np.degrees(np.arctan2(-gx,-gy))%360)
    return slope,aspect


def prepare_terrain(paths,bounds,target,output):
    from contextlib import ExitStack
    with ExitStack() as stack:
        datasets=[stack.enter_context(rasterio.open(p)) for p in paths]
        if any(ds.crs.to_epsg()!=4326 for ds in datasets): raise ValueError('AW3D DSM must be geographic')
        cog_sources={p.name: url for p,ds in zip(paths,datasets) if (url:=ds.tags().get('source_url'))}
        w,s,e,n=bounds
        source_tf=datasets[0].transform;rx,ry=datasets[0].res
        # Preserve the source grid before derivatives; an arbitrary merge
        # origin would create a second nearest-neighbour resampling.
        left=source_tf.c+math.floor((w-TERRAIN_HALO-source_tf.c)/rx)*rx
        right=source_tf.c+math.ceil((e+TERRAIN_HALO-source_tf.c)/rx)*rx
        bottom=source_tf.f+math.floor((s-TERRAIN_HALO-source_tf.f)/ry)*ry
        top=source_tf.f+math.ceil((n+TERRAIN_HALO-source_tf.f)/ry)*ry
        data,tf=merge(datasets,bounds=(left,bottom,right,top),nodata=-9999)
    elevation=data[0].astype(float);elevation[elevation==-9999]=np.nan
    slope,aspect=terrain_derivatives(elevation,tf);arrays=[]
    from pyproj import Transformer
    rr,cc=np.indices((target['height'],target['width']),dtype=float)
    x,y=target['transform']*(cc+.5,rr+.5)
    lon,lat=Transformer.from_crs(target['crs'],4326,always_xy=True).transform(x,y)
    columns,rows=(~tf)*(lon,lat);columns=np.floor(columns).astype(int);rows=np.floor(rows).astype(int)
    inside=(rows>=0)&(rows<elevation.shape[0])&(columns>=0)&(columns<elevation.shape[1])
    for values in [elevation,slope,aspect]:
        out=np.full((target['height'],target['width']),np.nan)
        out[inside]=values[rows[inside],columns[inside]]
        arrays.append(out)
    profile={**target,'driver':'GTiff','dtype':'float32','count':3,'nodata':np.nan,'compress':'deflate'}
    with rasterio.open(output,'w',**profile) as dst:
        dst.write(np.stack(arrays).astype('float32'))
        for i,name in enumerate(['elevation','slope','aspect'],1):dst.set_band_description(i,name)
        dst.update_tags(source='JAXA AW3D30 release_v2404',slope_units='percent',aspect_units='degrees',terrain_method='four-neighbour authalic sphere 6371007.180918 m, source-grid derivatives then exact nearest centres')
    provenance = dict(source='JAXA AW3D30 release_v2404',tiles={p.name:file_sha256(p) for p in paths},terrain_method='four-neighbour authalic sphere, float32 slope degrees, exact nearest centres')
    if cog_sources:
        provenance['earth_api_assets'] = cog_sources
    return provenance
