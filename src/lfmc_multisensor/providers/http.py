"""Atomic downloads with checksum receipts; credentials never enter receipts."""
import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse

import requests


class ProviderError(RuntimeError):
    pass


def file_sha256(path):
    h=hashlib.sha256()
    with open(path,"rb") as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b""): h.update(chunk)
    return h.hexdigest()


def request(session, method, url, **kwargs):
    try:
        response=session.request(method,url,timeout=(30,180),**kwargs)
    except requests.RequestException:
        raise ProviderError(f"Provider connection failed: {urlparse(url).hostname}") from None
    if response.status_code >= 400:
        code=response.status_code; response.close()
        raise ProviderError(f"Provider HTTP {code}: {urlparse(url).hostname}; check access, terms and availability")
    return response


def download(url,path,*,session=None,headers=None,auth=None,expected_sha256=None,expected_size=None):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    receipt=path.with_suffix(path.suffix+".receipt.json")
    if path.exists() and receipt.exists():
        record=json.loads(receipt.read_text())
        digest=file_sha256(path)
        if record.get("url")==url and record.get("sha256")==digest and (not expected_sha256 or digest==expected_sha256):
            if expected_size is None or path.stat().st_size==expected_size: return path
    session=session or requests.Session()
    partial=path.with_suffix(path.suffix+".part")
    # A failed partial is retried from zero; only fully verified files are reused.
    response=request(session,"GET",url,headers=headers,auth=auth,stream=True)
    try:
        with partial.open("wb") as stream:
            for chunk in response.iter_content(1024*1024): stream.write(chunk)
        size=partial.stat().st_size
        length=response.headers.get("Content-Length")
        if not size or (length and not response.headers.get("Content-Encoding") and size!=int(length)):
            raise ProviderError("Incomplete provider download; rerun to retry")
        if expected_size is not None and size!=expected_size: raise ProviderError("Provider file size mismatch")
        digest=file_sha256(partial)
        if expected_sha256 and digest!=expected_sha256: raise ProviderError("Provider source SHA256 mismatch")
        partial.replace(path)
        receipt.write_text(json.dumps(dict(url=url,sha256=digest,size=size),indent=2)+"\n")
        return path
    except requests.RequestException:
        raise ProviderError("Provider stream interrupted; rerun to retry") from None
    finally:
        response.close()
