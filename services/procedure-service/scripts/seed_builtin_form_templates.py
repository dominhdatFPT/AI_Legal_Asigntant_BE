"""Seed built-in forms explicitly during deployment, in one transaction.

Service không còn tự seed lúc startup/đọc: DB mới (hoặc khi thêm mẫu vào BUILTIN_ROWS) phải chạy tay,
idempotent (bỏ qua id đã có). Đứng ở services/procedure-service, PROCEDURE_DATABASE_URL trỏ đúng DB:
    python -m scripts.seed_builtin_form_templates
Docker (image không chứa scripts/):
    docker compose run --rm -v "${PWD}/services/procedure-service:/app" procedure-service python -m scripts.seed_builtin_form_templates
"""
from app.builtin_form_templates import seed_builtin_form_templates
from app.repositories.procedure_repository import db_connection
from app.core.catalog_cache import catalog_cache


def main():
    with db_connection(write=True) as conn:
        seed_builtin_form_templates(conn)
    catalog_cache.invalidate("custom_templates")


if __name__ == "__main__":
    main()
