import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.pool import StaticPool

SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

# Schema rút gọn của database/001 + 003, đủ cột cho các query của service.
TEST_SCHEMA = """
CREATE TABLE public.procedure_domains (code TEXT PRIMARY KEY, name TEXT, description TEXT, icon TEXT,
    order_index INTEGER DEFAULT 0, is_active BOOLEAN DEFAULT 1);
CREATE TABLE public.procedures (id TEXT PRIMARY KEY, code TEXT UNIQUE, name TEXT, domain TEXT, description TEXT,
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

    engine = _make_engine()
    monkeypatch.setattr(database, "_engine", engine)
    yield engine
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
