"""Explicit configuration for the initial single-scene multisensor workflow."""
from dataclasses import dataclass,field
from datetime import date,timedelta
from pathlib import Path
import json
import math


LANDSAT_COMBINED = 'L8/L9'
SENTINEL3_COMBINED = 'S3A/S3B'


@dataclass(frozen=True)
class RunConfig:
    bounds: tuple
    day: str
    models_dir: Path
    cache_dir: Path
    output: Path
    landcover_year: int = 2021
    landcover_policy: str = 'fixed-year'
    end_day: str = ''
    downscale: bool = False
    product_ids: tuple = ()
    tile: str = ""
    product: str = "s2_l2a"
    sensor: str = ""
    composite_policy: str = ""
    aoi_path: Path | None = None
    aoi_circle: tuple | None = None
    aoi_square: tuple | None = None
    output_label: str | None = None
    statistics: bool = False
    statistics_by_vegetation: bool = True
    statistics_centre: str = 'median'
    statistics_support: str = 'available'
    statistics_comparisons: bool = False
    statistics_block_length: int = 3
    statistics_source: Path | None = None
    max_cloud_cover: float | None = None
    include_unknown_cloud: bool = False
    aoi: object = field(default=None,init=False,repr=False,compare=False)

    def __post_init__(self):
        if sum(value is not None for value in (self.aoi_path,self.aoi_circle,self.aoi_square))>1:raise ValueError('Choose a polygon file, circle or square, not both or multiple AOIs')
        if self.aoi_path is not None or self.aoi_circle is not None or self.aoi_square is not None:
            from .aoi import load_polygon,point_radius,point_square
            if self.aoi_path is not None:aoi=load_polygon(self.aoi_path)
            elif self.aoi_circle is not None:
                if not isinstance(self.aoi_circle,tuple) or len(self.aoi_circle)!=3:raise ValueError('aoi_circle must contain latitude, longitude and radius_km')
                aoi=point_radius(*self.aoi_circle)
                object.__setattr__(self,'aoi_circle',(aoi.latitude,aoi.longitude,aoi.radius_km))
            else:
                if not isinstance(self.aoi_square,tuple) or len(self.aoi_square)!=3:raise ValueError('aoi_square must contain latitude, longitude and side_km')
                aoi=point_square(*self.aoi_square)
                object.__setattr__(self,'aoi_square',(aoi.latitude,aoi.longitude,aoi.side_km))
            if self.bounds is not None and (len(self.bounds)!=4 or any(abs(a-b)>1e-9 for a,b in zip(self.bounds,aoi.bounds))):
                raise ValueError('Provided bounds disagree with the polygon AOI')
            object.__setattr__(self,'aoi_path',aoi.path)
            object.__setattr__(self,'aoi',aoi)
            object.__setattr__(self,'bounds',aoi.bounds)
        elif self.bounds is None:raise ValueError('Specify aoi_path, aoi_circle, aoi_square or bounds')
        if self.output_label is not None:
            from .naming import validate_label
            object.__setattr__(self,'output_label',validate_label(self.output_label))
        if self.max_cloud_cover is not None:
            if isinstance(self.max_cloud_cover,bool) or not isinstance(self.max_cloud_cover,(int,float)) or not math.isfinite(self.max_cloud_cover) or not 0<=self.max_cloud_cover<=100:
                raise ValueError('Maximum cloud cover must be a finite percentage from 0 to 100, or null for no limit')
        for name in ('statistics','statistics_by_vegetation','statistics_comparisons','include_unknown_cloud'):
            if type(getattr(self,name)) is not bool:raise ValueError(name+' must be true or false')
        if self.statistics_centre not in ('median','mean'):raise ValueError('Choose median or mean for statistics')
        if self.statistics_support not in ('available','common'):raise ValueError('Choose available or common pixel support')
        if type(self.statistics_block_length) is not int or not 2<=self.statistics_block_length<=365:raise ValueError('Statistics block length must be an integer from 2 to 365 dates')
        if self.statistics_comparisons and (not self.statistics_by_vegetation or self.statistics_support!='common'):
            raise ValueError('Temporal comparison intervals require vegetation groups and common pixel support')
        d = date.fromisoformat(self.day)
        if d.isoformat()!=self.day:raise ValueError('Start/single date must use YYYY-MM-DD')
        if self.end_day:
            last=date.fromisoformat(self.end_day)
            if last.isoformat()!=self.end_day:raise ValueError('End date must use YYYY-MM-DD')
            if last<d:raise ValueError('End date must be on or after the start date')
        else:last=d
        if last==date.max:raise ValueError('End date must allow a next-day exclusive search boundary')
        if len(self.bounds) != 4 or not all(math.isfinite(v) for v in self.bounds):
            raise ValueError("bounds must be four finite WGS84 values")
        w,s,e,n = self.bounds
        if not (-180 <= w < e <= 180 and -60 <= s < n <= 60):
            raise ValueError("Initial workflow requires non-crossing bounds within 60S–60N")
        if (e-w) > 1 or (n-s) > 1:
            raise ValueError("Initial workflow is limited to AOIs at most one degree across")
        if type(self.landcover_year) is not int or self.landcover_year not in (2020,2021):
            raise ValueError('Select WorldCover 2021 (default) or 2020, independently of the acquisition year')
        if self.landcover_policy not in ('fixed-year','same-year-annual'):
            raise ValueError('landcover_policy must be fixed-year')
        # Accept older saved requests while recording the current policy honestly.
        object.__setattr__(self,'landcover_policy','fixed-year')
        if type(self.downscale) is not bool:
            raise ValueError("downscale must be true or false")
        if self.product not in ('s2_l2a','viirs','landsat_c2l2','modis','modis_aqua','s3syn300'):
            raise ValueError('Unsupported product; choose s2_l2a, viirs, landsat_c2l2, modis, modis_aqua or s3syn300')
        if self.product=='s3syn300':
            if self.sensor not in ('S3A','S3B',SENTINEL3_COMBINED):
                raise ValueError('Sentinel-3 requires an explicit sensor: S3A or S3B for one spacecraft, or S3A/S3B for both')
            if self.tile:raise ValueError('Sentinel-3 uses geolocated swaths; leave tile empty and select product_ids when ambiguous')
            if self.output.suffix.lower()!='.nc':raise ValueError('Sentinel-3 native geolocated output requires .nc')
        if self.product=='s2_l2a' and self.tile and (len(self.tile)!=5 or not self.tile.isalnum()):
            raise ValueError("Optional Sentinel-2 MGRS tile must have five characters (without T)")
        if self.product in ('viirs','modis','modis_aqua'):
            import re
            if self.tile and not re.fullmatch(r'h\d{2}v\d{2}',self.tile):raise ValueError('Specify the optional sinusoidal tile as hXXvYY')
            expected={'viirs':'Suomi NPP VIIRS','modis':'Terra MODIS','modis_aqua':'Aqua MODIS'}[self.product]
            if self.sensor not in ('',expected):raise ValueError('Sensor must match the selected published product domain')
            if self.composite_policy!='contains-analysis-date':raise ValueError('Eight-day products require explicit composite_policy: contains-analysis-date')
        elif self.composite_policy:
            raise ValueError('composite_policy applies only to eight-day products')
        if self.product=='landsat_c2l2' and self.sensor not in ('L8','L9',LANDSAT_COMBINED):
            raise ValueError('Landsat requires sensor L8 or L9 for one spacecraft, or L8/L9 for both')
        if self.product=='landsat_c2l2' and self.tile and (len(self.tile)!=6 or not self.tile.isdigit()):
            raise ValueError('Optional Landsat tile must be the six-digit WRS path/row')
        if not isinstance(self.product_ids,tuple):
            raise ValueError('product_ids must be a list of selected scene IDs')
        if any(not isinstance(key,str) or not key.strip() for key in self.product_ids):raise ValueError('Scene IDs must be non-empty strings')
        if len(set(self.product_ids))!=len(self.product_ids):raise ValueError('Selected scene IDs must be unique')
        if len(self.product_ids)>1 and not self.end_day:raise ValueError('Set an end date to process multiple scenes as a time series')

    @property
    def last_day(self):return self.end_day or self.day

    @property
    def search_end_exclusive(self):return str(date.fromisoformat(self.last_day)+timedelta(days=1))+'T00:00:00Z'

    @property
    def search_start(self):return self.day+'T00:00:00Z'

    @property
    def search_end_inclusive(self):return self.last_day+'T23:59:59.999999Z'


def load_config(path):
    path = Path(path).resolve()
    data = json.loads(path.read_text(encoding="utf-8"))
    if sum(data.get(key) is not None for key in ('aoi_path','aoi_circle','aoi_square','bounds'))>1:
        raise ValueError('Choose aoi_path, aoi_circle, aoi_square or bounds in JSON, not both or multiple AOIs')
    for key,size in [('aoi_circle','radius_km'),('aoi_square','side_km')]:
        if data.get(key) is not None:
            area=data[key]
            if not isinstance(area,dict) or set(area)!= {'latitude','longitude',size}:
                raise ValueError(f'{key} requires exactly latitude, longitude and {size}')
            data[key]=tuple(area[name] for name in ('latitude','longitude',size))
    data.setdefault('landcover_year',2021)
    data.setdefault('landcover_policy','fixed-year')
    for name in ("models_dir","cache_dir","output"):
        value = Path(data[name])
        data[name] = value if value.is_absolute() else path.parent/value
    if data.get('statistics_source'):
        value=Path(data['statistics_source']);data['statistics_source']=value if value.is_absolute() else path.parent/value
    else:data.pop('statistics_source',None)
    if data.get('aoi_path'):
        value=Path(data['aoi_path']);data['aoi_path']=value if value.is_absolute() else path.parent/value
    else:data.pop('aoi_path',None)
    data["bounds"] = tuple(data["bounds"]) if data.get('bounds') is not None else None
    data["product_ids"] = tuple(data.get("product_ids", []))
    return RunConfig(**data)
