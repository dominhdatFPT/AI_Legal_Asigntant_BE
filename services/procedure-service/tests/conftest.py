import os
import sys
import uuid
from pathlib import Path

os.environ["PROCEDURE_SKIP_DOTENV"] = "1"
os.environ["PROCEDURE_DATABASE_URL"] = "sqlite://"

# Vô hiệu hóa kết nối RabbitMQ nền trong unit test cục bộ nếu chưa được cấu hình
os.environ.setdefault("RABBITMQ_ENABLED", "false")

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.pool import StaticPool

SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))
TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

# Schema rút gọn của database/001 + 003, đủ cột cho các query của service.
TEST_SCHEMA = """
CREATE TABLE public.procedure_domains (code TEXT PRIMARY KEY, name TEXT, description TEXT, icon TEXT,
    order_index INTEGER DEFAULT 0, is_active BOOLEAN DEFAULT 1);
CREATE TABLE public.procedures (id TEXT PRIMARY KEY, code TEXT UNIQUE, name TEXT, domain TEXT, description TEXT,
    form_template_id TEXT,
    processing_days INTEGER, receiving_agency_level TEXT, fee INTEGER, fee_note TEXT, status TEXT DEFAULT 'active');
CREATE TABLE public.procedure_requirements (id TEXT PRIMARY KEY, procedure_id TEXT, name TEXT, is_required BOOLEAN,
    copies INTEGER, document_form TEXT, form_code TEXT, note TEXT, order_index INTEGER DEFAULT 0);
CREATE TABLE public.procedure_conditions (id TEXT PRIMARY KEY, procedure_id TEXT, condition_code TEXT, title TEXT,
    description TEXT, is_mandatory BOOLEAN, order_index INTEGER DEFAULT 0);
CREATE TABLE public.procedure_legal_bases (id TEXT PRIMARY KEY, procedure_id TEXT, doc_number TEXT, title TEXT,
    article_ref TEXT, source_url TEXT, order_index INTEGER DEFAULT 0);
CREATE TABLE public.procedure_fees (id TEXT PRIMARY KEY, procedure_id TEXT, name TEXT, amount INTEGER);
CREATE TABLE public.land_procedure_records (id TEXT PRIMARY KEY, user_id TEXT, procedure_code TEXT, procedure_id TEXT,
    status TEXT DEFAULT 'draft', applicant_name TEXT, applicant_id_number TEXT, applicant_phone TEXT,
    plot_number TEXT, map_sheet_number TEXT, land_address TEXT, land_area REAL,
    form_data TEXT DEFAULT '{}', extra_data TEXT DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE public.business_procedure_records (id TEXT PRIMARY KEY, user_id TEXT, procedure_code TEXT, procedure_id TEXT,
    status TEXT DEFAULT 'draft', applicant_name TEXT, applicant_id_number TEXT, applicant_phone TEXT,
    business_type TEXT, business_name_vn TEXT, business_code TEXT, headquarters_address TEXT, charter_capital INTEGER,
    form_data TEXT DEFAULT '{}', extra_data TEXT DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE public.custom_form_templates (
    id TEXT PRIMARY KEY, template_group_id TEXT, version INTEGER DEFAULT 1, status TEXT DEFAULT 'draft',
    deleted_at TIMESTAMP, procedure_id TEXT, name TEXT, domain TEXT,
    content TEXT DEFAULT '{}', word_data BLOB, word_filename TEXT, reference_pdf BLOB,
    reference_pdf_filename TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE public.custom_form_submissions (
    id TEXT PRIMARY KEY,
    template_id TEXT NOT NULL REFERENCES custom_form_templates(id),
    user_id TEXT, status TEXT DEFAULT 'created', form_values TEXT DEFAULT '{}', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE public.form_templates (
    id TEXT PRIMARY KEY, domain TEXT, name TEXT, procedure_code TEXT, procedure_name TEXT,
    file_name TEXT, format TEXT, media_type TEXT, file_size INTEGER, is_active BOOLEAN DEFAULT 1,
    order_index INTEGER DEFAULT 0, file_data BLOB, preview_pdf BLOB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE public.form_template_preview_pages (
    template_id TEXT NOT NULL REFERENCES form_templates(id),
    page_number INTEGER NOT NULL, image_data BLOB,
    PRIMARY KEY (template_id, page_number));
CREATE TABLE public.procedure_form_templates (
    id TEXT PRIMARY KEY, procedure_id TEXT,
    template_id TEXT REFERENCES custom_form_templates(id));
CREATE TABLE public.dynamic_form_templates (
    id TEXT PRIMARY KEY, template_group_id TEXT, version INTEGER DEFAULT 1, status TEXT DEFAULT 'draft',
    name TEXT, domain TEXT, procedure_id TEXT, code TEXT, content TEXT DEFAULT '{}',
    created_by TEXT, updated_by TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, deleted_at TIMESTAMP);
CREATE TABLE public.reference_classifications (
    key TEXT PRIMARY KEY, code TEXT, name TEXT, domain TEXT, kind TEXT, procedure_id TEXT);
CREATE TABLE public.contract_reference_uploads (
    id TEXT PRIMARY KEY, procedure_key TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}', file_data BLOB, page_images BLOB);
"""

PROCEDURES = [
    ("DD-001", "Đăng ký biến động quyền sử dụng đất do chuyển nhượng", "land", 10, "district", 0),
    ("DD-002", "Cấp đổi Giấy chứng nhận quyền sử dụng đất", "land", None, "province", 50000),
    ("HKD-001", "Đăng ký thành lập hộ kinh doanh", "business", 3, "commune", 100000),
    ("HKD-002", "Thay đổi nội dung đăng ký hộ kinh doanh", "business", 3, "commune", 0),
]


def _make_engine():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def _setup(dbapi_conn, _):
        dbapi_conn.execute("ATTACH DATABASE ':memory:' AS public")
        dbapi_conn.execute("PRAGMA foreign_keys = ON")
        # lower() mặc định của SQLite chỉ xử lý ASCII; Postgres xử lý cả tiếng Việt.
        dbapi_conn.create_function("lower", 1, lambda s: s.lower() if isinstance(s, str) else s)

    with engine.begin() as conn:
        for stmt in TEST_SCHEMA.split(";"):
            if stmt.strip():
                conn.execute(text(stmt))
        conn.execute(text("INSERT INTO public.procedure_domains (code, name, order_index) VALUES "
                          "('land', 'Đất đai', 1), ('business', 'Kinh doanh', 2)"))
        for code, name, domain, days, level, fee in PROCEDURES:
            pid = str(uuid.uuid4())
            conn.execute(text("INSERT INTO public.procedures (id, code, name, domain, description, processing_days, "
                              "receiving_agency_level, fee) VALUES (:id, :code, :name, :domain, :name, :days, :level, :fee)"),
                         {"id": pid, "code": code, "name": name, "domain": domain, "days": days, "level": level, "fee": fee})
            conn.execute(text("INSERT INTO public.procedure_requirements (id, procedure_id, name, is_required, copies) "
                              "VALUES (:id, :pid, 'Tờ khai', 1, 1)"), {"id": str(uuid.uuid4()), "pid": pid})
            conn.execute(text("INSERT INTO public.procedure_legal_bases (id, procedure_id, doc_number, title) "
                              "VALUES (:id, :pid, '01/2021/NĐ-CP', 'Nghị định')"), {"id": str(uuid.uuid4()), "pid": pid})
    return engine


@pytest.fixture(autouse=True)
def db_engine(monkeypatch):
    """Mọi test dùng SQLite in-memory đã seed, không cần Supabase/mạng."""
    import app.database as database
    from app.core.catalog_cache import catalog_cache
    catalog_cache.clear()

    # Unit tests must never connect to a configured external contract database.
    monkeypatch.delenv("CONTRACT_DATABASE_URL", raising=False)
    monkeypatch.setenv("PROCEDURE_DATABASE_URL", "sqlite://")
    engine = _make_engine()
    monkeypatch.setattr(database, "_engine", engine)
    monkeypatch.setattr(database, "_postgres_disabled_until", 0.0)
    yield engine
    catalog_cache.clear()
    engine.dispose()


@pytest.fixture(scope="session")
def key_pair():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption()).decode()
    public_pem = key.public_key().public_bytes(serialization.Encoding.PEM,
                                               serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    return private_pem, public_pem


@pytest.fixture
def token(monkeypatch, key_pair):
    """token(user_id, role="citizen", **claims) → access token ký bằng key test (chỉ trong RAM)."""
    from datetime import datetime, timedelta, timezone

    import jwt
    from common_auth import ALGORITHM, ISSUER

    monkeypatch.setenv("JWT_PUBLIC_KEY", key_pair[1])
    monkeypatch.delenv("JWT_PUBLIC_KEY_PATH", raising=False)

    def mint(user_id="11111111-1111-4111-8111-111111111111", role="citizen", **overrides):
        now = datetime.now(timezone.utc)
        payload = {"sub": user_id, "role": role, "type": "access", "iss": ISSUER,
                   "iat": now, "exp": now + timedelta(minutes=15)}
        payload.update(overrides)
        return jwt.encode(payload, key_pair[0], algorithm=ALGORITHM)

    return mint


@pytest.fixture
def auth(token):
    """auth(user_id, role) → headers Authorization."""
    return lambda user_id="11111111-1111-4111-8111-111111111111", role="citizen": {"Authorization": f"Bearer {token(user_id, role)}"}


@pytest.fixture(autouse=True)
def mock_profile_binding(monkeypatch):
    try:
        from app.services import profile_binding
        dummy_load = lambda user_id: {
            'profile.full_name': 'Lê Văn C',
            'profile.dob': '1990-01-01',
            'profile.birth_year': '1990',
            'profile.gender': 'Nam',
            'profile.cccd_number': '079090123456',
            'profile.cccd_issue_date': '2021-01-01',
            'profile.cccd_issue_place': 'Cục CS QLHC về TTXH',
            'profile.permanent_address': 'Xã Tân An, TP. Hội An, Quảng Nam',
            'profile.current_address': 'Xã Tân An, TP. Hội An, Quảng Nam',
            'profile.phone': '0988776655',
            'profile.email': 'levanc@example.com',
            'profile.tax_code': '0123456789',
        }
        monkeypatch.setattr(profile_binding, 'load_profile_values', dummy_load)
    except Exception:
        pass
