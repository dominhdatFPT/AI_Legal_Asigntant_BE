import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app.api.v1 import conversations
from app.core import database
from app.repositories import conversation as repo
from app.schemas.message import MessageOut
from app.services import history_cache


@pytest.mark.parametrize("cache_hit", [False, True])
def test_load_checks_ownership_and_closes_before_cache_io(db, monkeypatch, cache_hit):
    owner = uuid.uuid4()
    conversation = repo.create_conversation(db, owner)
    conversation_id = conversation.id
    repo.add_message_pair(db, conversation, "Hỏi", "Đáp", None, datetime.now(timezone.utc))
    db.close()
    expected = [{"role": "user", "content": "Hỏi"}, {"role": "assistant", "content": "Đáp"}]
    calls = []
    get_messages = repo.get_messages

    def get(user_id, cid):
        assert (user_id, cid) == (owner, conversation_id)
        assert not db.in_transaction()
        calls.append("get")
        return expected if cache_hit else None

    def read_history(*args, **kwargs):
        calls.append("read_db")
        return get_messages(*args, **kwargs)

    def put(user_id, cid, history):
        assert (user_id, cid) == (owner, conversation_id)
        assert not db.in_transaction()
        assert history == expected
        calls.append("put")

    monkeypatch.setattr(history_cache, "get", get)
    monkeypatch.setattr(history_cache, "put", put)
    monkeypatch.setattr(repo, "get_messages", read_history)

    with pytest.raises(HTTPException) as error:
        conversations._load_for_chat(db, conversation_id, SimpleNamespace(sub=str(uuid.uuid4())))
    assert error.value.status_code == 403
    assert error.value.detail["code"] == "CONVERSATION_FORBIDDEN"
    assert not db.in_transaction() and not calls

    loaded, history = conversations._load_for_chat(db, conversation_id, SimpleNamespace(sub=str(owner)))
    assert inspect(loaded).detached
    assert (loaded.user_id, loaded.id, history) == (owner, conversation_id, expected)
    assert calls == (["get"] if cache_hit else ["get", "read_db", "put"])


def test_save_releases_post_commit_refresh_before_cache_append(db, monkeypatch):
    owner = uuid.uuid4()
    conversation = repo.create_conversation(db, owner)
    conversation_id = conversation.id
    db.close()
    calls = []

    with Session(db.get_bind(), expire_on_commit=True) as save_db:
        close = save_db.close

        def close_tracking():
            calls.append("close")
            close()

        def append(user_id, cid, history, limit):
            assert calls == ["close"]
            assert not save_db.in_transaction()
            assert (user_id, cid) == (owner, conversation_id)
            assert history == [{"role": "user", "content": "Hỏi"}, {"role": "assistant", "content": "Đáp"}]
            calls.append("append")

        monkeypatch.setattr(save_db, "close", close_tracking)
        monkeypatch.setattr(history_cache, "append", append)
        message_pair = conversations._save_pair(
            save_db, conversation, "Hỏi", "Đáp", {"tokens_used": 7}, datetime.now(timezone.utc),
        )
        assert calls == ["close", "append"]
        assert all(inspect(message).detached for message in message_pair)
        user_message, assistant_message = (MessageOut.model_validate(message) for message in message_pair)

    assert (user_message.role, user_message.content, user_message.metadata) == ("user", "Hỏi", None)
    assert (assistant_message.role, assistant_message.content, assistant_message.metadata) == (
        "assistant", "Đáp", {"tokens_used": 7},
    )
    assert user_message.id != assistant_message.id
    assert user_message.created_at < assistant_message.created_at


def test_missing_conversation_releases_db_before_invalidation(db, monkeypatch):
    owner = uuid.uuid4()
    conversation = repo.create_conversation(db, owner)
    conversation_id = conversation.id
    db.close()
    with Session(db.get_bind()) as other_db:
        repo.delete_conversation(other_db, repo.get_conversation(other_db, conversation_id))

    calls = []

    def invalidate(user_id, cid):
        assert not db.in_transaction()
        calls.append((user_id, cid))

    monkeypatch.setattr(history_cache, "invalidate", invalidate)
    with pytest.raises(HTTPException) as error:
        conversations._save_pair(db, conversation, "Hỏi", "Đáp", None, datetime.now(timezone.utc))
    assert error.value.status_code == 404
    assert calls == [(owner, conversation_id)]


def test_engine_and_session_factories_initialized_once_across_threads(monkeypatch):
    for name in ("_engine", "_session_factory", "_read_session_factory"):
        monkeypatch.setattr(database, name, None)
    monkeypatch.setattr(database, "database_url", lambda: "sqlite://")
    create_engine = database.create_engine
    engines = []
    begin = threading.Barrier(10, timeout=5)

    def create(*args, **kwargs):
        time.sleep(0.05)  # Widen the window in which unlocked callers would create extra engines.
        engine = create_engine(*args, **kwargs)
        engines.append(engine)
        return engine

    def get():
        begin.wait()
        engine = database.get_engine()
        assert database._session_factory is database._read_session_factory
        with database._session_factory() as db:
            assert db.get_bind() is engine
        return engine

    monkeypatch.setattr(database, "create_engine", create)
    try:
        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = [pool.submit(get) for _ in range(10)]
            results = [future.result(timeout=10) for future in futures]
        assert len(engines) == 1
        assert all(engine is engines[0] for engine in results)
    finally:
        for engine in engines:
            engine.dispose()
