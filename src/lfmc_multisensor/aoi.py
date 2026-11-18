"""Polygon AOIs with explicit CRS, component fingerprints and centre masking."""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import numpy as np
from pyproj import CRS,Transformer
from .providers.http import file_sha256


@dataclass(frozen=True)
class PolygonAOI:
    path: Path | None
    _geometry_json: str
    bounds: tuple
    crs: str
    feature_count: int
    components: tuple
    geometry_sha256: str

    @property
    def geometry(self):return json.loads(self._geometry_json)

    def verify(self):
        if any(not p.exists() or file_sha256(p)!=digest for p,digest in self.components):
            raise ValueError('AOI files changed after configuration was loaded; reload the configuration')

    def metadata(self):
        return dict(kind='polygon',name=self.path.name,source_crs=self.crs,feature_count=self.feature_count,
            geometry_sha256=self.geometry_sha256,components={p.name:digest for p,digest in self.components},
            selection='Pixel centres on or inside the polygon union; holes excluded')


@dataclass(frozen=True)
class CircleAOI(PolygonAOI):
    latitude: float
    longitude: float
    radius_km: float

    def metadata(self):
        return dict(kind='point_radius',source_crs='EPSG:4326',latitude=self.latitude,
            longitude=self.longitude,radius_km=self.radius_km,ellipsoid='WGS84',boundary_vertices=720,
            geometry_sha256=self.geometry_sha256,
            selection='Pixel centres on or inside a 720-vertex geodesic circle polygon')


def point_radius(latitude,longitude,radius_km):
    """Construct a reproducible WGS84 geodesic buffer without a source file."""
    from pyproj import Geod
    from shapely.geometry import Polygon,mapping
    values=(latitude,longitude,radius_km)
    if any(isinstance(value,(bool,str)) for value in values):raise ValueError('Latitude, longitude and radius must be numbers')
    try:latitude,longitude,radius_km=map(float,values)
    except (ValueError,TypeError):raise ValueError('Latitude, longitude and radius must be numbers') from None
    if not np.isfinite([latitude,longitude,radius_km]).all():raise ValueError('Latitude, longitude and radius must be finite')
    if not (-90<latitude<90 and -180<=longitude<=180):raise ValueError('Enter WGS84 latitude (-90 to 90) and longitude (-180 to 180)')
    if radius_km<=0:raise ValueError('Radius must be greater than zero kilometres')
    # No circle over 60 km can meet the existing one-degree latitude limit.
    # Reject before forwarding huge distances that could wrap around the globe.
    if radius_km>60:raise ValueError('Radius exceeds the current one-degree AOI limit')
    azimuth=np.arange(720,dtype=float)*.5
    lon,lat,_=Geod(ellps='WGS84').fwd(np.full(720,longitude),np.full(720,latitude),azimuth,np.full(720,radius_km*1000))
    polygon=Polygon(np.column_stack([lon,lat]));w,s,e,n=polygon.bounds
    if not polygon.is_valid or polygon.is_empty or not (w<e and s<n):raise ValueError('Radius is too small to construct a valid AOI')
    if e-w>=180:raise ValueError('Point-radius AOIs cannot cross the antimeridian')
    if not (-60<=s<n<=60):raise ValueError('Initial workflow requires the whole circle within 60S–60N')
    if e-w>1 or n-s>1:raise ValueError('Initial workflow is limited to AOIs at most one degree across; reduce the radius')
    payload=json.dumps(mapping(polygon),sort_keys=True,separators=(',',':'),allow_nan=False).encode()
    return CircleAOI(None,payload.decode(),tuple(polygon.bounds),'EPSG:4326',1,(),hashlib.sha256(payload).hexdigest(),latitude,longitude,radius_km)


@dataclass(frozen=True)
class SquareAOI(PolygonAOI):
    latitude: float
    longitude: float
    side_km: float

    def metadata(self):
        return dict(kind='point_square',source_crs='EPSG:4326',latitude=self.latitude,
            longitude=self.longitude,side_km=self.side_km,ellipsoid='WGS84',boundary_vertices=256,
            construction='East/north square in a local azimuthal equidistant projection centred on the point',
            geometry_sha256=self.geometry_sha256,selection='Pixel centres on or inside the square polygon')


def point_square(latitude,longitude,side_km):
    """A metric square centred on the point; side length, rather than radius."""
    from shapely.geometry import Polygon,mapping
    if isinstance(side_km,(bool,str)):raise ValueError('Square side length must be a number in kilometres')
    try:side_km=float(side_km)
    except (TypeError,ValueError):raise ValueError('Square side length must be a number in kilometres') from None
    if not np.isfinite(side_km) or side_km<=0:raise ValueError('Square side length must be finite and greater than zero')
    centre=point_radius(latitude,longitude,side_km/2)
    latitude,longitude=centre.latitude,centre.longitude
    local=CRS.from_proj4(f'+proj=aeqd +lat_0={latitude} +lon_0={longitude} +datum=WGS84 +units=m +no_defs')
    half=side_km*500
    corners=np.array([[-half,-half],[half,-half],[half,half],[-half,half]])
    steps=np.linspace(0,1,64,endpoint=False)[:,None]
    xy=np.concatenate([a+(b-a)*steps for a,b in zip(corners,np.roll(corners,-1,axis=0))])
    lon,lat=Transformer.from_crs(local,4326,always_xy=True).transform(xy[:,0],xy[:,1])
    polygon=Polygon(np.column_stack([lon,lat]));w,s,e,n=polygon.bounds
    if polygon.is_empty or not polygon.is_valid or not np.isfinite(polygon.bounds).all():raise ValueError('Cannot construct a valid square AOI')
    if e-w>=180:raise ValueError('Square AOIs cannot cross the antimeridian')
    if not (-60<=s<n<=60):raise ValueError('Initial workflow requires the whole square within 60S–60N')
    if e-w>1 or n-s>1:raise ValueError('Initial workflow is limited to AOIs at most one degree across; reduce the square side length')
    payload=json.dumps(mapping(polygon),sort_keys=True,separators=(',',':'),allow_nan=False).encode()
    return SquareAOI(None,payload.decode(),tuple(polygon.bounds),'EPSG:4326',1,(),hashlib.sha256(payload).hexdigest(),latitude,longitude,side_km)


def load_polygon(path):
    from shapely.geometry import shape,mapping
    from shapely.ops import transform,unary_union
    from shapely.validation import explain_validity
    path=Path(path).resolve()
    if not path.is_file():raise ValueError('AOI file does not exist')
    if path.suffix.lower()=='.shp':
        import shapefile
        siblings={p.suffix.lower():p for p in path.parent.iterdir() if p.stem.lower()==path.stem.lower()}
        missing=[suffix for suffix in ('.shp','.shx','.dbf','.prj') if suffix not in siblings]
        if missing:raise ValueError('Shapefile requires matching .shp, .shx, .dbf and .prj files; missing '+', '.join(missing))
        try:crs=CRS.from_wkt(siblings['.prj'].read_text(encoding='utf-8-sig'))
        except (ValueError,UnicodeError) as exc:raise ValueError('Cannot read shapefile CRS from .prj') from exc
        files=tuple(siblings[s] for s in ('.shp','.shx','.dbf','.prj','.cpg') if s in siblings)
        hashes=tuple((p,file_sha256(p)) for p in files)
        try:
            with shapefile.Reader(shp=str(siblings['.shp']),shx=str(siblings['.shx']),dbf=str(siblings['.dbf'])) as reader:
                if reader.shapeType not in (5,15,25):raise ValueError('AOI shapefile must contain Polygon or MultiPolygon geometry')
                raw=[s.__geo_interface__ for s in reader.iterShapes()]
        except (OSError,shapefile.ShapefileException) as exc:raise ValueError('Cannot read AOI shapefile components') from exc
    elif path.suffix.lower() in ('.geojson','.json'):
        hashes=((path,file_sha256(path)),)
        data=json.loads(path.read_text(encoding='utf-8-sig'))
        if not isinstance(data,dict):raise ValueError('AOI GeoJSON must be a geometry, Feature or FeatureCollection object')
        if data.get('crs'):raise ValueError('GeoJSON AOIs must use WGS84 longitude/latitude without a legacy CRS member')
        crs=CRS.from_epsg(4326)
        if data.get('type')=='FeatureCollection':
            features=data.get('features')
            if not isinstance(features,list) or any(not isinstance(f,dict) for f in features):raise ValueError('Invalid GeoJSON feature collection')
            raw=[f.get('geometry') for f in features]
        else:raw=[data.get('geometry')] if data.get('type')=='Feature' else [data]
    else:raise ValueError('Choose a .shp shapefile or a WGS84 .geojson file')
    if not raw:raise ValueError('AOI contains no polygon features')
    polygons=[]
    project=Transformer.from_crs(crs,4326,always_xy=True).transform
    for index,item in enumerate(raw,1):
        if not isinstance(item,dict) or item.get('type') not in ('Polygon','MultiPolygon'):raise ValueError('Every AOI feature must be a non-empty Polygon or MultiPolygon')
        try:polygon=shape(item)
        except (ValueError,TypeError) as exc:raise ValueError(f'Cannot parse polygon geometry at feature {index}') from exc
        if polygon.is_empty or not polygon.is_valid:raise ValueError(f'Invalid AOI polygon at feature {index}: '+explain_validity(polygon))
        polygon=transform(project,polygon)
        if polygon.is_empty or not polygon.is_valid or not np.isfinite(polygon.bounds).all():raise ValueError('AOI cannot be transformed to a valid WGS84 polygon')
        polygons.append(polygon)
    union=unary_union(polygons)
    w,s,e,n=union.bounds
    if not (-180<=w<e<=180 and -90<=s<n<=90) or e-w>=180:raise ValueError('AOI must be a non-crossing WGS84 polygon')
    geometry=mapping(union);payload=json.dumps(geometry,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
    result=PolygonAOI(path,payload.decode(),tuple(union.bounds),crs.to_string(),len(polygons),hashes,hashlib.sha256(payload).hexdigest())
    result.verify()
    return result


def prepare_polygon(geometry):
    from shapely import prepare
    from shapely.geometry import shape
    polygon=shape(geometry) if isinstance(geometry,dict) else geometry
    if polygon.geom_type not in ('Polygon','MultiPolygon') or polygon.is_empty or not polygon.is_valid:
        raise ValueError('AOI mask requires a valid non-empty Polygon or MultiPolygon')
    prepare(polygon)
    return polygon


def polygon_mask(geometry,lon,lat):
    from shapely import intersects_xy
    polygon=prepare_polygon(geometry) if isinstance(geometry,dict) else geometry
    x,y=np.broadcast_arrays(np.asarray(lon,float),np.asarray(lat,float))
    valid=np.isfinite(x)&np.isfinite(y);result=np.zeros(x.shape,dtype=bool)
    result[valid]=intersects_xy(polygon,x[valid],y[valid])
    return result
