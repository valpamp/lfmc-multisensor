"""Windowed prepared-input inference; output grids are explicit and checked."""
from contextlib import ExitStack
from datetime import date
import json
from pathlib import Path

import numpy as np
from pyproj import Transformer
import rasterio
from rasterio.windows import Window

from .features import indices,calendar_features,vpd_from_tmean
from .models import load_published_bundle,model_provenance,model_output_tags
from .naming import source_output_tags
from .output_terms import OUTPUT_COPYRIGHT, OUTPUT_TERMS_TAGS
from .prediction import predict_features
from .routing import dynamic_world_route,worldcover_route
from .weather import strict_bilinear,adjust_daily_weather,WEATHER_FEATURE_VERSION
from .providers.http import file_sha256
from .providers.hres import SOURCE_SHA
from .calibration import calibrate_reflectance


def geographic_sample(ds,lon,lat):
    if ds.crs.to_epsg()!=4326: raise ValueError("Weather/reference must be EPSG:4326")
    if None in ds.descriptions or len(set(ds.descriptions))!=ds.count: raise ValueError("Input needs unique band names")
    x,y=(~ds.transform)*(lon,lat); x=np.asarray(x)-.5;y=np.asarray(y)-.5
    c0=max(0,int(np.floor(x.min())));r0=max(0,int(np.floor(y.min())))
    c1=min(ds.width,int(np.floor(x.max()))+2);r1=min(ds.height,int(np.floor(y.max()))+2)
    if c1<=c0 or r1<=r0: return {n:np.full(x.shape,np.nan) for n in ds.descriptions}
    arrays=ds.read(window=Window(c0,r0,c1-c0,r1-r0),masked=True).astype(float).filled(np.nan)
    return {n:strict_bilinear(v,x-c0,y-r0) for n,v in zip(ds.descriptions,arrays)}


def predict_rasters(*,optical,terrain,landcover,weather,models_dir,day,output,
                    landcover_kind="worldcover_fractions",reference=None,block_size=256,source_manifest=None,
                    product='s2_l2a',sensor='Sentinel-2 MSI',aoi_geometry=None,output_label=None):
    """Predict a prepared optical stack using windows and exact publication transforms.

    Inputs contain provider-scaled, QA-screened native reflectances. Frozen
    calibration runs separately for each vegetation class; do not pre-calibrate
    these rasters. Explicit product/sensor must match the prepared-input tags.

    An explicit reference enables appendix downscaling. No training masks,
    temporal gap filling or LFMC clipping. Writes float32 LFMC, a uint8 QA
    companion and a provenance manifest. Existing outputs are never overwritten.
    QA: 0 valid, 1 non-routed class, 2 optical missing, 3 feature missing.
    """
    if not isinstance(block_size,int) or isinstance(block_size,bool) or block_size<=0: raise ValueError("Invalid block size")
    if aoi_geometry is not None:
        from .aoi import prepare_polygon
        aoi_geometry=prepare_polygon(aoi_geometry)
    output=Path(output);qa_path=output.with_name(output.stem+"_qa.tif")
    vegetation_path=output.with_name(output.stem+'_vegetation.tif')
    output_label=output_label or output.stem
    metadata=dict(model_output_tags(),LFMC_OUTPUT_LABEL=output_label)
    metadata.update(source_output_tags((source_manifest or {}).get('source_product',{})))
    metadata.update(TIFFTAG_COPYRIGHT=OUTPUT_COPYRIGHT, **OUTPUT_TERMS_TAGS)
    manifest_path=output.with_suffix('.json')
    if any(p.exists() for p in (output,qa_path,vegetation_path,manifest_path)): raise FileExistsError("Choose a new output path")
    models={veg:load_published_bundle(Path(models_dir)/f"best_{veg}_gapfill_within_site.joblib",veg) for veg in ['Grass','Shrub','Tree']}
    # Validate the domain before IO or eligibility; all-cloud inputs must not
    # accidentally make an unsupported sensor look successful.
    for bundle in models.values():calibrate_reflectance(bundle,{b:np.array([.1]) for b in ['red','nir','swir1']},product=product,sensor=sensor)
    paths={"optical":Path(optical),"terrain":Path(terrain),"landcover":Path(landcover),"weather":Path(weather)}
    if reference: paths['reference']=Path(reference)
    hashes={name:file_sha256(p) for name,p in paths.items()}
    output.parent.mkdir(parents=True,exist_ok=True)
    partial=output.with_suffix('.part.tif');qpartial=qa_path.with_suffix('.part.tif')
    vpartial=vegetation_path.with_suffix('.part.tif')
    valid_pixels=0;class_counts={v:0 for v in models}
    try:
        with ExitStack() as stack:
            sources={n:stack.enter_context(rasterio.open(p)) for n,p in paths.items()}
            opt=sources['optical']; prof=opt.profile.copy()
            if opt.descriptions!=('red','nir','swir1'): raise ValueError("Optical stack must be red/nir/swir1")
            if opt.tags().get('product',product)!=product:
                raise ValueError('Prepared optical product differs from requested calibration domain')
            if product!='s2_l2a' and opt.tags().get('sensor',sensor)!=sensor:
                raise ValueError('Prepared optical spacecraft differs from requested calibration domain')
            for name in ('terrain','landcover'):
                ds=sources[name]
                if (ds.crs,ds.transform,ds.width,ds.height)!=(opt.crs,opt.transform,opt.width,opt.height): raise ValueError(f"Grid mismatch: {name}")
            wx=sources['weather'];tags=wx.tags()
            if tags.get('WEATHER_DATE')!=day or tags.get('LFMC_WEATHER_FEATURE_VERSION')!=WEATHER_FEATURE_VERSION: raise ValueError("Weather date/definition mismatch")
            if reference:
                rtags=sources['reference'].tags()
                if (rtags.get('source_sha256')!=SOURCE_SHA or rtags.get('reconstruction_method')!='bilinear'
                        or rtags.get('exact_agera5_reference_verified')!='false'):
                    raise ValueError('Downscaling requires the explicitly labelled appendix historical-HRES reconstruction')
            project=Transformer.from_crs(opt.crs,4326,always_xy=True)
            prof.update(driver='GTiff',dtype='float32',count=1,nodata=np.nan,compress='deflate')
            dst=stack.enter_context(rasterio.open(partial,'w',**prof));dst.set_band_description(1,'LFMC_percent_dry_mass')
            dst.update_tags(date=day,product=product,sensor=sensor,weather_mode='appendix_downscaled' if reference else 'original',training_suitability_applied='false')
            dst.update_tags(**metadata)
            qprof={**prof,'dtype':'uint8','nodata':255}
            qa=stack.enter_context(rasterio.open(qpartial,'w',**qprof));qa.set_band_description(1,'LFMC_quality_reason')
            qa.update_tags(codes='0=valid;1=non-routed;2=optical-missing;3=feature-missing;4=outside-polygon-aoi')
            qa.update_tags(**metadata)
            vegetation=stack.enter_context(rasterio.open(vpartial,'w',**qprof));vegetation.set_band_description(1,'dominant_vegetation_class')
            vegetation.update_tags(codes='10=Tree;20=Shrub;30=Grass;other=not-routed;255=outside-aoi',**metadata)
            for row in range(0,opt.height,block_size):
                for col in range(0,opt.width,block_size):
                    win=Window(col,row,min(block_size,opt.width-col),min(block_size,opt.height-row))
                    raw=opt.read(window=win,masked=True).astype(float).filled(np.nan);shape=raw.shape[1:]
                    lc=sources['landcover'].read(window=win,masked=True).astype(float).filled(np.nan)
                    if landcover_kind=='dynamic_world_probabilities': route,_,_=dynamic_world_route(lc[:9])
                    elif landcover_kind=='worldcover_fractions':
                        route=worldcover_route(lc,sources['landcover'].descriptions)
                    else: raise ValueError("Unknown land-cover routing policy")
                    static=dict(zip(sources['terrain'].descriptions,sources['terrain'].read(window=win,masked=True).astype(float).filled(np.nan)))
                    rr,cc=np.indices(shape);tf=opt.window_transform(win);x,y=tf*(cc+.5,rr+.5);lon,lat=project.transform(x,y)
                    if aoi_geometry is not None:
                        from .aoi import polygon_mask
                        in_aoi=polygon_mask(aoi_geometry,lon,lat)
                    else:in_aoi=np.ones(shape,dtype=bool)
                    vegetation.write(np.where(in_aoi,route,255).astype('uint8'),1,window=win)
                    weather_values=geographic_sample(wx,lon,lat);weather_values['VPD']=vpd_from_tmean(weather_values['Tmean'],weather_values['VP'])
                    if reference:
                        height=geographic_sample(sources['reference'],lon,lat)['elevation']
                        weather_values,_=adjust_daily_weather(weather_values,static['elevation'],height)
                        # Published S2 downscaled fields were materialized as float32.
                        weather_values={n:v.astype('float32').astype(float) for n,v in weather_values.items()}
                        if product!='s2_l2a':
                            # The generic trainer rebuilds VPD from the aligned
                            # Tmean/VP predictors. Preserve that arithmetic after
                            # materialization for the new product branches.
                            weather_values['VPD']=vpd_from_tmean(weather_values['Tmean'],weather_values['VP'])
                    common_features={**static,**weather_values,**{n:np.full(shape,v) for n,v in calendar_features(date.fromisoformat(day)).items()}}
                    complete=np.isfinite(raw).all(axis=0);result=np.full(shape,np.nan,dtype='float32')
                    reason=np.where(np.isin(route,[10,20,30]),3,1).astype('uint8');reason[(reason==3)&~complete]=2
                    reason[~in_aoi]=4
                    for veg,code in [('Grass',30),('Shrub',20),('Tree',10)]:
                        bands=calibrate_reflectance(models[veg],dict(zip(['red','nir','swir1'],raw)),product=product,sensor=sensor)
                        features={**common_features,**indices(**bands)}
                        eligible=(route==code)&complete&in_aoi
                        predicted=predict_features(models[veg],features,eligible);result[eligible]=predicted[eligible]
                        class_counts[veg]+=int(np.isfinite(predicted).sum())
                    valid=np.isfinite(result);reason[valid]=0;valid_pixels+=int(valid.sum());dst.write(result,1,window=win);qa.write(reason,1,window=win)
        # Detect changed inputs before promoting staged products.
        if any(file_sha256(p)!=hashes[n] for n,p in paths.items()): raise RuntimeError("Inputs changed during inference")
        partial.replace(output);qpartial.replace(qa_path);vpartial.replace(vegetation_path)
        record=dict(schema='lfmc_prediction_v1',date=day,output=output.name,valid_pixels=valid_pixels,classes=class_counts,
                    output_label=output_label,model_provenance=model_provenance(),
                    aoi_mask='polygon_centres' if aoi_geometry is not None else 'native_bounding_window',
                    product=product,sensor=sensor,calibration_mode='reference_passthrough' if product=='s2_l2a' else 'frozen_per_class_linear_then_clip',
                    weather_mode='appendix_downscaled' if reference else 'original',landcover_kind=landcover_kind,
                    inputs={n:dict(name=p.name,sha256=hashes[n]) for n,p in paths.items()},
                    outputs={output.name:file_sha256(output),qa_path.name:file_sha256(qa_path),vegetation_path.name:file_sha256(vegetation_path)},source_manifest=source_manifest,
                    model_sha256={v:file_sha256(Path(models_dir)/f'best_{v}_gapfill_within_site.joblib') for v in models},
                    exact_agera5_reference_verified=False if reference else None)
        manifest_path.write_text(json.dumps(record,indent=2,allow_nan=False)+'\n')
        return record
    finally:
        partial.unlink(missing_ok=True);qpartial.unlink(missing_ok=True)
        vpartial.unlink(missing_ok=True)
