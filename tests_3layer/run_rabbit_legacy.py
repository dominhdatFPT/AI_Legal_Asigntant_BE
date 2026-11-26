"""Start one existing RabbitMQ check against the runner's loopback broker only."""

import os
import runpy
import sys

from pathlib import Path
from tests_3layer.layer2_cross_service.db_bootstrap import database_url
from tests_3layer.support import BE, L2_AMQP

root = BE / "tests_rabbitmq_reality_check"
script = (root / sys.argv[1]).resolve()
if script.parent != root or not script.name.startswith("test_") or script.suffix != ".py":
    raise SystemExit("Expected an existing RabbitMQ reality-check test")

(Path.cwd() / "data").mkdir(parents=True, exist_ok=True)
os.environ["RABBITMQ_URL"] = L2_AMQP
os.environ["NOTIFICATION_DATABASE_URL"] = database_url("notification_db")
sys.path.insert(0, str(root))
import config

config.DIRECT_IPV4_URL = L2_AMQP
config.ENV_CONFIGURED_URL = L2_AMQP
runpy.run_path(str(script), run_name="__main__")
