"""Tra cứu nguyên văn Điều/Khoản (app/services/article_lookup.py + POST /internal/v1/rag/articles)."""
import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.main import app
from app.models import LegalDocument
from app.services import article_lookup
from app.services.article_lookup import ArticleLookupError, find_article


@pytest.fixture(autouse=True)
def clear_cache():
    article_lookup._articles_cache.clear()


@pytest.fixture
def law(db, sample_text):
    doc = LegalDocument(title="Luật Mẫu 2099", doc_type="luat", doc_number="01/2099/QH-MAU", content=sample_text)
    db.add(doc)
    db.commit()
    return doc


def test_dieu_tra_ve_nguyen_van_ca_dieu(db, law):
    a = find_article(db, "Luật Mẫu 2099", "4")
    assert a["content"].startswith("Điều 4. Điều kiện chuyển nhượng quyền sử dụng đất\n1. Người sử dụng đất")
    assert "d) Còn trong thời hạn sử dụng đất." in a["content"]
    assert a["content"].endswith("có hiệu lực kể từ thời điểm đăng ký vào sổ địa chính.")
    assert "Mục 2" not in a["content"] and "Điều 5. Thừa kế" not in a["content"]  # không lấn sang Điều sau
    assert a["document"]["doc_number"] == "01/2099/QH-MAU" and a["chuong"] == "II" and a["note"] is None


def test_khoan_chi_tra_dung_khoan_kem_ten_dieu(db, law):
    a = find_article(db, "Luật Mẫu 2099", "4", "2")
    assert a["content"] == (
        "Điều 4. Điều kiện chuyển nhượng quyền sử dụng đất\n"
        "2. Hồ sơ chuyển nhượng gồm hợp đồng chuyển nhượng có công chứng hoặc chứng thực, "
        "Giấy chứng nhận mẫu bản gốc và tờ khai lệ phí trước bạ."
    )
    assert a["khoan"] == "2"


@pytest.mark.parametrize("name", ["luật mẫu", "LUAT MAU", "luật mẫu năm 2099", "01/2099/QH-MAU", "Luật số 01/2099/QH-MAU"])
def test_tim_van_ban_theo_ten_hoac_so_hieu(db, law, name):
    assert find_article(db, name, "1")["document"]["title"] == "Luật Mẫu 2099"


def test_sai_nam_thi_dung_ban_dang_co_va_ghi_chu(db, law):
    a = find_article(db, "Luật Mẫu 2000", "1")
    assert a["document"]["title"] == "Luật Mẫu 2099"
    assert a["note"] == "Kho không có bản năm 2000, đang hiển thị Luật Mẫu 2099."


@pytest.mark.parametrize(
    "dieu, khoan, code, message",
    [
        ("99", None, "ARTICLE_NOT_FOUND", "Luật Mẫu 2099 không có Điều 99 (văn bản này có 6 Điều)."),
        ("4", "9", "KHOAN_NOT_FOUND", "Điều 4 Luật Mẫu 2099 không có khoản 9 (Điều này chỉ có 3 khoản)."),
        ("1", "1", "KHOAN_NOT_FOUND", "Điều 1 Luật Mẫu 2099 không có khoản 1 (Điều này không chia khoản)."),
    ],
)
def test_khong_co_dieu_khoan_bao_ro(db, law, dieu, khoan, code, message):
    with pytest.raises(ArticleLookupError) as e:
        find_article(db, "luật mẫu", dieu, khoan)
    assert (e.value.code, e.value.message, e.value.status) == (code, message, 404)


def test_khong_co_van_ban_thi_liet_ke_van_ban_dang_co(db, law):
    with pytest.raises(ArticleLookupError) as e:
        find_article(db, "Luật Hôn nhân và gia đình", "1")
    assert e.value.code == "DOCUMENT_NOT_FOUND"
    assert e.value.message == 'Kho văn bản chưa có "Luật Hôn nhân và gia đình". Hiện kho có: Luật Mẫu 2099.'


def test_nhieu_ban_thi_uu_tien_ban_con_hieu_luc(db, law, sample_text):
    db.add(LegalDocument(title="Luật Mẫu 2050", doc_type="luat", doc_number="02/2050/QH-MAU",
                         content=sample_text, status="superseded"))
    db.commit()
    assert find_article(db, "luật mẫu", "1")["document"]["title"] == "Luật Mẫu 2099"


def test_nhieu_ban_cung_hieu_luc_thi_hoi_lai(db, law, sample_text):
    db.add(LegalDocument(title="Luật Mẫu 2050", doc_type="luat", doc_number="02/2050/QH-MAU", content=sample_text))
    db.commit()
    with pytest.raises(ArticleLookupError) as e:
        find_article(db, "luật mẫu", "1")
    assert e.value.code == "DOCUMENT_AMBIGUOUS" and e.value.status == 409
    assert "Luật Mẫu 2050, Luật Mẫu 2099" in e.value.message


# ── API ────────────────────────────────────────────────────────────────────────

URL = "/internal/v1/rag/articles"
TOKEN = {"X-Internal-Token": "test-internal-token"}


@pytest.fixture
def client(db, law):
    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def test_api_tra_dung_hop_dong(client):
    r = client.post(URL, json={"law": "Luật Mẫu", "dieu": "4", "khoan": "2"}, headers={**TOKEN, "X-Request-ID": "req-1"})
    assert r.status_code == 200
    body = r.json()
    assert body["error"] is None and body["request_id"] == "req-1"
    assert body["data"]["content"].startswith("Điều 4.")
    assert set(body["data"]) == {"document", "chuong", "dieu", "khoan", "content", "note"}


def test_api_khong_co_dieu_tra_404_kem_ma_loi(client):
    r = client.post(URL, json={"law": "Luật Mẫu", "dieu": "99"}, headers=TOKEN)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "ARTICLE_NOT_FOUND"


@pytest.mark.parametrize("body", [{"law": "Luật Mẫu", "dieu": "abc"}, {"law": "", "dieu": "1"}, {"law": "x", "dieu": "1", "khoan": "a"}])
def test_api_du_lieu_sai_422(client, body):
    assert client.post(URL, json=body, headers=TOKEN).status_code == 422


def test_api_thieu_token_401(client):
    assert client.post(URL, json={"law": "Luật Mẫu", "dieu": "1"}).status_code == 401
