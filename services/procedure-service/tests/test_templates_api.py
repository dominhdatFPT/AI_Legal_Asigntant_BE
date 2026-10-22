import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.main import app
from scripts.seed_form_templates import collect_templates, split_name

client = TestClient(app)
BASE = "/api/v1/procedures/templates"

# Bản rút gọn của database/006 cho SQLite.
FORM_TEMPLATES_SCHEMA = """
CREATE TABLE public.form_templates (id TEXT PRIMARY KEY, domain TEXT, name TEXT, procedure_name TEXT, file_name TEXT,
    format TEXT, media_type TEXT, file_size INTEGER, file_data BLOB, preview_pdf BLOB, order_index INTEGER DEFAULT 0,
    is_active BOOLEAN DEFAULT 1)
"""


def _insert(conn, **row):
    row = {"id": str(uuid.uuid4()), "preview_pdf": None, "order_index": 0, "is_active": 1, **row}
    row.setdefault("file_size", len(row["file_data"]))
    conn.execute(text("INSERT INTO public.form_templates (id, domain, name, procedure_name, file_name, format, media_type, "
                      "file_size, file_data, preview_pdf, order_index, is_active) VALUES (:id, :domain, :name, "
                      ":procedure_name, :file_name, :format, :media_type, :file_size, :file_data, :preview_pdf, "
                      ":order_index, :is_active)"), row)
    return row["id"]


@pytest.fixture
def templates(db_engine):
    with db_engine.begin() as conn:
        conn.execute(text(FORM_TEMPLATES_SCHEMA))
        ids = {
            "pdf": _insert(conn, domain="land", name="Tách thửa hoặc hợp thửa đất", procedure_name="Tách thửa hoặc hợp thửa đất",
                           file_name="Thủ tục tách thửa hoặc hợp thửa đất.pdf", format="PDF", media_type="application/pdf",
                           file_data=b"%PDF-1.4 goc", order_index=1),
            "word": _insert(conn, domain="business", name="Đăng ký giải thể doanh nghiệp",
                            procedure_name="Đăng ký giải thể doanh nghiệp", file_name="Thủ tục đăng ký giải thể doanh nghiệp.doc",
                            format="DOC", media_type="application/msword", file_data=b"word goc",
                            preview_pdf=b"%PDF-1.7 ban xem", order_index=1),
            "no_preview": _insert(conn, domain="business", name="Đăng ký thuế", procedure_name="Đăng ký thuế",
                                  file_name="Thủ tục đăng ký thuế.docx", format="DOCX",
                                  media_type="application/msword", file_data=b"PK docx", order_index=2),
            "hidden": _insert(conn, domain="land", name="Mẫu đã ẩn", procedure_name="Mẫu đã ẩn", file_name="an.pdf",
                              format="PDF", media_type="application/pdf", file_data=b"%PDF", is_active=0),
        }
    return ids


def test_list_templates_from_database(templates):
    res = client.get(BASE)
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["total"] == len(data["items"]) == 3
    # Sắp theo thứ tự lĩnh vực (procedure_domains): đất đai trước kinh doanh.
    assert [i["domain"] for i in data["items"]] == ["land", "business", "business"]
    items = {i["id"]: i for i in data["items"]}
    assert templates["hidden"] not in items
    assert items[templates["pdf"]]["domain_label"] == "Đất đai"
    assert items[templates["word"]]["domain_label"] == "Kinh doanh"
    assert items[templates["pdf"]]["viewable"] is True
    assert items[templates["word"]]["viewable"] is True
    assert items[templates["no_preview"]]["viewable"] is False
    assert items[templates["word"]]["size"] == len(b"word goc")
    assert all("file_data" not in i and "preview_pdf" not in i for i in data["items"])


def test_filter_by_domain_and_keyword(templates):
    business = client.get(BASE, params={"domain": "business"}).json()["data"]
    assert business["total"] == 2
    found = client.get(BASE, params={"q": "GIẢI THỂ"}).json()["data"]["items"]
    assert [i["name"] for i in found] == ["Đăng ký giải thể doanh nghiệp"]


def test_invalid_domain_is_rejected(templates):
    assert client.get(BASE, params={"domain": "tax"}).status_code == 422


def test_pdf_file_inline_and_download(templates):
    res = client.get(f"{BASE}/{templates['pdf']}/file")
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/pdf"
    assert res.headers["content-disposition"].startswith("inline")
    assert res.content == b"%PDF-1.4 goc"

    res = client.get(f"{BASE}/{templates['pdf']}/file", params={"download": "true"})
    assert res.headers["content-disposition"].startswith("attachment")
    assert "filename*=utf-8''Th%E1%BB%A7" in res.headers["content-disposition"]


def test_word_template_viewed_through_pdf_preview(templates):
    view = client.get(f"{BASE}/{templates['word']}/file")
    assert view.headers["content-type"] == "application/pdf"
    assert view.headers["content-disposition"].startswith("inline")
    assert view.content == b"%PDF-1.7 ban xem"

    original = client.get(f"{BASE}/{templates['word']}/file", params={"download": "true"})
    assert original.headers["content-type"] == "application/msword"
    assert original.headers["content-disposition"].startswith("attachment")
    assert original.content == b"word goc"


def test_unknown_or_hidden_template_returns_404(templates):
    for template_id in [str(uuid.uuid4()), templates["hidden"], "main.py"]:
        res = client.get(f"{BASE}/{template_id}/file")
        assert res.status_code == 404
        assert res.json()["error"]["code"] == "TEMPLATE_NOT_FOUND"


def test_missing_table_returns_503():
    # Chưa chạy database/006: báo lỗi DB như các API khác, không trả danh sách giả.
    res = client.get(BASE)
    assert res.status_code == 503
    assert res.json()["error"]["code"] == "PROCEDURE_DB_UNAVAILABLE"


def test_existing_procedure_routes_still_work(templates):
    assert client.get("/api/v1/procedures/health").status_code == 200
    assert client.get("/api/v1/procedures", params={"domain": "land"}).status_code == 200


def test_seed_reads_template_folders_and_previews():
    rows = collect_templates()
    assert len(rows) == 30
    assert {r["domain"] for r in rows} == {"land", "business"}
    assert len({(r["domain"], r["file_name"]) for r in rows}) == 30
    word = [r for r in rows if r["format"] in {"DOC", "DOCX"}]
    assert len(word) == 21 and all(r["preview_pdf"].startswith(b"%PDF") for r in word)
    assert all(r["preview_pdf"] is None for r in rows if r["format"] not in {"DOC", "DOCX"})


def test_split_name():
    assert split_name("Thủ tục hòa giải tranh chấp đất đai") == (
        "Hòa giải tranh chấp đất đai", "Hòa giải tranh chấp đất đai")
    assert split_name("Thủ tục giao đất, cho thuê đất - Đơn xin giao đất") == (
        "Giao đất, cho thuê đất - Đơn xin giao đất", "Giao đất, cho thuê đất")
