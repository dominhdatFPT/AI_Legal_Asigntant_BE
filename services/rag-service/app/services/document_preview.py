"""Xem trước cách hệ thống cắt một văn bản (hàm thuần: không đụng DB, không gọi embedding).

Dùng lại `chunk_legal_text` và `embedding_text`, không viết lại thuật toán cắt.
"""

import math
import re
from itertools import groupby

from app.core.config import get_settings
from app.services.chunking import DEFAULT_MAX_CHARS, Chunk, chunk_legal_text, embedding_text, normalize_text

SAMPLE_CHUNKS = 3
SAMPLE_CHARS = 300
MAX_LISTED = 10  # số mục tối đa liệt kê trong một cảnh báo

_REPLACEMENT_CHAR = "�"


class EmptyContentError(ValueError):
    """Văn bản rỗng sau khi chuẩn hóa."""


def _level(chunk: Chunk) -> str:
    info = chunk.section_info
    if info.get("dieu") is None:
        return "doan_van"
    if info.get("diem") is not None:
        return "diem"
    if info.get("khoan") is not None:
        return "khoan"
    return "dieu"


def _article_number(dieu: str) -> int:
    """'10a' -> 10 (bỏ hậu tố chữ)."""
    return int(re.match(r"\d+", dieu).group())


def _more(n_total: int) -> str:
    return f" và {n_total - MAX_LISTED} chỗ khác" if n_total > MAX_LISTED else ""


def _missing_text(prev: int, cur: int) -> str:
    lo, hi = prev + 1, cur - 1
    missing = f"Điều {lo}" if lo == hi else f"Điều {lo} đến Điều {hi}"
    return f"thiếu {missing} (giữa Điều {prev} và Điều {cur})"


def analyze_document(text: str, title: str, batch_size: int | None = None) -> dict:
    """Cắt `text` như lúc nạp thật rồi thống kê. Ném EmptyContentError nếu text rỗng sau khi chuẩn hóa."""
    normalized = normalize_text(text)
    if not normalized:
        raise EmptyContentError("Nội dung văn bản rỗng.")
    chunks = chunk_legal_text(normalized)
    batch_size = batch_size or get_settings().embedding_batch_size

    levels = {"dieu": 0, "khoan": 0, "diem": 0, "doan_van": 0}
    for c in chunks:
        levels[_level(c)] += 1

    # Các Điều theo thứ tự xuất hiện; chunk liền nhau cùng Điều gộp thành một "đoạn" (run)
    runs = [(dieu, sum(1 for _ in grp)) for dieu, grp in groupby(c.section_info.get("dieu") for c in chunks)]
    runs = [(d, n) for d, n in runs if d is not None]
    distinct = list(dict.fromkeys(d.lower() for d, _ in runs))

    warnings: list[str] = []
    if not runs:
        warnings.append("Không tìm thấy dòng \"Điều N.\" nào, văn bản sẽ được cắt theo đoạn văn.")

    gaps = []
    for (prev_d, _), (cur_d, _) in zip(runs, runs[1:]):
        prev, cur = _article_number(prev_d), _article_number(cur_d)
        if cur > prev + 1:
            gaps.append(_missing_text(prev, cur))
    if gaps:
        warnings.append("Số Điều nhảy cóc: " + "; ".join(gaps[:MAX_LISTED]) + _more(len(gaps)) + ".")

    seen: set[str] = set()
    repeated_keys: set[str] = set()
    repeated: list[str] = []
    for d, _ in runs:
        key = d.lower()
        if key in seen and key not in repeated_keys:
            repeated_keys.add(key)
            repeated.append(d)
        seen.add(key)
    if repeated:
        listed = ", ".join(f"Điều {d}" for d in repeated[:MAX_LISTED])
        warnings.append(
            f"Có số Điều xuất hiện lặp ở nhiều vị trí không liền kề: {listed}{_more(len(repeated))}."
        )

    longest = max((len(c.content) for c in chunks), default=0)
    too_long = sum(1 for c in chunks if len(c.content) > DEFAULT_MAX_CHARS)
    if too_long:
        warnings.append(
            f"Có {too_long} đoạn dài hơn {DEFAULT_MAX_CHARS} ký tự (dài nhất {longest} ký tự)."
        )

    bad_chars = text.count(_REPLACEMENT_CHAR)
    if bad_chars:
        warnings.append(
            f"Văn bản có {bad_chars} ký tự lạ (U+FFFD), nghi lỗi mã hóa khi sao chép hoặc chuyển file."
        )

    embed_chars = sum(len(embedding_text(title, c)) for c in chunks)
    return {
        "total_chars": len(normalized),
        "chunk_count": len(chunks),
        "article_count": len(distinct),
        "chunks_by_level": levels,
        "first_article": runs[0][0] if runs else None,
        "last_article": runs[-1][0] if runs else None,
        "split_articles": [{"dieu": d, "chunk_count": n} for d, n in runs if n > 1],
        "warnings": warnings,
        "embedding_estimate": {
            "total_chars": embed_chars,
            "batch_size": batch_size,
            "batch_count": math.ceil(len(chunks) / batch_size),
        },
        "sample_chunks": [
            {"chunk_index": i, "section_info": c.section_info, "content": c.content[:SAMPLE_CHARS]}
            for i, c in enumerate(chunks[:SAMPLE_CHUNKS])
        ],
    }
