"""Tạo access token để test API khi chưa có đăng nhập Google. Chỉ dùng ở môi trường dev.

    python scripts/mint_dev_token.py --role lawyer
    python scripts/mint_dev_token.py --sub 5b1e2c3d --role admin --minutes 120
"""

import argparse
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
from common_auth import ALGORITHM, ISSUER

ROOT = Path(__file__).resolve().parents[1]


def read_env_file(path: Path) -> dict:
    values = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def load_private_key(key_file: str | None) -> str:
    path = key_file or os.getenv("JWT_PRIVATE_KEY_PATH") or read_env_file(ROOT / ".env").get("JWT_PRIVATE_KEY_PATH")
    if not path:
        raise SystemExit("Không tìm thấy private key: truyền --key-file hoặc đặt JWT_PRIVATE_KEY_PATH trong .env")
    return Path(path).read_text(encoding="utf-8")


def mint(sub: str, role: str, minutes: int, private_key: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": sub,
        "role": role,
        "type": "access",
        "iss": ISSUER,
        "iat": now,
        "exp": now + timedelta(minutes=minutes),
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, private_key, algorithm=ALGORITHM)


def main() -> None:
    parser = argparse.ArgumentParser(description="Tạo access token để test API (chỉ dùng ở dev)")
    parser.add_argument("--sub", default="00000000-0000-0000-0000-000000000001", help="user id")
    parser.add_argument("--role", default="citizen", choices=["citizen", "lawyer", "admin"])
    parser.add_argument("--minutes", type=int, default=60, help="thời gian sống của token")
    parser.add_argument("--key-file", help="đường dẫn private key, mặc định lấy JWT_PRIVATE_KEY_PATH")
    args = parser.parse_args()
    print(mint(args.sub, args.role, args.minutes, load_private_key(args.key_file)))


if __name__ == "__main__":
    main()
