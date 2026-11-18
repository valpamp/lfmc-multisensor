"""CDSE OData catalogue and authenticated SAFE-node acquisition."""
from datetime import date,timedelta
import os
from pathlib import Path
from time import monotonic
from urllib.parse import quote,urlparse
from uuid import UUID

import requests
from ..config import SENTINEL3_COMBINED
from .http import ProviderError,request,download
from .cloud import annotate,cdse_percentage

CATALOGUE="https://catalogue.dataspace.copernicus.eu/odata/v1/Products"
DOWNLOAD="https://download.dataspace.copernicus.eu/odata/v1/Products"
TOKEN="https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
S3_FILES=('xfdumanifest.xml','geolocation.nc','Syn_Oa08_reflectance.nc','Syn_Oa17_reflectance.nc','Syn_S5N_reflectance.nc','flags.nc')


def s3_filesystem():
    import s3fs

    key=os.environ.get("CDSE_S3_ACCESS_KEY"); secret=os.environ.get("CDSE_S3_SECRET_KEY")
    if not key or not secret: raise ProviderError("Set CDSE_S3_ACCESS_KEY and CDSE_S3_SECRET_KEY")
    return s3fs.S3FileSystem(key=key,secret=secret,client_kwargs={"endpoint_url":"https://eodata.dataspace.copernicus.eu"},config_kwargs={'connect_timeout':20,'read_timeout':60,'retries':{'max_attempts':2}})


def acquire_s3(product,folder):
    """Acquire required SAFE assets using user-generated CDSE S3 keys."""
    import json
    from fsspec.callbacks import Callback
    from .http import file_sha256

    fs=s3_filesystem()
    prefix=product["S3Path"].lstrip("/")
    sentinel3=product['Name'].startswith(('S3A_SY_2_SYN____','S3B_SY_2_SYN____'))
    expected='eodata/Sentinel-3/SYNERGY/SY_2_SYN___/' if sentinel3 else 'eodata/Sentinel-2/'
    if not prefix.startswith(expected): raise ProviderError("Unexpected optical product object prefix")
    folder=Path(folder)/str(UUID(product['Id']))/product['Name']; folder.mkdir(parents=True,exist_ok=True)
    try:
        print('CDSE: locating required files and checking the cache.',flush=True)
        objects=fs.find(prefix,detail=True)
        pending=[]
        cached=0
        for name,info in objects.items():
            relative=Path(name[len(prefix)+1:])
            if relative.is_absolute() or ".." in relative.parts: raise ProviderError("Invalid object path")
            if sentinel3:
                if relative.as_posix() not in S3_FILES:continue
            elif relative.name not in ("MTD_MSIL2A.xml","MTD_TL.xml") and not any(k in relative.name for k in ("_B04_10m.jp2","_B8A_20m.jp2","_B11_20m.jp2","_SCL_20m.jp2","MSK_CLASSI","MSK_CLOUDS")): continue
            target=folder/relative; receipt=target.with_suffix(target.suffix+".receipt.json")
            if target.exists() and receipt.exists():
                record=json.loads(receipt.read_text())
                if record.get('sha256')==file_sha256(target) and record.get('object')==name and record.get('etag')==info.get('ETag'):
                    cached+=1
                    continue
            pending.append((name,info,target,receipt))
        if cached:
            print(f'CDSE: reusing {cached} verified cached file(s).',flush=True)
        total=sum(info['size'] for _,info,_,_ in pending)
        completed=0
        last_percent=-1
        last_update=0.0
        for name,info,target,receipt in pending:
            def progress(_,position,*,force=False):
                nonlocal last_percent,last_update
                received=completed+min(position,info['size'])
                percentage=100*received//total if total else 100
                now=monotonic()
                if force or percentage==100 and last_percent!=100 or now-last_update>=1:
                    print(f'CDSE download: {percentage}% | {received/1e6:.1f} / {total/1e6:.1f} MB | {target.name}',flush=True)
                    last_percent,last_update=percentage,now

            progress(None,0,force=True)
            target.parent.mkdir(parents=True,exist_ok=True); partial=target.with_suffix(target.suffix+'.part')
            # Native geolocation files exceed s3fs' multipart threshold.
            # CDSE interrupted a concurrent range transfer in live SYN checks;
            # use a complete sequential asset transfer for this backend.
            callback=Callback(hooks={'progress':progress})
            fs.get_file(name,str(partial),callback=callback,**({'max_concurrency':1} if sentinel3 else {}))
            if partial.stat().st_size!=info['size']: raise ProviderError("Incomplete CDSE object")
            partial.replace(target)
            receipt.write_text(json.dumps(dict(object=name,etag=info.get('ETag'),sha256=file_sha256(target),size=info['size']),indent=2)+'\n')
            completed+=info['size']
    except ProviderError: raise
    except Exception as exc:
        raise ProviderError(f"CDSE S3 acquisition failed ({type(exc).__name__}); check keys, access and provider availability") from None
    if sentinel3:
        if any(not (folder/name).exists() for name in S3_FILES):raise ProviderError('Sentinel-3 product lacks required native assets')
    elif not (folder/'MTD_MSIL2A.xml').exists(): raise ProviderError("Product has no SAFE L2A metadata")
    print('CDSE download complete. Required files are ready.',flush=True)
    return folder


def discover(config):
    w,s,e,n=config.bounds
    polygon=f"POLYGON(({w} {s},{e} {s},{e} {n},{w} {n},{w} {s}))"
    tile_filter=f"and contains(Name,'_T{config.tile}_') " if config.tile else ''
    query=("Collection/Name eq 'SENTINEL-2' and contains(Name,'MSIL2A') "
           f"{tile_filter}and ContentDate/Start ge {config.day}T00:00:00.000Z "
           f"and ContentDate/Start lt {config.search_end_exclusive} "
           f"and OData.CSC.Intersects(area=geography'SRID=4326;{polygon}')")
    if config.product=='s3syn300':
        sensors = ('S3A', 'S3B') if config.sensor == SENTINEL3_COMBINED else (config.sensor,)
        spacecraft_filter = " or ".join(f"startswith(Name,'{sensor}_SY_2_SYN____')" for sensor in sensors)
        query=(f"Collection/Name eq 'SENTINEL-3' and ({spacecraft_filter}) "
               "and contains(Name,'_NT_002.SEN3') "
               f"and ContentDate/Start ge {config.day}T00:00:00.000Z and ContentDate/Start lt {config.search_end_exclusive} "
               f"and OData.CSC.Intersects(area=geography'SRID=4326;{polygon}')")
    session=requests.Session(); url=CATALOGUE; result=[]
    params={"$filter":query,"$top":100,"$orderby":"ContentDate/Start asc","$expand":"Attributes"}
    while url:
        if urlparse(url).hostname!="catalogue.dataspace.copernicus.eu": raise ProviderError("Unexpected catalogue continuation host")
        response=request(session,"GET",url,params=params); data=response.json(); response.close()
        result.extend(data["value"]); url=data.get("@odata.nextLink"); params=None
    if config.product == 's3syn300':
        result = [item for item in result if item['Name'].startswith(tuple(f'{sensor}_SY_2_SYN____' for sensor in sensors))]
    return annotate(result,config,'CDSE cloudCover attribute',cdse_percentage)


class CDSE:
    def __init__(self):
        self.session=requests.Session(); self.token=None

    def authenticate(self):
        token=os.environ.get("CDSE_ACCESS_TOKEN")
        if token: self.token=token; return
        user=os.environ.get("CDSE_USERNAME"); password=os.environ.get("CDSE_PASSWORD")
        if not user or not password: raise ProviderError("Set CDSE_USERNAME/CDSE_PASSWORD or CDSE_ACCESS_TOKEN outside source control")
        response=request(self.session,"POST",TOKEN,data=dict(client_id="cdse-public",grant_type="password",username=user,password=password))
        try: self.token=response.json()["access_token"]
        except (ValueError,KeyError): raise ProviderError("CDSE authentication returned no token") from None
        finally: response.close()

    def acquire(self,product_id,folder):
        product_id=str(UUID(product_id)); folder=Path(folder)/product_id
        folder.mkdir(parents=True,exist_ok=True)
        if not self.token: self.authenticate()
        base=f"{DOWNLOAD}({product_id})"
        def nodes(url):
            response=request(self.session,"GET",url+"/Nodes",headers={"Authorization":"Bearer "+self.token})
            data=response.json();response.close();return data.get("result",data.get("value",[]))
        def walk(url,relative):
            files=[]
            for node in nodes(url):
                name=node["Name"]
                if Path(name).name!=name or name in (".",".."): raise ProviderError("Invalid SAFE node name")
                child=url+"/Nodes('"+quote(name.replace("'","''"),safe="")+"')"
                target=relative/name
                if int(node.get("ChildrenNumber",0))>0:
                    files.extend(walk(child,target))
                elif name in ("MTD_MSIL2A.xml","MTD_TL.xml") or any(k in name for k in ("_B04_10m.jp2","_B8A_20m.jp2","_B11_20m.jp2","_SCL_20m.jp2","MSK_CLASSI","MSK_CLOUDS")):
                    length=node.get("ContentLength")
                    download(child+"/$value",target,session=self.session,headers={"Authorization":"Bearer "+self.token},expected_size=int(length) if length is not None else None)
                    files.append(target)
            return files
        files=walk(base,folder)
        metadata=[p for p in files if p.name=="MTD_MSIL2A.xml"]
        if len(metadata)!=1: raise ProviderError("Expected one SAFE product metadata file")
        return metadata[0].parent
