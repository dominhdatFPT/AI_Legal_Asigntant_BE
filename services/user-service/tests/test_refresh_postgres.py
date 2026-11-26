"""Opt-in integration against a dedicated Postgres test database."""
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Event

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from app.models import RefreshToken
from app.repositories import refresh_token_repository as tokens

URL = os.getenv("AUTH_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not URL, reason="AUTH_TEST_POSTGRES_URL is not configured")


def test_original_schema_and_rotation_lock_order():
    url = URL.replace("postgresql://", "postgresql+psycopg://", 1) if URL.startswith("postgresql://") else URL
    engine = create_engine(url)
    schema = "auth_r2_" + uuid.uuid4().hex
    user_id = uuid.uuid4()
    digest = "a" * 64
    with engine.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    scoped = engine.execution_options(schema_translate_map={None: schema})
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(f'CREATE TABLE "{schema}".users (id uuid PRIMARY KEY)')
            connection.execute(text(f'INSERT INTO "{schema}".users (id) VALUES (:id)'), {"id": user_id})
            # Original table layout, independent of future ORM model additions.
            connection.exec_driver_sql(f'''
                CREATE TABLE "{schema}".refresh_tokens (
                    id uuid PRIMARY KEY,
                    user_id uuid NOT NULL REFERENCES "{schema}".users(id),
                    token_hash varchar NOT NULL UNIQUE,
                    expires_at timestamptz NOT NULL,
                    revoked_at timestamptz,
                    user_agent varchar,
                    created_at timestamptz NOT NULL DEFAULT now()
                )
            ''')
        columns = {column["name"] for column in inspect(engine).get_columns("refresh_tokens", schema=schema)}
        assert columns == set(RefreshToken.__table__.columns.keys())
        with Session(scoped) as db:
            tokens.add(db, user_id=user_id, token_hash=digest,
                       expires_at=datetime.now(timezone.utc) + timedelta(days=1))
            db.commit()
        started, acquired = Event(), Event()

        def competitor():
            with Session(scoped) as db:
                started.set()
                token = tokens.get_by_hash(db, digest, for_update=True)
                acquired.set()
                was_rotation = tokens.is_rotation(token)
                tokens.revoke_all_for_user(db, user_id)
                db.commit()
                return was_rotation

        with ThreadPoolExecutor(max_workers=1) as pool:
            with Session(scoped) as winner:
                old = tokens.get_by_hash(winner, digest, for_update=True)
                tokens.revoke(winner, old, rotated=True)
                future = pool.submit(competitor)
                try:
                    assert started.wait(5)
                    assert not acquired.wait(0.2), "competitor escaped the user/token lock"
                finally:
                    winner.commit()
            assert future.result(timeout=10) is True
        with Session(scoped) as db:
            old = tokens.get_by_hash(db, digest)
            assert old.revoked_at is not None
            assert not tokens.is_rotation(old)
            assert old.token_hash == digest
    finally:
        with engine.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        engine.dispose()
