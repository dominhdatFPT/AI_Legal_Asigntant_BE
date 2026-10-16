"""Nạp nhiều văn bản theo một file danh sách (CSV).

- Văn bản đã có trong database (trùng số hiệu) thì bỏ qua → chạy lại bao nhiêu lần cũng được.
- Hết quota Gemini (vẫn 429 sau khi đã thử lại) thì dừng gọn; lần sau chạy lại sẽ làm tiếp phần còn thiếu.
- Văn bản lỗi vì lý do khác thì ghi lại rồi chuyển sang văn bản tiếp theo.
"""

import csv
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable

from sqlalchemy.orm import Session

from app.repositories import document as document_repo
from app.services.chunking import chunk_legal_text
from app.services.embedding import Embedder, EmbeddingError
from app.services.ingest import DocumentExistsError, EmptyDocumentError, ingest_document

DOC_TYPES = {"luat", "nghi_dinh", "thong_tu", "quyet_dinh", "khac"}
STATUSES = {"active", "superseded", "draft"}
REQUIRED_COLUMNS = ["file", "title", "doc_type", "doc_number"]


class ManifestError(Exception):
    pass


@dataclass
class ManifestRow:
    line: int
    file: Path
    title: str
    doc_type: str
    doc_number: str
    issuing_body: str | None = None
    issued_date: date | None = None
    effective_date: date | None = None
    status: str = "active"
    source_url: str | None = None


@dataclass
class BatchResult:
    done: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    stopped_for_quota: bool = False


def _opt(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


def _date(value: str | None, line: int, column: str) -> date | None:
    value = _opt(value)
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as e:
        raise ManifestError(f"Dòng {line}: cột {column} phải dạng YYYY-MM-DD, đang là '{value}'") from e


def load_manifest(path: Path) -> list[ManifestRow]:
    """Đọc file CSV (UTF-8, có dòng tiêu đề). Đường dẫn file văn bản tính theo thư mục chứa CSV.
    Dòng có cột file bắt đầu bằng # thì bỏ qua (dùng để tạm tắt một văn bản)."""
    path = Path(path)
    if not path.is_file():
        raise ManifestError(f"Không tìm thấy file danh sách: {path}")
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        missing = [c for c in REQUIRED_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise ManifestError(f"File danh sách thiếu cột: {', '.join(missing)}")
        rows: list[ManifestRow] = []
        seen: set[str] = set()
        for line, raw in enumerate(reader, start=2):
            file_value = (raw.get("file") or "").strip()
            if not file_value or file_value.startswith("#"):
                continue
            title = (raw.get("title") or "").strip()
            doc_type = (raw.get("doc_type") or "").strip()
            doc_number = (raw.get("doc_number") or "").strip()
            status = _opt(raw.get("status")) or "active"
            if not title or not doc_number:
                raise ManifestError(f"Dòng {line}: thiếu title hoặc doc_number")
            if doc_type not in DOC_TYPES:
                raise ManifestError(f"Dòng {line}: doc_type phải là một trong {sorted(DOC_TYPES)}")
            if status not in STATUSES:
                raise ManifestError(f"Dòng {line}: status phải là một trong {sorted(STATUSES)}")
            if doc_number in seen:
                raise ManifestError(f"Dòng {line}: số hiệu {doc_number} bị trùng trong danh sách")
            seen.add(doc_number)
            file_path = Path(file_value)
            if not file_path.is_absolute():
                file_path = path.parent / file_path
            rows.append(
                ManifestRow(
                    line=line,
                    file=file_path,
                    title=title,
                    doc_type=doc_type,
                    doc_number=doc_number,
                    issuing_body=_opt(raw.get("issuing_body")),
                    issued_date=_date(raw.get("issued_date"), line, "issued_date"),
                    effective_date=_date(raw.get("effective_date"), line, "effective_date"),
                    status=status,
                    source_url=_opt(raw.get("source_url")),
                )
            )
    return rows


def _is_quota_error(e: Exception) -> bool:
    text = str(e)
    return "429" in text or "RESOURCE_EXHAUSTED" in text or "quota" in text.lower()


def describe_chunks(content: str, max_chars: int) -> str:
    chunks = chunk_legal_text(content, max_chars=max_chars)
    levels = Counter(
        "điểm" if c.section_info.get("diem") else
        "khoản" if c.section_info.get("khoan") else
        "điều" if c.section_info.get("dieu") else "đoạn văn"
        for c in chunks
    )
    so_dieu = len({c.section_info.get("dieu") for c in chunks if c.section_info.get("dieu")})
    return f"{len(chunks)} đoạn, {so_dieu} Điều, cắt theo {dict(levels)}"


def run_batch(
    db: Session,
    embedder: Embedder | None,
    rows: list[ManifestRow],
    *,
    dry_run: bool = False,
    max_chars: int = 2500,
    log: Callable[[str], None] = print,
) -> BatchResult:
    """Nạp lần lượt từng văn bản. dry_run=True: chỉ đọc file + cắt đoạn, không gọi Gemini, không ghi DB."""
    result = BatchResult()
    for i, row in enumerate(rows, start=1):
        head = f"[{i}/{len(rows)}] {row.title} ({row.doc_number})"
        if not row.file.is_file():
            result.failed.append((row.doc_number, f"không tìm thấy file {row.file}"))
            log(f"{head}: ❌ không tìm thấy file {row.file}")
            continue
        content = row.file.read_text(encoding="utf-8")
        info = describe_chunks(content, max_chars)

        if not dry_run and document_repo.get_by_doc_number(db, row.doc_number):
            result.skipped.append(row.doc_number)
            log(f"{head}: ⏭ đã có trong database, bỏ qua")
            continue
        if dry_run:
            log(f"{head}: {info}")
            if "đoạn văn" in info:
                log("   ⚠ Có đoạn cắt theo 'đoạn văn': kiểm tra lại file, có thể các dòng 'Điều ...' bị dính nhau")
            continue

        log(f"{head}: bắt đầu nạp — {info}")
        started = time.monotonic()
        try:
            doc = ingest_document(
                db,
                embedder,
                title=row.title,
                doc_type=row.doc_type,
                content=content,
                doc_number=row.doc_number,
                issuing_body=row.issuing_body,
                issued_date=row.issued_date,
                effective_date=row.effective_date,
                status=row.status,
                source_url=row.source_url,
                max_chars=max_chars,
            )
        except DocumentExistsError:
            result.skipped.append(row.doc_number)
            log(f"{head}: ⏭ đã có trong database, bỏ qua")
            continue
        except EmbeddingError as e:
            if _is_quota_error(e):
                result.stopped_for_quota = True
                log(f"{head}: ⛔ Gemini báo hết quota ({e}). Dừng lại — các văn bản đã nạp vẫn giữ nguyên, "
                    "chạy lại lệnh sau ít phút (hoặc sau 14:00 nếu hết quota ngày) để nạp tiếp.")
                break
            result.failed.append((row.doc_number, str(e)))
            log(f"{head}: ❌ lỗi embedding: {e}")
            continue
        except EmptyDocumentError as e:
            result.failed.append((row.doc_number, str(e)))
            log(f"{head}: ❌ {e}")
            continue
        result.done.append(row.doc_number)
        log(f"{head}: ✅ đã nạp {doc.chunk_count} đoạn trong {time.monotonic() - started:.0f}s")
    return result
