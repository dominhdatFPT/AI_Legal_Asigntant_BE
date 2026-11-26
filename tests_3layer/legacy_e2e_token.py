"""Mint a test-stack token for the existing Docker HTTP pytest file."""

import os
from pathlib import Path

from tests_3layer.layer2_cross_service.db_bootstrap import E2E_PORT
from tests_3layer.layer2_cross_service.live import seed_user
from tests_3layer.support import auth_headers

if __name__ == "__main__":
    user_id = seed_user(E2E_PORT)
    headers = auth_headers(Path(os.environ["LEGAL_TEST_KEYS_DIR"]), user_id, "citizen")
    print(headers["Authorization"].removeprefix("Bearer "))
