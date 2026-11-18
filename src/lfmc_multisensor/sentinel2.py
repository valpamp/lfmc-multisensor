"""Direct SAFE L2A preparation on the native B11 20 m support."""
import math
from pathlib import Path
import xml.etree.ElementTree as ET

import numpy as np
import rasterio
from rasterio.features import rasterize
from rasterio.warp import reproject,Resampling,transform_bounds
from rasterio.windows import Window,from_bounds

EXCLUDED_SCL=(0,1,3,7,8,9,10,11)


def radiometry(metadata):
    root=ET.parse(metadata).getroot()
    values={node.tag.split('}')[-1]:node.text for node in root.iter()}
    baseline=float(values['PROCESSING_BASELINE'])
    quant=float(values['BOA_QUANTIFICATION_VALUE'])
    if quant!=10000: raise ValueError("Unvalidated Sentinel-2 quantification value")
    offsets={int(node.attrib['band_id']):float(node.text) for node in root.iter() if node.tag.split('}')[-1]=='BOA_ADD_OFFSET'}
    if baseline>=4 and not all(k in offsets for k in [3,8,11]): raise ValueError("SAFE offsets required for baseline >=04.00")
    return {name:(offsets.get(k,0.),quant) for name,k in [('red',3),('nir',8),('swir1',11)]}


def one(paths,pattern):
    matches=[p for p in paths if p.match(pattern)]
    if len(matches)!=1: raise ValueError(f"Expected exactly one SAFE asset: {pattern}")
    return matches[0]


def scaled_dn(dn,offset,quant):
    # Harmonized reference is uint16 after offset removal; native DN zero is fill.
    return np.where(dn==0,np.nan,np.maximum(dn.astype(float)+offset,0)/quant)


def clear_scl(scl):
    return np.isfinite(scl)&~np.isin(scl,EXCLUDED_SCL)


def prepare_safe(safe,bounds,output):
    safe=Path(safe);paths=[p for p in safe.rglob('*') if p.is_file() and not p.name.endswith('.json')]
    calibration=radiometry(safe/'MTD_MSIL2A.xml')
    b11=one(paths,'*_B11_20m.*');b8a=one(paths,'*_B8A_20m.*');b04=one(paths,'*_B04_10m.*');scl=one(paths,'*_SCL_20m.*')
    with rasterio.open(b11) as ds:
        target_bounds=transform_bounds('EPSG:4326',ds.crs,*bounds,densify_pts=21)
        win=from_bounds(*target_bounds,ds.transform)
        c0=math.floor(win.col_off);r0=math.floor(win.row_off);c1=math.ceil(win.col_off+win.width);r1=math.ceil(win.row_off+win.height)
        if c0<0 or r0<0 or c1>ds.width or r1>ds.height: raise ValueError("Initial adapter requires AOI entirely inside the selected MGRS tile")
        win=Window(c0,r0,c1-c0,r1-r0);tf=ds.window_transform(win);shape=(int(win.height),int(win.width));crs=ds.crs;profile=ds.profile.copy()
    def aligned(path,index=1,method=Resampling.nearest):
        with rasterio.open(path) as src:
            out=np.full(shape,np.nan)
            reproject(rasterio.band(src,index),out,src_transform=src.transform,src_crs=src.crs,dst_transform=tf,dst_crs=crs,
                      src_nodata=src.nodata,dst_nodata=np.nan,resampling=method)
            return out
    clear=clear_scl(aligned(scl))
    classi=[p for p in paths if p.stem=='MSK_CLASSI_B00' and p.suffix in ('.jp2','.tif')]
    if classi:
        if len(classi)!=1: raise ValueError("Ambiguous cloud classification mask")
        with rasterio.open(classi[0]) as ds:
            if ds.count<2: raise ValueError("MSK_CLASSI needs opaque and cirrus bands")
        clear&=(aligned(classi[0],1)==0)&(aligned(classi[0],2)==0)
        cloud_method='MSK_CLASSI opaque/cirrus nearest to 20m'
    else:
        masks=[p for p in paths if 'MSK_CLOUDS' in p.name and p.suffix=='.gml']
        if len(masks)!=1: raise ValueError("No auditable opaque/cirrus cloud mask; SCL-only fallback is disabled")
        root=ET.parse(masks[0]).getroot();polygons=[]
        for node in root.iter():
            declared=node.attrib.get('srsName')
            if declared and str(crs.to_epsg()) not in declared: raise ValueError('Cloud GML CRS does not match the optical tile')
        for node in root.iter():
            if node.tag.split('}')[-1]!='Polygon': continue
            rings=[]
            for ring in node.iter():
                if ring.tag.split('}')[-1]=='posList' and ring.text:
                    coords=np.fromstring(ring.text,sep=' ')
                    if len(coords)%2: raise ValueError("Unsupported GML coordinates")
                    rings.append(coords.reshape(-1,2).tolist())
            if rings: polygons.append((dict(type='Polygon',coordinates=rings),1))
        # Rasterize to native 60m QA grid before nearest alignment to 20m.
        with rasterio.open(scl) as ds:
            tags=ds.tags()
            if tags.get('native_grid_cutout')=='true' and tags.get('native_60m_aligned')!='true':
                raise ValueError('Legacy GML QA requires an SCL window aligned with the original 60 m grid')
            qtf=ds.transform*rasterio.Affine.scale(3,3);qshape=(ds.height//3,ds.width//3)
        qa=rasterize(polygons,out_shape=qshape,transform=qtf,fill=0,dtype='uint8') if polygons else np.zeros(qshape,dtype='uint8')
        target=np.zeros(shape,dtype='uint8')
        reproject(qa,target,src_transform=qtf,src_crs=crs,dst_transform=tf,dst_crs=crs,resampling=Resampling.nearest)
        clear&=target==0;cloud_method='GML opaque/cirrus rasterized to native 60m then nearest'
    arrays=[]
    for name,path in [('red',b04),('nir',b8a),('swir1',b11)]:
        offset,quant=calibration[name]
        if name=='red':
            with rasterio.open(path) as ds:
                if ds.crs!=crs or ds.res!=(10.,10.): raise ValueError("Unexpected B4 native support")
                highwin=from_bounds(*rasterio.windows.bounds(win,profile['transform']),ds.transform)
                if any(abs(v-round(v))>1e-6 for v in [highwin.col_off,highwin.row_off,highwin.width,highwin.height]): raise ValueError("B4/B11 grids are not nested")
                raw=ds.read(1,window=highwin.round_offsets().round_lengths())
            values=scaled_dn(raw,offset,quant)
            if values.shape!=(shape[0]*2,shape[1]*2): raise ValueError("B4/B11 shape mismatch")
            values=np.where(np.repeat(np.repeat(clear,2,axis=0),2,axis=1),values,np.nan)
            block=values.reshape(shape[0],2,shape[1],2);count=np.isfinite(block).sum(axis=(1,3))
            values=np.divide(np.nansum(block,axis=(1,3)),count,out=np.full(shape,np.nan),where=count>0)
        else: values=scaled_dn(aligned(path),offset,quant)
        arrays.append(np.where(clear,values,np.nan))
    profile.update(driver='GTiff',width=shape[1],height=shape[0],transform=tf,dtype='float32',count=3,nodata=np.nan,compress='deflate')
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
    with rasterio.open(output,'w',**profile) as dst:
        dst.write(np.stack(arrays).astype('float32'))
        for i,name in enumerate(['red','nir','swir1'],1): dst.set_band_description(i,name)
        dst.update_tags(product='s2_l2a',cloud_method=cloud_method,red_support='QA-masked native 10m area mean to 20m B11',scl_excluded=','.join(map(str,EXCLUDED_SCL)))
    return dict(cloud_method=cloud_method,radiometry=calibration,profile={k:str(v) for k,v in profile.items()})
