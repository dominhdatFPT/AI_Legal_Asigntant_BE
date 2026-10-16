"""Local engine boundaries, conservative parsing and actual SQL/API integration.

Engine subprocess mocked here; scripts/check_local_ocr.py exercises real models.
Legacy VNPT tests remain regression-only; the production router never uses VNPT.
"""
import io
import json
import subprocess
import sys
import time
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from test_document_foundation import foundation, config_env
from test_document_identity_ocr import sessions, pair, vnpt_env
from test_document_upload import upload_database


def raw(texts=None):
    return {"engine": "tesseract-local", "lines": [
        {"text": text, "confidence": .99, "polygon": [[0, i*40],[600,i*40],[600,i*40+30],[0,i*40+30]]}
        for i, text in enumerate(texts or ["Số / No.: 000000000001", "Họ và tên / Full name:",
            "NGUYỄN VĂN AN", "Ngày sinh / Date of birth: 01/01/2000",
            "Nơi thường trú / Place of residence: Hà Nội", "Ngày cấp / Date of issue: 01/01/2020", "Nơi cấp: TEST AUTHORITY"])]}


def test_parser_six_fields_and_bilingual_labels(foundation):
    from app.services.identity_parser import parse_fields
    fields = parse_fields(raw())
    assert fields == {"identity_number":"000000000001", "full_name":"NGUYỄN VĂN AN",
                      "date_of_birth":"2000-01-01", "issue_date":"2020-01-01",
                      "issue_place":"TEST AUTHORITY", "permanent_address":"Hà Nội"}


def test_tsv_skips_zero_size_noise_but_rejects_bad_boxes(foundation):
    from app.services.local_ocr import tsv_lines
    head = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
    word = "5\t1\t1\t1\t1\t1\t10\t10\t50\t20\t90\tAN\n"
    lines = tsv_lines((head + word + "5\t1\t1\t1\t1\t2\t70\t10\t0\t20\t5\t.\n").encode(), {"width": 100, "height": 100})
    assert [line["text"] for line in lines] == ["AN"]
    with pytest.raises(ValueError):
        tsv_lines((head + "5\t1\t1\t1\t1\t1\t90\t10\t50\t20\t90\tAN\n").encode(), {"width": 100, "height": 100})


@pytest.mark.parametrize("lines,key", [(["000000000001"], "identity_number"),
    (["Số: O00000000001"], "identity_number"), (["Ngày sinh: 31/02/2000"], "date_of_birth"),
    (["Ngày sinh: 01/01/2999"], "date_of_birth"), (["Có giá trị đến: 01/01/2030"], "issue_date"),
    (["Họ và tên: CỘNG HÒA 123"], "full_name"),
    (["Số: 000000000001", "Số: 000000000002"], "identity_number"),
    (["Nơi cư trú / Place of residence: Hà Nội"], "permanent_address"),
    (["Place of residence: Hà Nội"], "permanent_address"),
    (["Nơi thường trú:", "Ngày sinh: 01/01/2000"], "permanent_address")])
def test_no_guessing_or_ambiguous_values(foundation,lines,key):
    from app.services.identity_parser import parse_fields
    assert parse_fields(raw(lines))[key] is None


def test_low_confidence_and_empty_image(foundation):
    from app.services.identity_parser import parse_fields
    value=raw(); value["lines"][0]["confidence"]=.6
    assert parse_fields(value)["identity_number"] is None
    assert not any(parse_fields({"lines": []}).values())


def test_new_identity_card_explicit_labels(foundation):
    from app.services.identity_parser import parse_fields
    fields=parse_fields(raw(["Số định danh cá nhân: 000000000001",
        "Họ, chữ đệm và tên khai sinh / Full name: NGUYỄN VĂN AN",
        "Ngày, tháng, năm sinh / Date of birth: 01/01/2000", "Ngày, tháng, năm cấp: 01/01/2020"]))
    assert fields["full_name"]=="NGUYỄN VĂN AN" and fields["date_of_birth"]=="2000-01-01"
    assert fields["identity_number"]=="000000000001" and fields["issue_date"]=="2020-01-01"


@pytest.mark.parametrize("value",["0","5","not-int"])
def test_cpu_config_guard(foundation,monkeypatch,value):
    from app.services.local_ocr import OcrSettings
    monkeypatch.setenv("DOCUMENT_OCR_CPU_THREADS",value)
    with pytest.raises(foundation[2]): OcrSettings.from_env()


@pytest.fixture
def local(foundation,tmp_path,monkeypatch):
    from app.services import local_ocr
    for name in local_ocr.MODELS:
        (tmp_path/(name+".traineddata")).touch()
    monkeypatch.setattr(local_ocr.shutil,"which",lambda _:"/usr/bin/tesseract")
    return local_ocr, local_ocr.LocalOcr(local_ocr.OcrSettings(str(tmp_path)))


def test_missing_models_no_download(foundation,tmp_path):
    from app.services.local_ocr import LocalOcr,OcrSettings
    with pytest.raises(foundation[3]) as caught:
        LocalOcr(OcrSettings(str(tmp_path))).read_image(pair()[0],deadline=time.monotonic()+20,on_response=lambda _:None)
    assert caught.value.code=="OCR_MODEL_UNAVAILABLE"


class Process:
    returncode=0
    killed=False
    timeout=False
    output=("level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
            "5\t1\t1\t1\t1\t1\t0\t0\t10\t10\t99\tSố: 000000000001\n").encode("utf-8")
    def __enter__(self): return self
    def __exit__(self,*args): pass
    def communicate(self,content=None,timeout=None):
        if self.timeout and not self.killed: raise subprocess.TimeoutExpired("worker",timeout)
        return self.output,None
    def kill(self): self.killed=True


def test_worker_protocol_strips_secrets_and_preserves_raw(local,monkeypatch):
    module,engine=local
    monkeypatch.setenv("EKYC_TOKEN","not-for-child"); monkeypatch.setenv("DOCUMENT_DATABASE_URL","private")
    def spawn(command,**kwargs):
        assert command[0]=="/usr/bin/tesseract" and "vie+eng" in command
        assert command[1:3]==["stdin","stdout"]
        assert "EKYC_TOKEN" not in kwargs["env"] and "DOCUMENT_DATABASE_URL" not in kwargs["env"]
        assert kwargs["stderr"]==subprocess.DEVNULL
        return Process()
    monkeypatch.setattr(module.subprocess,"Popen",spawn)
    received=[]
    fields=engine.read_image(pair()[0],deadline=time.monotonic()+20,on_response=received.append)
    assert received[0]["engine"]=="tesseract-local"
    assert received[0]["lines"][0]["text"]=="Số: 000000000001"
    assert fields["identity_number"]=="000000000001"


def test_hard_timeout_kills_reaps_and_unlocks(local,monkeypatch,foundation):
    module,engine=local; process=Process(); process.timeout=True
    monkeypatch.setattr(module.subprocess,"Popen",lambda *a,**kw:process)
    with pytest.raises(foundation[3]) as caught:
        engine.read_image(pair()[0],deadline=time.monotonic()+20,on_response=lambda _:None)
    assert caught.value.code=="OCR_TIMEOUT" and process.killed
    assert module._inference_lock.acquire(blocking=False)
    module._inference_lock.release()


def test_timeout_reaps_real_child(local,monkeypatch,foundation):
    module,engine=local; real_spawn=subprocess.Popen; children=[]
    def spawn(command,**kwargs):
        child=real_spawn([sys.executable,"-c","import time; time.sleep(30)"],**kwargs)
        children.append(child); return child
    monkeypatch.setattr(module.subprocess,"Popen",spawn)
    with pytest.raises(foundation[3]) as caught:
        engine.read_image(pair()[0],deadline=time.monotonic()+.3,on_response=lambda _:None)
    assert caught.value.code=="OCR_TIMEOUT" and children[0].poll() is not None


def test_detail_dependency_ignores_ekyc_and_ocr_model_config(foundation,config_env,monkeypatch):
    from app.api.identity_ocr import get_identity_ocr
    from app.services.local_ocr import LocalOcr
    monkeypatch.setenv("EKYC_PROVIDER","unused")
    monkeypatch.setenv("DOCUMENT_OCR_CPU_THREADS","invalid")
    service=get_identity_ocr()
    assert isinstance(service.provider,LocalOcr) and service.provider.settings is None


def test_busy_no_unbounded_queue(local,foundation):
    module,engine=local
    module._inference_lock.acquire()
    try:
        with pytest.raises(foundation[3]) as caught:
            engine.read_image(pair()[0],deadline=time.monotonic()+20,on_response=lambda _:None)
        assert caught.value.code=="OCR_BUSY"
    finally: module._inference_lock.release()


@pytest.mark.parametrize("output,returncode",[(b"private native error",1),(b"[]",0),(b"{}",0),(b"x"*1048577,0)],
                         ids=["native-failure","array","missing-fields","oversized"])
def test_worker_errors_sanitized(local,monkeypatch,foundation,output,returncode):
    module,engine=local; process=Process(); process.output=output; process.returncode=returncode
    monkeypatch.setattr(module.subprocess,"Popen",lambda *a,**kw:process)
    with pytest.raises(foundation[3]) as caught:
        engine.read_image(pair()[0],deadline=time.monotonic()+20,on_response=lambda _:None)
    assert "private native" not in str(caught.value)


def test_local_api_and_replay_use_real_sql_not_vnpt(sessions,foundation,service_auth):
    from app.api.identity_ocr import get_identity_ocr
    from app.services.identity_parser import parse_fields
    from test_document_contract_design import DOC,schema_validator
    service,storage,_,url=sessions
    calls=[]
    class Engine:
        request_namespace="local-test"
        def read_image(self,image,*,deadline,on_response):
            calls.append(image.side); value=raw() if image.side=="front" else {"engine":"tesseract-local","lines":[]}
            on_response(value); return parse_fields(value)
    service.provider=Engine()
    foundation[0].dependency_overrides[get_identity_ocr]=lambda:service
    client=TestClient(foundation[0]); headers={"Authorization":"Bearer "+service_auth(),"Idempotency-Key":str(uuid4())}
    files={"front":("front.png",pair()[0].content,"image/jpeg"),"back":("back.png",pair()[1].content,"image/jpeg")}
    # Extension must agree with MIME after normalization.
    files={key:(key+".jpg",value[1],value[2]) for key,value in files.items()}
    response=client.post("/api/v1/documents/identity-ocr",headers=headers,files=files)
    assert response.status_code==201,response.text
    schema_validator(DOC["components"]["schemas"]["IdentityOcrResponse"],DOC).validate(response.json())
    assert response.json()["data"]["verification_status"]=="not_performed"
    assert "OCR_REQUIRES_USER_REVIEW" in response.json()["data"]["warnings"]
    replay=client.post("/api/v1/documents/identity-ocr",headers=headers,files=files)
    assert replay.status_code==201 and replay.json()["data"]==response.json()["data"]
    assert calls==["front","back"] and storage.puts==2
    with psycopg.connect(url) as conn:
        value=conn.execute("SELECT raw_front FROM document_identity_ocr_sessions WHERE id=%s",(response.json()["data"]["session_id"],)).fetchone()[0]
        assert value==raw()


def test_unreadable_persists_raw_both_sides_no_false_success(sessions,foundation):
    from app.services.identity_parser import parse_fields
    service,_,_,url=sessions
    class Empty:
        def read_image(self,image,*,deadline,on_response):
            value={"engine":"tesseract-local","lines":[]}; on_response(value); return parse_fields(value)
    service.provider=Empty(); owner=str(uuid4())
    with pytest.raises(foundation[3]) as caught: service.create(owner,uuid4(),pair())
    assert caught.value.code=="OCR_UNREADABLE" and caught.value.status==422
    with psycopg.connect(url) as conn:
        status,front,back=conn.execute("SELECT status,raw_front,raw_back FROM document_identity_ocr_sessions WHERE owner_user_id=%s",(owner,)).fetchone()
        assert status=="failed" and front["lines"]==[] and back["lines"]==[]


def test_failed_local_api_error_matches_group_schema(sessions,foundation,service_auth):
    from app.api.identity_ocr import get_identity_ocr
    from test_document_contract_design import DOC,schema_validator
    service,_,_,_=sessions
    def fail(*args,**kwargs):
        raise foundation[3]("OCR_TIMEOUT","OCR quá thời gian xử lý.",504)
    service.provider.read_image=fail
    foundation[0].dependency_overrides[get_identity_ocr]=lambda:service
    client=TestClient(foundation[0]); headers={"Authorization":"Bearer "+service_auth(),"Idempotency-Key":str(uuid4())}
    response=client.post("/api/v1/documents/identity-ocr",headers=headers,files={
        "front":("front.jpg",pair()[0].content,"image/jpeg"),"back":("back.jpg",pair()[1].content,"image/jpeg")})
    assert response.status_code==504
    schema_validator(DOC["components"]["schemas"]["ErrorResponse"],DOC).validate(response.json())
    assert response.json()["error"]["details"]["session_id"]
