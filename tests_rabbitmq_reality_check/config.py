"""Configuration and helpers for RabbitMQ reality verification tests.
DO NOT MODIFY ANY EXISTING CODE IN THE PROJECT.
"""
import importlib.util
import os
import sys
from pathlib import Path

BASE_DIR = Path("D:/Legal Assistant AI/BE")
LIBS_DIR = BASE_DIR / "libs" / "common_auth"

if str(LIBS_DIR) not in sys.path:
    sys.path.insert(0, str(LIBS_DIR))

# 1. URL from BE/.env as configured in the repository
_RMQ_AUTH = f"{os.getenv('RABBITMQ_USER', 'guest')}:{os.getenv('RABBITMQ_PASSWORD', 'guest')}"
ENV_CONFIGURED_URL = f"amqp://{_RMQ_AUTH}@localhost:5672/"

# 2. Direct IPv4 URL (used to test actual AMQP broker performance when IPv4 is specified)
DIRECT_IPV4_URL = f"amqp://{_RMQ_AUTH}@127.0.0.1:5672/"

# By default, use DIRECT_IPV4_URL for deep testing, but test suite will explicitly verify both!
RABBITMQ_URL = os.getenv("RABBITMQ_URL", DIRECT_IPV4_URL)
os.environ["RABBITMQ_URL"] = RABBITMQ_URL
os.environ["RABBITMQ_ENABLED"] = "true"

EXCHANGE = "legal.events.topic"
DEAD_LETTER_EXCHANGE = "legal.events.dlx"
DEAD_LETTER_QUEUE = "q.dead_letter"


def load_service_module(service_name: str, relative_module_path: str):
    """Load a module from a specific microservice without namespace collision."""
    svc_dir = BASE_DIR / "services" / service_name
    file_path = svc_dir / (relative_module_path.replace(".", "/") + ".py")
    mod_name = f"{service_name.replace('-', '_')}_{relative_module_path.replace('.', '_')}"

    orig_path = list(sys.path)
    # Temporarily remove any other service dirs and purge 'app' from sys.modules
    to_restore = {}
    for k in list(sys.modules.keys()):
        if k == "app" or k.startswith("app."):
            to_restore[k] = sys.modules.pop(k)

    if str(svc_dir) not in sys.path:
        sys.path.insert(0, str(svc_dir))
    try:
        spec = importlib.util.spec_from_file_location(mod_name, file_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load spec from {file_path}")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = mod
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path = orig_path
        # restore old app modules if needed
        for k, v in to_restore.items():
            if k not in sys.modules:
                sys.modules[k] = v
