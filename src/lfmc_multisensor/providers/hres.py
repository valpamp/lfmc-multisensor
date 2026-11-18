"""Pinned appendix historical HRES reconstruction; explicitly approximate."""
import math
from pathlib import Path
import numpy as np
import rasterio
from rasterio.transform import from_origin
import xarray as xr
from .http import download

SOURCE_SHA='f2942ecc7d6b48e42e77361748ee3fc01f8f749d513fe72d4709836547d24897'
SOURCE_URL='https://osdf-director.osg-htc.org/ncar/gdex/d113001/ec.oper.an.sfc/201604/ec.oper.an.sfc.128_129_z.regn1280sc.20160401.nc'


def prepare_reference(bounds,cache,output):
    # Uses the fixed appendix reconstruction, not the requested date's HRES
    # terrain. General date/cycle equivalence is unverified and never asserted.
    cache=Path(cache)
    source=download(SOURCE_URL,cache/'hres_20160401.nc',expected_sha256=SOURCE_SHA)
    w,s,e,n=bounds
    longitude=np.round(np.arange(math.floor((w-.2)*10)/10,math.ceil((e+.2)*10)/10+.05,.1),1)
    latitude=np.round(np.arange(math.ceil((n+.2)*10)/10,math.floor((s-.2)*10)/10-.05,-.1),1)
    with xr.open_dataset(source) as ds:
        if ds.Z.attrs.get('ecmwf_parameter')!=129 or ds.Z.attrs.get('units')!='m**2 s**-2': raise ValueError('Wrong HRES source parameter')
        values=ds.Z.isel(time=0).interp(latitude=xr.DataArray(latitude,dims='latitude'),longitude=xr.DataArray(longitude%360,dims='longitude'),method='linear').values/9.80665
    if not np.isfinite(values).all(): raise ValueError('HRES reference has missing nodes')
    with rasterio.open(output,'w',driver='GTiff',height=len(latitude),width=len(longitude),count=1,dtype='float32',crs='EPSG:4326',transform=from_origin(longitude[0]-.05,latitude[0]+.05,.1,.1),nodata=np.nan,compress='deflate') as dst:
        dst.write(values.astype('float32'),1);dst.set_band_description(1,'elevation')
        dst.update_tags(source_sha256=SOURCE_SHA,reconstruction_method='bilinear',exact_agera5_reference_verified='false')
    return dict(source=SOURCE_URL,source_sha256=SOURCE_SHA,exact_agera5_reference_verified=False,method='historical-HRES bilinear reconstruction, geopotential / 9.80665')
