"""Tra cứu nguyên văn 1 Điều (hoặc 1 Khoản) theo tên hoặc số hiệu văn bản, ví dụ "Điều 45 Luật Đất đai".

Không dùng embedding, không gọi Gemini: tìm văn bản theo tên/số hiệu trong legal_documents, rồi cắt đúng Điều/Khoản
từ toàn văn gốc (cột content) bằng chính bộ tách Điều/Khoản của chunking.py. Nội dung trả về đúng từng chữ như
văn bản đã nạp (không lặp dòng tiêu đề như các chunk dùng để tìm kiếm).
"""
import re
import unicodedata

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import LegalDocument
from app.services.chunking import _KHOAN, _group_by, _split_articles, normalize_text

_CACHE_SIZE = 16
# (id văn bản, updated_at) -> {số điều: _Article}. Văn bản nạp lại (updated_at đổi) thì tự cắt lại.
# shortcut: cache riêng từng tiến trình, mất khi restart; đủ dùng vì kho chỉ vài chục văn bản
_articles_cache: dict[tuple[str, str], dict] = {}


class ArticleLookupError(Exception):
    def __init__(self, code: str, message: str, status: int = 404):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def _plain(text: str | None) -> str:
    """Chữ thường, bỏ dấu, mọi ký tự không phải chữ/số thành 1 dấu cách: "31/2024/QH15" -> "31 2024 qh15"."""
    text = unicodedata.normalize("NFD", (text or "").lower().replace("đ", "d"))
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return " ".join(re.findall(r"\w+", text))


def _hits(docs: list, q: str) -> list:
    return [
        d for d in docs
        if f" {q} " in f" {_plain(d.title)} "
        or (d.doc_number and (q == _plain(d.doc_number) or f" {_plain(d.doc_number)} " in f" {q} "))
    ]


def _find_document(db: Session, law: str):
    """Trả về (văn bản, ghi chú). Không có năm khớp thì thử bỏ năm: "luật đầu tư 2020" -> kho chỉ có bản 2025."""
    docs = db.execute(
        select(LegalDocument.id, LegalDocument.title, LegalDocument.doc_number, LegalDocument.doc_type,
               LegalDocument.status, LegalDocument.updated_at)
    ).all()
    q = re.sub(r"\bnam (\d{4})\b", r"\1", _plain(law))
    note = None
    found = _hits(docs, q)
    year = re.search(r"\s(\d{4})$", q)
    if not found and year:
        found = _hits(docs, q[: year.start()])
        if found:
            note = f"Kho không có bản năm {year.group(1)}, đang hiển thị {found[0].title}." if len(found) == 1 else None
    if len(found) > 1:  # "luật đất đai" khớp nhiều bản: ưu tiên bản còn hiệu lực
        active = [d for d in found if d.status == "active"]
        found = active if len(active) == 1 else found
    if not found:
        available = ", ".join(sorted(d.title for d in docs if d.status == "active")) or "chưa có văn bản nào"
        raise ArticleLookupError("DOCUMENT_NOT_FOUND", f"Kho văn bản chưa có \"{law.strip()}\". Hiện kho có: {available}.")
    if len(found) > 1:
        raise ArticleLookupError(
            "DOCUMENT_AMBIGUOUS",
            f"\"{law.strip()}\" khớp nhiều văn bản: {', '.join(sorted(d.title for d in found))}. "
            "Vui lòng ghi rõ năm hoặc số hiệu.",
            409,
        )
    return found[0], note


def _articles_of(db: Session, doc) -> dict:
    key = (str(doc.id), str(doc.updated_at))
    if key not in _articles_cache:
        content = db.scalar(select(LegalDocument.content).where(LegalDocument.id == doc.id)) or ""
        articles: dict = {}
        for article in _split_articles(normalize_text(content).split("\n")):
            articles.setdefault(article.dieu.lower(), article)  # trùng số (trích dẫn luật khác) thì giữ Điều đầu tiên
        if len(_articles_cache) >= _CACHE_SIZE:
            _articles_cache.clear()
        _articles_cache[key] = articles
    return _articles_cache[key]


def _lines(lines: list[str]) -> list[str]:
    return [line.strip() for line in lines if line.strip()]


def find_article(db: Session, law: str, dieu: str, khoan: str | None = None) -> dict:
    """Nguyên văn Điều `dieu` (hoặc Khoản `khoan` của Điều đó) trong văn bản khớp `law`.
    Lỗi (ArticleLookupError): DOCUMENT_NOT_FOUND, DOCUMENT_AMBIGUOUS, ARTICLE_NOT_FOUND, KHOAN_NOT_FOUND."""
    doc, note = _find_document(db, law)
    articles = _articles_of(db, doc)
    article = articles.get(dieu.lower())
    if article is None:
        raise ArticleLookupError(
            "ARTICLE_NOT_FOUND", f"{doc.title} không có Điều {dieu} (văn bản này có {len(articles)} Điều).", 404,
        )
    lines = [article.heading_line, *_lines(article.body)]
    if khoan is not None:
        _, khoans = _group_by(article.body, _KHOAN)
        match = next((group for number, group in khoans if number == khoan), None)
        if match is None:
            reason = f"chỉ có {len(khoans)} khoản" if khoans else "không chia khoản"
            raise ArticleLookupError(
                "KHOAN_NOT_FOUND", f"Điều {article.dieu} {doc.title} không có khoản {khoan} (Điều này {reason}).", 404,
            )
        lines = [article.heading_line, *_lines(match)]
    return {
        "document": {
            "id": str(doc.id), "title": doc.title, "doc_number": doc.doc_number,
            "doc_type": doc.doc_type, "status": doc.status,
        },
        "chuong": article.chuong,
        "dieu": article.dieu,
        "khoan": khoan,
        "content": "\n".join(lines),
        "note": note,
    }
