from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from app.core.catalog_cache import CatalogCache
from app.repositories import procedure_repository as repo
from contextlib import contextmanager
from unittest.mock import patch


def test_cache_copies_values_and_does_not_cache_failure():
    cache = CatalogCache()
    calls = []

    def load():
        calls.append(1)
        return {"items": [1]}

    cache.get_or_load("procedures", "x", 30, load)["items"].append(2)
    assert cache.get_or_load("procedures", "x", 30, load) == {"items": [1]}
    assert len(calls) == 1
    cache.invalidate("procedures")
    assert cache.get_or_load("procedures", "x", 30, load) == {"items": [1]}
    assert len(calls) == 2

    def fail():
        raise RuntimeError("database unavailable")

    with pytest.raises(RuntimeError):
        cache.get_or_load("templates", "x", 30, fail)
    assert cache.get_or_load("templates", "x", 30, lambda: "ok") == "ok"


def test_invalidation_during_load_does_not_restore_stale_value():
    cache = CatalogCache()
    started, finish = Event(), Event()

    def load():
        started.set()
        assert finish.wait(5)
        return "old"

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(cache.get_or_load, "procedures", "x", 30, load)
        assert started.wait(5)
        cache.invalidate("procedures")
        finish.set()
        assert future.result() == "old"
    assert cache.get_or_load("procedures", "x", 30, lambda: "new") == "new"


def test_procedure_invalidation_clears_dynamic_templates():
    from app.core.catalog_cache import catalog_cache
    from app.services.procedure_service import invalidate_procedure_caches

    catalog_cache.invalidate("dynamic_templates")
    catalog_cache.get_or_load("dynamic_templates", "test", 30, lambda: "old")
    invalidate_procedure_caches()
    assert catalog_cache.get_or_load("dynamic_templates", "test", 30, lambda: "new") == "new"


def test_procedure_list_removes_total_from_every_item():
    @contextmanager
    def connection():
        yield object()

    rows = [{"id": "1", "_total": 2}, {"id": "2", "_total": 2}]
    with patch.object(repo, "db_connection", connection), patch.object(repo, "_rows", return_value=rows):
        items, total = repo.ProcedureRepository.get_procedures.__wrapped__()
    assert total == 2
    assert len(items) == 2
    assert all("_total" not in item for item in items)
