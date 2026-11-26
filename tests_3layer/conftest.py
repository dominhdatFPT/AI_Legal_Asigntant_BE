import os

import pytest


@pytest.fixture(autouse=True)
def isolate_service_settings(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PROCEDURE_SKIP_DOTENV", "1")
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    if os.getenv("TEST_LAYER2_ENABLED") != "1":
        monkeypatch.setenv("RABBITMQ_ENABLED", "false")
