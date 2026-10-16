"""Opt-in local SQL + signed JWT + actual OCR API smoke. Storage is in-memory.

Never reads .env or cloud. Requires an initialized document_schema_test DB on
localhost or host.docker.internal. Deletes its synthetic sessions unless
--include-profile retains rows until the disposable test DB is removed.
"""
import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit
from uuid import uuid4

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from common_auth import ALGORITHM, ISSUER
from app.api.identity_ocr import get_identity_ocr
from app.core.config import Settings
from app.main import app
from app.repositories.database import Database
from app.repositories.identity_ocr import IdentityOcr
from app.services.local_ocr import LocalOcr,OcrSettings
from check_local_ocr import synthetic_pair


async def run(url, include_profile=False):
    database=Database(Settings(url,"https://synthetic-only.invalid","unused"))
    owner=str(uuid4()); other=str(uuid4()); ids=[]
    private=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    os.environ["JWT_PUBLIC_KEY"]=private.public_key().public_bytes(
        serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    os.environ.pop("JWT_PUBLIC_KEY_PATH",None)
    def token(subject):
        now=datetime.now(timezone.utc)
        return jwt.encode({"sub":subject,"role":"citizen","type":"access","iss":ISSUER,
                           "iat":now,"exp":now+timedelta(minutes=5)},private,algorithm=ALGORITHM)
    class MemoryStorage:
        def __init__(self): self.objects={}
        def put_quarantine(self,key,content,media_type): self.objects[key]=content
    storage=MemoryStorage(); service=IdentityOcr(database,storage,LocalOcr(OcrSettings.from_env()))
    app.dependency_overrides[get_identity_ocr]=lambda:service
    from app.core.security import get_user_status
    class SyntheticActiveUser:
        def ensure_document_write(self, user_id, request_id): pass
    app.dependency_overrides[get_user_status] = SyntheticActiveUser
    if include_profile:
        from app.api.identity_profile import get_identity_profile
        from app.repositories.identity_profile import IdentityProfile
        app.dependency_overrides[get_identity_profile]=lambda:IdentityProfile(database)
    images=synthetic_pair()
    files={side:(side+".jpg",images[index].content,"image/jpeg") for index,side in enumerate(("front","back"))}
    headers={"Authorization":"Bearer "+token(owner),"Idempotency-Key":str(uuid4())}
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test") as client:
            assert (await client.post("/api/v1/documents/identity-ocr",files=files)).status_code==401
            response=await client.post("/api/v1/documents/identity-ocr",files=files,headers=headers)
            data=response.json()
            if data.get("error") and data["error"].get("details",{}).get("session_id"):
                ids.append(data["error"]["details"]["session_id"])
            assert response.status_code==201,"API did not complete synthetic OCR"
            row=data["data"]; ids.append(row["session_id"])
            assert row["fields"]["full_name"]=="NGUYỄN VĂN AN" and row["fields"]["identity_number"]=="000000000001"
            assert row["verification_status"]=="not_performed" and len(storage.objects)==2
            replay=await client.post("/api/v1/documents/identity-ocr",files=files,headers=headers)
            assert replay.status_code==201 and replay.json()["data"]==row and len(storage.objects)==2
            path="/api/v1/documents/identity-ocr/"+row["session_id"]
            assert (await client.get(path,headers=headers)).status_code==200
            assert (await client.get(path,headers={"Authorization":"Bearer "+token(other)})).status_code==404
            with database.connection(read_only=True) as conn:
                stored=conn.execute("SELECT raw_front,raw_back FROM document_identity_ocr_sessions WHERE id=%s AND owner_user_id=%s",
                                    (row["session_id"],owner)).fetchone()
                assert stored["raw_front"]["engine"]=="tesseract-local" and stored["raw_back"]["lines"]
            if include_profile:
                profile_path="/api/v1/documents/identity-profile/me"
                assert (await client.get(profile_path,headers=headers)).status_code==404
                reviewed={**row["fields"],"full_name":"NGUYỄN VĂN BÌNH"}
                body={"source_session_id":row["session_id"],"expected_revision":0,"fields":reviewed}
                confirmed=await client.put(profile_path,json=body,headers=headers)
                assert confirmed.status_code==200 and confirmed.json()["data"]["revision"]==1
                assert confirmed.json()["data"]["fields"]==reviewed
                assert (await client.get(profile_path,headers=headers)).json()["data"]["fields"]==reviewed
                assert (await client.put(profile_path,json=body,headers=headers)).status_code==409
                assert (await client.get(profile_path,headers={"Authorization":"Bearer "+token(other)})).status_code==404
                body["expected_revision"]=1
                assert (await client.put(profile_path,json=body,headers=headers)).json()["data"]["revision"]==2
                deleted=await client.delete(profile_path,params={"expected_revision":2},headers=headers)
                assert deleted.status_code==204 and not deleted.content
                assert (await client.get(profile_path,headers=headers)).json()["error"]["current_revision"]==3
                body["expected_revision"]=3
                assert (await client.put(profile_path,json=body,headers=headers)).json()["data"]["revision"]==4
                print("PASS: OCR -> explicit user review -> profile GET/PUT/CAS/owner -> 204 unlink -> monotonic recreate.")
        print("PASS: actual OCR + signed JWT + HTTP API + local PostgreSQL + private Storage stub + replay/owner guards.")
    finally:
        app.dependency_overrides.clear()
        # Profiles cannot be hard-deleted/reset. Opt-in profile smoke retains only
        # synthetic rows in the disposable local DB, removed with its container.
        if ids and not include_profile:
            with database.connection() as conn:
                conn.execute("DELETE FROM document_identity_ocr_sessions WHERE id=ANY(%s::uuid[]) AND owner_user_id=%s",(ids,owner))


def main():
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument("--run-local-test",action="store_true")
    parser.add_argument("--include-profile",action="store_true")
    args=parser.parse_args(); url=os.getenv("DOCUMENT_TEST_DATABASE_URL","")
    parsed=urlsplit(url)
    if (not args.run_local_test or parsed.scheme not in {"postgresql","postgres"}
            or parsed.hostname not in {"127.0.0.1","localhost","host.docker.internal"}
            or parsed.path!="/document_schema_test" or parsed.query or parsed.fragment):
        print("No changes: requires --run-local-test and a guarded local test DB."); return 1
    try:
        asyncio.run(run(url,args.include_profile))
    except Exception:
        print("FAIL: local OCR API smoke; no credentials or response body printed."); return 1
    return 0


if __name__=="__main__":
    raise SystemExit(main())
