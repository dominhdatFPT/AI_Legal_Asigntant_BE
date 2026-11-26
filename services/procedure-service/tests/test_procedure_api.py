from contextlib import contextmanager
import json
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, text

from app.repositories.procedure_repository import db_connection
from form_samples import valid_form
from app.main import app

client = TestClient(app)


def _seed_purge_data(code="DD-001"):
    template_id = str(uuid4())
    linked_template_id = str(uuid4())
    legacy_template_id = str(uuid4())

    with db_connection(write=True) as conn:
        procedure = conn.execute(
            text("SELECT id, code, name FROM public.procedures WHERE code = :code"),
            {"code": code},
        ).mappings().one()
        proc_id = str(procedure["id"])
        params = {
            "proc_id": proc_id,
            "code": code,
            "name": procedure["name"],
            "template_id": template_id,
            "legacy_id": legacy_template_id,
            "content": json.dumps({"code": code}),
            "bytes": b"permanent-purge-test",
        }

        # One template is reachable only through procedures.form_template_id.
        conn.execute(
            text(
                "INSERT INTO public.custom_form_templates "
                "(id, procedure_id, name, content, word_data, reference_pdf) "
                "VALUES (:template_id, NULL, :name, :content, :bytes, :bytes)"
            ),
            params,
        )
        conn.execute(
            text(
                "UPDATE public.procedures SET form_template_id = :template_id "
                "WHERE id = :proc_id"
            ),
            params,
        )
        conn.execute(
            text(
                "INSERT INTO public.custom_form_templates "
                "(id, procedure_id, name, content, word_data, reference_pdf) "
                "VALUES (:id, :proc_id, :name, :content, :bytes, :bytes)"
            ),
            {**params, "id": linked_template_id},
        )
        for custom_id in (template_id, linked_template_id):
            conn.execute(
                text(
                    "INSERT INTO public.custom_form_submissions "
                    "(id, template_id, form_values) VALUES (:id, :template_id, '{}')"
                ),
                {"id": str(uuid4()), "template_id": custom_id},
            )
        conn.execute(
            text(
                "INSERT INTO public.form_templates "
                "(id, procedure_code, procedure_name, file_data, preview_pdf) "
                "VALUES (:legacy_id, :code, :name, :bytes, :bytes)"
            ),
            params,
        )
        conn.execute(
            text(
                "INSERT INTO public.form_template_preview_pages "
                "(template_id, page_number, image_data) "
                "VALUES (:legacy_id, 1, :bytes)"
            ),
            params,
        )
        for table in ("land_procedure_records", "business_procedure_records"):
            conn.execute(
                text(
                    f"INSERT INTO public.{table} "
                    "(id, procedure_id, procedure_code) "
                    "VALUES (:id, :proc_id, :code)"
                ),
                {**params, "id": str(uuid4())},
            )
        conn.execute(
            text(
                "INSERT INTO public.procedure_form_templates "
                "(id, procedure_id, template_id) "
                "VALUES (:id, :proc_id, :template_id)"
            ),
            {**params, "id": str(uuid4())},
        )
        conn.execute(
            text(
                "INSERT INTO public.procedure_fees (id, procedure_id, name, amount) "
                "VALUES (:id, :proc_id, 'Lệ phí thử nghiệm', 1000)"
            ),
            {**params, "id": str(uuid4())},
        )
        conn.execute(
            text(
                "INSERT INTO public.procedure_conditions "
                "(id, procedure_id, title) VALUES (:id, :proc_id, 'Điều kiện thử nghiệm')"
            ),
            {**params, "id": str(uuid4())},
        )
        conn.execute(
            text(
                "INSERT INTO public.dynamic_form_templates (id, procedure_id, code) "
                "VALUES (:id, :proc_id, :code)"
            ),
            {**params, "id": str(uuid4())},
        )
        conn.execute(
            text(
                "INSERT INTO public.reference_classifications "
                "(key, code, name, procedure_id) "
                "VALUES ('land-purge-alias', :code, :name, :proc_id)"
            ),
            params,
        )
        conn.execute(
            text(
                "INSERT INTO public.contract_reference_uploads "
                "(id, procedure_key, metadata, file_data, page_images) "
                "VALUES (:id, 'land-purge-alias', :metadata, :bytes, :bytes)"
            ),
            {
                **params,
                "id": str(uuid4()),
                "metadata": json.dumps({
                    "procedure_code": code,
                    "procedure_name": procedure["name"],
                    "procedure_id": proc_id,
                }),
            },
        )
    return proc_id, template_id, linked_template_id


def test_admin_purge_procedure(auth):
    from app.api import custom_form_templates
    from app.core.rabbitmq_procedure import procedure_mq

    proc_id, template_id, linked_template_id = _seed_purge_data()
    custom_form_templates._PUBLIC_LIST_CACHE[(None, None)] = (0.0, {})
    custom_form_templates._PDF_CACHE[template_id] = b"cached-pdf"
    custom_form_templates._PDF_HEALTH_CACHE[template_id] = ("ok", None)

    forbidden = client.delete(
        "/api/v1/procedures/admin/DD-001", headers=auth()
    )
    assert forbidden.status_code == 403

    with patch.object(
        procedure_mq,
        "publish_form_template_deleted",
        side_effect=AssertionError("Purge must not publish RabbitMQ events"),
    ) as publish:
        response = client.delete(
            "/api/v1/procedures/admin/DD-001",
            headers=auth(role="admin"),
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is True
    assert body["status"] == "success"
    assert body["data"]["contract_cleanup"]["status"] == "skipped"
    assert "CONTRACT_DATABASE_URL is not configured" in body["message"]
    assert body["warning"] == body["message"]
    assert body["data"]["procedure_id"] == proc_id
    assert body["data"]["orphan_cleanup"] is False
    assert set(body["data"]["custom_template_ids"]) == {
        template_id, linked_template_id,
    }
    publish.assert_not_called()
    assert custom_form_templates._PUBLIC_LIST_CACHE == {}
    assert custom_form_templates._PDF_CACHE == {}
    assert custom_form_templates._PDF_HEALTH_CACHE == {}

    with db_connection() as conn:
        assert conn.execute(
            text("SELECT count(*) FROM public.procedures WHERE id = :id"),
            {"id": proc_id},
        ).scalar_one() == 0
        for table in (
            "land_procedure_records",
            "business_procedure_records",
            "procedure_form_templates",
            "procedure_requirements",
            "procedure_legal_bases",
            "procedure_fees",
            "procedure_conditions",
            "dynamic_form_templates",
        ):
            assert conn.execute(
                text(
                    f"SELECT count(*) FROM public.{table} "
                    "WHERE procedure_id = :id"
                ),
                {"id": proc_id},
            ).scalar_one() == 0, table

        for table in (
            "custom_form_submissions",
            "custom_form_templates",
            "form_template_preview_pages",
            "form_templates",
            "reference_classifications",
            "contract_reference_uploads",
        ):
            assert conn.execute(
                text(f"SELECT count(*) FROM public.{table}")
            ).scalar_one() == 0, table

        # Existing unrelated procedures and their children remain intact.
        assert conn.execute(
            text("SELECT count(*) FROM public.procedures")
        ).scalar_one() == 3
        assert conn.execute(
            text("SELECT count(*) FROM public.procedure_requirements")
        ).scalar_one() == 3
        assert conn.execute(
            text("SELECT count(*) FROM public.procedure_legal_bases")
        ).scalar_one() == 3

    missing = client.delete(
        "/api/v1/procedures/admin/DD-001",
        headers=auth(role="admin"),
    )
    assert missing.status_code == 404


def test_admin_purge_procedure_by_id(auth):
    proc_id, _, _ = _seed_purge_data()
    response = client.delete(
        f"/api/v1/procedures/admin/{proc_id}",
        headers=auth(role="admin"),
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["procedure_id"] == proc_id


def test_admin_purge_shared_template_is_rejected(auth):
    proc_id, template_id, _ = _seed_purge_data()
    with db_connection(write=True) as conn:
        conn.execute(
            text(
                "UPDATE public.procedures SET form_template_id = :template_id "
                "WHERE code = 'DD-002'"
            ),
            {"template_id": template_id},
        )

    response = client.delete(
        "/api/v1/procedures/admin/DD-001",
        headers=auth(role="admin"),
    )
    assert response.status_code == 409, response.text

    with db_connection() as conn:
        assert conn.execute(
            text("SELECT count(*) FROM public.procedures WHERE id = :id"),
            {"id": proc_id},
        ).scalar_one() == 1
        assert conn.execute(
            text("SELECT count(*) FROM public.custom_form_templates")
        ).scalar_one() == 2
        assert conn.execute(
            text("SELECT count(*) FROM public.custom_form_submissions")
        ).scalar_one() == 2
        assert conn.execute(
            text("SELECT count(*) FROM public.contract_reference_uploads")
        ).scalar_one() == 1


def test_admin_purge_orphan_classification(auth):
    orphan_id = str(uuid4())
    template_id = str(uuid4())
    with db_connection(write=True) as conn:
        params = {
            "proc_id": orphan_id,
            "template_id": template_id,
            "code": "DD-999",
            "name": "Thủ tục mồ côi",
            "metadata": json.dumps({
                "procedure_code": "DD-999",
                "procedure_name": "Thủ tục mồ côi",
                "procedure_id": orphan_id,
            }),
        }
        conn.execute(
            text(
                "INSERT INTO public.reference_classifications "
                "(key, code, name, procedure_id) "
                "VALUES ('orphan-land', :code, :name, :proc_id)"
            ),
            params,
        )
        conn.execute(
            text(
                "INSERT INTO public.custom_form_templates "
                "(id, procedure_id, name, word_data) "
                "VALUES (:template_id, :proc_id, :name, :bytes)"
            ),
            {**params, "bytes": b"orphan-word"},
        )
        conn.execute(
            text(
                "INSERT INTO public.custom_form_submissions (id, template_id) "
                "VALUES (:id, :template_id)"
            ),
            {**params, "id": str(uuid4())},
        )
        conn.execute(
            text(
                "INSERT INTO public.contract_reference_uploads "
                "(id, procedure_key, metadata, file_data) "
                "VALUES (:id, 'orphan-land', :metadata, :bytes)"
            ),
            {**params, "id": str(uuid4()), "bytes": b"orphan-pdf"},
        )

    response = client.delete(
        "/api/v1/procedures/admin/DD-999",
        headers=auth(role="admin"),
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["orphan_cleanup"] is True
    with db_connection() as conn:
        for table in (
            "custom_form_templates",
            "custom_form_submissions",
            "reference_classifications",
            "contract_reference_uploads",
        ):
            assert conn.execute(
                text(f"SELECT count(*) FROM public.{table}")
            ).scalar_one() == 0, table
        assert conn.execute(
            text("SELECT count(*) FROM public.procedures")
        ).scalar_one() == 4


def _purge_table_counts():
    tables = (
        "procedures",
        "custom_form_templates",
        "custom_form_submissions",
        "form_templates",
        "form_template_preview_pages",
        "procedure_form_templates",
        "procedure_requirements",
        "procedure_legal_bases",
        "procedure_fees",
        "procedure_conditions",
        "land_procedure_records",
        "business_procedure_records",
        "dynamic_form_templates",
        "reference_classifications",
        "contract_reference_uploads",
    )
    with db_connection() as conn:
        return {
            table: conn.execute(
                text(f"SELECT count(*) FROM public.{table}")
            ).scalar_one()
            for table in tables
        }


@pytest.mark.parametrize("legacy_code", [None, "", "DD-002"])
def test_purge_legacy_name_match_respects_foreign_code(auth, legacy_code):
    proc_id, _, _ = _seed_purge_data()
    extra_id = str(uuid4())

    with db_connection(write=True) as conn:
        name = conn.execute(
            text("SELECT name FROM public.procedures WHERE id = :id"),
            {"id": proc_id},
        ).scalar_one()

        if legacy_code == "DD-002":
            conn.execute(
                text(
                    "UPDATE public.procedures SET name = :name "
                    "WHERE code = 'DD-002'"
                ),
                {"name": name},
            )

        conn.execute(
            text(
                "INSERT INTO public.form_templates "
                "(id, procedure_code, procedure_name, file_data, preview_pdf) "
                "VALUES (:id, :code, :name, :bytes, :bytes)"
            ),
            {
                "id": extra_id,
                "code": legacy_code,
                "name": name,
                "bytes": b"keep-foreign-bytea",
            },
        )
        conn.execute(
            text(
                "INSERT INTO public.form_template_preview_pages "
                "(template_id, page_number, image_data) "
                "VALUES (:id, 1, :bytes)"
            ),
            {"id": extra_id, "bytes": b"keep-foreign-preview"},
        )

    response = client.delete(
        f"/api/v1/procedures/admin/{proc_id}",
        headers=auth(role="admin"),
    )
    assert response.status_code == 200, response.text

    with db_connection() as conn:
        legacy = conn.execute(
            text(
                "SELECT file_data, preview_pdf FROM public.form_templates "
                "WHERE id = :id"
            ),
            {"id": extra_id},
        ).first()
        preview = conn.execute(
            text(
                "SELECT image_data FROM public.form_template_preview_pages "
                "WHERE template_id = :id"
            ),
            {"id": extra_id},
        ).first()

    if legacy_code == "DD-002":
        assert tuple(legacy) == (
            b"keep-foreign-bytea", b"keep-foreign-bytea"
        )
        assert tuple(preview) == (b"keep-foreign-preview",)
    else:
        assert legacy is None
        assert preview is None


@pytest.mark.parametrize("content_key", ["code", "procedure_code"])
def test_purge_by_uuid_finds_templates_using_resolved_code(auth, content_key):
    proc_id, _, _ = _seed_purge_data()
    extra_id = str(uuid4())

    with db_connection(write=True) as conn:
        conn.execute(
            text(
                "INSERT INTO public.custom_form_templates "
                "(id, procedure_id, name, content, word_data, reference_pdf) "
                "VALUES (:id, NULL, 'JSON-only', :content, :bytes, :bytes)"
            ),
            {
                "id": extra_id,
                "content": json.dumps({content_key: "DD-001"}),
                "bytes": b"json-only-bytea",
            },
        )
        conn.execute(
            text(
                "INSERT INTO public.custom_form_submissions "
                "(id, template_id) VALUES (:id, :template)"
            ),
            {"id": str(uuid4()), "template": extra_id},
        )

    response = client.delete(
        f"/api/v1/procedures/admin/{proc_id}",
        headers=auth(role="admin"),
    )
    assert response.status_code == 200, response.text
    assert extra_id in response.json()["data"]["custom_template_ids"]

    counts = _purge_table_counts()
    assert counts["custom_form_templates"] == 0
    assert counts["custom_form_submissions"] == 0


@pytest.mark.parametrize("drop_pointer_column", [False, True])
def test_purge_rejects_template_shared_through_association(
    auth, drop_pointer_column
):
    proc_id, template_id, _ = _seed_purge_data()

    with db_connection(write=True) as conn:
        conn.execute(
            text(
                "INSERT INTO public.procedure_form_templates "
                "(id, procedure_id, template_id) "
                "SELECT :id, id, :template_id FROM public.procedures "
                "WHERE code = 'DD-002'"
            ),
            {"id": str(uuid4()), "template_id": template_id},
        )
        if drop_pointer_column:
            conn.execute(
                text(
                    "ALTER TABLE public.procedures "
                    "DROP COLUMN form_template_id"
                )
            )

    before = _purge_table_counts()
    response = client.delete(
        f"/api/v1/procedures/admin/{proc_id}",
        headers=auth(role="admin"),
    )
    assert response.status_code == 409, response.text
    payload = response.json()
    message = (
        payload.get("detail")
        or payload.get("error", {}).get("message")
    )
    assert message == (
        "Không thể xóa: Mẫu biểu mẫu đang được sử dụng chung "
        "bởi thủ tục khác."
    )
    assert _purge_table_counts() == before


def test_purge_supports_missing_procedure_template_column(auth):
    proc_id, _, _ = _seed_purge_data()
    with db_connection(write=True) as conn:
        conn.execute(
            text(
                "ALTER TABLE public.procedures "
                "DROP COLUMN form_template_id"
            )
        )

    response = client.delete(
        f"/api/v1/procedures/admin/{proc_id}",
        headers=auth(role="admin"),
    )
    assert response.status_code == 200, response.text

    counts = _purge_table_counts()
    assert counts["procedures"] == 3
    assert counts["custom_form_templates"] == 0
    assert counts["custom_form_submissions"] == 0
    assert counts["contract_reference_uploads"] == 0


def test_contract_connection_failure_happens_after_primary_commit(
    auth, monkeypatch, caplog
):
    from app.services import procedure_service

    proc_id, _, _ = _seed_purge_data()
    observed = []
    secret = "do-not-log-contract-password"

    def unavailable_contract():
        counts = _purge_table_counts()
        observed.append(
            (
                counts["procedures"],
                counts["custom_form_templates"],
                counts["reference_classifications"],
            )
        )
        raise RuntimeError(secret)

    monkeypatch.setenv(
        "CONTRACT_DATABASE_URL",
        "postgresql+psycopg://unused",
    )
    monkeypatch.setattr(
        procedure_service,
        "_purge_contract_connection",
        unavailable_contract,
    )

    with caplog.at_level("WARNING", logger=procedure_service.__name__):
        response = client.delete(
            f"/api/v1/procedures/admin/{proc_id}",
            headers=auth(role="admin"),
        )

    assert response.status_code == 200, response.text
    assert observed == [(3, 0, 0)]
    assert response.json()["status"] == "partial_success"
    data = response.json()["data"]
    assert data["contract_cleanup"]["status"] == "failed"
    assert data["contract_cleanup"]["error"]
    assert "_contract_cleanup" not in data
    assert "Post-commit upload cleanup failed" in caplog.text
    assert secret not in caplog.text
    assert _purge_table_counts()["contract_reference_uploads"] == 0


def test_contract_cleanup_rollback_does_not_rollback_primary(
    auth, monkeypatch
):
    from importlib.util import spec_from_file_location, module_from_spec
    from pathlib import Path
    spec = spec_from_file_location("procedure_test_conftest", Path(__file__).with_name("conftest.py"))
    local_conftest = module_from_spec(spec)
    spec.loader.exec_module(local_conftest)
    _make_engine = local_conftest._make_engine
    from app.services import procedure_service

    proc_id, _, _ = _seed_purge_data()
    remote = _make_engine()

    try:
        with remote.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO public.contract_reference_uploads "
                    "(id, procedure_key, metadata, file_data) "
                    "VALUES (:id, 'DD-001', :metadata, :bytes)"
                ),
                {
                    "id": str(uuid4()),
                    "metadata": json.dumps({
                        "procedure_code": "DD-001",
                        "procedure_id": proc_id,
                    }),
                    "bytes": b"remote-file",
                },
            )

        @contextmanager
        def failing_remote_transaction():
            with remote.begin() as conn:
                yield conn
                raise RuntimeError("remote transaction failed")

        monkeypatch.setenv(
            "CONTRACT_DATABASE_URL",
            "postgresql+psycopg://unused",
        )
        monkeypatch.setattr(
            procedure_service,
            "_purge_contract_connection",
            failing_remote_transaction,
        )

        response = client.delete(
            f"/api/v1/procedures/admin/{proc_id}",
            headers=auth(role="admin"),
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "partial_success"
        data = response.json()["data"]
        assert data["contract_cleanup"]["status"] == "failed"
        assert data["contract_cleanup"]["error"]
        assert "contract.contract_reference_uploads" not in data["deleted"]

        counts = _purge_table_counts()
        assert counts["procedures"] == 3
        assert counts["custom_form_templates"] == 0
        assert counts["reference_classifications"] == 0

        with remote.connect() as conn:
            assert conn.execute(
                text(
                    "SELECT file_data "
                    "FROM public.contract_reference_uploads"
                )
            ).scalar_one() == b"remote-file"
    finally:
        remote.dispose()


def test_primary_failure_rolls_back_and_skips_contract_cleanup(auth):
    from app.services import procedure_service

    _seed_purge_data()
    before = _purge_table_counts()

    def failing_purge(conn, identifier):
        conn.execute(
            text(
                "DELETE FROM public.land_procedure_records "
                "WHERE procedure_code = 'DD-001'"
            )
        )
        raise RuntimeError("forced primary failure")

    with patch(
        "app.api.procedures.purge_procedure", side_effect=failing_purge
    ):
        with patch(
            "app.api.procedures.cleanup_purged_procedure_contract_uploads"
        ) as cleanup:
            old_raise = client._transport.raise_server_exceptions
            client._transport.raise_server_exceptions = False
            try:
                response = client.delete(
                    "/api/v1/procedures/admin/DD-001",
                    headers=auth(role="admin"),
                )
            finally:
                client._transport.raise_server_exceptions = old_raise
            assert response.status_code == 500, response.text
            cleanup.assert_not_called()

    assert _purge_table_counts() == before


@pytest.mark.parametrize("raw_url", [None, "", " \t "])
def test_contract_cleanup_status_skipped_when_no_db_url(
    auth, monkeypatch, raw_url
):
    from app.services import procedure_service

    _seed_purge_data()
    if raw_url is None:
        monkeypatch.delenv("CONTRACT_DATABASE_URL", raising=False)
    else:
        monkeypatch.setenv("CONTRACT_DATABASE_URL", raw_url)

    with patch.object(
        procedure_service, "_purge_contract_connection"
    ) as remote:
        response = client.delete(
            "/api/v1/procedures/admin/DD-001",
            headers=auth(role="admin"),
        )

    assert response.status_code == 200, response.text
    body = response.json()
    cleanup = body["data"]["contract_cleanup"]
    assert body["status"] == "success"
    assert cleanup["status"] == "skipped"
    assert cleanup["reason"] == (
        "CONTRACT_DATABASE_URL is not configured"
    )
    assert cleanup["sources"]["procedure"]["status"] == "completed"
    assert cleanup["sources"]["contract"]["status"] == "skipped"
    assert body["warning"] == body["message"]
    remote.assert_not_called()

    counts = _purge_table_counts()
    assert counts["procedures"] == 3
    assert counts["custom_form_templates"] == 0
    assert counts["contract_reference_uploads"] == 0


def test_contract_cleanup_failure_returns_partial_success(
    auth, monkeypatch
):
    from sqlalchemy.exc import OperationalError
    from importlib.util import spec_from_file_location, module_from_spec
    from pathlib import Path
    spec = spec_from_file_location("procedure_test_conftest", Path(__file__).with_name("conftest.py"))
    local_conftest = module_from_spec(spec)
    spec.loader.exec_module(local_conftest)
    _make_engine = local_conftest._make_engine
    from app.services import procedure_service

    proc_id, _, _ = _seed_purge_data()
    remote = _make_engine()

    try:
        with remote.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO public.contract_reference_uploads "
                    "(id, procedure_key, metadata, file_data) "
                    "VALUES (:id, 'DD-001', '{}', :bytes)"
                ),
                {"id": str(uuid4()), "bytes": b"remote-file"},
            )

        @event.listens_for(remote, "before_cursor_execute")
        def fail_remote_delete(
            conn, cursor, statement, parameters, context, executemany
        ):
            if statement.lstrip().upper().startswith(
                "DELETE FROM PUBLIC.CONTRACT_REFERENCE_UPLOADS"
            ):
                raise OperationalError(
                    "private SQL",
                    {"secret": "private parameter"},
                    RuntimeError("private driver detail"),
                )

        @contextmanager
        def remote_transaction():
            with remote.begin() as conn:
                yield conn

        monkeypatch.setenv(
            "CONTRACT_DATABASE_URL",
            "postgresql+psycopg://unused",
        )
        monkeypatch.setattr(
            procedure_service,
            "_purge_contract_connection",
            remote_transaction,
        )

        response = client.delete(
            f"/api/v1/procedures/admin/{proc_id}",
            headers=auth(role="admin"),
        )

        assert response.status_code == 200, response.text
        body = response.json()
        cleanup = body["data"]["contract_cleanup"]
        assert body["success"] is True
        assert body["status"] == "partial_success"
        assert cleanup["status"] == "failed"
        assert "OperationalError" in cleanup["error"]
        assert (
            "Thủ tục và biểu mẫu trong Procedure DB đã xóa vĩnh viễn, "
            "nhưng dọn tệp Contract DB thất bại:"
        ) in body["message"]
        assert "private SQL" not in response.text
        assert "private parameter" not in response.text
        assert "private driver detail" not in response.text
        assert "contract.contract_reference_uploads" not in (
            body["data"]["deleted"]
        )

        counts = _purge_table_counts()
        assert counts["procedures"] == 3
        assert counts["custom_form_templates"] == 0
        assert counts["reference_classifications"] == 0
        assert counts["contract_reference_uploads"] == 0

        with remote.connect() as conn:
            assert conn.execute(
                text(
                    "SELECT count(*) "
                    "FROM public.contract_reference_uploads"
                )
            ).scalar_one() == 1
    finally:
        remote.dispose()


def test_contract_cleanup_completed_returns_success(auth, monkeypatch):
    from importlib.util import spec_from_file_location, module_from_spec
    from pathlib import Path
    spec = spec_from_file_location("procedure_test_conftest", Path(__file__).with_name("conftest.py"))
    local_conftest = module_from_spec(spec)
    spec.loader.exec_module(local_conftest)
    _make_engine = local_conftest._make_engine
    from app.services import procedure_service

    _seed_purge_data()
    remote = _make_engine()

    try:
        with remote.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO public.contract_reference_uploads "
                    "(id, procedure_key, metadata, file_data) "
                    "VALUES (:id, 'DD-001', '{}', :bytes)"
                ),
                {"id": str(uuid4()), "bytes": b"remote-file"},
            )

        @contextmanager
        def remote_transaction():
            with remote.begin() as conn:
                yield conn

        monkeypatch.setenv(
            "CONTRACT_DATABASE_URL",
            "postgresql+psycopg://unused",
        )
        monkeypatch.setattr(
            procedure_service,
            "_purge_contract_connection",
            remote_transaction,
        )

        response = client.delete(
            "/api/v1/procedures/admin/DD-001",
            headers=auth(role="admin"),
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "success"
        assert body["warning"] is None
        assert body["data"]["contract_cleanup"]["status"] == "completed"
        assert body["data"]["deleted"][
            "contract.contract_reference_uploads"
        ] == 1

        with remote.connect() as conn:
            assert conn.execute(
                text(
                    "SELECT count(*) "
                    "FROM public.contract_reference_uploads"
                )
            ).scalar_one() == 0
    finally:
        remote.dispose()


def test_purge_deletes_reference_classifications(auth):
    proc_id, _, _ = _seed_purge_data()

    with db_connection(write=True) as conn:
        other_id = conn.execute(
            text(
                "SELECT id FROM public.procedures "
                "WHERE code = 'DD-002'"
            )
        ).scalar_one()
        conn.execute(
            text(
                "INSERT INTO public.reference_classifications "
                "(key, code, name, procedure_id) "
                "VALUES (:key, :code, :name, :procedure_id)"
            ),
            [
                {
                    "key": "classification-by-id",
                    "code": None,
                    "name": "By ID",
                    "procedure_id": proc_id,
                },
                {
                    "key": "classification-by-code",
                    "code": "DD-001",
                    "name": "By code",
                    "procedure_id": None,
                },
                {
                    "key": "classification-unrelated",
                    "code": "DD-002",
                    "name": "Keep",
                    "procedure_id": other_id,
                },
            ],
        )

    response = client.delete(
        f"/api/v1/procedures/admin/{proc_id}",
        headers=auth(role="admin"),
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["deleted"][
        "procedure.reference_classifications"
    ] == 3

    with db_connection() as conn:
        remaining = conn.execute(
            text("SELECT key FROM public.reference_classifications")
        ).scalars().all()
    assert remaining == ["classification-unrelated"]


def test_purge_supports_missing_reference_classifications(auth):
    _seed_purge_data()
    with db_connection(write=True) as conn:
        conn.execute(text("DROP TABLE public.reference_classifications"))

    response = client.delete(
        "/api/v1/procedures/admin/DD-001",
        headers=auth(role="admin"),
    )
    assert response.status_code == 200, response.text
    assert "procedure.reference_classifications" not in (
        response.json()["data"]["deleted"]
    )


def test_purge_uses_row_locks_without_lock_table(monkeypatch):
    import inspect as python_inspect
    from app.services import procedure_service

    proc_id = str(uuid4())
    conn = MagicMock()
    conn.dialect.name = "postgresql"
    executed = conn.execute.return_value
    executed.mappings.return_value.first.return_value = None
    executed.mappings.return_value.all.return_value = [
        {
            "id": proc_id,
            "code": "DD-001",
            "name": "Target",
            "form_template_id": None,
        }
    ]
    executed.rowcount = 1

    monkeypatch.setattr(
        procedure_service,
        "_purge_schema",
        lambda _: {"procedures": {"id", "code", "name"}},
    )
    monkeypatch.setattr(
        procedure_service,
        "_purge_has_procedure_template_id",
        lambda _: False,
    )

    result = procedure_service.purge_procedure(conn, proc_id)
    statements = [
        str(call.args[0])
        for call in conn.execute.call_args_list
    ]
    procedure_selects = [
        statement
        for statement in statements
        if statement.startswith("SELECT id, code, name,")
    ]

    assert result["procedure_id"] == proc_id
    assert len(procedure_selects) == 1
    assert procedure_selects[0].endswith("ORDER BY id FOR UPDATE")
    assert any(
        statement.startswith("DELETE FROM public.procedures ")
        for statement in statements
    )
    assert all(
        "LOCK TABLE" not in statement.upper()
        for statement in statements
    )
    assert "LOCK TABLE" not in python_inspect.getsource(
        procedure_service
    ).upper()


def test_health():
    res = client.get("/api/v1/procedures/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok", "service": "procedure-service"}


def test_list_domains():
    res = client.get("/api/v1/procedures/domains")
    assert res.status_code == 200
    body = res.json()
    data = body["data"]
    assert len(data) >= 2
    codes = [d["code"] for d in data]
    assert "business" in codes
    assert "land" in codes
    assert "total_procedures" in body
    assert body["total_procedures"] >= 2


def test_get_procedures_by_domain():
    res = client.get("/api/v1/procedures/domains/land")
    assert res.status_code == 200
    body = res.json()
    assert body["error"] is None
    data = body["data"]
    assert data["domain"] == "land"
    assert len(data["items"]) > 0
    first = data["items"][0]
    assert "name" in first
    assert "code" in first
    assert "processing_days" in first


def test_get_procedure_names_by_domain():
    res = client.get("/api/v1/procedures/domains/business/names")
    assert res.status_code == 200
    body = res.json()
    assert body["error"] is None
    data = body["data"]
    assert data["domain"] == "business"
    assert len(data["items"]) > 0
    first = data["items"][0]
    assert "id" in first
    assert "code" in first
    assert "name" in first


def test_list_procedures():
    res = client.get("/api/v1/procedures")
    assert res.status_code == 200
    body = res.json()
    assert body["error"] is None
    assert "items" in body["data"]
    assert "pagination" in body["data"]
    assert len(body["data"]["items"]) > 0
    codes = [i["code"] for i in body["data"]["items"]]
    assert codes == sorted(codes)


def test_list_procedures_filter_domain():
    res_biz = client.get("/api/v1/procedures?domain=business")
    assert res_biz.status_code == 200
    for item in res_biz.json()["data"]["items"]:
        assert item["domain"] == "business"

    res_land = client.get("/api/v1/procedures?domain=land")
    assert res_land.status_code == 200
    for item in res_land.json()["data"]["items"]:
        assert item["domain"] == "land"


def test_search_procedures():
    # Tìm có kết quả
    res = client.post("/api/v1/procedures/search", json={"query": "đăng ký hộ kinh doanh"})
    assert res.status_code == 200
    items = res.json()["data"]["items"]
    assert len(items) > 0
    assert any(i["code"] == "HKD-001" for i in items)

    # Tìm không dấu
    res_no_accent = client.post("/api/v1/procedures/search", json={"query": "HO KINH DOANH"})
    assert res_no_accent.status_code == 200
    assert len(res_no_accent.json()["data"]["items"]) > 0

    # Tìm không ra
    res_none = client.post("/api/v1/procedures/search", json={"query": "xin chào không có thủ tục này"})
    assert res_none.status_code == 200
    assert res_none.json()["data"]["items"] == []


def test_search_invalid_input():
    res = client.post("/api/v1/procedures/search", json={"query": "   "})
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"


def test_get_procedure_by_id_and_code():
    # Tìm theo mã HKD-001
    res_code = client.get("/api/v1/procedures/HKD-001")
    assert res_code.status_code == 200
    data_code = res_code.json()["data"]
    assert data_code["code"] == "HKD-001"
    assert len(data_code["legal_bases"]) > 0
    assert len(data_code["requirements"]) > 0

    # Tìm theo UUID
    proc_id = data_code["id"]
    res_id = client.get(f"/api/v1/procedures/{proc_id}")
    assert res_id.status_code == 200
    assert res_id.json()["data"]["id"] == proc_id


def test_get_procedure_sub_resources():
    # Requirements
    res_req = client.get("/api/v1/procedures/HKD-001/requirements")
    assert res_req.status_code == 200
    assert "requirements" in res_req.json()["data"]
    assert len(res_req.json()["data"]["requirements"]) > 0

    # Conditions
    res_cond = client.get("/api/v1/procedures/HKD-001/conditions")
    assert res_cond.status_code == 200
    assert "conditions" in res_cond.json()["data"]

    # Fees
    res_fees = client.get("/api/v1/procedures/HKD-001/fees")
    assert res_fees.status_code == 200
    assert "fees" in res_fees.json()["data"]


def test_get_procedure_not_found():
    res = client.get("/api/v1/procedures/UNKNOWN-999")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "PROCEDURE_NOT_FOUND"


def test_get_procedure_form_schema():
    # Test DD-001
    res_dd = client.get("/api/v1/procedures/form-schema/DD-001")
    assert res_dd.status_code == 200
    body_dd = res_dd.json()["data"]
    assert body_dd["procedure_code"] == "DD-001"
    assert body_dd["domain"] == "land"
    assert body_dd["target_table"] == "land_procedure_records"
    assert len(body_dd["sections"]) > 0

    # Test HKD-001
    res_hkd = client.get("/api/v1/procedures/HKD-001/form-schema")
    assert res_hkd.status_code == 200
    body_hkd = res_hkd.json()["data"]
    assert body_hkd["procedure_code"] == "HKD-001"
    assert body_hkd["domain"] == "business"
    assert body_hkd["target_table"] == "business_procedure_records"

    # Test invalid code
    res_inv = client.get("/api/v1/procedures/form-schema/UNKNOWN-999")
    assert res_inv.status_code == 404
    assert res_inv.json()["error"]["code"] == "SCHEMA_NOT_FOUND"


def test_submit_and_get_applications(auth):
    headers = auth()
    # Submit land application
    payload_land = {
        "procedure_code": "DD-001",
        "status": "draft",
        "form_data": valid_form(
            "DD-001",
            applicant_name="Lê Văn C",
            applicant_phone="0988776655",
        ),
        "extra_data": {"source": "unit_test"},
    }
    res_sub_land = client.post("/api/v1/procedures/applications", json=payload_land, headers=headers)
    assert res_sub_land.status_code == 200
    land_rec = res_sub_land.json()["data"]
    assert "id" in land_rec
    assert land_rec["procedure_code"] == "DD-001"
    land_id = land_rec["id"]

    # Get land application by ID
    res_get_land = client.get(f"/api/v1/procedures/applications/land/{land_id}", headers=headers)
    assert res_get_land.status_code == 200
    fetched_land = res_get_land.json()["data"]
    assert fetched_land["id"] == land_id
    assert fetched_land["applicant_name"] == "Lê Văn C"

    # Submit business application
    payload_biz = {
        "procedure_code": "HKD-001",
        "status": "submitted",
        "form_data": valid_form(
            "HKD-001",
            applicant_name="Phạm Thị D",
            business_name_vn="CỬA HÀNG HOA TƯƠI MAI",
            headquarters_address="Quận 3, TP. Hồ Chí Minh",
            charter_capital=50000000,
        ),
    }
    res_sub_biz = client.post("/api/v1/procedures/applications", json=payload_biz, headers=headers)
    assert res_sub_biz.status_code == 200
    biz_rec = res_sub_biz.json()["data"]
    assert "id" in biz_rec
    assert biz_rec["procedure_code"] == "HKD-001"
    biz_id = biz_rec["id"]

    # Get business application by ID
    res_get_biz = client.get(f"/api/v1/procedures/applications/business/{biz_id}", headers=headers)
    assert res_get_biz.status_code == 200
    fetched_biz = res_get_biz.json()["data"]
    assert fetched_biz["id"] == biz_id
    assert fetched_biz["business_name_vn"] == "CỬA HÀNG HOA TƯƠI MAI"

    # Test invalid domain
    res_bad_domain = client.get(f"/api/v1/procedures/applications/finance/{land_id}", headers=headers)
    assert res_bad_domain.status_code == 400
    assert res_bad_domain.json()["error"]["code"] == "INVALID_DOMAIN"

    # Test lookup by composite code (e.g. DD-001-XXXXXXXX)
    short_land_id = land_id[:8].upper()
    comp_code = f"DD-001-{short_land_id}"
    res_lookup = client.get(f"/api/v1/procedures/applications/lookup/{comp_code}", headers=headers)
    assert res_lookup.status_code == 200
    lookup_data = res_lookup.json()["data"]
    assert lookup_data["application_code"] == comp_code
    assert lookup_data["record"]["id"] == land_id
    assert lookup_data["record"]["applicant_name"] == "Lê Văn C"
    assert lookup_data["record"]["form_data"]["applicant_name"] == "Lê Văn C"
    assert "schema" in lookup_data
    assert "sections" in lookup_data["schema"]

    # Test lookup by short id
    res_lookup_short = client.get(f"/api/v1/procedures/applications/lookup/{short_land_id}", headers=headers)
    assert res_lookup_short.status_code == 200
    assert res_lookup_short.json()["data"]["record"]["id"] == land_id

    # Test lookup not found
    res_lookup_nf = client.get("/api/v1/procedures/applications/lookup/NON-EXISTENT-9999", headers=headers)
    assert res_lookup_nf.status_code == 404

