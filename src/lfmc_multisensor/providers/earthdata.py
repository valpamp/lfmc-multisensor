"""NASA CMR discovery and standard external Earthdata authentication."""
from datetime import date,datetime,timedelta
import os
from pathlib import Path
import re
from urllib.parse import urlparse
import requests

from ..config import MODIS_COMBINED
from .http import ProviderError,request,download
from .cloud import annotate

CMR='https://cmr.earthdata.nasa.gov/search/granules.json'
PRODUCTS={'viirs':('VNP09H1','002','Suomi NPP VIIRS','.h5'),
          'modis':('MOD09A1','061','Terra MODIS','.hdf'),
          'modis_aqua':('MYD09A1','061','Aqua MODIS','.hdf')}


def composite_interval(day):
    day=date.fromisoformat(day);start=date(day.year,1,1)+timedelta(days=((day.timetuple().tm_yday-1)//8)*8)
    return start,min(start+timedelta(days=8),date(day.year+1,1,1))


def discover(config):
    if config.product=='modis' and config.sensor==MODIS_COMBINED:
        return _discover_collection(config,'modis') + _discover_collection(config,'modis_aqua')
    return _discover_collection(config,config.product)


def _discover_collection(config,product):
    short,version,sensor,extension=PRODUCTS[product]
    first,_=composite_interval(config.day);last,_=composite_interval(config.last_day);page=1;items=[];session=requests.Session()
    while True:
        response=request(session,'GET',CMR,params=dict(short_name=short,version=version,
            temporal=f'{first}T00:00:00Z,{last}T23:59:59.999999Z',bounding_box=','.join(map(str,config.bounds)),page_size=100,page_num=page))
        entries=response.json()['feed']['entry'];response.close()
        for entry in entries:
            name=entry['producer_granule_id']
            tile=config.tile or r'h\d{2}v\d{2}'
            match=re.fullmatch(rf'{short}\.A(\d{{4}})(\d{{3}})\.{tile}\.{version}\.\d+',name)
            if not match:continue
            year,doy=map(int,match.groups());start=date(year,1,1)+timedelta(days=doy-1)
            if start.year!=year or start.timetuple().tm_yday!=doy or not first<=start<=last:continue
            if composite_interval(str(start))[0]!=start:raise ProviderError('CMR filename is not an eight-day composite start')
            _,end=composite_interval(str(start))
            if entry['time_start'][:10]!=str(start) or entry.get('time_end','')[:10]!=str(end-timedelta(days=1)):
                raise ProviderError('CMR composite dates disagree with the filename interval')
            links=[l['href'] for l in entry.get('links',[]) if l.get('rel','').endswith('/data#') and l.get('href','').endswith(extension) and not l.get('inherited')]
            links=[u for u in links if urlparse(u).scheme=='https' and urlparse(u).hostname=='data.lpdaac.earthdatacloud.nasa.gov']
            if len(links)!=1:raise ProviderError('CMR must provide one authoritative product HDF HTTPS link')
            items.append(dict(id=name,url=links[0],collection=short+'.'+version,sensor=sensor,
                composite_start=str(start),composite_end_exclusive=str(end),analysis_date=max(config.day,str(start)),
                cloud_cover=entry.get('cloud_cover'),
                selection_policy=config.composite_policy,cmr_time_start=entry['time_start'],cmr_time_end=entry.get('time_end')))
        if len(entries)<100:break
        page+=1
    return annotate(items,config,'CMR cloud_cover',lambda item:item.get('cloud_cover'))


def acquire(item,cache):
    session=requests.Session()
    token=os.environ.get('EARTHDATA_TOKEN')
    if token:session.headers['Authorization']='Bearer '+token
    else:
        user=os.environ.get('EARTHDATA_USERNAME');password=os.environ.get('EARTHDATA_PASSWORD')
        if user and password:session.auth=(user,password)
        # Otherwise requests uses the user's .netrc/_netrc on the URS redirect.
    extension='.h5' if item['collection']=='VNP09H1.002' else '.hdf'
    target=Path(cache)/(item['id']+extension)
    path=download(item['url'],target,session=session)
    with path.open('rb') as stream:magic=stream.read(8)
    valid=magic==b'\x89HDF\r\n\x1a\n' if extension=='.h5' else magic[:4]==b'\x0e\x03\x13\x01'
    if not valid:
        # Never retain a successful HTTP login page as a verified data asset.
        path.unlink(missing_ok=True)
        path.with_suffix(path.suffix+'.receipt.json').unlink(missing_ok=True)
        raise ProviderError('Earthdata returned no HDF data; check account/application authorization, token or external netrc')
    return path
