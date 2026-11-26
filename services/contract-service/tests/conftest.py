import pytest


@pytest.fixture(autouse=True)
def clear_procedure_registry_mode(monkeypatch):
    monkeypatch.delenv("PROCEDURE_SERVICE_URL", raising=False)
    monkeypatch.delenv("PROCEDURE_WRITE_MODE", raising=False)
