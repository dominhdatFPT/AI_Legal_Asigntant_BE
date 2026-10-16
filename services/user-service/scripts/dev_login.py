"""Cấp cặp token cho một user để test bằng Postman khi chưa có đăng nhập Firebase. Chỉ dùng ở dev.

    cd services/user-service
    python scripts/dev_login.py --email test@gmail.com
    python scripts/dev_login.py --email admin@gmail.com --role admin
"""

import argparse
import json
import sys
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(SERVICE_ROOT.parents[1] / ".env")

from app.core.database import get_db  # noqa: E402
from app.models import UserRole  # noqa: E402
from app.repositories import user_repository as users  # noqa: E402
from app.services.auth_service import issue_token_pair  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Cấp token để test (chỉ dùng ở dev)")
    parser.add_argument("--email", required=True)
    parser.add_argument("--role", choices=[r.value for r in UserRole])
    args = parser.parse_args()

    db = next(get_db())
    try:
        user = users.get_by_firebase_uid(db, f"dev:{args.email}")
        if user is None:
            user = users.create(db, firebase_uid=f"dev:{args.email}", email=args.email, auth_provider="dev")
        if args.role:
            user.role = UserRole(args.role)
        pair = issue_token_pair(db, user, user_agent="dev_login")
        db.commit()
    finally:
        db.close()
    print(json.dumps({"user_id": str(user.id), "role": user.role.value, **pair}, indent=2))


if __name__ == "__main__":
    main()
