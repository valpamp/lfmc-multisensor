"""Direct single-scene optical workflow with explicit product/date semantics."""
from dataclasses import replace
from datetime import date,datetime,timedelta,timezone
import hashlib
import json
import os
from pathlib import Path

import rasterio
from .config import LANDSAT_COMBINED,RunConfig
from .providers.http import ProviderError
from .providers.cdse import discover,CDSE
from .providers.sentinel2_windows import acquire_s2_windows
from .providers.static import worldcover,aw3d30,prepare_terrain
from .providers import agera5
from .providers.hres import prepare_reference
from .providers import earthdata,landsat as landsat_provider
from .viirs import prepare_hdf
from .modis import prepare_hdf as prepare_modis
from .landsat import prepare_assets
from .sentinel2 import prepare_safe
from .raster import predict_rasters
from .naming import output_label,labelled_output,scene_output,source_product_identity
from .models import model_provenance


def preparation_key(request):
    # Summary/plot choices do not change acquisition or predictor preparation.
    inputs={key:value for key,value in request.items() if key not in ('statistics','cloud_filter')}
    if 'model_provenance' in inputs:
        provenance=inputs['model_provenance']
        model_set={key:value for key,value in provenance['model_set'].items() if key!='map_version'}
        inputs['model_provenance']=dict(provenance,model_set=model_set)
    return hashlib.sha256(json.dumps(inputs,sort_keys=True).encode()).hexdigest()[:20]


def plan(config):
    """No network or credential reads; list acquisition dependencies."""
    optical={'s3syn300':'SY_2_SYN NTC collection 002; native geolocation; Oa08/Oa17/S5N; metadata cloud/snow/retrieval QA',
             's2_l2a':'B4 10m masked area mean; B8A/B11 20m; SCL plus opaque/cirrus',
             'modis':'MOD09A1.061 six-band screen; native sinusoidal ~463m; published MODIS state QA',
             'modis_aqua':'MYD09A1.061 six-band screen; native sinusoidal ~463m; published MODIS state QA',
             'viirs':'VNP09H1.002 I1/I2/I3; native sinusoidal ~463m; published state QA and 0<SR<1.6',
             'landsat_c2l2':'Collection 2 Tier-1 Level-2 SR B4/B5/B6; 30m; QA_PIXEL and QA_RADSAT'}
    result=dict(product=config.product,sensor=config.sensor,date=config.day,bounds=list(config.bounds),tile=config.tile,
                optical=optical[config.product],
                rainfall_start=str(date.fromisoformat(config.day)-timedelta(days=84)),rainfall_end=config.day,
                landcover=dict(source='WorldCover',year=config.landcover_year,policy=config.landcover_policy),
                terrain='JAXA AW3D30 v4.1; source-grid four-neighbour authalic derivatives; exact nearest centres',
                downscale=config.downscale,reference='historical-HRES appendix reconstruction' if config.downscale else None,
                status='native-grid workflow; readiness is product-specific; no GEE')
    result['search_interval']=dict(start_utc=config.search_start,end_utc_inclusive=config.search_end_inclusive,end_utc_exclusive=config.search_end_exclusive)
    result['time_series']=bool(config.end_day)
    result['output_label']=output_label(config)
    result['model_provenance']=model_provenance()
    result['cloud_filter']=dict(maximum_percent=config.max_cloud_cover,include_unknown=config.include_unknown_cloud,
        scope='Provider scene/granule metadata, not cloud percentage within the AOI; pixel QA remains unchanged')
    result['statistics']=dict(enabled=config.statistics,by_vegetation=config.statistics_by_vegetation,
        centre=config.statistics_centre,support=config.statistics_support,
        exploratory_intervals=config.statistics_comparisons,block_length=config.statistics_block_length)
    if config.end_day:
        result['series_policy']='All matching scenes, or selected product_ids; sequential native maps with per-scene analysis-date weather; no mosaic'
        result['rainfall_end']=config.last_day
    if config.product=='s3syn300':
        result['native_output']='CF NetCDF LFMC/QA with 2D lat/lon and source row/column IDs; no affine resampling'
        result['native_landcover_support']='nominal 300m UTM rectangle area weights over 20m WorldCover fractions'
        result['quality_version']='s3syn_cloud_snow_retrieval_v1_20261012'
    if config.product in ('viirs','modis','modis_aqua'):
        start,end=earthdata.composite_interval(config.day)
        result['composite']=dict(start=str(start),end_exclusive=str(end),policy=config.composite_policy,
            weather_date=config.day,warning='Composite may include observations after analysis date; availability follows composite completion and processing')
    if config.aoi:result['aoi']=config.aoi.metadata()
    return result


def discover_products(config):
    if config.aoi:config.aoi.verify()
    if config.product in ('viirs','modis','modis_aqua'):return earthdata.discover(config)
    if config.product=='landsat_c2l2':return landsat_provider.discover(config)
    return discover(config)


def discovery_summary(config):
    items=discover_products(config)
    if config.product in ('s2_l2a','s3syn300'):return [{k:p[k] for k in ['Id','Name','ContentDate','S3Path','cloud_cover','cloud_cover_source']} for p in items]
    if config.product in ('viirs','modis','modis_aqua'):return items
    return [dict(id=p['id'],sensor=p['properties']['platform'],datetime=p['properties']['datetime'],
                 cloud_cover=p.get('cloud_cover'),cloud_cover_source=p.get('cloud_cover_source')) for p in items]


def access_check(config):
    # Presence only: no credential values or private config contents returned.
    return dict(cdse_s3=bool(os.environ.get('CDSE_S3_ACCESS_KEY') and os.environ.get('CDSE_S3_SECRET_KEY')),
                cdse_oauth=bool(os.environ.get('CDSE_ACCESS_TOKEN') or (os.environ.get('CDSE_USERNAME') and os.environ.get('CDSE_PASSWORD'))),
                jaxa=bool(os.environ.get('JAXA_USERNAME') and os.environ.get('JAXA_PASSWORD')),
                cds_config_present=bool(os.environ.get('CDSAPI_KEY') or (Path.home()/'.cdsapirc').exists()),
                earthdata_config_present=bool(os.environ.get('EARTHDATA_TOKEN') or (os.environ.get('EARTHDATA_USERNAME') and os.environ.get('EARTHDATA_PASSWORD')) or (Path.home()/'.netrc').exists() or (Path.home()/'_netrc').exists()),
                landsat_access='public STAC with ephemeral in-memory SAS links')


def run(config):
    if config.aoi:config.aoi.verify()
    config=replace(config,output=labelled_output(config),output_label=output_label(config))
    if config.end_day:
        result=run_series(config);manifest=config.output.with_name(config.output.stem+'_timeseries.json')
    else:
        result=_run_single(config)
    if config.statistics:
        from .statistics import analyse
        if not config.end_day:
            manifest=config.output.with_name(result['output']).with_suffix('.json')
        try:result['statistics']=analyse(config,source=manifest)
        except Exception as exc:
            result['statistics']=dict(status='failed',error_type=type(exc).__name__)
            if config.end_day:result['status']='maps_complete_statistics_failed'
            manifest.write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
            raise RuntimeError('Maps completed; AOI statistics failed: '+str(exc)+'. Analyse the saved outputs with a new statistics label; no new inference is required.') from None
        manifest.write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    return result


def _scene_id(config,item):return item['Id'] if config.product in ('s2_l2a','s3syn300') else item['id']


def _scene_day(config,item):
    if config.product in ('viirs','modis','modis_aqua'):return item['analysis_date']
    stamp=item['ContentDate']['Start'] if config.product in ('s2_l2a','s3syn300') else item['properties']['datetime']
    acquired=datetime.fromisoformat(stamp.replace('Z','+00:00'))
    if acquired.tzinfo is None:raise ValueError('Catalogue acquisition timestamp must include a UTC offset')
    return acquired.astimezone(timezone.utc).date().isoformat()


def run_series(config):
    """One native map per matching/selected scene; no mosaicking or gap filling."""
    if config.aoi:config.aoi.verify()
    config=replace(config,output=labelled_output(config),output_label=output_label(config))
    items=discover_products(config)
    lookup={_scene_id(config,item):item for item in items}
    if len(lookup)!=len(items):raise ProviderError('Catalogue returned duplicate scene IDs')
    if config.product_ids:
        if any(key not in lookup for key in config.product_ids):raise ValueError('Selected scene is not in the requested date range/AOI catalogue')
        items=[lookup[key] for key in config.product_ids]
    if not items:raise ValueError('No scenes match the requested date range and area')
    items=sorted(items,key=lambda item:(_scene_day(config,item),_scene_id(config,item)))
    jobs=[]
    for item in items:
        if config.aoi:config.aoi.verify()
        key=_scene_id(config,item);day=_scene_day(config,item)
        if not config.day<=day<=config.last_day:raise ValueError('Catalogue scene date lies outside the requested range')
        job=replace(config,day=day,end_day='',product_ids=(key,))
        output=scene_output(job,source_product_identity(config.product,item))
        jobs.append((key,replace(job,output=output),item))
    manifest=config.output.with_name(config.output.stem+'_timeseries.json')
    if config.output.exists() or manifest.exists():raise FileExistsError('Choose a new time-series output prefix')
    if len({job.output.name.casefold() for _,job,_ in jobs}) != len(jobs):
        raise ValueError('Multiple catalogue items share the same source product name; select one revision')
    for _,job,_ in jobs:
        companions=[job.output,job.output.with_suffix('.json')]
        if job.output.suffix.lower()!='.nc':companions.extend(job.output.with_name(job.output.stem+suffix) for suffix in ('_qa.tif','_vegetation.tif'))
        if any(path.exists() for path in companions):raise FileExistsError('A time-series output already exists; choose a new prefix')
    manifest.parent.mkdir(parents=True,exist_ok=True)
    record=dict(schema='lfmc_time_series_v1',status='running',request=plan(config),
        output_label=config.output_label,model_provenance=model_provenance(),
        selected_product_ids=list(config.product_ids),scene_count=len(jobs),completed_count=0,maps=[])
    def save():
        partial=manifest.with_suffix('.part.json')
        partial.write_text(json.dumps(record,indent=2,allow_nan=False)+'\n');partial.replace(manifest)
    save()
    for index,(key,job,item) in enumerate(jobs,1):
        print(f'[{index}/{len(jobs)}] {job.day} {key}',flush=True)
        try:
            if config.aoi:config.aoi.verify()
            result=_run_single(job,item=item)
        except Exception as exc:
            record.update(status='failed',failed_product_id=key,failed_date=job.day,error_type=type(exc).__name__);save();raise
        record['maps'].append(dict(product_id=key,date=job.day,output=job.output.name,
            cloud_cover=lookup[key].get('cloud_cover'),cloud_cover_source=lookup[key].get('cloud_cover_source'),
            provenance=job.output.with_suffix('.json').name,valid_pixels=result['valid_pixels'],classes=result['classes'],outputs=result['outputs']))
        record['completed_count']=index;save()
    record['status']='passed';save()
    return record


def _run_single(config,*,item=None):
    """Acquire required inputs and predict within the initial adapter limits."""
    if config.aoi:config.aoi.verify()
    if config.product=='s3syn300':
        from .sentinel3 import run_native
        return run_native(config,item=item)
    access=access_check(config)
    if config.product=='s2_l2a' and not access['cdse_s3'] and not access['cdse_oauth']: raise ProviderError('Configure CDSE S3 keys or OAuth credentials')
    if config.product in ('viirs','modis','modis_aqua') and not access['earthdata_config_present']:raise ProviderError('Configure Earthdata in external netrc or EARTHDATA_TOKEN/USERNAME/PASSWORD')
    if not access['cds_config_present']: raise ProviderError('Configure CDS credentials and accept AgERA5 terms')
    request=plan(config);key=preparation_key(request)
    if item is None:
        products=discover_products(config)
        if config.product_ids:
            lookup={_scene_id(config,p):p for p in products}
            if any(i not in lookup for i in config.product_ids): raise ValueError('Explicit product is not in the requested day/tile/AOI catalogue after cloud filtering')
            products=[lookup[i] for i in config.product_ids]
        elif len(products)!=1:
            raise ValueError(f'Found {len(products)} products; specify one product_id to avoid implicit scene or reprocessing selection')
        if len(products)!=1: raise ValueError('Initial workflow supports one product/granule per AOI/date')
        item=products[0]
    product=item
    identity=source_product_identity(config.product,product)
    output=scene_output(config,identity)
    if output.exists() or output.with_suffix('.json').exists():
        raise FileExistsError('Choose a new output path')
    folder=config.cache_dir/key;folder.mkdir(parents=True,exist_ok=True)
    optical=folder/'optical.tif';terrain=folder/'terrain.tif';landcover=folder/'landcover.tif'
    if config.product=='s2_l2a':
        if access['cdse_s3']:
            safe,acquisition=acquire_s2_windows(product,config.bounds,config.cache_dir/'cdse')
        else:
            print('CDSE: downloading complete required SAFE files via OData. S3 keys enable AOI window reads.',flush=True)
            safe=CDSE().acquire(product['Id'],config.cache_dir/'cdse')
            acquisition=dict(method='CDSE OData complete required SAFE files')
        optical_info=prepare_safe(safe,config.bounds,optical)
        optical_info['acquisition']=acquisition
        product_info={k:product[k] for k in ['Id','Name','S3Path','ContentDate','cloud_cover','cloud_cover_source']};sensor=product['Name'].split('_')[0]
    elif config.product in ('viirs','modis','modis_aqua'):
        path=earthdata.acquire(product,config.cache_dir/config.product)
        prepare=prepare_hdf if config.product=='viirs' else prepare_modis
        optical_info=prepare(path,config.bounds,optical,item=product)
        product_info=product;sensor=product['sensor']
    else:
        sensor=landsat_provider.sensor_for_item(product) if config.sensor==LANDSAT_COMBINED else config.sensor
        paths=landsat_provider.acquire(product,config.bounds,config.cache_dir/'landsat')
        optical_info=prepare_assets(paths,optical,item=product,sensor=sensor)
        product_info=dict(id=product['id'],properties=product['properties'],cloud_cover=product.get('cloud_cover'),cloud_cover_source=product.get('cloud_cover_source'),assets={k:dict(href=product['assets'][k]['href']) for k in landsat_provider.ASSETS})
    with rasterio.open(optical) as ds: target=ds.profile.copy()
    dem=aw3d30(config.bounds,config.cache_dir/'aw3d30')
    terrain_info=prepare_terrain(dem,config.bounds,target,terrain)
    lc_info=worldcover(config.bounds,config.landcover_year,target,landcover,config.cache_dir/'worldcover')
    weather=agera5.prepare('',[config.day],config.cache_dir/'weather',aoi_name='aoi_'+key,aoi_bbox=config.bounds)
    sources=dict(request=request,product=product_info,source_product=identity,optical=optical_info,terrain=terrain_info,landcover=lc_info)
    reference=None
    if config.downscale:
        reference=folder/'hres_reference.tif'
        sources['reference']=prepare_reference(config.bounds,config.cache_dir/'hres',reference)
        sources['reference']['requested_date_cycle_equivalence_verified']=False
    (folder/'preparation.json').write_text(json.dumps(sources,indent=2)+'\n')
    if config.aoi:config.aoi.verify()
    return predict_rasters(optical=optical,terrain=terrain,landcover=landcover,weather=weather[config.day],models_dir=config.models_dir,
                           day=config.day,output=output,source_manifest=sources,reference=reference,product=config.product,sensor=sensor,
                           output_label=config.output_label,
                           aoi_geometry=config.aoi.geometry if config.aoi else None)
