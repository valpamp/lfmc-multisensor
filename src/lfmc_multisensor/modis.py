"""Terra MOD09A1 / Aqua MYD09A1 collection 6.1 native HDF4 preparation."""
from datetime import date,timedelta
from pathlib import Path
import re
import numpy as np
from rasterio.transform import from_origin
from rasterio.windows import transform as window_transform
from .viirs import SINU,state_valid as viirs_state_valid,decode_reflectance,scalar
from .products import native_window,write_optical
from .providers.http import file_sha256

DOMAINS={'MOD09A1':('modis','Terra MODIS','Terra'),
         'MYD09A1':('modis_aqua','Aqua MODIS','Aqua')}
BANDS=(3,4,1,2,6,7)  # blue, green, red, NIR, SWIR1, SWIR2


def state_valid(state):
    state=np.asarray(state,dtype='uint16')
    return viirs_state_valid(state)&(((state>>13)&1)==0)


def screen_reflectance(arrays,state):
    """All six sampler bands must pass, including non-predictor bands."""
    if len(arrays)!=6:raise ValueError('MODIS screening requires six reflectance bands')
    complete=state_valid(state)&np.isfinite(np.stack(arrays)).all(axis=0)
    return np.stack([np.where(complete,arrays[i],np.nan) for i in (2,3,4)]),complete


def metadata_value(text,key):
    matches=re.findall(rf'OBJECT\s*=\s*{key}\s+(.*?)END_OBJECT\s*=\s*{key}\b',text,re.S)
    if len(matches)!=1:raise ValueError(f'Missing/ambiguous MODIS metadata object: {key}')
    match=re.search(r'\bVALUE\s*=\s*(?:"([^"]*)"|([^\s]+))',matches[0])
    if not match:raise ValueError(f'Missing MODIS metadata value: {key}')
    return match[1] if match[1] is not None else match[2]


def prepare_hdf(path,bounds,output,*,item):
    from pyhdf.SD import SD,SDC
    path=Path(path);short,version=item['collection'].split('.')
    if short not in DOMAINS or version!='061':raise ValueError('Expected MOD09A1/MYD09A1.061')
    product,sensor,platform=DOMAINS[short]
    if path.stem!=item['id'] or not re.fullmatch(rf'{short}\.A\d{{7}}\.h\d{{2}}v\d{{2}}\.061\.\d+\.hdf',path.name) or item['sensor']!=sensor:
        raise ValueError('MODIS artifact identity differs from selected product/domain')
    if not item['composite_start']<=item['analysis_date']<item['composite_end_exclusive']:
        raise ValueError('MODIS analysis date outside explicit composite interval')
    source=SD(str(path),SDC.READ)
    try:
        attrs=source.attributes();core=attrs['CoreMetadata.0'];text=attrs['StructMetadata.0']
        expected={'LOCALGRANULEID':path.name,'SHORTNAME':short,'VERSIONID':'61',
                  'ASSOCIATEDPLATFORMSHORTNAME':platform,'ASSOCIATEDINSTRUMENTSHORTNAME':'MODIS',
                  'RANGEBEGINNINGDATE':item['composite_start'],
                  'RANGEENDINGDATE':str(date.fromisoformat(item['composite_end_exclusive'])-timedelta(days=1))}
        for key,value in expected.items():
            if metadata_value(core,key)!=value:raise ValueError(f'MODIS native identity mismatch: {key}')
        if attrs.get('identifier_product_doi')!='10.5067/MODIS/'+item['collection']:
            raise ValueError('MODIS native DOI differs from selected collection')
        def pair(name):
            match=re.search(rf'{name}\s*=\s*\(([^)]+)\)',text)
            if not match:raise ValueError(f'Missing HDF-EOS grid metadata: {name}')
            return tuple(map(float,match[1].split(',')))
        if 'Projection=GCTP_SNSOID' not in text or abs(pair('ProjParams')[0]-6371007.181)>1e-6:
            raise ValueError('Expected native MODIS sinusoidal sphere')
        left,top=pair('UpperLeftPointMtrs');right,bottom=pair('LowerRightMtrs')
        rx=(right-left)/2400;ry=(top-bottom)/2400
        if not np.isclose(rx,463.31271652777775,atol=1e-7,rtol=0) or not np.isclose(rx,ry,atol=1e-7,rtol=0):
            raise ValueError('Unexpected MODIS native spacing')
        tf=from_origin(left,top,rx,ry);win=native_window(bounds,tf,SINU,2400,2400)
        def read(name):
            ds=source.select(name)
            try:
                if ds.info()[2] != [2400,2400]:raise ValueError('Expected native MODIS 2400x2400 band')
                values=ds.get(start=(int(win.row_off),int(win.col_off)),count=(int(win.height),int(win.width)))
                return values,ds.attributes()
            finally:ds.endaccess()
        state,qa_attrs=read('sur_refl_state_500m')
        if scalar(qa_attrs,'_FillValue')!=65535:raise ValueError('Unexpected MODIS state fill marker')
        arrays=[decode_reflectance(*read(f'sur_refl_b{i:02d}')) for i in BANDS]
        values,complete=screen_reflectance(arrays,state)
    finally:source.end()
    write_optical(output,values,window_transform(win,tf),SINU,product=product,sensor=sensor,
        composite_start=item['composite_start'],composite_end_exclusive=item['composite_end_exclusive'],analysis_date=item['analysis_date'],
        nominal_resolution_m=500,actual_resolution_m=rx,qa='published MODIS state bits including adjacent cloud; all six 0<SR<1.6; no neighbourhood mask')
    return dict(product=product,sensor=sensor,source_id=item['id'],source_sha256=file_sha256(path),
                native_spacing_m=rx,valid_optical_pixels=int(complete.sum()),screened_bands=list(BANDS),scale_factor=.0001,fill_value=-28672,
                composite_start=item['composite_start'],composite_end_exclusive=item['composite_end_exclusive'],
                analysis_date=item['analysis_date'],selection_policy=item['selection_policy'])
