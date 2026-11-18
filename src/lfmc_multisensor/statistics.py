"""Native-pixel AOI summaries, figures and paired-date exploratory contrasts.

Raster windows and disk-backed quantile buffers bound memory use. No provider
access, model loading, resampling or independent-pixel significance tests.
"""
from contextlib import ExitStack
import csv
from datetime import date,datetime,timedelta
import hashlib
from itertools import combinations
import json
import math
import os
from pathlib import Path
import tempfile

import numpy as np

from .aoi import polygon_mask,prepare_polygon
from .naming import labelled_output,output_label
from .providers.http import file_sha256
from .routing import dynamic_world_route,worldcover_route

GROUPS={'All':None,'Grass':30,'Shrub':20,'Tree':10}
COLOURS={'All':'#24557f','Grass':'#bc7b20','Shrub':'#7950a3','Tree':'#147a55'}


def _leaf(name):
    if not isinstance(name,str) or Path(name).name!=name or name in ('.','..'):raise ValueError('Invalid output filename in statistics source')
    return name


def source_maps(source):
    source=Path(source).resolve()
    if not source.is_file():raise ValueError('Statistics source not found: '+source.name)
    if source.suffix.lower() in ('.tif','.tiff','.nc'):return [(source,None)],'single-map'
    data=json.loads(source.read_text(encoding='utf-8'))
    if data.get('schema')=='lfmc_time_series_v1':
        entries=data.get('maps',[])
        if not entries:raise ValueError('The selected series has no completed maps')
        result=[]
        for entry in entries:
            name=_leaf(entry['output']);expected=entry['outputs'].get(name)
            if not expected:raise ValueError('Series map is missing its checksum')
            result.append((source.parent/name,expected))
        if len({path.resolve() for path,_ in result})!=len(result):raise ValueError('Statistics source repeats the same map')
        return result,data.get('status','unknown')
    if data.get('schema') in ('lfmc_prediction_v1','lfmc_native_prediction_v1'):
        names=[name for name in data['outputs'] if Path(name).suffix.lower() in ('.tif','.tiff','.nc') and Path(name).stem.casefold()==source.stem.casefold()]
        if len(names)!=1:raise ValueError('Select a single LFMC map or a time-series manifest')
        return [(source.parent/_leaf(names[0]),data['outputs'][names[0]])],'single-map'
    raise ValueError('Select a generated LFMC map, its provenance JSON or a time-series manifest')


def default_statistics_source(config, prefix):
    if config.end_day:
        return prefix.with_name(prefix.stem+'_timeseries.json')
    if prefix.is_file() or not prefix.parent.is_dir():
        return prefix
    matches = []
    for path in prefix.parent.iterdir():
        if path.suffix.lower() != '.json' or not path.name.startswith(prefix.stem+'_'):
            continue
        try:
            record = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if not isinstance(record, dict) or record.get('schema') not in ('lfmc_prediction_v1','lfmc_native_prediction_v1'):
            continue
        if (record.get('date'),record.get('product'),record.get('output_label')) != (config.day,config.product,output_label(config)):
            continue
        source_id = (record.get('source_manifest') or {}).get('source_product',{}).get('catalogue_id')
        if config.product_ids and source_id not in config.product_ids:
            continue
        if config.sensor and '/' not in config.sensor and record.get('sensor') != config.sensor:
            continue
        matches.append(path)
    if len(matches)>1:
        raise ValueError('Multiple LFMC maps match this request. Choose a map or provenance file as the statistics source.')
    return matches[0] if matches else prefix


class MapReader:
    """Verified inference outputs, including the exact vegetation routing."""
    def __init__(self,path,config,expected=None):
        self.path=Path(path);self.config=config;self.expected=expected;self.assets={};self.stack=ExitStack()

    def asset(self,path,expected):
        if not expected or not Path(path).is_file() or file_sha256(path)!=expected:raise ValueError('Missing or changed statistics input: '+Path(path).name)
        self.assets[Path(path)]=expected

    def __enter__(self):
        try:
            if not self.path.with_suffix('.json').is_file():raise ValueError('LFMC provenance JSON is required: '+self.path.with_suffix('.json').name)
            self.record=json.loads(self.path.with_suffix('.json').read_text(encoding='utf-8'))
            expected=self.record['outputs'].get(self.path.name)
            if self.expected and self.expected!=expected:raise ValueError('Series and map provenance checksums disagree')
            self.asset(self.path,expected)
            self.day=self.record['date'];date.fromisoformat(self.day)
            self.native=self.path.suffix.lower()=='.nc';self.classes=None;self.class_kind='stored'
            if self.native:
                import netCDF4
                self.ds=self.stack.enter_context(netCDF4.Dataset(self.path))
                for name in ['LFMC','QA','lat','lon','dominant_class']:
                    if name not in self.ds.variables:raise ValueError('Native map lacks '+name)
                self.shape=self.ds['LFMC'].shape
                if len(self.shape)!=2 or any(self.ds[name].shape!=self.shape for name in ['QA','lat','lon','dominant_class']):raise ValueError('Native map variables have different grids')
                self.classes=self.ds['dominant_class']
                digest=hashlib.sha256()
                for name in ['lat','lon']:
                    for row in range(0,self.shape[0],256):digest.update(np.ma.asarray(self.ds[name][row:row+256,:],dtype='float64').filled(np.nan).tobytes())
                self.grid=('native',self.shape,digest.hexdigest())
            else:
                import rasterio
                from pyproj import Transformer
                self.stack.enter_context(rasterio.Env(GDAL_CACHEMAX=64*1024*1024))
                self.ds=self.stack.enter_context(rasterio.open(self.path));self.shape=self.ds.shape
                if self.ds.crs is None:raise ValueError('LFMC map has no CRS')
                qa_path=self.path.with_name(self.path.stem+'_qa.tif');self.asset(qa_path,self.record['outputs'].get(qa_path.name))
                self.qa=self.stack.enter_context(rasterio.open(qa_path));self.check_grid(self.qa)
                self.grid=('affine',str(self.ds.crs),tuple(self.ds.transform),self.shape)
                self.project=Transformer.from_crs(self.ds.crs,4326,always_xy=True)
                classes=self.path.with_name(self.path.stem+'_vegetation.tif')
                if self.config.statistics_by_vegetation and classes.name in self.record['outputs']:
                    self.asset(classes,self.record['outputs'][classes.name])
                    self.classes=self.stack.enter_context(rasterio.open(classes));self.check_grid(self.classes)
                elif self.config.statistics_by_vegetation:
                    from .workflow import preparation_key
                    info=self.record.get('inputs',{}).get('landcover',{})
                    request=self.record.get('source_manifest',{}).get('request')
                    if not info or not request:raise ValueError('This older map has no vegetation companion or recoverable land-cover provenance')
                    classes=self.config.cache_dir/preparation_key(request)/_leaf(info['name'])
                    self.asset(classes,info.get('sha256'))
                    self.classes=self.stack.enter_context(rasterio.open(classes));self.check_grid(self.classes)
                    self.class_kind=self.record['landcover_kind']
            request=self.record.get('source_manifest',{}).get('request',{})
            self.context=dict(product=self.record.get('product'),weather_mode=self.record.get('weather_mode'),
                landcover=request.get('landcover'),model_sha256=self.record.get('model_sha256'))
            # Policy spelling changed between releases; compare the actual map year.
            if self.context['landcover']:self.context['landcover']={key:self.context['landcover'].get(key) for key in ['source','year']}
            self.polygon=prepare_polygon(self.config.aoi.geometry) if self.config.aoi else None
            return self
        except Exception:
            self.stack.close();raise

    def __exit__(self,kind,value,traceback):
        self.stack.close()
        if kind is None and any(file_sha256(path)!=sha for path,sha in self.assets.items()):raise ValueError('Statistics input changed during analysis')

    def check_grid(self,other):
        if (other.crs,other.transform,other.shape)!=(self.ds.crs,self.ds.transform,self.shape):raise ValueError('Vegetation/QA grid differs from LFMC map')

    def windows(self):
        for row in range(0,self.shape[0],256):
            for col in range(0,self.shape[1],256):
                height,width=min(256,self.shape[0]-row),min(256,self.shape[1]-col)
                index=np.s_[row:row+height,col:col+width]
                if self.native:
                    values=np.ma.asarray(self.ds['LFMC'][index],dtype='float32').filled(np.nan)
                    qa=np.ma.asarray(self.ds['QA'][index]).filled(255)
                    lon=np.ma.asarray(self.ds['lon'][index],dtype=float).filled(np.nan);lat=np.ma.asarray(self.ds['lat'][index],dtype=float).filled(np.nan)
                    classes=np.ma.asarray(self.classes[index]).filled(255)
                else:
                    from rasterio.windows import Window
                    win=Window(col,row,width,height)
                    values=self.ds.read(1,window=win,masked=True).astype('float32').filled(np.nan);qa=self.qa.read(1,window=win)
                    rr,cc=np.indices((height,width));x,y=self.ds.window_transform(win)*(cc+.5,rr+.5);lon,lat=self.project.transform(x,y)
                    classes=None
                    if self.classes is not None:
                        if self.class_kind=='stored':classes=self.classes.read(1,window=win)
                        else:
                            bands=self.classes.read(window=win,masked=True).astype(float).filled(np.nan)
                            if self.class_kind=='worldcover_fractions':classes=worldcover_route(bands,self.classes.descriptions)
                            elif self.class_kind=='dynamic_world_probabilities':classes=dynamic_world_route(bands[:9])[0]
                            else:raise ValueError('Unsupported legacy vegetation routing')
                w,s,e,n=self.config.bounds
                inside=(qa!=4)&(qa!=255)&np.isfinite(lon)&np.isfinite(lat)
                inside &= polygon_mask(self.polygon,lon,lat) if self.polygon is not None else (lon>=w)&(lon<=e)&(lat>=s)&(lat<=n)
                valid=inside&(qa==0)&np.isfinite(values)
                if classes is not None and (valid&~np.isin(classes,[10,20,30])).any():raise ValueError('Valid LFMC pixels have inconsistent vegetation routing')
                yield index,values,classes,inside,valid


class Values:
    """Exact linear quantiles using disk-backed partitioning, streamed moments."""
    def __init__(self,path):
        self.path=path;self.stream=path.open('wb');self.n=0;self.mean=0.;self.m2=0.;self.minimum=None;self.maximum=None

    def add(self,values):
        values=np.asarray(values,dtype='float32');n=values.size
        if not n:return
        mean=float(np.mean(values,dtype='float64'));delta=mean-self.mean;total=self.n+n
        self.m2+=float(np.sum((values.astype(float)-mean)**2))+delta*delta*self.n*n/total
        self.mean+=delta*n/total;self.n=total
        low,high=float(np.min(values)),float(np.max(values))
        self.minimum=low if self.minimum is None else min(low,self.minimum)
        self.maximum=high if self.maximum is None else max(high,self.maximum)
        values.tofile(self.stream)

    def finish(self):
        self.stream.close()
        result=dict(n=self.n,mean=self.mean if self.n else None,std=math.sqrt(max(0,self.m2)/(self.n-1)) if self.n>1 else None,
            minimum=self.minimum,maximum=self.maximum,p05=None,p25=None,median=None,p75=None,p95=None)
        if self.n:
            data=np.memmap(self.path,dtype='float32',mode='r+',shape=(self.n,))
            positions=(self.n-1)*np.array([.05,.25,.5,.75,.95]);lo=np.floor(positions).astype(int);hi=np.ceil(positions).astype(int)
            data.partition(np.unique(np.r_[lo,hi]))
            for name,l,h,p in zip(['p05','p25','median','p75','p95'],lo,hi,positions):result[name]=float(data[l])+(float(data[h])-float(data[l]))*(p-l)
            data.flush();data._mmap.close()
        self.path.unlink();return result

    def close(self):self.stream.close()


def temporal_comparisons(rows,config):
    """Paired dates are units; optional circular block intervals, never pixels."""
    index={}
    for row in rows:index.setdefault(row['map_index'],{})[row['vegetation']]=row
    dates=[groups['All']['date'] for _,groups in sorted(index.items())]
    differences=[];comparisons=[];centre=config.statistics_centre
    for pair,(left,right) in enumerate(combinations(['Grass','Shrub','Tree'],2)):
        values=[];paired_dates=[]
        for _,groups in sorted(index.items()):
            a,b=groups.get(left,{}).get(centre),groups.get(right,{}).get(centre)
            difference=a-b if a is not None and b is not None else None
            differences.append(dict(map_index=groups['All']['map_index'],date=groups['All']['date'],left=left,right=right,difference=difference))
            if difference is not None:values.append(difference);paired_dates.append(groups['All']['date'])
        aggregate=np.median if centre=='median' else np.mean
        entry=dict(left=left,right=right,paired_dates=len(values),temporal_statistic=centre,
            estimate=float(aggregate(values)) if values else None,units='LFMC percentage points',status='descriptive_only',
            lower=None,upper=None,interval_excludes_zero=None)
        if config.statistics_comparisons:
            n=len(values);block=config.statistics_block_length
            if len(set(dates))!=len(dates):entry['status']='duplicate_dates'
            elif n<max(12,4*block):entry['status']='insufficient_paired_dates'
            elif len(set(np.diff([date.fromisoformat(day).toordinal() for day in paired_dates])))!=1:entry['status']='irregular_dates'
            else:
                data=np.asarray(values);rng=np.random.default_rng(7341+pair);replicates=[]
                batch=min(100,max(1,2_000_000//n))
                for start in range(0,2000,batch):
                    starts=rng.integers(0,n,size=(min(batch,2000-start),math.ceil(n/block)))
                    selected=((starts[:,:,None]+np.arange(block))%n).reshape(len(starts),-1)[:,:n]
                    replicates.extend(aggregate(data[selected],axis=1).tolist())
                # Bonferroni covers the three planned pairwise intervals.
                lo,hi=np.quantile(replicates,[.05/6,1-.05/6])
                entry.update(status='exploratory_interval',lower=float(lo),upper=float(hi),interval_excludes_zero=bool(lo>0 or hi<0),
                    method='paired-date circular block percentile bootstrap',block_length=block,replicates=2000,seed=7341+pair,
                    familywise_confidence=.95,planned_comparisons=3,stationarity_assumed_not_tested=True,
                    uncertainty_scope='Temporal resampling of model-derived summaries; excludes model/field uncertainty')
        comparisons.append(entry)
    return differences,comparisons


def _csv(path,rows):
    with path.open('w',newline='',encoding='utf-8') as stream:
        if rows:
            fields=list(dict.fromkeys(key for row in rows for key in row))
            writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader();writer.writerows(rows)


def analyse(config,source=None):
    """Summarise saved outputs inside the currently selected AOI, offline."""
    config.cache_dir.mkdir(parents=True,exist_ok=True)
    os.environ.setdefault('MPLCONFIGDIR',str(config.cache_dir/'matplotlib'))
    try:import matplotlib
    except ImportError:raise RuntimeError('AOI figures require Matplotlib; install the optional statistics dependencies inside your Conda environment') from None
    matplotlib.use('Agg',force=True)
    if config.aoi:config.aoi.verify()
    prefix=labelled_output(config)
    source=source or config.statistics_source or default_statistics_source(config,prefix)
    maps,series_status=source_maps(source)
    prefix.parent.mkdir(parents=True,exist_ok=True);config.cache_dir.mkdir(parents=True,exist_ok=True)
    names=[prefix.stem+'_aoi_statistics.csv',prefix.stem+'_aoi_statistics.json',prefix.stem+'_aoi_timeseries.png',prefix.stem+'_aoi_timeseries.pdf']
    if config.statistics_by_vegetation:names += [prefix.stem+'_vegetation_differences.csv',prefix.stem+'_vegetation_comparisons.csv',prefix.stem+'_vegetation_differences.png',prefix.stem+'_vegetation_differences.pdf']
    if any((prefix.parent/name).exists() for name in names):raise FileExistsError('Statistics outputs already exist; choose a new filename label or output directory')
    groups=GROUPS if config.statistics_by_vegetation else {'All':None}
    rows=[];sources=[];common=None;class_reference=None;grid=None;context=None;model_info=None;model_origin='not-recorded'
    with tempfile.TemporaryDirectory(prefix='lfmc-statistics-',dir=config.cache_dir) as scratch_name:
        scratch=Path(scratch_name)
        try:
            if config.statistics_support=='common':
                for number,(path,expected) in enumerate(maps):
                    print(f'Common support {number+1}/{len(maps)}: {path.name}',flush=True)
                    with MapReader(path,config,expected) as reader:
                        if grid is None:
                            grid=reader.grid
                            common=np.memmap(scratch/'common.bin',dtype=bool,mode='w+',shape=reader.shape);common[:]=True
                            if config.statistics_by_vegetation:class_reference=np.memmap(scratch/'classes.bin',dtype='uint8',mode='w+',shape=reader.shape)
                        elif reader.grid!=grid:raise ValueError('Common support requires identical native grids and coordinates; choose available support for shifted grids')
                        for index,values,classes,inside,valid in reader.windows():
                            common[index] &= valid
                            if class_reference is not None:
                                if number==0:class_reference[index]=classes
                                elif np.any(common[index]&(class_reference[index]!=classes)):raise ValueError('Vegetation routing differs on common pixels; use compatible maps')
                common.flush()
            for number,(path,expected) in enumerate(maps):
                print(f'Statistics {number+1}/{len(maps)}: {path.name}',flush=True)
                with MapReader(path,config,expected) as reader:
                    if context is None:
                        context=reader.context;model_info=reader.record.get('model_provenance')
                        if model_info:model_origin='source-provenance'
                        else:
                            from .models import model_provenance
                            registered=model_provenance()
                            if context['model_sha256']=={name:spec['sha256'] for name,spec in registered['artifacts'].items()}:
                                model_info=registered;model_origin='registry matched by exact source model checksums'
                    elif reader.context!=context:raise ValueError('Statistics maps must use one product, weather mode, land-cover snapshot and model set')
                    stores={group:Values(scratch/(group+'.bin')) for group in groups}
                    counts={group:dict(group_pixels=0,available_valid_pixels=0) for group in groups}
                    try:
                        for index,values,classes,inside,valid in reader.windows():
                            used=valid if common is None else valid&common[index]
                            for group,code in groups.items():
                                population=inside if code is None else inside&(classes==code)
                                counts[group]['group_pixels']+=int(population.sum())
                                counts[group]['available_valid_pixels']+=int((valid&population).sum())
                                stores[group].add(values[used&population])
                        for group,store in stores.items():
                            values=store.finish();population=counts[group]['group_pixels']
                            rows.append(dict(map_index=number,date=reader.day,source=path.name,vegetation=group,
                                product=reader.record.get('product',''),weather_mode=reader.record.get('weather_mode',''),
                                landcover_year=(context.get('landcover') or {}).get('year'),
                                model_set_version=(model_info or {}).get('model_set',{}).get('version'),
                                sensor=reader.record.get('sensor',''),support=config.statistics_support,
                                **counts[group],valid_fraction=values['n']/population if population else None,**values))
                    finally:
                        for store in stores.values():store.close()
                    sources.append(dict(map=path.name,date=reader.day,assets=[dict(name=p.name,sha256=sha) for p,sha in reader.assets.items()]))
        finally:
            for array in [common,class_reference]:
                if array is not None:array.flush();array._mmap.close()
        differences,comparisons=temporal_comparisons(rows,config) if config.statistics_by_vegetation else ([],[])
        if config.aoi:config.aoi.verify()
        with tempfile.TemporaryDirectory(prefix='lfmc-statistics-export-',dir=prefix.parent) as staged_name:
            staged=Path(staged_name)
            _csv(staged/names[0],rows)
            if differences:
                _csv(staged/(prefix.stem+'_vegetation_differences.csv'),differences)
                _csv(staged/(prefix.stem+'_vegetation_comparisons.csv'),comparisons)
            from .statistics_plot import plot_summary
            plot_summary(staged,prefix.stem,output_label(config),rows,differences,comparisons,config)
            record=dict(schema='lfmc_aoi_statistics_v1',centre=config.statistics_centre,support=config.statistics_support,
                by_vegetation=config.statistics_by_vegetation,source_status=series_status,map_count=len(maps),
                date_count=len({row['date'] for row in rows}),output_label=output_label(config),
                aoi=config.aoi.metadata() if config.aoi else dict(kind='bounds',bounds=list(config.bounds)),
                context=context,model_provenance=model_info,model_version_source=model_origin,sources=sources,rows=rows,comparisons=comparisons,
                comparison_intervals_requested=config.statistics_comparisons,block_length=config.statistics_block_length,
                minimum_paired_dates=max(12,4*config.statistics_block_length),
                definitions=dict(valid='QA 0 and finite LFMC, pixel centre inside selected AOI and original map footprint',
                    weighting='Unweighted native pixel centres; no resampling or area weighting',
                    All='All AOI centres within the map footprint, including non-routed centres in the coverage denominator',
                    vegetation='Dominant model-routing categories: Grass 30, Shrub 20, Tree 10',
                    spread='Spatial IQR for median plots, spatial sample standard deviation (ddof=1) for mean plots; not prediction confidence',
                    coverage='n / group_pixels; available_valid_pixels is reported separately for common support',
                    contrasts='Paired differences of per-map class summaries, in LFMC percentage points',
                    intervals='Optional exploratory paired-date circular block percentile intervals; Bonferroni for 3 pairs; no independent-pixel tests',
                    interval_requirements='Common support, unique regularly spaced paired dates, at least max(12,4*block_length); stationarity assumed, not verified',
                    incomplete='Only completed maps in the selected source are analysed; no missing-date interpolation'),
                method_sources=['https://bashtage.github.io/arch/bootstrap/generated/arch.bootstrap.CircularBlockBootstrap.html'])
            record['outputs']={name:file_sha256(staged/name) for name in names if not name.endswith('_aoi_statistics.json')}
            (staged/names[1]).write_text(json.dumps(record,indent=2,allow_nan=False)+'\n',encoding='utf-8')
            for name in names:
                if (prefix.parent/name).exists():raise FileExistsError('A statistics output appeared during analysis; choose a new label')
            for name in names:(staged/name).rename(prefix.parent/name)
    return dict(status='passed',map_count=len(maps),date_count=record['date_count'],
        outputs={name:file_sha256(prefix.parent/name) for name in names},comparisons=comparisons)
