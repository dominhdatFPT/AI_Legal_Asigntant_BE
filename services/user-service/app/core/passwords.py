import bcrypt

# Hash giả để vẫn tốn thời gian bcrypt khi username không tồn tại (tránh dò username qua thời gian phản hồi).
_DUMMY_HASH = bcrypt.hashpw(b"dummy-password", bcrypt.gensalt()).decode()


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str | None) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), (password_hash or _DUMMY_HASH).encode("utf-8")) and bool(
            password_hash
        )
    except ValueError:
        return False
