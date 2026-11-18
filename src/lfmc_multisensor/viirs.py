"""VNP09H1.002 native sinusoidal reflectance and published state-QA rules."""
from pathlib import Path
from datetime import date,timedelta
import re
import h5py
import numpy as np
from rasterio.crs import CRS
from rasterio.transform import from_origin
from rasterio.windows import transform as window_transform
from .products import native_window,write_optical
from .providers.http import file_sha256

SINU=CRS.from_string('+proj=sinu +R=6371007.181 +lon_0=0 +x_0=0 +y_0=0 +units=m +no_defs')


def state_valid(state):
    """Central product QA only; no training 3x3 count/suitability masks."""
    state=np.asarray(state,dtype='uint16')
    return ((state!=65535)&((state&3)==0)&(((state>>2)&1)==0)&(((state>>3)&7)!=0)
            &(((state>>6)&3)!=3)&(((state>>8)&3)<=1)&(((state>>10)&1)==0)
            &(((state>>11)&1)==0)&(((state>>12)&1)==0)&(((state>>15)&1)==0))


def scalar(attrs,key):
    if key not in attrs:raise ValueError(f'Missing VIIRS attribute: {key}')
    value=np.asarray(attrs[key]).ravel()
    if value.size!=1:raise ValueError(f'Expected scalar VIIRS attribute: {key}')
    return float(value[0])


def decode_reflectance(raw,attrs):
    if not np.isclose(scalar(attrs,'scale_factor'),.0001,rtol=0,atol=1e-11) or scalar(attrs,'add_offset')!=0:
        raise ValueError('Unvalidated VIIRS reflectance scale/offset')
    valid_range=np.asarray(attrs['valid_range']).ravel()
    if not np.array_equal(valid_range,[-100,16000]):raise ValueError('Unexpected VIIRS valid range')
    fill=scalar(attrs,'_FillValue')
    if fill!=-28672:raise ValueError('Unexpected VNP09H1.002 reflectance fill marker')
    good=(raw!=fill)&(raw>=valid_range[0])&(raw<=valid_range[1])
    values=np.asarray(raw,float)*.0001
    # Match the unified sampler's physical-reflectance screening, not all
    # provider-valid negative/zero values. Strict upper bound is intentional.
    return np.where(good&(values>0)&(values<1.6),values,np.nan)


def prepare_hdf(path,bounds,output,*,item):
    path=Path(path)
    if path.stem!=item['id'] or not path.name.startswith('VNP09H1.') or '.002.' not in path.name:
        raise ValueError('VIIRS artifact identity differs from selected VNP09H1.002 granule')
    with h5py.File(path) as source:
        def text_attr(name):
            if name not in source.attrs:raise ValueError(f'Missing VIIRS native identity field: {name}')
            value=np.asarray(source.attrs[name]).ravel()[0]
            return value.decode() if isinstance(value,bytes) else str(value)
        expected={'LocalGranuleID':path.name,'PlatformShortName':'SUOMI-NPP','SensorShortname':'VIIRS',
                  'ShortName':'VNP09H1','VersionID':'002','RangeBeginningDate':item['composite_start'],
                  'RangeEndingDate':str(date.fromisoformat(item['composite_end_exclusive'])-timedelta(days=1))}
        for key,value in expected.items():
            if text_attr(key)!=value:raise ValueError(f'VIIRS native metadata identity mismatch: {key}')
        if not item['composite_start']<=item['analysis_date']<item['composite_end_exclusive']:
            raise ValueError('VIIRS requested analysis date is outside the explicit composite interval')
        meta=source['HDFEOS INFORMATION/StructMetadata.0'][()]
        text=meta.decode() if isinstance(meta,bytes) else str(meta)
        def pair(name):
            match=re.search(rf'{name}\s*=\s*\(([^)]+)\)',text)
            if not match:raise ValueError(f'Missing HDF-EOS grid metadata: {name}')
            return tuple(map(float,match[1].split(',')))
        left,top=pair('UpperLeftPointMtrs');right,bottom=pair('LowerRightMtrs')
        if 'Projection=HE5_GCTP_SNSOID' not in text or abs(pair('ProjParams')[0]-6371007.181)>1e-6:
            raise ValueError('VIIRS requires the native documented sinusoidal sphere')
        grid=source['HDFEOS/GRIDS/VIIRS_Grid_500m_L3_2d/Data Fields']
        shape=grid['SurfReflect_I1'].shape
        if shape!=(2400,2400):raise ValueError('Expected native 2400x2400 VIIRS 500m tile')
        rx=(right-left)/2400;ry=(top-bottom)/2400
        if not np.isclose(rx,463.31271652777775,atol=1e-7,rtol=0) or not np.isclose(rx,ry,atol=1e-7,rtol=0):
            raise ValueError('Unexpected native VIIRS grid spacing')
        tf=from_origin(left,top,rx,ry);win=native_window(bounds,tf,SINU,2400,2400)
        rows=slice(int(win.row_off),int(win.row_off+win.height));cols=slice(int(win.col_off),int(win.col_off+win.width))
        if scalar(grid['SurfReflect_State_500m'].attrs,'_FillValue')!=65535:raise ValueError('Unexpected VIIRS state fill marker')
        state=grid['SurfReflect_State_500m'][rows,cols];clear=state_valid(state)
        arrays=[decode_reflectance(grid[f'SurfReflect_I{i}'][rows,cols],grid[f'SurfReflect_I{i}'].attrs) for i in [1,2,3]]
        complete=clear&np.isfinite(np.stack(arrays)).all(axis=0)
        values=np.stack([np.where(complete,v,np.nan) for v in arrays])
    write_optical(output,values,window_transform(win,tf),SINU,product='viirs',sensor='Suomi NPP VIIRS',
        composite_start=item['composite_start'],composite_end_exclusive=item['composite_end_exclusive'],analysis_date=item['analysis_date'],
        nominal_resolution_m=500,actual_resolution_m=rx,qa='published state bits; all three 0<reflectance<1.6; no neighbourhood suitability mask')
    return dict(product='viirs',sensor='Suomi NPP VIIRS',source_id=item['id'],native_spacing_m=rx,valid_optical_pixels=int(complete.sum()),
                source_sha256=file_sha256(path),scale_factor=.0001,fill_value=-28672,
                composite_start=item['composite_start'],composite_end_exclusive=item['composite_end_exclusive'],analysis_date=item['analysis_date'],selection_policy=item['selection_policy'])
