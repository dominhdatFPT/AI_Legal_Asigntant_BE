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


def test_name_skips_whitespace_but_not_other_labels(foundation):
    from app.services.identity_parser import parse_fields
    assert parse_fields(raw(["Họ và tên:", "  ", "NGUYỄN VĂN AN"]))["full_name"] == "NGUYỄN VĂN AN"
    assert parse_fields(raw(["Họ và tên:", "  ", "Ngày sinh: 01/01/2000", "NGUYỄN VĂN AN"]))["full_name"] is None


@pytest.mark.parametrize("side,score,digits,gap,expected", [
    ("front", .96, "000000000001", 40, "000000000001"),
    ("back", .96, "000000000001", 40, None),
    ("front", .6, "000000000001", 40, None),
    ("front", .96, "O00000000001", 40, None),
    ("front", .96, "000000000001", 500, None),
])
def test_number_with_damaged_label_uses_word_confidence_and_layout(foundation, side, score, digits, gap, expected):
    from app.services.identity_parser import parse_fields
    value = raw(["damaged: " + digits, "Họ và tên:", " ", "NGUYỄN VĂN AN"])
    value["side"] = side
    value["lines"][0]["confidence"] = .35
    value["lines"][0]["words"] = [{"text": digits, "confidence": score, "bbox": [150, 0, 200, 30]}]
    value["lines"][1]["polygon"] = [[0,gap],[600,gap],[600,gap+30],[0,gap+30]]
    assert parse_fields(value)["identity_number"] == expected


def test_damaged_number_label_does_not_hide_conflicting_numbers(foundation):
    from app.services.identity_parser import parse_fields
    value = raw(["damaged label", "Họ và tên:", "NGUYỄN VĂN AN"])
    value["side"] = "front"
    value["lines"][0]["words"] = [
        {"text": digits, "confidence": .96, "bbox": [100, 0, 100, 30]}
        for digits in ("000000000001", "000000000002")]
    assert parse_fields(value)["identity_number"] is None


@pytest.mark.parametrize("noise", ['`"', "security pattern", " "])
def test_number_geometry_survives_interleaved_background_line(foundation, noise):
    from app.services.identity_parser import parse_fields
    value = raw(["damaged label", noise, "Họ và tên:", "TEST PERSON"])
    value["side"] = "front"
    value["lines"][0]["words"] = [{"text": "099000000001", "confidence": .96, "bbox": [100, 0, 200, 30]}]
    # OCR emitted a fragment in another column after the real number.
    value["lines"][1]["polygon"] = [[700,20],[720,20],[720,45],[700,45]]
    assert parse_fields(value)["identity_number"] == "099000000001"
    value["lines"][1]["text"] = "Ngày sinh: 01/01/2000"
    assert parse_fields(value)["identity_number"] is None


def test_nearby_number_search_rejects_conflicting_digit_words(foundation):
    from app.services.identity_parser import parse_fields
    value = raw(["number one", "number two", "noise", "Họ và tên:"])
    value["side"] = "front"
    for i, digits in enumerate(("099000000001", "099000000002")):
        value["lines"][i]["words"] = [{"text": digits, "confidence": .96, "bbox": [100, 40*i, 200, 30]}]
    assert parse_fields(value)["identity_number"] is None


def test_date_uses_value_confidence_not_bilingual_heading(foundation):
    from app.services.identity_parser import parse_fields
    value = raw(["Ngày sinh / Date of birth: 01/01/2000"])
    value["lines"][0]["confidence"] = .3
    value["lines"][0]["words"] = [{"text": "birth:", "confidence": .3}, {"text": "01/01/2000", "confidence": .95}]
    assert parse_fields(value)["date_of_birth"] == "2000-01-01"
    value["lines"][0]["words"][1]["confidence"] = .4
    assert parse_fields(value)["date_of_birth"] is None


def test_old_card_back_issue_heading_and_printed_authority(foundation):
    from app.services.identity_parser import parse_fields
    value = raw(["Ngày, tháng, năm / Date, month, year: 13/05/2021",
                 "CỤC TRƯỞNG CỤC CẢNH SÁT", "QUẢN LÝ HÀNH CHÍNH VỀ TRẬT TỰ XÃ HỘI", "TEST SIGNATURE"])
    value["side"] = "back"
    fields = parse_fields(value)
    assert fields["issue_date"] == "2021-05-13"
    assert fields["issue_place"] == "CỤC CẢNH SÁT QUẢN LÝ HÀNH CHÍNH VỀ TRẬT TỰ XÃ HỘI"
    value["side"] = "front"
    assert parse_fields(value)["issue_date"] is None


def test_multiline_address_skips_expiry_column(foundation):
    from app.services.identity_parser import parse_fields
    value = raw(["Nơi thường trú: Khối A", "Có giá trị đến: 01/01/2030", "Phường B, Thành phố C, Tỉnh D"])
    value["lines"][0]["polygon"] = [[300,0],[800,0],[800,30],[300,30]]
    value["lines"][1]["polygon"] = [[0,20],[200,20],[200,50],[0,50]]
    value["lines"][2]["polygon"] = [[300,35],[800,35],[800,65],[300,65]]
    assert parse_fields(value)["permanent_address"] == "Khối A Phường B, Thành phố C, Tỉnh D"


def test_additional_fields_and_attempt_conflicts(foundation):
    from app.services.identity_parser import parse_fields, parse_additional_fields
    value = raw(["Giới tính / Sex: Nam Quốc tịch / Nationality: Việt Nam",
                 "Quê quán: Phường B, Tỉnh D", "Có giá trị đến: 01/01/2030"])
    assert parse_additional_fields(value) == {"sex": "male", "nationality": "Việt Nam",
        "place_of_origin": "Phường B, Tỉnh D", "expiry_date": "2030-01-01",
        "residence_address": None, "registered_birth_place": None}
    primary = {"lines": [], "attempts": [raw(["Ngày sinh: 01/01/2000"]), raw(["Ngày sinh: 02/01/2000"])]}
    assert parse_fields(primary)["date_of_birth"] is None
    primary["attempts"].pop()
    assert parse_fields(primary)["date_of_birth"] == "2000-01-01"


def test_nationality_does_not_use_confidence_of_sex_word(foundation):
    from app.services.identity_parser import parse_additional_fields
    value = raw(["Giới tính / Sex: Nam Quốc tịch / Nationality: Việt Nam"])
    words = value["lines"][0]["text"].split()
    value["lines"][0]["words"] = [{"text": word, "confidence": .5 if i == 4 else .96}
        for i, word in enumerate(words)]
    value["lines"][0]["confidence"] = .5
    fields = parse_additional_fields(value)
    assert fields["sex"] is None and fields["nationality"] == "Việt Nam"


@pytest.mark.parametrize("text", [
    "CỤC CÀNH SAT _ QUAN LY HANH CHÍNH VỀ TRAT TỰ XÃ HỘI",
    "cuc canh sat - quan ly hanh chinh ve trat tu xa hoi",
    "CỤC CẢNH SÁT QUẢN LÝ HÀNH CHÍNH VỀ TRẬT TỰ XÃ HỘI",
])
def test_issuer_restores_accents_only_for_complete_known_name(foundation, text):
    from app.services.identity_parser import parse_fields
    assert parse_fields(raw(["Nơi cấp: " + text]))["issue_place"] == "CỤC CẢNH SÁT QUẢN LÝ HÀNH CHÍNH VỀ TRẬT TỰ XÃ HỘI"


@pytest.mark.parametrize("text", ["CỤC CẢNH SÁT", "CONG AN TINH TEST", "CỤC CẢNH SÁT QUẢN LÝ HÀNH CHÍNH VỀ TRẬT TỰ XÃ HỘI KHÁC"])
def test_issuer_normalization_preserves_unknown_or_incomplete_name(foundation, text):
    from app.services.identity_parser import parse_fields
    assert parse_fields(raw(["Nơi cấp: " + text]))["issue_place"] == text


def test_origin_ignores_split_english_label(foundation):
    from app.services.identity_parser import parse_additional_fields
    value = raw(["Quê quán / Place", "of origin", "Xã TEST, Tỉnh TEST"])
    assert parse_additional_fields(value)["place_of_origin"] == "Xã TEST, Tỉnh TEST"
    assert parse_additional_fields(raw(["Quê quán / Place"]))["place_of_origin"] is None


def test_origin_uses_confident_vietnamese_label_and_next_line(foundation):
    from app.services.identity_parser import parse_additional_fields
    value = raw(["Quê quán / Place of origin ca", "Xã TEST, Tỉnh TEST"])
    value["lines"][0]["confidence"] = .5
    value["lines"][0]["words"] = [{"text": word, "confidence": .95 if i < 2 else .5}
        for i, word in enumerate(value["lines"][0]["text"].split())]
    assert parse_additional_fields(value)["place_of_origin"] == "Xã TEST, Tỉnh TEST"


def test_new_card_dates_below_numbered_headings(foundation):
    from app.services.identity_parser import parse_fields, parse_additional_fields
    value = raw(["1 Ngày, tháng, năm cấp / Date of issue:", "01/01/2024",
                 "2 Ngày, tháng, năm hết hạn / Date of expiry:", "01/01/2030"])
    assert parse_fields(value)["issue_date"] == "2024-01-01"
    assert parse_additional_fields(value)["expiry_date"] == "2030-01-01"


def test_new_card_birth_sex_and_nationality_below_heading(foundation):
    from app.services.identity_parser import parse_fields, parse_additional_fields
    value = raw(["Ngày, tháng, năm sinh / Date of birth: Giới tính / Sex:", "01/01/2000 Nam",
                 "Quốc tịch / Nationality:", "Việt Nam"])
    value["lines"][1]["words"] = [{"text":"01/01/2000", "confidence":.95}, {"text":"Nam", "confidence":.95}]
    assert parse_fields(value)["date_of_birth"] == "2000-01-01"
    assert parse_additional_fields(value)["sex"] == "male"
    assert parse_additional_fields(value)["nationality"] == "Việt Nam"


def test_new_residence_and_registration_are_not_permanent_or_origin(foundation):
    from app.services.identity_parser import parse_fields, parse_additional_fields
    value = raw(["Nơi cư trú / Place of residence: Xã TEST, Tỉnh TEST",
                 "Nơi đăng ký khai sinh / Place of birth: Xã OTHER, Tỉnh OTHER"])
    assert parse_fields(value)["permanent_address"] is None
    extra = parse_additional_fields(value)
    assert extra["place_of_origin"] is None
    assert extra["residence_address"] == "Xã TEST, Tỉnh TEST"
    assert extra["registered_birth_place"] == "Xã OTHER, Tỉnh OTHER"


def test_new_region_low_confidence_stays_empty(foundation):
    from app.services.identity_parser import parse_additional_fields
    value = raw(["TEST ADDRESS"])
    value.update(source="new_card_region", field="residence_address")
    value["lines"][0]["confidence"] = .5
    assert parse_additional_fields(value)["residence_address"] is None
    value["lines"][0]["confidence"] = .95
    assert parse_additional_fields(value)["residence_address"] == "TEST ADDRESS"


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


@pytest.mark.parametrize("width,height", [(0,10), (10,0), (0,0)])
def test_zero_area_noise_does_not_discard_valid_sibling(local, width, height):
    module, _ = local
    noise = f"5\t1\t1\t1\t1\t2\t20\t0\t{width}\t{height}\t90\tnoise\n".encode()
    lines = module.tsv_lines(Process.output + noise, {"width":100,"height":100})
    assert len(lines) == 1 and lines[0]["text"] == "Số: 000000000001"
    assert len(lines[0]["words"]) == 1 and lines[0]["confidence"] == .99


@pytest.mark.parametrize("box", [(-1,0,10,10), (0,0,-1,10), (95,0,10,10), (0,95,10,10)])
def test_invalid_geometry_still_rejected(local, box):
    module, _ = local
    header = Process.output.splitlines(keepends=True)[0]
    output = header + ("5\t1\t1\t1\t1\t1\t" + "\t".join(map(str,box)) + "\t99\ttext\n").encode()
    with pytest.raises(ValueError): module.tsv_lines(output, {"width":100,"height":100})


def test_all_zero_area_tokens_do_not_become_fields(local):
    module, _ = local
    from app.services.identity_parser import parse_fields
    output = Process.output.replace(b"\t10\t10\t99\t", b"\t0\t10\t99\t")
    lines = module.tsv_lines(output, {"width":100,"height":100})
    assert lines == [] and not any(parse_fields({"lines":lines}).values())


@pytest.mark.parametrize("confidence", ["nan", "inf", "101", "-1"])
def test_zero_area_does_not_hide_invalid_confidence(local, confidence):
    module, _ = local
    output = Process.output.replace(b"\t10\t10\t99\t", ("\t0\t10\t"+confidence+"\t").encode())
    with pytest.raises(ValueError): module.tsv_lines(output, {"width":100,"height":100})


def test_zero_area_noise_still_obeys_total_row_limit(local):
    module, _ = local
    header, row = Process.output.splitlines(keepends=True)
    row = row.replace(b"\t10\t10\t99\t", b"\t0\t10\t99\t")
    with pytest.raises(ValueError): module.tsv_lines(header+row*10001, {"width":100,"height":100})


def test_worker_protocol_strips_secrets_and_preserves_raw(local,monkeypatch):
    module,engine=local
    monkeypatch.setenv("EKYC_TOKEN","not-for-child"); monkeypatch.setenv("DOCUMENT_DATABASE_URL","private")
    def spawn(command,**kwargs):
        assert command[0]=="/usr/bin/tesseract"
        assert command[command.index("-l")+1] in {"vie+eng", "vie"}
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
            from test_document_identity_card_match import mrz_raw
            calls.append(image.side); value=raw() if image.side=="front" else mrz_raw("000000000001", "NGUYEN<<VAN<AN")
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
