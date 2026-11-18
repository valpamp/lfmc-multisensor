"""Shared native-grid crop and prepared optical output helpers."""
import math
from pathlib import Path
import numpy as np
import rasterio
from rasterio.warp import transform_bounds
from rasterio.windows import from_bounds,Window


def native_window(bounds,transform,crs,width,height):
    native=transform_bounds('EPSG:4326',crs,*bounds,densify_pts=41)
    win=from_bounds(*native,transform)
    c0=math.floor(win.col_off);r0=math.floor(win.row_off)
    c1=math.ceil(win.col_off+win.width);r1=math.ceil(win.row_off+win.height)
    if c0<0 or r0<0 or c1>width or r1>height:
        raise ValueError('AOI crosses the selected product tile/scene boundary; split AOI or select a covering scene')
    return Window(c0,r0,c1-c0,r1-r0)


def write_optical(output,values,transform,crs,**tags):
    values=np.asarray(values,dtype='float32')
    if values.ndim!=3 or values.shape[0]!=3:raise ValueError('Need three optical bands')
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
    partial=output.with_suffix('.part.tif')
    with rasterio.open(partial,'w',driver='GTiff',width=values.shape[2],height=values.shape[1],count=3,dtype='float32',
                       crs=crs,transform=transform,nodata=np.nan,compress='deflate') as dst:
        dst.write(values)
        for i,name in enumerate(['red','nir','swir1'],1):dst.set_band_description(i,name)
        dst.update_tags(**{k:str(v) for k,v in tags.items()})
    partial.replace(output)
