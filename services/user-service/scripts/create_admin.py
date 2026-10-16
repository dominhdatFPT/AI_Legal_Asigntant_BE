"""Tạo mới hoặc đặt lại mật khẩu cho tài khoản đăng nhập bằng username/mật khẩu (mặc định role admin).

    cd services/user-service
    python scripts/create_admin.py --username admin2026
    python scripts/create_admin.py --username canbo01 --email canbo01@ai-legal.local --role citizen

Mật khẩu được hỏi khi chạy (không hiện trên màn hình) và chỉ lưu dạng bcrypt hash vào database.
"""

import argparse
import getpass
import sys
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(SERVICE_ROOT.parents[1] / ".env")

from app.core.database import get_db  # noqa: E402
from app.core.passwords import hash_password  # noqa: E402
from app.models import User, UserRole, UserStatus  # noqa: E402
from app.repositories import user_repository as users  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Tạo / đặt lại mật khẩu tài khoản đăng nhập bằng username")
    parser.add_argument("--username", required=True)
    parser.add_argument("--email", help="Mặc định: <username>@ai-legal.local")
    parser.add_argument("--full-name", default="Quản trị viên hệ thống")
    parser.add_argument("--role", choices=[r.value for r in UserRole], default=UserRole.admin.value)
    args = parser.parse_args()

    password = getpass.getpass("Mật khẩu: ")
    if not password or password != getpass.getpass("Nhập lại mật khẩu: "):
        sys.exit("Mật khẩu trống hoặc không khớp.")

    db = next(get_db())
    try:
        user = users.get_by_username(db, args.username)
        if user is None:
            user = User(
                email=args.email or f"{args.username.lower()}@ai-legal.local",
                username=args.username,
                full_name=args.full_name,
                auth_provider="local",
            )
            db.add(user)
            action = "Đã tạo"
        else:
            action = "Đã cập nhật"
        user.password_hash = hash_password(password)
        user.role = UserRole(args.role)
        user.status = UserStatus.active
        db.commit()
        print(f"{action} tài khoản {user.username} (role={user.role.value}, id={user.id})")
    finally:
        db.close()


if __name__ == "__main__":
    main()
