import os
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateTable

from app.core.database import Base
from app.models.consultation import Consultation, ConsultationSummary, utcnow


@pytest.fixture(params=["sqlite", "postgresql"])
def summary_db(request, db):
    if request.param == "sqlite":
        yield db
        return

    url = os.getenv("TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Cần TEST_POSTGRES_URL để kiểm tra PostgreSQL thật")

    url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    schema = f"t_summary_{uuid.uuid4().hex}"
    admin = create_engine(url)
    engine = None
    created = False
    try:
        with admin.begin() as conn:
            version = int(conn.scalar(text("show server_version_num")))
            assert version >= 150000, "Cần PostgreSQL >= 15"
            conn.execute(text(f"create schema {schema}"))
        created = True

        engine = create_engine(
            url,
            connect_args={"options": f"-csearch_path={schema}"},
        )
        Base.metadata.create_all(engine)
        with Session(engine, expire_on_commit=False) as session:
            yield session
    finally:
        if engine is not None:
            engine.dispose()
        try:
            if created:
                with admin.begin() as conn:
                    conn.execute(text(f"drop schema {schema} cascade"))
        finally:
            admin.dispose()


def _consultation(db):
    row = Consultation(
        citizen_user_id=uuid.uuid4(),
        source="lobby",
        question="Câu hỏi kiểm thử",
    )
    db.add(row)
    db.flush()
    return row.id


def _summary(consultation_id, stamp, message_id):
    return ConsultationSummary(
        consultation_id=consultation_id,
        window_end=stamp,
        window_end_message_id=message_id,
        message_count=1,
        summary={"overview": "Kiểm thử"},
        schema_version=1,
    )


def test_postgresql_ddl_co_nulls_not_distinct():
    ddl = str(CreateTable(ConsultationSummary.__table__).compile(
        dialect=postgresql.dialect(),
    ))
    assert (
        "CONSTRAINT consultation_summaries_window_key "
        "UNIQUE NULLS NOT DISTINCT"
    ) in ddl


@pytest.mark.parametrize("message_id", [None, uuid.UUID(int=1)])
def test_chan_trung_window_ke_ca_message_id_null(summary_db, message_id):
    db = summary_db
    cid = _consultation(db)
    stamp = utcnow()
    db.add(_summary(cid, stamp, message_id))
    db.commit()

    db.add(_summary(cid, stamp, message_id))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

    assert db.scalar(
        select(func.count()).select_from(ConsultationSummary)
    ) == 1


def test_cung_timestamp_khac_message_id_van_hop_le(summary_db):
    db = summary_db
    cid = _consultation(db)
    stamp = utcnow()
    db.add_all([
        _summary(cid, stamp, None),
        _summary(cid, stamp, uuid.UUID(int=1)),
        _summary(cid, stamp, uuid.UUID(int=2)),
    ])
    db.commit()

    assert db.scalar(
        select(func.count()).select_from(ConsultationSummary)
    ) == 3


def test_null_id_khac_consultation_hoac_timestamp_van_hop_le(summary_db):
    db = summary_db
    first = _consultation(db)
    second = _consultation(db)
    stamp = utcnow()
    db.add_all([
        _summary(first, stamp, None),
        _summary(second, stamp, None),
        _summary(first, stamp + timedelta(seconds=1), None),
    ])
    db.commit()

    assert db.scalar(
        select(func.count()).select_from(ConsultationSummary)
    ) == 3


def test_sql_migration_nang_cap_constraint_cu_va_chay_lai_duoc(summary_db):
    db = summary_db
    if db.get_bind().dialect.name != "postgresql":
        pytest.skip("Migration SQL này dành cho PostgreSQL")

    # Mô phỏng constraint cũ, vốn cho phép NULL trùng.
    db.execute(text(
        "alter table consultation_summaries "
        "drop constraint consultation_summaries_window_key, "
        "add constraint consultation_summaries_window_key "
        "unique (consultation_id, window_end, window_end_message_id)"
    ))
    db.commit()

    script = (
        Path(__file__).resolve().parents[1]
        / "database"
        / "001_consultation_schema.sql"
    ).read_text(encoding="utf-8")

    add_column = (
        "alter table consultation_summaries add column if not exists "
        "window_end_message_id uuid;"
    )
    migration = script.split(add_column, 1)[1].split(";", 1)[0].strip()
    assert migration.startswith("alter table consultation_summaries")

    for _ in range(2):
        db.execute(text(add_column))
        db.execute(text(migration))
        db.commit()

    cid = _consultation(db)
    stamp = utcnow()
    db.add(_summary(cid, stamp, None))
    db.commit()

    db.add(_summary(cid, stamp, None))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

    assert db.scalar(
        select(func.count()).select_from(ConsultationSummary)
    ) == 1
