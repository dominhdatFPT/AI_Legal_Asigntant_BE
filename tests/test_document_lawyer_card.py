"""Ảnh thẻ luật sư (chức năng Luật sư): chuẩn hóa ảnh, tách trường OCR, OCR lỗi vẫn lưu ảnh, API công khai + nội bộ.
API CCCD cũ không đổi (xem test_document_identity_*). Test SQL thật chỉ chạy khi có DOCUMENT_TEST_DATABASE_URL."""

import io
import shutil
from dataclasses import replace
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from test_document_foundation import config_env, foundation  # noqa: F401
from test_document_identity_images import picture
from test_document_upload import SERVICE, upload_database  # noqa: F401

LINES = ["LIÊN ĐOÀN LUẬT SƯ VIỆT NAM", "ĐOÀN LUẬT SƯ THÀNH PHỐ HỒ CHÍ MINH", "THẺ LUẬT SƯ",
         "Họ và tên: Nguyễn Văn A", "Ngày sinh: 15/01/1990", "Số thẻ: 12345/LS",
         "TP. Hồ Chí Minh, ngày 12 tháng 05 năm 2020"]


def _lines(texts):
    return [{"text": t} for t in texts]


# ---------------------------------------------------------------- Tách trường
def test_parser_doc_du_bon_truong(foundation):
    from app.services.lawyer_card_parser import parse_lawyer_card
    assert parse_lawyer_card(_lines(LINES)) == {"full_name": "NGUYỄN VĂN A", "bar_card_number": "12345/LS",
                                                "bar_association": "ĐOÀN LUẬT SƯ THÀNH PHỐ HỒ CHÍ MINH",
                                                "issue_date": "2020-05-12"}


def test_parser_ho_ten_dong_sau_va_ngay_cap_dang_so(foundation):
    from app.services.lawyer_card_parser import parse_lawyer_card
    fields = parse_lawyer_card(_lines(["Họ và tên:", "TRẦN THỊ B", "Số: 678/LS-HN", "Ngày cấp: 31/02/2021"]))
    assert fields["full_name"] == "TRẦN THỊ B" and fields["bar_card_number"] == "678/LS-HN"
    assert fields["issue_date"] is None  # ngày không có thật thì không đoán


def test_parser_khong_doan_khi_khong_co_nhan(foundation):
    from app.services.lawyer_card_parser import is_readable, parse_lawyer_card
    fields = parse_lawyer_card(_lines(["xxxx 123", "Sổ tay"]))
    assert fields == {"full_name": None, "bar_card_number": None, "bar_association": None, "issue_date": None}
    assert not is_readable(fields)


# ---------------------------------------------------------------- OCR lỗi không làm hỏng upload
class FakeProvider:
    engine = "fake"

    def __init__(self, result=None, error=None):
        self.result, self.error, self.calls = result, error, 0

    def read_card(self, image, *, deadline):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result, {"engine": "fake", "lines": []}


def test_ocr_doc_duoc(foundation):
    from app.services.lawyer_card_ocr import read_card_safely
    fields = {"full_name": "A", "bar_card_number": None, "bar_association": None, "issue_date": None}
    outcome = read_card_safely(FakeProvider(fields), object())
    assert outcome.status == "completed" and outcome.error_code is None
    assert outcome.warnings == ["OCR_REQUIRES_USER_REVIEW", "OCR_BAR_CARD_NUMBER_NOT_FOUND"]


@pytest.mark.parametrize("error_code,expected", [("OCR_TIMEOUT", "OCR_TIMEOUT"), ("OCR_BUSY", "OCR_BUSY"),
                                                 ("CONFIGURATION_ERROR", "OCR_UNAVAILABLE")])
def test_ocr_loi_thanh_failed_co_cau_bao(foundation, error_code, expected):
    from app.services.lawyer_card_ocr import FAILURE_MESSAGES, read_card_safely
    outcome = read_card_safely(FakeProvider(error=foundation[3](error_code, "x", 503)), object())
    assert (outcome.status, outcome.fields, outcome.error_code) == ("failed", None, expected)
    assert FAILURE_MESSAGES[expected].startswith(("Ảnh", "Đọc", "Hệ thống", "Chưa"))


def test_anh_mo_la_ocr_unreadable(foundation):
    from app.services.lawyer_card_ocr import read_card_safely
    empty = dict.fromkeys(("full_name", "bar_card_number", "bar_association", "issue_date"))
    outcome = read_card_safely(FakeProvider(empty), object())
    assert (outcome.status, outcome.error_code) == ("failed", "OCR_UNREADABLE")


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="Cần Tesseract local")
def test_tesseract_that_tren_anh_trang(foundation):
    from app.services.lawyer_card_images import prepare_lawyer_card_image
    from app.services.lawyer_card_ocr import LocalLawyerCardOcr, read_card_safely
    image, _ = prepare_lawyer_card_image("card.png", "image/png", picture("white"))
    assert read_card_safely(LocalLawyerCardOcr(), image).status in {"completed", "failed"}


# ---------------------------------------------------------------- Chuẩn hóa ảnh
def test_anh_bi_bo_exif_va_ma_hoa_lai(foundation):
    from app.services.lawyer_card_images import prepare_lawyer_card_image
    exif = Image.Exif()
    exif[0x010F] = "SecretCamera"
    with Image.new("RGB", (40, 30), "red") as img, io.BytesIO() as out:
        img.save(out, format="JPEG", exif=exif.tobytes())
        original = out.getvalue()
    image, fingerprint = prepare_lawyer_card_image("card.jpg", "image/jpeg", original)
    assert image.media_type == "image/jpeg" and image.content != original and b"SecretCamera" not in image.content
    with Image.open(io.BytesIO(image.content)) as clean:
        assert not clean.getexif()
    assert prepare_lawyer_card_image("card.jpg", "image/jpeg", original)[1] == fingerprint


@pytest.mark.parametrize("filename,mime,content,status", [
    ("a.pdf", "application/pdf", b"%PDF-1.4", 415), ("a.gif", "image/gif", b"GIF89a", 415),
    ("a.png", "image/png", b"\x89PNG\r\n\x1a\nbroken", 422), ("a.png", "image/png", "big", 413),
], ids=["pdf", "gif", "broken", "too_big"])
def test_anh_sai_bi_tu_choi(foundation, filename, mime, content, status):
    from app.services.lawyer_card_images import prepare_lawyer_card_image
    if content == "big":
        content = b"\x89PNG\r\n\x1a\n" + b"x" * (4 * 1024 * 1024)
    with pytest.raises(foundation[3]) as caught:
        prepare_lawyer_card_image(filename, mime, content)
    assert caught.value.status == status


# ---------------------------------------------------------------- HTTP
class FakeCards:
    def __init__(self):
        self.rows = {}

    def create(self, owner, key, image, fingerprint):
        card_id = str(uuid4())
        row = {"card_id": card_id, "ocr_status": "failed", "fields": None, "warnings": [],
               "error_code": "OCR_UNREADABLE", "error_message": "Ảnh thẻ mờ hoặc không đọc được.",
               "created_at": "2026-10-15T02:00:00Z"}
        self.rows[card_id] = (owner, row)
        return row

    def detail(self, owner, card_id):
        found = self.rows.get(str(card_id))
        if not found or found[0] != owner:
            raise LookupError
        return found[1]

    def internal(self, card_id):
        owner, row = self.rows[str(card_id)]
        return {"card_id": row["card_id"], "owner_user_id": owner, "created_at": row["created_at"],
                "ocr_result": {"status": "failed", "fields": None, "warnings": [], "error_code": "OCR_UNREADABLE",
                               "engine": "fake"}}

    def view_url(self, card_id):
        return {"card_id": str(card_id), "url": "https://aaaaaaaaaaaaaaaaaaaa.supabase.co/storage/v1/object/sign/x",
                "expires_in": 300}


@pytest.fixture
def api(foundation, monkeypatch):
    from app.api.lawyer_card_ocr import get_lawyer_cards
    from app.core.errors import DocumentError
    cards = FakeCards()
    original = cards.detail

    def detail(owner, card_id):
        try:
            return original(owner, card_id)
        except LookupError:
            raise DocumentError("NOT_FOUND", "Không tìm thấy ảnh thẻ luật sư.", 404) from None

    cards.detail = detail
    monkeypatch.setenv("INTERNAL_TOKEN", "internal-secret")
    foundation[0].dependency_overrides[get_lawyer_cards] = lambda: cards
    yield TestClient(foundation[0], raise_server_exceptions=False), cards
    foundation[0].dependency_overrides.pop(get_lawyer_cards, None)


def _post(client, headers, files=None, data=None):
    return client.post("/api/v1/documents/lawyer-card-ocr", headers=headers, data=data,
                       files=files if files is not None else {"file": ("card.png", picture(), "image/png")})


def test_upload_anh_the_ocr_loi_van_201_va_khop_contract(api, service_auth):
    client, _ = api
    owner = str(uuid4())
    headers = {"Authorization": "Bearer " + service_auth(sub=owner), "Idempotency-Key": str(uuid4())}
    res = _post(client, headers)
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["data"]["ocr_status"] == "failed" and body["data"]["error_message"]
    from openapi_schema_validator import OAS30Validator
    from jsonschema import RefResolver
    import yaml
    spec = yaml.safe_load((SERVICE / "openapi.yaml").read_text(encoding="utf-8"))
    OAS30Validator(spec["components"]["schemas"]["LawyerCardOcrResponse"],
                   resolver=RefResolver.from_schema(spec)).validate(body)
    card_id = body["data"]["card_id"]
    assert client.get(f"/api/v1/documents/lawyer-card-ocr/{card_id}", headers=headers).status_code == 200
    other = {"Authorization": "Bearer " + service_auth(sub=str(uuid4()), role="admin")}
    assert client.get(f"/api/v1/documents/lawyer-card-ocr/{card_id}", headers=other).status_code == 404


@pytest.mark.parametrize("case,status", [("no_token", 401), ("no_key", 422), ("json", 400), ("wrong_mime", 415),
                                         ("extra_field", 400), ("two_files", 400), ("bad_id", 422)])
def test_upload_anh_the_loi(api, service_auth, case, status):
    client, _ = api
    headers = {"Authorization": "Bearer " + service_auth(), "Idempotency-Key": str(uuid4())}
    if case == "no_token":
        headers.pop("Authorization")
    if case == "no_key":
        headers.pop("Idempotency-Key")
    if case == "json":
        res = client.post("/api/v1/documents/lawyer-card-ocr", headers=headers, json={"file": "x"})
    elif case == "wrong_mime":
        res = _post(client, headers, files={"file": ("a.pdf", b"%PDF-1.4", "application/pdf")})
    elif case == "extra_field":
        res = _post(client, headers, data={"owner_user_id": str(uuid4())})
    elif case == "two_files":
        res = _post(client, headers, files=[("file", ("a.png", picture(), "image/png"))] * 2)
    elif case == "bad_id":
        res = client.get("/api/v1/documents/lawyer-card-ocr/abc", headers=headers)
    else:
        res = _post(client, headers)
    assert res.status_code == status, res.text


def test_api_noi_bo_can_token_va_tra_ocr(api, service_auth):
    client, cards = api
    owner = str(uuid4())
    headers = {"Authorization": "Bearer " + service_auth(sub=owner), "Idempotency-Key": str(uuid4())}
    card_id = _post(client, headers).json()["data"]["card_id"]
    url = f"/internal/v1/documents/lawyer-cards/{card_id}"
    assert client.get(url).status_code == 401
    assert client.get(url, headers={"X-Internal-Token": "sai"}).status_code == 401
    assert client.get(url, headers={"Authorization": headers["Authorization"]}).status_code == 401  # JWT không đủ
    data = client.get(url, headers={"X-Internal-Token": "internal-secret"}).json()["data"]
    assert data["owner_user_id"] == owner and data["ocr_result"]["status"] == "failed"
    view = client.post(url + "/view-url", headers={"X-Internal-Token": "internal-secret"}).json()["data"]
    assert view["expires_in"] == 300 and view["url"].startswith("https://")


# ---------------------------------------------------------------- SQL thật (skip khi không có DB test local)
class SafeStorage:
    def __init__(self, fail=False):
        self.objects, self.fail = {}, fail

    def put_safe(self, key, content, media_type):
        from app.core.errors import DocumentError
        if self.fail:
            raise DocumentError("STORAGE_UNAVAILABLE", "x")
        assert key.startswith("safe/") and key not in self.objects
        self.objects[key] = content

    def signed_download(self, key, filename, *, ttl=300):
        assert key in self.objects and ttl == 300
        return "https://example.supabase.co/storage/v1/object/sign/" + key


@pytest.fixture
def real_cards(foundation, config_env, upload_database):
    from app.repositories.lawyer_cards import LawyerCards
    with psycopg.connect(upload_database, autocommit=True) as conn:
        if not conn.execute("SELECT to_regclass('public.document_lawyer_card_ocr')").fetchone()[0]:
            conn.execute((SERVICE / "database/006_lawyer_card_ocr.sql").read_text(encoding="utf-8"))
    settings = replace(foundation[1].from_env(), database_url=upload_database)
    fields = {"full_name": "NGUYỄN VĂN A", "bar_card_number": "12345/LS", "bar_association": None, "issue_date": None}
    provider = FakeProvider(fields)
    return LawyerCards(foundation[5](settings), SafeStorage(), provider), provider


def test_sql_tao_replay_va_chu_so_huu(real_cards, foundation):
    from app.services.lawyer_card_images import prepare_lawyer_card_image
    cards, provider = real_cards
    owner, key = str(uuid4()), uuid4()
    image, fingerprint = prepare_lawyer_card_image("card.png", "image/png", picture())
    row = cards.create(owner, key, image, fingerprint)
    assert row["ocr_status"] == "completed" and row["fields"]["bar_card_number"] == "12345/LS"
    assert cards.create(owner, key, image, fingerprint)["card_id"] == row["card_id"] and provider.calls == 1
    other, other_fp = prepare_lawyer_card_image("card.png", "image/png", picture("blue"))
    with pytest.raises(foundation[3]) as caught:
        cards.create(owner, key, other, other_fp)
    assert caught.value.status == 409
    with pytest.raises(foundation[3]) as caught:
        cards.detail(str(uuid4()), row["card_id"])
    assert caught.value.status == 404
    internal = cards.internal(row["card_id"])
    assert str(internal["owner_user_id"]) == owner and internal["ocr_result"]["engine"] == "fake"
    assert cards.view_url(row["card_id"])["url"].endswith(f"/lawyer-card/{UUID(str(row['card_id']))}.jpg")


def test_sql_storage_loi_khong_de_lai_dong(real_cards, foundation, upload_database):
    from app.services.lawyer_card_images import prepare_lawyer_card_image
    cards, _ = real_cards
    cards.storage = SafeStorage(fail=True)
    owner = str(uuid4())
    image, fingerprint = prepare_lawyer_card_image("card.png", "image/png", picture())
    with pytest.raises(foundation[3]):
        cards.create(owner, uuid4(), image, fingerprint)
    with psycopg.connect(upload_database) as conn:
        assert conn.execute("SELECT count(*) FROM public.document_lawyer_card_ocr WHERE owner_user_id=%s",
                            (owner,)).fetchone()[0] == 0
