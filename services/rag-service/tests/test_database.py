"""Chống treo khi kết nối DB chết ngầm: cấu hình và tham số tạo engine. Mock hết, không nối DB thật."""
import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError

from app.core import database
from app.core.config import get_settings
from app.core.database import get_db
from app.main import app

DB_VARS = ("DB_CONNECT_TIMEOUT_SECONDS", "DB_COMMAND_TIMEOUT_SECONDS", "DB_POOL_RECYCLE_SECONDS")
URL = "postgresql://u:p@host:5432/db"
# Thiết lập cố định của pool và kết nối (không đổi theo cấu hình DB_*): pool nhỏ cho Supabase pooler, tắt prepared statement, keepalive
KEEPALIVE_ARGS = {
    "prepare_threshold": None, "keepalives": 1, "keepalives_idle": 30, "keepalives_interval": 10, "keepalives_count": 3,
}


@pytest.fixture
def clean_env(monkeypatch):
    """Không có biến DB_* nào, có URL giả, engine chưa tạo, cache cấu hình sạch."""
    for name in DB_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RAG_DATABASE_URL", URL)
    monkeypatch.setattr(database, "_engine", None)
    monkeypatch.setattr(database, "_session_factory", None)
    get_settings.cache_clear()


def set_env(monkeypatch, **values):
    for name, value in values.items():
        monkeypatch.setenv(name, str(value))
    get_settings.cache_clear()


class FakeDbapiConnection:
    """Kết nối DBAPI giả: ghi lại câu lệnh đã chạy, commit và việc đóng cursor."""

    def __init__(self):
        self.log = []

    def cursor(self):
        connection = self

        class Cursor:
            def execute(self, sql):
                connection.log.append(("execute", sql))

            def close(self):
                connection.log.append(("close",))

        return Cursor()

    def commit(self):
        self.log.append(("commit",))


@pytest.fixture
def fake_create_engine(monkeypatch, clean_env):
    """Thay create_engine và event của database.py: ghi lại tham số tạo engine và các hàm lắng nghe sự kiện connect."""
    seen = {"engine": object(), "calls": [], "listeners": []}

    def create(url, **kwargs):
        seen["calls"].append((url, kwargs))
        return seen["engine"]

    class FakeEvent:
        @staticmethod
        def listens_for(target, name):
            def decorator(function):
                seen["listeners"].append((target, name, function))
                return function

            return decorator

    monkeypatch.setattr(database, "create_engine", create)
    monkeypatch.setattr(database, "event", FakeEvent)
    return seen


# (a) cấu hình mặc định ────────────────────────────────────────────────────────

def test_default_settings_are_the_four_expected_values(clean_env):
    settings = get_settings()

    assert settings.db_connect_timeout_seconds == 10
    assert settings.db_command_timeout_seconds == 20
    assert settings.db_pool_recycle_seconds == 300


def test_settings_come_from_environment_variables(clean_env, monkeypatch):
    set_env(monkeypatch, DB_CONNECT_TIMEOUT_SECONDS=3, DB_COMMAND_TIMEOUT_SECONDS=7, DB_POOL_RECYCLE_SECONDS=60)

    settings = get_settings()

    assert (settings.db_connect_timeout_seconds, settings.db_command_timeout_seconds, settings.db_pool_recycle_seconds) == (3, 7, 60)


def test_default_engine_options_have_pre_ping_pool_recycle_and_connect_timeout(clean_env):
    assert database.engine_options() == {
        "pool_pre_ping": True, "pool_size": 3, "max_overflow": 2, "pool_recycle": 300,
        "connect_args": {**KEEPALIVE_ARGS, "connect_timeout": 10},
    }


def test_default_options_keep_pool_and_keepalive_settings(clean_env):
    options = database.engine_options()

    assert options["pool_pre_ping"] is True and options["pool_size"] == 3 and options["max_overflow"] == 2
    args = options["connect_args"]
    assert args["prepare_threshold"] is None
    assert (args["keepalives"], args["keepalives_idle"], args["keepalives_interval"], args["keepalives_count"]) == (1, 30, 10, 3)


def test_sqlite_url_gets_only_pre_ping_and_no_statement_timeout_listener(fake_create_engine, monkeypatch):
    assert database.engine_options("sqlite:///x.db") == {"pool_pre_ping": True}
    monkeypatch.setenv("RAG_DATABASE_URL", "sqlite:///x.db")
    get_settings.cache_clear()

    database.get_engine()

    assert fake_create_engine["calls"] == [("sqlite:///x.db", {"pool_pre_ping": True})]
    assert fake_create_engine["listeners"] == []  # sqlite không hiểu câu SET statement_timeout


# (b) 0 = không áp giới hạn ───────────────────────────────────────────────────

@pytest.mark.parametrize("value", [0, -1])
def test_zero_or_negative_means_no_limit_and_keeps_only_fixed_settings(clean_env, monkeypatch, value):
    set_env(monkeypatch, DB_CONNECT_TIMEOUT_SECONDS=value, DB_COMMAND_TIMEOUT_SECONDS=value, DB_POOL_RECYCLE_SECONDS=value)

    # không còn pool_recycle và connect_timeout; pool, keepalive cố định vẫn giữ
    assert database.engine_options() == {
        "pool_pre_ping": True, "pool_size": 3, "max_overflow": 2, "connect_args": KEEPALIVE_ARGS,
    }


def test_zero_command_timeout_registers_no_connect_listener(fake_create_engine, monkeypatch):
    set_env(monkeypatch, DB_COMMAND_TIMEOUT_SECONDS=0)

    database.get_engine()

    assert fake_create_engine["listeners"] == []
    assert "connect_args" in fake_create_engine["calls"][0][1]  # các giới hạn khác vẫn áp


def test_each_limit_can_be_turned_off_on_its_own(clean_env, monkeypatch):
    set_env(monkeypatch, DB_CONNECT_TIMEOUT_SECONDS=0)
    assert database.engine_options()["connect_args"] == KEEPALIVE_ARGS and database.engine_options()["pool_recycle"] == 300

    set_env(monkeypatch, DB_CONNECT_TIMEOUT_SECONDS=10, DB_POOL_RECYCLE_SECONDS=0)
    assert "pool_recycle" not in database.engine_options()
    assert database.engine_options()["connect_args"] == {**KEEPALIVE_ARGS, "connect_timeout": 10}


# (c) engine được tạo với đúng tham số ────────────────────────────────────────

def test_engine_created_with_default_parameters(fake_create_engine):
    engine = database.get_engine()

    assert engine is fake_create_engine["engine"]
    # URL vẫn đổi sang driver psycopg như trước; đủ tham số pool/keepalive (885c398) và timeout (9dfac52)
    assert fake_create_engine["calls"] == [
        ("postgresql+psycopg://u:p@host:5432/db",
         {"pool_pre_ping": True, "pool_size": 3, "max_overflow": 2, "pool_recycle": 300,
          "connect_args": {**KEEPALIVE_ARGS, "connect_timeout": 10}}),
    ]


def test_engine_created_with_custom_parameters(fake_create_engine, monkeypatch):
    set_env(monkeypatch, DB_CONNECT_TIMEOUT_SECONDS=5, DB_COMMAND_TIMEOUT_SECONDS=7, DB_POOL_RECYCLE_SECONDS=60)

    database.get_engine()

    assert fake_create_engine["calls"][0][1] == {
        "pool_pre_ping": True, "pool_size": 3, "max_overflow": 2, "pool_recycle": 60,
        "connect_args": {**KEEPALIVE_ARGS, "connect_timeout": 5},
    }
    connection = FakeDbapiConnection()
    fake_create_engine["listeners"][0][2](connection, None)
    assert ("execute", "SET statement_timeout = 7000") in connection.log


def test_statement_timeout_is_set_on_every_new_connection_and_committed(fake_create_engine):
    database.get_engine()

    [(target, name, listener)] = fake_create_engine["listeners"]
    assert target is fake_create_engine["engine"] and name == "connect"
    connection = FakeDbapiConnection()
    listener(connection, None)
    # SET trước, rồi đóng cursor, rồi commit (không commit thì rollback khi trả về pool sẽ xoá SET)
    assert connection.log == [("execute", "SET statement_timeout = 20000"), ("close",), ("commit",)]


def test_engine_is_created_once(fake_create_engine):
    first, second = database.get_engine(), database.get_engine()

    assert first is second and len(fake_create_engine["calls"]) == 1 and len(fake_create_engine["listeners"]) == 1


def test_asyncpg_url_is_still_converted_to_psycopg(fake_create_engine, monkeypatch):
    monkeypatch.setenv("RAG_DATABASE_URL", "postgresql+asyncpg://u:p@host:5432/db")
    get_settings.cache_clear()

    database.get_engine()

    assert fake_create_engine["calls"][0][0] == "postgresql+psycopg://u:p@host:5432/db"


def test_options_are_accepted_by_real_sqlalchemy_without_connecting(clean_env):
    """create_engine thật (lười, chưa nối DB) nhận các tham số và đặt vào pool."""
    engine = create_engine("postgresql+psycopg://u:p@localhost:5432/db", **database.engine_options())

    assert engine.pool._pre_ping is True and engine.pool._recycle == 300
    engine.dispose()


# Lỗi quá giờ đi qua đường xử lý lỗi DB sẵn có ────────────────────────────────

def test_statement_timeout_error_is_a_sqlalchemy_operational_error():
    # psycopg ném QueryCanceled (con của psycopg.OperationalError): SQLAlchemy đổi thành OperationalError
    assert issubclass(psycopg.errors.QueryCanceled, psycopg.OperationalError)


def test_statement_timeout_gives_fast_503_through_existing_error_path(monkeypatch):
    def timed_out(*args, **kwargs):
        raise OperationalError(
            "select", {}, psycopg.errors.QueryCanceled("canceling statement due to statement timeout"),
        )

    monkeypatch.setattr("app.api.v1.internal.search_chunks", timed_out)
    app.dependency_overrides[get_db] = lambda: iter([None])
    try:
        response = TestClient(app, raise_server_exceptions=False).post(
            "/internal/v1/rag/search", json={"query": "x"}, headers={"X-Internal-Token": "test-internal-token"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "RAG_DATABASE_UNAVAILABLE"
    assert "statement timeout" not in response.json()["error"]["message"]  # không lộ chi tiết nội bộ
