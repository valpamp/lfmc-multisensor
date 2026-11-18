"""Direct SY_2_SYN NTC inference on the native two-dimensional geolocation."""
from dataclasses import replace
from datetime import date,datetime
import hashlib
import json
from pathlib import Path
import re

import netCDF4
import numpy as np
from pyproj import Transformer
import rasterio
from rasterio.transform import from_origin,array_bounds
from rasterio.windows import from_bounds,Window

from .calibration import calibrate_reflectance
from .config import SENTINEL3_COMBINED
from .features import indices,calendar_features,vpd_from_tmean
from .models import load_published_bundle
from .naming import output_label,scene_output,source_output_tags,source_product_identity
from .output_terms import OUTPUT_COPYRIGHT, OUTPUT_TERMS_TAGS
from .prediction import predict_features
from .raster import geographic_sample
from .routing import area_probabilities
from .weather import adjust_daily_weather,WEATHER_FEATURE_VERSION
from .providers import agera5
from .providers.cdse import discover,acquire_s3,S3_FILES
from .providers.http import ProviderError,file_sha256
from .providers.static import aw3d30,prepare_terrain,worldcover,WC_CODES
from .providers.hres import prepare_reference

QUALITY_VERSION='s3syn_cloud_snow_retrieval_v1_20261012'
REJECT_FLAGS={
    'CLOUD_flags':('CLOUD','CLOUD_AMBIGUOUS','CLOUD_MARGIN','SNOW_ICE'),
    'SYN_flags':('SYN_cloud','SYN_snow_risk','SYN_partly_cloudy','SYN_SDR_OOR',
                 'SYN_negative_curvature','SYN_too_low','SYN_high_error')}
BANDS={'red':('Syn_Oa08_reflectance.nc','SDR_Oa08',665.),
       'nir':('Syn_Oa17_reflectance.nc','SDR_Oa17',865.),
       'swir1':('Syn_S5N_reflectance.nc','SDR_S5N',1610.)}


def quality_mask(arrays,metadata):
    """Metadata-decoded audited sampler QA; no training support thresholds."""
    good=None
    for variable,names in REJECT_FLAGS.items():
        attrs=metadata[variable];meanings=str(attrs['flag_meanings']).split()
        masks=np.asarray(attrs['flag_masks']).ravel()
        if len(meanings)!=len(masks) or len(set(meanings))!=len(meanings):raise ValueError('Invalid Sentinel-3 flag metadata')
        if not np.issubdtype(masks.dtype,np.integer) or any(int(m)<=0 or int(m)&(int(m)-1) for m in masks) or len(set(map(int,masks)))!=len(masks):
            raise ValueError('Sentinel-3 flag masks must be unique powers of two')
        lookup=dict(zip(meanings,map(int,masks)))
        if set(names)-set(lookup):raise ValueError('Unknown Sentinel-3 quality schema')
        a=np.ma.asarray(arrays[variable]);raw=np.asarray(a.filled(0))
        if not np.issubdtype(raw.dtype,np.integer):raise ValueError('Quality flags must be integers')
        keep=~np.ma.getmaskarray(a)&((raw&sum(lookup[n] for n in names))==0)
        if good is not None and good.shape!=keep.shape:raise ValueError('Quality layer shape mismatch')
        good=keep if good is None else good&keep
    return good


def subset_native(folder,bounds,*,item,sensor,day,output,aoi_geometry=None):
    """Read packed NetCDF metadata/fill once, retaining original row/column IDs."""
    folder=Path(folder);name=item['Name']
    match=re.fullmatch(r'(S3[AB])_SY_2_SYN____(\d{8}T\d{6})_(\d{8}T\d{6})_.*_NT_002\.SEN3',name)
    if not match or match[1]!=sensor or match[2][:8]!=day.replace('-','') or folder.name!=name:
        raise ValueError('Sentinel-3 product/platform/date identity mismatch')
    for field,stamp in [('Start',match[2]),('End',match[3])]:
        actual=datetime.fromisoformat(item['ContentDate'][field].replace('Z','+00:00')).replace(tzinfo=None)
        if abs((actual-datetime.strptime(stamp,'%Y%m%dT%H%M%S')).total_seconds())>1:
            raise ValueError('Sentinel-3 catalogue and filename acquisition times disagree')
    def open_native(filename):
        ds=netCDF4.Dataset(folder/filename)
        if getattr(ds,'product_name',None)!=name:
            ds.close();raise ValueError('Sentinel-3 native product identity mismatch')
        return ds
    with open_native('geolocation.nc') as ds:
        for key,units in [('lat','degrees_north'),('lon','degrees_east')]:
            v=ds[key]
            if v.ndim!=2 or getattr(v,'units',None)!=units or not np.isclose(getattr(v,'scale_factor',np.nan),1e-6,rtol=0,atol=1e-14):
                raise ValueError('Unknown Sentinel-3 native geolocation encoding')
        shape=ds['lat'].shape
        if ds['lon'].shape!=shape:raise ValueError('Sentinel-3 geolocation shape mismatch')
        # Scan in row chunks instead of loading two entire swath arrays.
        rows=[];cols=[];w,s,e,n=bounds
        for start in range(0,shape[0],256):
            lat=np.ma.filled(ds['lat'][start:start+256],np.nan);lon=np.ma.filled(ds['lon'][start:start+256],np.nan)
            rr,cc=np.where((lon>=w)&(lon<=e)&(lat>=s)&(lat<=n))
            if len(rr):rows.extend([int(rr.min()+start),int(rr.max()+start)]);cols.extend([int(cc.min()),int(cc.max())])
        if not rows:raise ValueError('No native Sentinel-3 centres inside requested AOI')
        r0=max(0,min(rows)-2);r1=min(shape[0],max(rows)+3);c0=max(0,min(cols)-2);c1=min(shape[1],max(cols)+3)
        window=(slice(r0,r1),slice(c0,c1))
        arrays={k:np.ma.filled(ds[k][window],np.nan).astype(float) for k in ['lat','lon']}
    if not np.isfinite(np.stack(list(arrays.values()))).all():raise ValueError('Missing native geolocation in selected window')
    arrays['in_extent']=(arrays['lon']>=w)&(arrays['lon']<=e)&(arrays['lat']>=s)&(arrays['lat']<=n)
    if aoi_geometry is not None:
        from .aoi import polygon_mask
        arrays['in_extent']&=polygon_mask(aoi_geometry,arrays['lon'],arrays['lat'])
        if not arrays['in_extent'].any():raise ValueError('No native Sentinel-3 centres inside the polygon AOI')
    for band,(filename,key,wavelength) in BANDS.items():
        with open_native(filename) as ds:
            v=ds[key]
            expected=dict(_FillValue=-10000,valid_min=0,valid_max=10000,scale_factor=.0001,wavelength=wavelength)
            if v.shape!=shape or any(not np.isclose(getattr(v,k,np.nan),value,rtol=0,atol=1e-10) for k,value in expected.items()) or getattr(v,'add_offset',0)!=0:
                raise ValueError('Unvalidated Sentinel-3 reflectance schema/band/grid')
            arrays[band]=np.ma.filled(v[window],np.nan).astype('float32')
    flags={};metadata={}
    with open_native('flags.nc') as ds:
        for key in REJECT_FLAGS:
            v=ds[key]
            if v.shape!=shape:raise ValueError('Sentinel-3 quality/geolocation shape mismatch')
            flags[key]=v[window];metadata[key]={k:v.getncattr(k) for k in ['flag_meanings','flag_masks']}
            arrays[key]=np.asarray(flags[key].filled(0))
    arrays['clear']=quality_mask(flags,metadata)
    arrays['source_row']=np.arange(r0,r1);arrays['source_column']=np.arange(c0,c1)
    np.savez_compressed(output,**arrays)
    return arrays,dict(source_id=item['Id'],sensor=sensor,product='s3syn300',quality_version=QUALITY_VERSION,
        band_mapping={k:v[1] for k,v in BANDS.items()},native_shape=list(shape),window=[r0,r1,c0,c1],
        native_centres_in_extent=int(arrays['in_extent'].sum()),clear_centres_in_extent=int((arrays['clear']&arrays['in_extent']).sum()),
        sources={name:file_sha256(folder/name) for name in S3_FILES})


def sample_nearest(path,lon,lat):
    with rasterio.open(path) as ds:
        x,y=Transformer.from_crs(4326,ds.crs,always_xy=True).transform(lon,lat)
        c,r=(~ds.transform)*(x,y);c=np.floor(c).astype(int);r=np.floor(r).astype(int)
        good=(r>=0)&(r<ds.height)&(c>=0)&(c<ds.width)
        data=ds.read(masked=True).astype(float).filled(np.nan);out={}
        for key,values in zip(ds.descriptions,data):
            a=np.full(lon.shape,np.nan);a[good]=values[r[good],c[good]];out[key]=a
    return out


def ancillary_native(arrays,config,folder):
    lon,lat=arrays['lon'],arrays['lat'];bounds=(float(lon.min()-.004),float(lat.min()-.004),float(lon.max()+.004),float(lat.max()+.004))
    # Derive terrain once on its original geographic grid; sample exact centres.
    paths=aw3d30(bounds,config.cache_dir/'aw3d30')
    with rasterio.open(paths[0]) as ds:
        win=from_bounds(*bounds,ds.transform);c0=int(np.floor(win.col_off));r0=int(np.floor(win.row_off))
        win=Window(c0,r0,int(np.ceil(win.col_off+win.width))-c0,int(np.ceil(win.row_off+win.height))-r0)
        target=dict(crs=ds.crs,transform=ds.window_transform(win),width=int(win.width),height=int(win.height))
    terrain=folder/'terrain.tif';terrain_info=prepare_terrain(paths,bounds,target,terrain)
    static=sample_nearest(terrain,lon,lat)
    # Explicit nominal 300m central supports in local UTM, following the appendix.
    zone=int((float(lon.mean())+180)//6)+1;epsg=(32600 if lat.mean()>=0 else 32700)+zone
    if not 1<=zone<=60:raise ValueError('Invalid Sentinel-3 UTM support zone')
    x,y=Transformer.from_crs(4326,epsg,always_xy=True).transform(lon,lat)
    left=np.floor((x.min()-160)/20)*20;top=np.ceil((y.max()+160)/20)*20
    target=dict(crs=f'EPSG:{epsg}',transform=from_origin(left,top,20,20),width=int(np.ceil((x.max()+160-left)/20)),height=int(np.ceil((top-y.min()+160)/20)))
    landcover=folder/'landcover.tif';lc_info=worldcover(bounds,config.landcover_year,target,landcover,config.cache_dir/'worldcover')
    with rasterio.open(landcover) as ds:
        fractions,support=area_probabilities(ds.read(masked=True).astype(float).filled(np.nan),ds.transform,x,y,band_count=len(WC_CODES))
    good=np.isfinite(fractions).all(axis=0)&np.isclose(fractions.sum(axis=0),1,atol=1e-5)&(fractions>=0).all(axis=0)
    route=np.where(good,np.asarray(WC_CODES)[np.argmax(np.nan_to_num(fractions,nan=-1),axis=0)],0).astype('uint8')
    lc_info.update(native_support='area-weighted nominal 300m UTM rectangles over 20m class fractions; no suitability threshold',support_crs=f'EPSG:{epsg}')
    return static,route,support,dict(terrain=terrain_info,landcover=lc_info)


def native_features(arrays,static,weather,day,models):
    shape=arrays['lat'].shape
    common={**static,**weather,**{k:np.full(shape,v) for k,v in calendar_features(date.fromisoformat(day)).items()}}
    return {veg:{**common,**indices(**calibrate_reflectance(bundle,{k:arrays[k] for k in BANDS},product='s3syn300',sensor=arrays['sensor']))} for veg,bundle in models.items()}


def predict_native(arrays,static,route,weather,day,models):
    features=native_features(arrays,static,weather,day,models);shape=arrays['lat'].shape
    complete=np.isfinite(np.stack([arrays[k] for k in BANDS])).all(axis=0)&arrays['clear']
    result=np.full(shape,np.nan,dtype='float32');counts={}
    qa=np.where(np.isin(route,[10,20,30]),3,1).astype('uint8');qa[(qa==3)&~complete]=2
    qa[~arrays['in_extent']]=4
    for veg,code in [('Grass',30),('Shrub',20),('Tree',10)]:
        eligible=(route==code)&arrays['in_extent']&complete
        predicted=predict_features(models[veg],features[veg],eligible);result[eligible]=predicted[eligible]
        counts[veg]=int(np.isfinite(predicted).sum())
    qa[np.isfinite(result)]=0
    return result,qa,counts


def write_native(output,arrays,result,qa,route,support,*,day,sensor,downscale,output_label=None,source_product=None):
    """CF auxiliary lat/lon coordinates, preserving curved native pixel support."""
    with netCDF4.Dataset(output,'w',format='NETCDF4') as ds:
        from .models import model_output_tags
        ds.setncatts(model_output_tags())
        ds.setncatts(source_output_tags(source_product or {}))
        ds.setncatts(dict(copyright=OUTPUT_COPYRIGHT, **OUTPUT_TERMS_TAGS))
        ds.LFMC_OUTPUT_LABEL=output_label or Path(output).stem
        ds.Conventions='CF-1.8';ds.product='s3syn300';ds.sensor=sensor;ds.analysis_date=day
        ds.quality_version=QUALITY_VERSION;ds.weather_mode='appendix_downscaled' if downscale else 'original'
        ds.native_grid='true';ds.training_suitability_applied='false'
        ds.createDimension('rows',result.shape[0]);ds.createDimension('columns',result.shape[1])
        for key,dim in [('source_row','rows'),('source_column','columns')]:ds.createVariable(key,'i4',(dim,))[:]=arrays[key]
        for key,standard,units in [('lat','latitude','degrees_north'),('lon','longitude','degrees_east')]:
            v=ds.createVariable(key,'f8',('rows','columns'),zlib=True);v[:]=arrays[key];v.standard_name=standard;v.units=units
        for key,data,dtype,fill in [('LFMC',result,'f4',np.nan),('QA',qa,'u1',255),('dominant_class',route,'u1',255),('landcover_support_fraction',support,'f4',np.nan)]:
            v=ds.createVariable(key,dtype,('rows','columns'),fill_value=fill,zlib=True);v[:]=data;v.coordinates='lat lon'
        ds['LFMC'].long_name='Live fuel moisture content, percent dry mass';ds['LFMC'].units='%'
        ds['QA'].flag_values=np.arange(5,dtype='uint8');ds['QA'].flag_meanings='valid non_routed optical_or_quality_missing feature_missing outside_aoi'
        ds['dominant_class'].long_name='Dominant WorldCover class over nominal 300m central support'
        ds['landcover_support_fraction'].units='1'


def run_native(config,*,item=None):
    from .workflow import plan,access_check,preparation_key
    if config.aoi:config.aoi.verify()
    access=access_check(config)
    if not access['cdse_s3']:raise ProviderError('Sentinel-3 requires external CDSE_S3_ACCESS_KEY and CDSE_S3_SECRET_KEY')
    if not access['cds_config_present']:raise ProviderError('Configure CDS credentials and accept AgERA5 terms')
    if item is None:
        items=discover(config)
        if config.product_ids:
            lookup={i['Id']:i for i in items}
            if any(i not in lookup for i in config.product_ids):raise ValueError('Explicit Sentinel-3 product outside requested catalogue after cloud filtering')
            items=[lookup[i] for i in config.product_ids]
        if len(items)!=1:raise ValueError(f'Found {len(items)} products; select exactly one product_id, without implicit mosaic/reprocessing selection')
        item=items[0]
    sensor=item['Name'].split('_',1)[0]
    if sensor not in ('S3A','S3B') or config.sensor not in (sensor,SENTINEL3_COMBINED):
        raise ValueError('Sentinel-3 scene does not match the requested spacecraft')
    config=replace(config,sensor=sensor,output_label=output_label(config))
    identity=source_product_identity(config.product,item)
    output=scene_output(config,identity);manifest=output.with_suffix('.json')
    if output.exists() or manifest.exists():raise FileExistsError('Choose a new native output path')
    models={v:load_published_bundle(config.models_dir/f'best_{v}_gapfill_within_site.joblib',v) for v in ['Grass','Shrub','Tree']}
    for bundle in models.values():calibrate_reflectance(bundle,{b:np.array([.1]) for b in BANDS},product='s3syn300',sensor=config.sensor)
    request=plan(config);key=preparation_key(request)
    folder=config.cache_dir/key;folder.mkdir(parents=True,exist_ok=True)
    source=acquire_s3(item,config.cache_dir/'cdse')
    print('Sentinel-3: cropping native files to the AOI.',flush=True)
    native=folder/'native.npz';arrays,optical_info=subset_native(source,config.bounds,item=item,sensor=config.sensor,day=config.day,output=native,
        aoi_geometry=config.aoi.geometry if config.aoi else None)
    print('Sentinel-3: preparing terrain and land cover.',flush=True)
    static,route,support,ancillary=ancillary_native(arrays,config,folder)
    print('Sentinel-3: preparing weather inputs.',flush=True)
    path=agera5.prepare('',[config.day],config.cache_dir/'weather',aoi_name='aoi_'+key,aoi_bbox=config.bounds)[config.day]
    with rasterio.open(path) as ds:
        tags=ds.tags()
        if tags.get('WEATHER_DATE')!=config.day or tags.get('LFMC_WEATHER_FEATURE_VERSION')!=WEATHER_FEATURE_VERSION:raise ValueError('Weather date/definition mismatch')
        weather=geographic_sample(ds,arrays['lon'],arrays['lat'])
    weather['VPD']=vpd_from_tmean(weather['Tmean'],weather['VP'])
    sources=dict(request=request,product={k:item[k] for k in ['Id','Name','S3Path','ContentDate','cloud_cover','cloud_cover_source']},
                 source_product=identity,optical=optical_info,**ancillary)
    inputs={'native':native,'terrain':folder/'terrain.tif','landcover':folder/'landcover.tif','weather':Path(path)}
    if config.downscale:
        reference=folder/'hres_reference.tif';sources['reference']=prepare_reference(config.bounds,config.cache_dir/'hres',reference)
        sources['reference']['requested_date_cycle_equivalence_verified']=False
        with rasterio.open(reference) as ds:height=geographic_sample(ds,arrays['lon'],arrays['lat'])['elevation']
        weather,_=adjust_daily_weather(weather,static['elevation'],height)
        # Native appendix S3 fields retain float64; no S2 raster materialization.
        weather['VPD']=vpd_from_tmean(weather['Tmean'],weather['VP']);inputs['reference']=reference
    arrays['sensor']=config.sensor
    np.savez_compressed(folder/'predictors.npz',**static,**{f'wx_{k}':v for k,v in weather.items()},route=route,support=support)
    inputs['predictors']=folder/'predictors.npz'
    hashes={k:file_sha256(p) for k,p in inputs.items()}
    print('Sentinel-3: predicting LFMC.',flush=True)
    result,qa,counts=predict_native(arrays,static,route,weather,config.day,models)
    output.parent.mkdir(parents=True,exist_ok=True);partial=output.with_suffix('.part.nc')
    try:
        write_native(partial,arrays,result,qa,route,support,day=config.day,sensor=config.sensor,downscale=config.downscale,
                     output_label=config.output_label,source_product=identity)
        if any(file_sha256(p)!=hashes[k] for k,p in inputs.items()):raise RuntimeError('Inputs changed during native inference')
        if config.aoi:config.aoi.verify()
        partial.replace(output)
    finally:partial.unlink(missing_ok=True)
    from .models import model_provenance
    record=dict(schema='lfmc_native_prediction_v1',product='s3syn300',sensor=config.sensor,date=config.day,output=output.name,
        output_label=config.output_label,model_provenance=model_provenance(),
        valid_pixels=int(np.isfinite(result).sum()),classes=counts,native_grid=True,quality_version=QUALITY_VERSION,
        weather_mode='appendix_downscaled' if config.downscale else 'original',landcover_kind='worldcover_fractions_nominal_300m',
        calibration_mode='frozen_per_class_linear_then_clip',source_manifest=sources,
        inputs={k:dict(name=p.name,sha256=hashes[k]) for k,p in inputs.items()},outputs={output.name:file_sha256(output)},
        model_sha256={v:file_sha256(config.models_dir/f'best_{v}_gapfill_within_site.joblib') for v in models},
        exact_agera5_reference_verified=False if config.downscale else None)
    manifest.write_text(json.dumps(record,indent=2,allow_nan=False)+'\n')
    (folder/'preparation.json').write_text(json.dumps(sources,indent=2)+'\n')
    return record
