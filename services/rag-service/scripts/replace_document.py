"""Thay toàn văn + toàn bộ chunk của MỘT văn bản đã có trong database bằng file .txt mới.

Giữ nguyên title, doc_type, doc_number, ngày tháng, status; chỉ đổi content, chunk và updated_at.

Cách chạy (ở thư mục gốc legal-assistant-ai, dùng .venv, .env có RAG_DATABASE_URL + GEMINI_API_KEY):

    # 1. Dry-run (mặc định): chỉ ĐỌC database, không ghi, không gọi Gemini
    .venv\\Scripts\\python services/rag-service/scripts/replace_document.py 31/2024/QH15 D:\\AI_03\\BE\\data\\luat_dat_dai_2024.txt

    # 2. Thay thật (gọi Gemini tạo embedding cho TẤT CẢ chunk rồi mới ghi, bắt buộc --backup)
    .venv\\Scripts\\python services/rag-service/scripts/replace_document.py 31/2024/QH15 D:\\AI_03\\BE\\data\\luat_dat_dai_2024.txt ^
        --apply --backup D:\\AI_03\\BE\\data\\backup_31-2024-QH15.txt

Lỗi giữa chừng (hết quota Gemini...) thì database giữ nguyên như cũ; chạy lại đúng lệnh là được.
Gặp lỗi `statement timeout` khi ghi thì đặt DB_COMMAND_TIMEOUT_SECONDS=0 cho lần chạy đó (README-RAG mục 6).
"""

import argparse
import difflib
import logging
import math
import sys
from pathlib import Path

from dotenv import load_dotenv

SERVICE_DIR = Path(__file__).resolve().parents[1]  # services/rag-service
ROOT_DIR = SERVICE_DIR.parents[1]                  # legal-assistant-ai
sys.path.insert(0, str(SERVICE_DIR))
load_dotenv(ROOT_DIR / ".env")

MAX_DIFF_LINES = 20
DIFF_LINE_WIDTH = 120
# Điều dùng để người chạy nhìn xác nhận sau khi ghi, theo số hiệu văn bản
CHECK_DIEU = {"31/2024/QH15": "252", "29/2023/QH15": "82"}


def _short(line: str) -> str:
    line = line.strip()
    return line if len(line) <= DIFF_LINE_WIDTH else line[: DIFF_LINE_WIDTH - 3] + "..."


def _dieu_per_line(lines: list[str]) -> list[str | None]:
    """Điều chứa từng dòng, theo cùng quy tắc của chunking (tên Chương/Mục không thuộc Điều nào)."""
    from app.services.chunking import _CHUONG, _DIEU, _MUC

    out: list[str | None] = []
    current: str | None = None
    for line in lines:
        if _CHUONG.match(line) or _MUC.match(line):
            current = None
        else:
            m = _DIEU.match(line)
            if m:
                current = m.group(1)
        out.append(current)
    return out


def print_diff(old_content: str, new_content: str) -> None:
    from app.services.chunking import normalize_text

    old = normalize_text(old_content).split("\n")
    new = normalize_text(new_content).split("\n")
    old_dieu, new_dieu = _dieu_per_line(old), _dieu_per_line(new)

    rows: list[str] = []
    n_old = n_new = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        for i in range(i1, i2):
            n_old += 1
            rows.append(f"- [{'Điều ' + old_dieu[i] if old_dieu[i] else 'ngoài Điều'}] {_short(old[i])}")
        for j in range(j1, j2):
            n_new += 1
            rows.append(f"+ [{'Điều ' + new_dieu[j] if new_dieu[j] else 'ngoài Điều'}] {_short(new[j])}")

    print(f"\nKhác nhau (sau khi chuẩn hoá chữ): {n_old} dòng cũ bị bỏ/đổi, {n_new} dòng mới")
    if not rows:
        print("  Hai bản giống hệt nhau.")
        return
    for row in rows[:MAX_DIFF_LINES]:
        print("  " + row)
    if len(rows) > MAX_DIFF_LINES:
        print(f"  ... còn {len(rows) - MAX_DIFF_LINES} dòng khác nữa (chỉ in {MAX_DIFF_LINES})")


def main() -> int:
    p = argparse.ArgumentParser(description="Thay toàn văn và toàn bộ chunk của một văn bản trong rag-service")
    p.add_argument("doc_number", help="Số hiệu văn bản, VD 31/2024/QH15")
    p.add_argument("file", type=Path, help="File .txt UTF-8 chứa toàn văn bản mới")
    p.add_argument("--apply", action="store_true", help="Ghi thật (không có thì chỉ dry-run)")
    p.add_argument("--backup", type=Path, help="File lưu toàn văn cũ trước khi ghi (bắt buộc khi --apply)")
    p.add_argument("--max-chars", type=int, default=2500)
    args = p.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if args.apply and not args.backup:
        print("❌ --apply bắt buộc có --backup <file> để lưu toàn văn cũ. Không làm gì cả.")
        return 1
    if not args.file.is_file():
        print(f"❌ Không tìm thấy file {args.file}")
        return 1

    from sqlalchemy import func, select

    from app.core.config import get_settings
    from app.core.database import get_session
    from app.models import DocumentChunk
    from app.repositories import document as document_repo
    from app.services.chunking import chunk_legal_text, embedding_text
    from app.services.embedding import EmbeddingError, GeminiEmbedder, get_embedder
    from app.services.ingest import EmptyDocumentError

    new_content = args.file.read_text(encoding="utf-8")  # đúng cách ingest_batch.py đọc file, lưu nguyên văn

    db = get_session()
    try:
        doc = document_repo.get_by_doc_number(db, args.doc_number)
        if doc is None:
            print(f"❌ Không có văn bản số {args.doc_number} trong database")
            return 1
        doc_id, title, old_content = doc.id, doc.title, doc.content
        old_chunks = db.scalar(select(func.count()).select_from(DocumentChunk).where(DocumentChunk.document_id == doc_id))
        db.rollback()  # đóng giao dịch đọc, không để kết nối "idle in transaction" suốt lúc chờ embedding

        chunks = chunk_legal_text(new_content, max_chars=args.max_chars)
        if not chunks:
            print(f"❌ {EmptyDocumentError('Văn bản rỗng, không cắt được đoạn nào')}")
            return 1
        so_dieu = len({c.section_info.get("dieu") for c in chunks if c.section_info.get("dieu")})
        texts = [embedding_text(title, c) for c in chunks]
        settings = get_settings()
        calls = math.ceil(len(chunks) / settings.embedding_batch_size)
        tokens = GeminiEmbedder.estimate_tokens(texts)

        print(f"Văn bản: {title} ({args.doc_number})")
        print(f"Độ dài content: cũ {len(old_content):,} ký tự → mới {len(new_content):,} ký tự")
        print(f"Số chunk: hiện có trong DB {old_chunks} → sau khi cắt bản mới {len(chunks)}")
        print(f"Tổng số Điều tìm thấy trong bản mới: {so_dieu}")
        if any(not c.section_info.get("dieu") for c in chunks):
            print("⚠ Có đoạn cắt theo 'đoạn văn' (không thuộc Điều nào): kiểm tra file, có thể các dòng 'Điều ...' bị dính nhau")
        print_diff(old_content, new_content)
        print(
            f"\nSố lần gọi embedding sẽ cần: {calls} (mỗi lần tối đa {settings.embedding_batch_size} đoạn, "
            f"{len(chunks)} đoạn, ước ~{tokens:,} token)"
        )

        if not args.apply:
            print("\nDry-run: chưa ghi gì, chưa gọi Gemini. Thêm --apply --backup <file> để thay thật.")
            return 0

        # 1. Lưu toàn văn cũ trước khi ghi. Không ghi đè backup có sẵn khác nội dung (tránh mất bản gốc khi chạy lại)
        backup = args.backup
        old_bytes = old_content.encode("utf-8")
        if backup.exists() and backup.read_bytes() != old_bytes:
            print(f"❌ Backup {backup} đã có và khác toàn văn cũ trong DB; đổi tên file --backup. Không ghi gì.")
            return 1
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_bytes(old_bytes)
        if backup.read_bytes() != old_bytes:
            print(f"❌ Ghi backup {backup} không khớp. Không ghi gì vào database.")
            return 1
        print(f"\nĐã lưu toàn văn cũ vào {backup} ({len(old_bytes):,} byte)")

        # 2. Embedding TẤT CẢ chunk trước (gọi Gemini thật); chưa chạm database
        print(f"Bắt đầu tạo embedding cho {len(chunks)} đoạn...")
        embedder = get_embedder()
        try:
            vectors = embedder.embed_documents(texts)
        except EmbeddingError as e:
            print(f"❌ Tạo embedding thất bại: {e}\n   Database CHƯA bị thay đổi, văn bản vẫn như cũ. Chạy lại sau (nếu hết quota thì chờ).")
            return 1

        # 3. Ghi trong MỘT transaction: content + updated_at + toàn bộ chunk
        try:
            fresh = document_repo.get_document(db, doc_id)
            if fresh is None or fresh.content != old_content:
                db.rollback()
                print("❌ Văn bản trong DB đã bị đổi trong lúc tạo embedding. Không ghi gì; chạy lại từ đầu.")
                return 1
            fresh.content = new_content
            document_repo.replace_chunks(  # xoá chunk cũ, thêm chunk mới, đặt chunk_count/is_processed/updated_at
                db,
                fresh,
                [{"content": c.content, "section_info": c.section_info, "embedding": v} for c, v in zip(chunks, vectors)],
                embedding_model=embedder.model_name,
            )
            db.commit()
        except Exception as e:
            db.rollback()
            print(f"❌ Ghi database lỗi, đã rollback, văn bản vẫn như cũ: {type(e).__name__}: {e}")
            return 1
    finally:
        db.close()

    # 4. Đọc lại để người chạy nhìn xác nhận
    db = get_session()
    try:
        n = db.scalar(select(func.count()).select_from(DocumentChunk).where(DocumentChunk.document_id == doc_id))
        print(f"\n✅ Đã thay xong. Số chunk trong DB sau khi ghi: {n} (dự kiến {len(chunks)})")
        dieu = CHECK_DIEU.get(args.doc_number)
        if dieu is None:
            print("(Không có Điều mẫu để in cho văn bản này.)")
            return 0
        from sqlalchemy.orm import defer

        rows = db.scalars(
            select(DocumentChunk)
            .where(DocumentChunk.document_id == doc_id)
            .order_by(DocumentChunk.chunk_index)
            .options(defer(DocumentChunk.embedding))
        ).all()
        hits = [r for r in rows if (r.section_info or {}).get("dieu") == dieu]
        if not hits:
            print(f"⚠ Không thấy chunk nào thuộc Điều {dieu}")
        else:
            print(f"Điều {dieu}: {len(hits)} chunk. 150 ký tự đầu của chunk đầu tiên:")
            print(hits[0].content[:150])
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
