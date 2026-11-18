"""Landsat 8/9 C2 L2 native optical predictors and published QA screening."""
import numpy as np
import rasterio
from .products import write_optical
from .providers.http import file_sha256


def qa_valid(qa_pixel,qa_radsat):
    qa=np.asarray(qa_pixel,dtype='uint16');sat=np.asarray(qa_radsat,dtype='uint16')
    # Published QA_PIXEL bits 1..5, plus intrinsic C2 fill (bit 0/DN zero).
    return ((qa & 0b111111)==0)&(sat==0)


def decode_reflectance(raw):
    return np.where(np.asarray(raw)!=0,np.asarray(raw,float)*.0000275-.2,np.nan)


def prepare_assets(paths,output,*,item,sensor):
    expected_platform={'L8':'landsat-8','L9':'landsat-9'}.get(sensor)
    if expected_platform is None or item['properties']['platform']!=expected_platform:
        raise ValueError('Landsat spacecraft differs from requested calibrated domain')
    reference=None;values={}
    for name in ['red','nir08','swir16','qa_pixel','qa_radsat']:
        if name in ['red','nir08','swir16']:
            band_metadata=item['assets'][name].get('raster:bands',[])
            if len(band_metadata)!=1:raise ValueError('Landsat SR requires one declared raster band with scale/offset')
            metadata=band_metadata[0]
            if metadata.get('scale')!=.0000275 or metadata.get('offset')!=-.2 or metadata.get('nodata')!=0:
                raise ValueError('Unexpected Landsat C2 SR metadata scale/offset/fill')
        with rasterio.open(paths[name]) as ds:
            grid=(ds.crs,ds.transform,ds.width,ds.height)
            if reference is None:reference=grid
            if grid!=reference or ds.res!=(30.,30.):raise ValueError('Landsat prepared assets must retain one native 30m grid')
            values[name]=ds.read(1)
    clear=qa_valid(values['qa_pixel'],values['qa_radsat'])
    bands=[decode_reflectance(values[k]) for k in ['red','nir08','swir16']]
    complete=clear&np.isfinite(np.stack(bands)).all(axis=0)
    write_optical(output,[np.where(complete,v,np.nan) for v in bands],reference[1],reference[0],
        product='landsat_c2l2',sensor=sensor,source_id=item['id'],acquisition_time=item['properties']['datetime'],
        scale=.0000275,offset=-.2,qa='QA_PIXEL bits 1-5 clear, intrinsic fill masked, QA_RADSAT==0; no extra suitability mask')
    return dict(product='landsat_c2l2',sensor=sensor,source_id=item['id'],acquisition_time=item['properties']['datetime'],
                native_asset_crops_sha256={name:file_sha256(path) for name,path in paths.items()},
                scale=.0000275,offset=-.2,native_spacing_m=30,valid_optical_pixels=int(complete.sum()))
