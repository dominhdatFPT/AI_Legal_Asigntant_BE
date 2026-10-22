import base64
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import threading
import time
from uuid import uuid4

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient

from test_document_foundation import foundation, config_env
from test_document_upload import upload_database, MemoryStorage, SERVICE
from test_document_identity_images import picture


@pytest.fixture
def vnpt_env(monkeypatch):
    for name, value in {"EKYC_PROVIDER":"vnpt", "EKYC_BASE_URL":"https://api.idg.vnpt.vn", "EKYC_TOKEN_ID":"test-id",
                        "EKYC_TOKEN":"test-secret", "EKYC_VALIDATION_ENABLED":"false"}.items():
        monkeypatch.setenv(name,value)


def pair():
    from app.services.identity_images import prepare_identity_pair
    return prepare_identity_pair(("front.png","image/png",picture("red")),("back.png","image/png",picture("blue")))


def result():
    from test_document_identity_card_match import mrz_raw
    return {"id":"099000000001", "full_name":"TEST PERSON", "date_of_birth":"01/01/2000",
            "place_of_residence":"TEST ADDRESS", "issue_date":"2020-01-01", "confidence":98,
            "extra_provider_field":{"original":True}, "side":"back", "lines":mrz_raw()["lines"]}


def test_adapter_exact_java_contract_and_preserves_raw(foundation,vnpt_env):
    from app.services.vnpt import Vnpt, VnptSettings
    raw=[]
    image=pair()[0]
    def handler(request):
        assert request.url.path == "/v1/ocr/id-card"
        assert request.headers["Token"]=="test-secret" and request.headers["TokenId"]=="test-id"
        assert "authorization" not in request.headers
        payload=json.loads(request.content)
        assert set(payload)=={"img"} and base64.b64decode(payload["img"])==image.content
        return httpx.Response(200,json=result())
    fields=Vnpt(VnptSettings.from_env(),transport=httpx.MockTransport(handler)).read_image(
        image,deadline=time.monotonic()+20,on_response=raw.append)
    assert raw==[result()] and fields["date_of_birth"]=="2000-01-01"
    assert fields["issue_place"] is None and "extra_provider_field" not in fields
    assert "test-secret" not in repr(VnptSettings.from_env())


@pytest.mark.parametrize("name,value",[("EKYC_BASE_URL","https://evil.example"),("EKYC_BASE_URL","https://api.idg.vnpt.vn/?secret=x"),
    ("EKYC_TOKEN",""),("EKYC_TOKEN","bad\nheader"),("EKYC_PROVIDER","mock"),("EKYC_VALIDATION_ENABLED","true")])
def test_provider_config_fail_closed(foundation,vnpt_env,monkeypatch,name,value):
    from app.services.vnpt import VnptSettings
    monkeypatch.setenv(name,value)
    with pytest.raises(foundation[2]): VnptSettings.from_env()


@pytest.mark.parametrize("status,expected",[(401,503),(403,503),(404,503),(405,503),(429,503),(500,502),(302,502),(400,502)])
def test_provider_status_errors_no_retry_or_leak(foundation,vnpt_env,status,expected):
    from app.services.vnpt import Vnpt,VnptSettings
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(status,text="test-secret private CCCD",headers={"Location":"https://evil.example"})
    with pytest.raises(foundation[3]) as caught:
        Vnpt(VnptSettings.from_env(),transport=httpx.MockTransport(handler)).read_image(
            pair()[0],deadline=time.monotonic()+20,on_response=lambda _:None)
    assert caught.value.status==expected and len(calls)==1 and "test-secret" not in str(caught.value)


@pytest.mark.parametrize("payload",[[],{}, {"error":"provider failure"}, {"id":123}, {"id":"123"},
    {"full_name":"TEST","date_of_birth":"31/02/2000"},{"full_name":"TEST","confidence":float("inf")},
    {"full_name":"TEST","is_fake":True},{"full_name":"TEST","full_name_extra":"safe","is_valid":False},
    {"full_name":"bad\nvalue"}])
def test_provider_invalid_json_fields(foundation,vnpt_env,payload):
    from app.services.vnpt import Vnpt,VnptSettings
    body=json.dumps(payload).encode()
    with pytest.raises(foundation[3]) as caught:
        Vnpt(VnptSettings.from_env(),transport=httpx.MockTransport(lambda _:httpx.Response(200,content=body,
            headers={"Content-Type":"application/json"}))).read_image(pair()[0],deadline=time.monotonic()+20,on_response=lambda _:None)
    assert caught.value.status==502


@pytest.mark.parametrize("kind,expected",[("timeout",504),("network",503),("large",502),("html",502)])
def test_provider_transport_and_bounds(foundation,vnpt_env,kind,expected):
    from app.services.vnpt import Vnpt,VnptSettings
    def handler(request):
        if kind=="timeout": raise httpx.ReadTimeout("secret",request=request)
        if kind=="network": raise httpx.ConnectError("secret",request=request)
        if kind=="large": return httpx.Response(200,content=b"x"*(1024*1024+1),headers={"Content-Type":"application/json"})
        return httpx.Response(200,text="<html>secret</html>")
    with pytest.raises(foundation[3]) as caught:
        Vnpt(VnptSettings.from_env(),transport=httpx.MockTransport(handler)).read_image(pair()[0],deadline=time.monotonic()+20,on_response=lambda _:None)
    assert caught.value.status==expected


@pytest.fixture
def sessions(foundation,config_env,vnpt_env,upload_database):
    from app.repositories.identity_ocr import IdentityOcr
    from app.services.vnpt import Vnpt,VnptSettings
    with psycopg.connect(upload_database,autocommit=True) as conn:
        if not conn.execute("SELECT to_regclass('public.document_identity_ocr_sessions')").fetchone()[0]:
            conn.execute((SERVICE/"database/003_identity_ocr_sessions.sql").read_text(encoding="utf-8"))
    settings=replace(foundation[1].from_env(),database_url=upload_database)
    calls=[]
    provider=Vnpt(VnptSettings.from_env(),transport=httpx.MockTransport(lambda req: (calls.append(req) or httpx.Response(200,json=result()))))
    storage=MemoryStorage()
    return IdentityOcr(foundation[5](settings),storage,provider),storage,calls,upload_database


def test_sessions_sql_owner_raw_and_replay(sessions,foundation):
    service,store,calls,url=sessions
    owner,key=str(uuid4()),uuid4()
    row=service.create(owner,key,pair())
    assert row["status"]=="completed" and row["verification_status"]=="not_performed"
    assert len(calls)==2 and store.puts==2
    assert "raw_front" not in row and "front_storage_key" not in row
    replay=service.create(owner,key,pair())
    assert replay["session_id"]==row["session_id"] and len(calls)==2
    with pytest.raises(foundation[3]) as caught: service.detail(str(uuid4()),row["session_id"])
    assert caught.value.status==404
    with psycopg.connect(url) as conn:
        raw=conn.execute("SELECT raw_front,raw_back FROM public.document_identity_ocr_sessions WHERE id=%s",(row["session_id"],)).fetchone()
        expected_back = result()
        expected_back["card_pair_check"] = {"status":"matched", "method":"td1_mrz_consistency", "version":"1"}
        assert raw==(result(),expected_back)
        conn.execute("SET ROLE authenticated")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT * FROM public.document_identity_ocr_sessions")


def test_provider_failure_preserves_original_json_and_no_retry(sessions,foundation):
    service,store,calls,url=sessions
    service.provider.transport=httpx.MockTransport(lambda _:httpx.Response(200,json={"error":"TEST ERROR","private":"original"}))
    owner,key=str(uuid4()),uuid4()
    with pytest.raises(foundation[3]) as caught: service.create(owner,key,pair())
    assert caught.value.status==502
    with psycopg.connect(url) as conn:
        row=conn.execute("SELECT status,raw_front,raw_back FROM public.document_identity_ocr_sessions WHERE owner_user_id=%s",(owner,)).fetchone()
        assert row==("failed",{"error":"TEST ERROR","private":"original"},None)
    service.provider.read_image=lambda *a,**kw: pytest.fail("Must not repeat provider call")
    with pytest.raises(foundation[3]): service.create(owner,key,pair())
    assert store.puts==2


def test_concurrent_reservation_prevents_duplicate_charges(sessions,foundation):
    service,store,calls,url=sessions
    original=service.provider.read_image
    entered,release=threading.Event(),threading.Event()
    def slow(*a,**kw):
        entered.set(); assert release.wait(5); return original(*a,**kw)
    service.provider.read_image=slow
    owner,key=str(uuid4()),uuid4()
    prepared=pair()
    with ThreadPoolExecutor(2) as pool:
        first=pool.submit(service.create,owner,key,prepared)
        try:
            assert entered.wait(5)
            with pytest.raises(foundation[3]) as caught: service.create(owner,key,prepared)
            assert caught.value.code=="EKYC_IN_PROGRESS"
        finally: release.set()
        assert first.result()["status"]=="completed"
    assert len(calls)==2 and store.puts==2


def test_conflicts_require_review(sessions):
    service,_,_,_=sessions
    counter=[]
    def handler(_):
        data=result(); counter.append(1)
        if len(counter)==2: data["id"]="099000000002"
        return httpx.Response(200,json=data)
    service.provider.transport=httpx.MockTransport(handler)
    row=service.create(str(uuid4()),uuid4(),pair())
    assert row["fields"]["identity_number"] is None and "CONFLICT_IDENTITY_NUMBER" in row["warnings"]


def test_storage_failure_replay_no_provider(sessions,foundation):
    service,store,calls,url=sessions
    store.put_quarantine=lambda *a: (_ for _ in ()).throw(foundation[3]("STORAGE_TIMEOUT","Storage timeout",504))
    owner,key=str(uuid4()),uuid4()
    for _ in range(2):
        with pytest.raises(foundation[3]) as caught: service.create(owner,key,pair())
        assert caught.value.status==504
    assert calls==[]


def test_identity_api_jwt_multipart_envelope(sessions,foundation,service_auth):
    from app.api.identity_ocr import get_identity_ocr
    from test_document_contract_design import DOC,schema_validator
    service,_,calls,_=sessions
    app=foundation[0]; app.dependency_overrides[get_identity_ocr]=lambda:service
    client=TestClient(app)
    files={"front":("front.png",picture("red"),"image/png"),"back":("back.png",picture("blue"),"image/png")}
    assert client.post("/api/v1/documents/identity-ocr",files=files).status_code==401
    headers={"Authorization":"Bearer "+service_auth(),"Idempotency-Key":str(uuid4())}
    response=client.post("/api/v1/documents/identity-ocr",files=files,headers=headers)
    assert response.status_code==201, response.text
    schema_validator(DOC["components"]["schemas"]["IdentityOcrResponse"],DOC).validate(response.json())
    assert response.headers["Cache-Control"]=="no-store" and response.json()["error"] is None
    session_id=response.json()["data"]["session_id"]
    assert client.get("/api/v1/documents/identity-ocr/"+session_id,headers=headers).status_code==200
    assert client.post("/api/v1/documents/identity-ocr",files=files,headers={"Authorization":headers["Authorization"]}).status_code==422
    client.cookies.set("access_token",service_auth())
    assert client.post("/api/v1/documents/identity-ocr",files=files,headers={"Idempotency-Key":str(uuid4())}).status_code==403
    assert len(calls)==2


def test_idempotency_different_images_and_unknown_owner(sessions,foundation):
    from app.services.identity_images import prepare_identity_pair
    service,_,calls,_=sessions
    owner,key=str(uuid4()),uuid4()
    service.create(owner,key,pair())
    changed=prepare_identity_pair(("front.png","image/png",picture("green")),("back.png","image/png",picture("blue")))
    with pytest.raises(foundation[3]) as caught: service.create(owner,key,changed)
    assert caught.value.code=="IDEMPOTENCY_CONFLICT" and len(calls)==2
    with pytest.raises(foundation[3]) as caught: service.detail(owner,uuid4())
    assert caught.value.status==404


def test_api_failed_session_visible_only_to_owner(sessions,foundation,service_auth):
    from app.api.identity_ocr import get_identity_ocr
    from test_document_contract_design import DOC,schema_validator
    service,_,_,_=sessions
    service.provider.transport=httpx.MockTransport(lambda _:httpx.Response(404,text="private provider message"))
    foundation[0].dependency_overrides[get_identity_ocr]=lambda:service
    client=TestClient(foundation[0])
    headers={"Authorization":"Bearer "+service_auth(),"Idempotency-Key":str(uuid4())}
    response=client.post("/api/v1/documents/identity-ocr",headers=headers,files={
        "front":("front.png",picture("red"),"image/png"),"back":("back.png",picture("blue"),"image/png")})
    assert response.status_code==503 and "private provider message" not in response.text
    session_id=response.json()["error"]["details"]["session_id"]
    own=client.get("/api/v1/documents/identity-ocr/"+session_id,headers=headers)
    assert own.json()["data"]["status"]=="failed" and own.json()["data"]["fields"] is None
    schema_validator(DOC["components"]["schemas"]["IdentityOcrResponse"],DOC).validate(own.json())
    other={"Authorization":"Bearer "+service_auth(sub=str(uuid4()),role="admin")}
    assert client.get("/api/v1/documents/identity-ocr/"+session_id,headers=other).status_code==404


@pytest.mark.parametrize("case,expected",[("duplicate",422),("extra",400),("single",422),("oversize",413),("invalid_mime",415)])
def test_api_multipart_admission_without_provider(sessions,foundation,service_auth,case,expected):
    from app.api.identity_ocr import get_identity_ocr
    service,_,calls,_=sessions
    foundation[0].dependency_overrides[get_identity_ocr]=lambda:service
    client=TestClient(foundation[0])
    headers={"Authorization":"Bearer "+service_auth(),"Idempotency-Key":str(uuid4())}
    files=[("front",("front.png",picture("red"),"image/png")),("back",("back.png",picture("blue"),"image/png"))]
    data=None
    if case=="duplicate": files[1]=("front",files[1][1])
    if case=="extra": data={"owner_user_id":"private input"}
    if case=="single": files.pop()
    if case=="oversize": files[0]=("front",("front.png",b"x"*(4*1024*1024+1),"image/png"))
    if case=="invalid_mime": files[0]=("front",("front.pdf",b"%PDF","application/pdf"))
    response=client.post("/api/v1/documents/identity-ocr",headers=headers,files=files,data=data)
    assert response.status_code==expected,response.text
    assert "private input" not in response.text and calls==[]
