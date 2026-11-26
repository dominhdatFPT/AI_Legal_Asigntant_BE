import os

import pytest

from tests_3layer.support import L2_AMQP


def pytest_collection_modifyitems(items):
    if os.getenv("TEST_LAYER2_ENABLED") != "1":
        marker = pytest.mark.skip(reason="Start the disposable Docker test infrastructure with -Layer 2")
        for item in items:
            if "layer2_cross_service" in item.path.parts:
                item.add_marker(marker)


@pytest.fixture(autouse=True)
def real_broker_url(monkeypatch):
    monkeypatch.setenv("RABBITMQ_ENABLED", "true")
    monkeypatch.setenv("RABBITMQ_URL", L2_AMQP)
