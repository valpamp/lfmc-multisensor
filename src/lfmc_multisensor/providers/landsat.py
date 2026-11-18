"""USGS Collection-2 L2 assets via the public Planetary Computer STAC mirror.

Temporary SAS links exist only in memory; provenance stores unsigned URLs.
"""
from datetime import date,timedelta
import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse

import fsspec
import rasterio
import requests
from .http import ProviderError,request,file_sha256
from .cloud import annotate
from ..config import LANDSAT_COMBINED
from ..products import native_window

STAC='https://planetarycomputer.microsoft.com/api/stac/v1'
SIGN='https://planetarycomputer.microsoft.com/api/sas/v1/sign'
ASSETS=('red','nir08','swir16','qa_pixel','qa_radsat')
PLATFORMS = {'landsat-8': ('L8', 'LC08_'), 'landsat-9': ('L9', 'LC09_')}


def sensor_for_item(item):
    return PLATFORMS[item['properties']['platform']][0]


def discover(config):
    platforms = [name for name, (sensor, _) in PLATFORMS.items() if config.sensor in (sensor, LANDSAT_COMBINED)]
    platform_query = {'in': platforms} if config.sensor == LANDSAT_COMBINED else {'eq': platforms[0]}
    body=dict(collections=['landsat-c2-l2'],bbox=list(config.bounds),
        datetime=f'{config.search_start}/{config.search_end_inclusive}',limit=100,
        query={'platform':platform_query})
    session=requests.Session();response=request(session,'POST',STAC+'/search',json=body)
    data=response.json();response.close();items=[]
    while True:
        for item in data.get('features',[]):
            props=item['properties']
            platform = props.get('platform')
            if platform not in platforms or not config.day<=props['datetime'][:10]<=config.last_day:continue
            if not item['id'].startswith(PLATFORMS[platform][1]) or not item['id'].endswith('_02_T1'):continue
            if config.tile and '_'+config.tile+'_' not in item['id']:continue
            if not all(k in item['assets'] for k in ASSETS):raise ProviderError('Landsat item lacks required SR/QA assets')
            items.append(item)
        next_link=next((l for l in data.get('links',[]) if l.get('rel')=='next'),None)
        if not next_link:break
        url=next_link['href']
        if urlparse(url).hostname!='planetarycomputer.microsoft.com':raise ProviderError('Unexpected STAC continuation host')
        response=request(session,next_link.get('method','GET'),url,**({'json':next_link.get('body',{})} if next_link.get('method')=='POST' else {}))
        data=response.json();response.close()
    return annotate(items,config,'STAC eo:cloud_cover',lambda item:item['properties'].get('eo:cloud_cover'))


def sign(href):
    if urlparse(href).scheme!='https' or not urlparse(href).hostname.endswith('.blob.core.windows.net'):
        raise ProviderError('Expected an HTTPS Planetary Computer Azure asset')
    response=request(requests.Session(),'GET',SIGN,params={'href':href},headers={'Cache-Control':'no-cache'})
    try:signed=response.json()['href']
    except (ValueError,KeyError):raise ProviderError('Asset signing returned no readable URL') from None
    finally:response.close()
    if urlparse(signed).hostname!=urlparse(href).hostname:raise ProviderError('Unexpected signed-asset host')
    return signed


def acquire(item,bounds,cache):
    key=hashlib.sha256(json.dumps(dict(id=item['id'],bounds=bounds),sort_keys=True).encode()).hexdigest()[:20]
    folder=Path(cache)/key;folder.mkdir(parents=True,exist_ok=True)
    fs=fsspec.filesystem('http',skip_instance_cache=True);paths={};reference=None
    for name in ASSETS:
        asset=item['assets'][name];url=asset['href']
        if '?' in url:raise ProviderError('Catalogue assets must be unsigned for durable provenance')
        path=folder/(name+'.tif');receipt=path.with_suffix('.receipt.json')
        cached=False
        if path.exists() and receipt.exists():
            record=json.loads(receipt.read_text())
            cached=record.get('url')==url and record.get('sha256')==file_sha256(path)
        if not cached:
            signed=sign(url)
            alias=name+'.tif'
            def opener(p,mode='rb'):
                if not str(p).endswith(alias):raise FileNotFoundError
                return fs.open(signed,mode,block_size=1024*1024)
            try:
                with rasterio.open(alias,opener=opener) as ds:
                    if ds.count!=1 or ds.res!=(30.,30.):raise ProviderError('Expected single-band native Landsat 30m assets')
                    grid=(str(ds.crs),tuple(ds.transform),ds.width,ds.height)
                    if reference is None:reference=grid
                    if grid!=reference:raise ProviderError('Landsat assets do not share one native grid')
                    win=native_window(bounds,ds.transform,ds.crs,ds.width,ds.height)
                    values=ds.read(1,window=win);profile=ds.profile.copy()
                    profile.update(driver='GTiff',height=values.shape[0],width=values.shape[1],transform=ds.window_transform(win),compress='deflate')
                    partial=path.with_suffix('.part.tif')
                    with rasterio.open(partial,'w',**profile) as dst:dst.write(values,1);dst.update_tags(source_url=url,source_id=item['id'])
                    partial.replace(path)
            except ProviderError:raise
            except Exception as exc:
                # Underlying HTTP/GDAL errors can include the signed URL.
                raise ProviderError(f'Landsat COG access failed ({type(exc).__name__}); retry discovery/signing') from None
            receipt.write_text(json.dumps(dict(url=url,source_id=item['id'],sha256=file_sha256(path)),indent=2)+'\n')
        paths[name]=path
    return paths
