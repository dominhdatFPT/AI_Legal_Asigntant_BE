"""Nạp file mẫu đơn vào bảng form_templates trên database Procedure (Supabase). Chạy một lần sau 006.

Đọc file ở hai thư mục "Template các thủ tục ..." (chỉ file nằm trực tiếp, bỏ thư mục con trùng lặp)
và bản PDF xem trước cùng tên trong database/form_templates_preview/. Chạy lại không bị nhân đôi:
mẫu đã có (cùng domain + tên file) được cập nhật nội dung.
"""
import sys
from pathlib import Path

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

from sqlalchemy import text, create_engine

SERVICE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE_ROOT))

from app.database import database_url  # noqa: E402

# Thư mục mẫu -> mã lĩnh vực (khớp procedure_domains.code).
TEMPLATE_FOLDERS = {
    "Template các thủ tục đất đai": "land",
    "Template các thủ tục kinh doanh": "business",
}
PREVIEW_ROOT = SERVICE_ROOT / "database" / "form_templates_preview"

MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}
PROCEDURE_PREFIX = "Thủ tục "

UPSERT = text("""
INSERT INTO public.form_templates
    (domain, name, procedure_name, file_name, format, media_type, file_size, file_data, preview_pdf, order_index)
VALUES (:domain, :name, :procedure_name, :file_name, :format, :media_type, :file_size, :file_data, :preview_pdf, :order_index)
ON CONFLICT (domain, file_name) DO UPDATE SET
    name = EXCLUDED.name, procedure_name = EXCLUDED.procedure_name, format = EXCLUDED.format,
    media_type = EXCLUDED.media_type, file_size = EXCLUDED.file_size, file_data = EXCLUDED.file_data,
    preview_pdf = EXCLUDED.preview_pdf, order_index = EXCLUDED.order_index, is_active = true, updated_at = now()
""")


def split_name(stem: str) -> tuple[str, str]:
    title = stem[len(PROCEDURE_PREFIX):] if stem.startswith(PROCEDURE_PREFIX) else stem
    title = title[:1].upper() + title[1:]
    return title, title.partition(" - ")[0].strip()


def collect_templates(root: Path = SERVICE_ROOT, preview_root: Path = PREVIEW_ROOT) -> list[dict]:
    rows = []
    for folder, domain in TEMPLATE_FOLDERS.items():
        directory = root / folder
        if not directory.is_dir():
            continue
        files = [p for p in sorted(directory.iterdir(), key=lambda p: p.name)
                 if p.is_file() and p.suffix.lower() in MEDIA_TYPES]
        for index, path in enumerate(files, start=1):
            suffix = path.suffix.lower()
            name, procedure_name = split_name(path.stem)
            preview = preview_root / folder / f"{path.stem}.pdf"
            data = preview.read_bytes() if (suffix == ".pdf" and preview.is_file()) else path.read_bytes()
            rows.append({
                "domain": domain,
                "name": name,
                "procedure_name": procedure_name,
                "file_name": path.name,
                "format": suffix[1:].upper(),
                "media_type": MEDIA_TYPES[suffix],
                "file_size": len(data),
                "file_data": data,
                "preview_pdf": preview.read_bytes() if suffix in {".doc", ".docx"} and preview.is_file() else None,
                "order_index": index,
            })
    return rows


def main() -> None:
    db_url = database_url()
    if not db_url:
        sys.exit("Thiếu PROCEDURE_DATABASE_URL trong .env, không biết nạp vào database nào.")
    engine = create_engine(
        db_url,
        connect_args={"prepare_threshold": None}
    )
    rows = collect_templates()
    if not rows:
        sys.exit(f"Không thấy file mẫu nào trong {SERVICE_ROOT}.")
    with engine.begin() as conn:
        for row in rows:
            conn.execute(UPSERT, row)
    previews = sum(r["preview_pdf"] is not None for r in rows)
    print(f"Đã nạp thành công {len(rows)} mẫu đơn ({previews} mẫu Word có bản PDF xem trước) vào form_templates.")


if __name__ == "__main__":
    main()
