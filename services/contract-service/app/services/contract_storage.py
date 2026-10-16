"""Contract-only private Supabase bucket; bounded HTTP, never redirect or log secrets."""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import os
import re
import time
from urllib.parse import quote, urlsplit, parse_qs, urlencode

import httpx

from app.core.config import DatabaseSettings
from app.core.errors import ContractError
from app.services.user_gate import unique_object
import json

MIMES={"docx":"application/vnd.openxmlformats-officedocument.wordprocessingml.document","pdf":"application/pdf"}
LIMIT=52428800


def storage_error(code="STORAGE_UNAVAILABLE", status=503):
    return ContractError(code,"Storage Contract chưa sẵn sàng; vui lòng thử lại.",status)


@dataclass(frozen=True)
class StorageSettings:
    url: str
    bucket: str
    key: str = field(repr=False)

    @classmethod
    def from_env(cls):
        url=os.getenv("CONTRACT_SUPABASE_URL","").rstrip("/")
        bucket=os.getenv("CONTRACT_STORAGE_BUCKET","contract-files")
        key=os.getenv("CONTRACT_SUPABASE_SERVICE_ROLE_KEY","")
        try:
            parsed=urlsplit(url)
            if (parsed.scheme!="https" or not re.fullmatch(r"[a-z0-9]{20}\.supabase\.co",parsed.netloc)
                or parsed.path or parsed.query or parsed.fragment or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}",bucket)
                or not key or any(c.isspace() or ord(c)<33 or ord(c)>126 for c in key)):
                raise ValueError()
            # Prevent accidentally using the Document project's Storage with Contract DB.
            db=urlsplit(DatabaseSettings.from_env().database_url)
            project=parsed.hostname.split(".")[0]
            if not (db.hostname=="db."+project+".supabase.co" and db.username=="postgres" or
                db.hostname.endswith(".pooler.supabase.com") and db.username=="postgres."+project):
                raise ValueError()
        except (ValueError,TypeError,AttributeError):
            raise storage_error("STORAGE_CONFIGURATION_ERROR") from None
        return cls(url,bucket,key)


class ContractStorage:
    def __init__(self, settings, transport=None):
        self.settings,self.transport=settings,transport

    @classmethod
    def from_env(cls):
        return cls(StorageSettings.from_env())

    def request(self, method, path, *, body=None, content=None, format=None, limit=65536):
        headers={"apikey":self.settings.key,"Accept-Encoding":"identity","x-upsert":"false"}
        if not self.settings.key.startswith("sb_secret_"):
            headers["Authorization"]="Bearer "+self.settings.key
        if format:
            headers["Content-Type"]=MIMES[format]
        deadline=time.monotonic()+25
        try:
            with httpx.Client(transport=self.transport,timeout=10,trust_env=False,follow_redirects=False) as client:
                with client.stream(method,self.settings.url+"/storage/v1"+path,headers=headers,json=body,content=content) as response:
                    if not 200 <= response.status_code < 300:
                        raise storage_error()
                    data=bytearray()
                    for chunk in response.iter_bytes(65536):
                        data.extend(chunk)
                        if len(data)>limit or time.monotonic()>deadline:
                            raise storage_error("STORAGE_BAD_RESPONSE",502)
                    return bytes(data)
        except httpx.TimeoutException:
            raise storage_error("STORAGE_TIMEOUT",504) from None
        except httpx.HTTPError:
            raise storage_error() from None

    def json(self, method,path,body=None):
        try:
            return json.loads(self.request(method,path,body=body),object_pairs_hook=unique_object)
        except (ValueError,TypeError,RecursionError):
            raise storage_error("STORAGE_BAD_RESPONSE",502) from None

    def ready(self):
        bucket=self.json("GET","/bucket/"+quote(self.settings.bucket,safe=""))
        if (not isinstance(bucket,dict) or bucket.get("id")!=self.settings.bucket or bucket.get("public") is not False
            or type(bucket.get("file_size_limit")) is not int or bucket["file_size_limit"]!=LIMIT
            or not isinstance(bucket.get("allowed_mime_types"),list)
            or any(not isinstance(m,str) for m in bucket["allowed_mime_types"])
            or set(bucket["allowed_mime_types"])!=set(MIMES.values())):
            raise storage_error("STORAGE_CONFIGURATION_ERROR")

    def path(self,key):
        if not isinstance(key,str) or not re.fullmatch(r"(results|snapshots)/[0-9a-f-]{36}/[0-9a-f-]{36}/[0-9a-f-]{36}\.(docx|pdf)",key):
            raise storage_error("STORAGE_CONFIGURATION_ERROR")
        return quote(self.settings.bucket,safe="")+"/"+quote(key,safe="/")

    def put(self,key,data,format):
        if format not in MIMES or not 1<=len(data)<=LIMIT or not key.endswith("."+format):
            raise storage_error("STORAGE_CONFIGURATION_ERROR")
        self.request("POST","/object/"+self.path(key),content=data,format=format)
        # Verify persisted bytes before recording completed; upload receipt is not proof.
        stored=self.request("GET","/object/authenticated/"+self.path(key),limit=len(data))
        if len(stored)!=len(data) or hashlib.sha256(stored).digest()!=hashlib.sha256(data).digest():
            raise storage_error("STORAGE_BAD_RESPONSE",502)

    def remove(self,key):
        self.path(key)
        self.json("DELETE","/object/"+quote(self.settings.bucket,safe=""),{"prefixes":[key]})

    def read(self,key,digest,size):
        if type(size) is not int or not 1 <= size <= LIMIT:
            raise storage_error("STORAGE_BAD_RESPONSE",502)
        data=self.request("GET","/object/authenticated/"+self.path(key),limit=size)
        if len(data)!=size or hashlib.sha256(data).hexdigest()!=digest:
            raise storage_error("STORAGE_BAD_RESPONSE",502)
        return data

    def download(self,key):
        path="/object/sign/"+self.path(key)
        self.ready() # never sign a now-public bucket
        issued=datetime.now(timezone.utc)
        body=self.json("POST",path,{"expiresIn":300})
        try:
            value=body["signedURL"]
            if not isinstance(value,str) or len(value)>8192:
                raise ValueError()
            parsed=urlsplit(value)
            if parsed.fragment or parsed.username or parsed.password or parsed.path not in {path,"/storage/v1"+path}:
                raise ValueError()
            if parsed.netloc and (parsed.scheme!="https" or parsed.netloc!=urlsplit(self.settings.url).netloc) or parsed.scheme and not parsed.netloc:
                raise ValueError()
            query=parse_qs(parsed.query,keep_blank_values=True)
            if set(query)-{"token","download"} or len(query.get("token",[]))!=1 or not query["token"][0] or any(ord(c)<32 for c in query["token"][0]):
                raise ValueError()
            url=self.settings.url+"/storage/v1"+path+"?"+urlencode({"token":query["token"][0],"download":"contract."+key.rsplit(".",1)[1]})
            return {"url":url,"expires_at":issued+timedelta(seconds=300)}
        except (ValueError,TypeError,KeyError):
            raise storage_error("STORAGE_BAD_RESPONSE",502) from None
