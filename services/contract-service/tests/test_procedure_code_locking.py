"""Kiểm thử khóa nguyên tử cấp mã thủ tục (pg_advisory_xact_lock + retry UniqueViolation) trong contract-service."""
from contextlib import contextmanager
import sys
from pathlib import Path

import psycopg
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api import reference_catalog as rc  # noqa: E402
from app.core.errors import ContractError  # noqa: E402


class _FakeResult:
    def __init__(self, rows=None):
        self._rows = rows or []

    def fetchall(self):
        return list(self._rows)


def test_auto_procedure_code_uses_advisory_lock_and_retries_unique_violation(monkeypatch):
    """Cấp mã tự động phải gọi pg_advisory_xact_lock và tự thử mã kế tiếp nếu gặp UniqueViolation."""
    monkeypatch.setenv("PROCEDURE_DATABASE_URL", "postgresql://fake/proc")

    lock_calls = []
    inserted_proc_codes = []
    contract_cls_codes = []
    attempt_state = {"insert_attempts": 0}

    class FakeProcConn:
        def execute(self, sql, params=None):
            if "pg_advisory_xact_lock" in sql:
                lock_calls.append(params)
                return _FakeResult()
            if "SELECT code FROM public.procedures" in sql:
                return _FakeResult([("DD-004",)])
            if "INSERT INTO public.procedures" in sql:
                attempt_state["insert_attempts"] += 1
                code = params[1]
                if attempt_state["insert_attempts"] == 1:
                    raise psycopg.errors.UniqueViolation("duplicate key value violates unique constraint")
                inserted_proc_codes.append(code)
                return _FakeResult()
            return _FakeResult()

    class FakeContractConn:
        def execute(self, sql, params=None):
            if "SELECT code FROM public.reference_classifications" in sql:
                return _FakeResult([("DD-004",)])
            if "INSERT INTO public.reference_classifications" in sql:
                contract_cls_codes.append(params[1])
                return _FakeResult()
            return _FakeResult()

    @contextmanager
    def fake_pooled(url=None, **kwargs):
        yield FakeProcConn()

    @contextmanager
    def fake_contract_conn():
        yield FakeContractConn()

    monkeypatch.setattr(rc, "pooled_connection", fake_pooled)
    from app.repositories import reference_uploads
    monkeypatch.setattr(reference_uploads, "connection", fake_contract_conn)

    classification, proc_id = rc.create_new_procedure_record(
        name="Thủ tục sang tên sổ đỏ",
        domain="land",
        code=None,
        auto_code=True,
    )

    assert proc_id
    assert len(lock_calls) == 2
    assert all(ns == rc._PROC_CODE_LOCK_NS and key == 1 for ns, key in lock_calls)
    # Lần 1 đụng DD-005 -> lần 2 tự tăng lên DD-006
    assert inserted_proc_codes == ["DD-006"]
    assert contract_cls_codes == ["DD-006"]
    assert classification["code"] == "DD-006"
    assert classification["key"] == "DD-006"


def test_explicit_duplicate_procedure_code_raises_409_without_overwriting_contract_db(monkeypatch):
    """Admin tự nhập mã đã tồn tại phải nhận 409 PROCEDURE_CODE_EXISTS, không được nuốt lỗi hay ghi đè Contract DB."""
    monkeypatch.setenv("PROCEDURE_DATABASE_URL", "postgresql://fake/proc")
    contract_writes = []

    class FakeProcConn:
        def execute(self, sql, params=None):
            if "INSERT INTO public.procedures" in sql:
                raise psycopg.errors.UniqueViolation("duplicate key value violates unique constraint")
            return _FakeResult()

    class FakeContractConn:
        def execute(self, sql, params=None):
            contract_writes.append((sql, params))
            return _FakeResult()

    @contextmanager
    def fake_pooled(url=None, **kwargs):
        yield FakeProcConn()

    @contextmanager
    def fake_contract_conn():
        yield FakeContractConn()

    monkeypatch.setattr(rc, "pooled_connection", fake_pooled)
    from app.repositories import reference_uploads
    monkeypatch.setattr(reference_uploads, "connection", fake_contract_conn)

    with pytest.raises(ContractError) as err:
        rc.create_new_procedure_record(
            name="Thủ tục trùng mã",
            domain="land",
            code="DD-001",
            auto_code=False,
        )

    assert err.value.status == 409
    assert err.value.code == "PROCEDURE_CODE_EXISTS"
    assert contract_writes == []
