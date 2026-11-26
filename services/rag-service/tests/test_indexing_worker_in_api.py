import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app import main, rag_indexing_worker


@pytest.mark.parametrize(("flag", "url", "starts"), [
    (" YeS ", "amqp://localhost/", True),
    ("false", "amqp://localhost/", False),
    (None, "amqp://localhost/", False),
    ("true", None, False),
])
def test_indexing_worker_needs_flag_and_broker_url_and_is_cancelled(monkeypatch, flag, url, starts):
    monkeypatch.setenv("RABBITMQ_ENABLED", "true")
    if url is None:
        monkeypatch.delenv("RABBITMQ_URL", raising=False)
    else:
        monkeypatch.setenv("RABBITMQ_URL", url)
    if flag is None:
        monkeypatch.delenv("RUN_RAG_INDEXING_WORKER_IN_API", raising=False)
    else:
        monkeypatch.setenv("RUN_RAG_INDEXING_WORKER_IN_API", flag)
    monkeypatch.setattr(main, "get_settings", lambda: SimpleNamespace(rag_database_url=None))
    monkeypatch.setattr(main, "close_async_embedding_client", AsyncMock())
    cancelled = []

    async def run_worker():
        try:
            await asyncio.Future()
        finally:
            cancelled.append(True)

    worker = AsyncMock(side_effect=run_worker)
    monkeypatch.setattr(rag_indexing_worker, "main", worker)

    async def run():
        async with main.lifespan(main.app):
            await asyncio.sleep(0)
            if starts:
                worker.assert_awaited_once_with()
            else:
                worker.assert_not_awaited()

    asyncio.run(run())
    assert cancelled == ([True] if starts else [])
