"""Tra cứu có cấu trúc theo (văn bản, Điều, Khoản, Điểm, tên Điều) và dẫn chiếu chéo giữa các điều luật.

Không dùng embedding, không gọi mạng: chỉ đọc kho qua document_repo.retrieval_rows (tham số hoá, MỘT truy vấn cho cả lô).
- parse_query_refs: "khoản 2 Điều 328 BLDS", "K.3 Đ.6 NQ 03/2026", "Điều 121 Luật Đất đai" + exact_refs do agent gửi.
- extract_citations: "khoản 2 Điều 328 của Bộ luật Dân sự", "Điều 357" (cùng văn bản) trong nội dung chunk.
- lookup: lô RefSpec -> các dòng (chunk, văn bản, 1.0) đã được kiểm chứng khớp đúng cặp khoá.
"""

import re
import unicodedata
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.repositories import document as document_repo
from app.services import chunking

MAX_SPECS = 12  # số tham chiếu tối đa tra cứu cho một lần search
MAX_PER_REF = 3  # số chunk tối đa trả về cho mỗi tham chiếu (Điều dài bị tách theo Khoản)
FETCH_LIMIT = 400  # trần số dòng đọc từ DB cho một lô


@dataclass(frozen=True)
class RefSpec:
    doc_number: str | None
    dieu: str | None = None
    khoan: str | None = None
    diem: str | None = None
    title: str | None = None


# (mẫu, số hiệu văn bản). Mẫu dài/cụ thể đứng trước. Số hiệu phải đúng dạng lưu trong legal_documents.doc_number.
_ALIASES: list[tuple[str, str]] = [
    (r"luật đất đai\s*(?:năm\s*)?2013", "45/2013/QH13"),
    (r"luật đất đai(?:\s*(?:năm\s*)?2024)?|lđđ|ldd", "31/2024/QH15"),
    (r"bộ luật tố tụng dân sự(?:\s*(?:năm\s*)?2015)?|bltds", "92/2015/QH13"),
    (r"bộ luật dân sự(?:\s*(?:năm\s*)?2015)?|blds", "91/2015/QH13"),
    (r"(?:nghị quyết|nq)\s*(?:số\s*)?03(?:/2026(?:/nq-hđtp)?)?(?![\d/])", "03/2026/NQ-HĐTP"),
    (r"(?:nghị định|nđ)(?:-cp)?\s*(?:số\s*)?21(?:/2021(?:/nđ-cp)?)?(?![\d/])", "21/2021/NĐ-CP"),
    (r"luật thương mại|ltm", "36/2005/QH11"),
    (r"luật doanh nghiệp|ldn", "59/2020/QH14"),
    (r"luật hôn nhân và gia đình|lhnđ|lhngđ|lhnvgđ", "52/2014/QH13"),
    (r"bộ luật lao động|bllđ", "45/2019/QH14"),
    (r"bộ luật hình sự|blhs", "100/2015/QH13"),
    (r"luật nhà ở", "27/2023/QH15"),
    (r"luật kinh doanh bất động sản|lkdbđs", "29/2023/QH15"),
]
_ALIAS_RES = [(re.compile(rf"(?<!\w)(?:{p})(?!\w)", re.I), num) for p, num in _ALIASES]
_NUMBER = re.compile(r"(?<![\w/])\d{1,4}/\d{4}/[\wĐđ-]+", re.I)

# [điểm x] [khoản k] Điều n — cũng nhận dạng viết tắt "K.3 Đ.6"
_ART = re.compile(
    r"(?:(?<!\w)điểm\s+([a-zđ])(?!\w)[\s,]*(?:của\s+)?)?"
    r"(?:(?<!\w)(?:khoản|k\.)\s*(\d+)(?!\d)[\s,]*(?:của\s+)?)?"
    r"(?<!\w)(?:điều|đ\.)\s*(\d+[a-zđ]?)(?!\w)",
    re.I,
)
_BREAK = re.compile(r"[;\n?!]|\.\s")  # ngắt câu: tên văn bản bên kia không thuộc về Điều này
_OF = re.compile(r"[\s,]*(?:của|trong)\s+(\S.*)", re.I | re.S)
_SELF = re.compile(r"(?:luật|bộ luật|nghị định|nghị quyết|thông tư|pháp lệnh)\s+này", re.I)
_LAW_WORD = re.compile(r"(?:luật|bộ luật|nghị định|nghị quyết|thông tư|pháp lệnh|quyết định)\b", re.I)
_FILLER = {"của", "theo", "tại", "quy", "định", "và", "trong", "điểm", "khoản", "điều", "k", "đ", "xem"}


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def find_docs(text: str) -> list[tuple[int, int, str]]:
    """Mọi tên/viết tắt/số hiệu văn bản trong text: [(bắt đầu, kết thúc, số hiệu)], không chồng lấn, theo thứ tự xuất hiện."""
    found: list[tuple[int, int, str]] = []
    for rx, num in _ALIAS_RES:
        found.extend((m.start(), m.end(), num) for m in rx.finditer(text))
    found.extend((m.start(), m.end(), m.group(0).rstrip("-")) for m in _NUMBER.finditer(text))
    found.sort(key=lambda d: (d[0], -(d[1] - d[0])))
    out: list[tuple[int, int, str]] = []
    for d in found:
        if not out or d[0] >= out[-1][1]:
            out.append(d)
    return out


def find_articles(text: str) -> list[tuple[int, int, str, str | None, str | None]]:
    """[(bắt đầu, kết thúc, điều, khoản, điểm)]."""
    return [
        (m.start(), m.end(), m.group(3).lower(), m.group(2), m.group(1).lower() if m.group(1) else None)
        for m in _ART.finditer(text)
    ]


def _doc_near(text: str, art_start: int, art_end: int, docs, limit: int = 80) -> str | None:
    """Văn bản đứng NGAY SAU Điều ("Điều 328 BLDS"), nếu không thì ngay trước ("BLDS Điều 328"), cùng một câu."""
    for s, _e, num in docs:
        if s >= art_end and s - art_end <= limit and not _BREAK.search(text[art_end:s]):
            return num
    for _s, e, num in reversed(docs):
        if e <= art_start and art_start - e <= 40 and not _BREAK.search(text[e:art_start]):
            return num
    return None


def _norm_title(title: str) -> str:
    return " ".join(_nfc(title).casefold().replace("đ", "d").split()).rstrip(".:")


def _from_hint(hint: dict, fallback_doc: str | None) -> RefSpec | None:
    doc = None
    doc_ref = str(hint.get("doc_ref") or "").strip()
    if doc_ref:
        docs = find_docs(_nfc(doc_ref))
        doc = docs[0][2] if docs else None
    doc = doc or fallback_doc
    m = re.search(r"\d+[a-zđ]?", str(hint.get("article") or ""), re.I)
    title = str(hint.get("title") or "").strip() or None
    if not doc or not (m or title):
        return None

    def one(key: str, pattern: str) -> str | None:
        found = re.search(pattern, str(hint.get(key) or ""), re.I)
        return found.group(0).lower() if found else None

    khoan = one("clause", r"\d+") if m else None
    diem = one("point", r"[a-zđ]") if khoan else None
    return RefSpec(doc, m.group(0).lower() if m else None, khoan, diem, title if not m else None)


def parse_query_refs(query: str, exact_refs: list[dict] | None = None) -> list[RefSpec]:
    """Tham chiếu có cấu trúc trong câu hỏi + gợi ý exact_refs. Chỉ trả những tham chiếu đã giải nghĩa được văn bản."""
    text = _nfc(query)
    docs = find_docs(text)
    unique_doc = docs[0][2] if len({d[2] for d in docs}) == 1 else None
    specs: list[RefSpec] = []
    for start, end, dieu, khoan, diem in find_articles(text):
        doc = _doc_near(text, start, end, docs) or unique_doc
        if doc:
            specs.append(RefSpec(doc, dieu, khoan, diem))
    for hint in exact_refs or []:
        if isinstance(hint, dict) and (spec := _from_hint(hint, unique_doc)):
            specs.append(spec)
    return list(dict.fromkeys(specs))[:MAX_SPECS]


def is_pure_reference(query: str) -> bool:
    """Câu hỏi chỉ gồm tham chiếu Điều/Khoản/văn bản (không còn từ nội dung nào cần embedding)."""
    text = _nfc(query)
    spans = [(s, e) for s, e, *_ in find_articles(text)] + [(s, e) for s, e, _ in find_docs(text)]
    if not spans:
        return False
    chars = list(text)
    for s, e in spans:
        chars[s:e] = " " * (e - s)
    words = re.findall(r"[^\W\d_]+", "".join(chars).lower())
    return all(w in _FILLER for w in words)


def extract_citations(content: str, source_doc: str | None, source_dieu: str | None) -> list[RefSpec]:
    """Dẫn chiếu tường minh trong nội dung chunk. "Điều 357" trơ trọi hoặc "của Luật này" = cùng văn bản nguồn;
    "của Bộ luật Dân sự" = văn bản được gọi tên; gọi tên văn bản không nhận ra thì bỏ (không đoán sang văn bản khác)."""
    text = _nfc(content)
    docs = find_docs(text)
    specs: list[RefSpec] = []
    for _start, end, dieu, khoan, diem in find_articles(text):
        doc = source_doc
        tail = _OF.match(text, end)
        if tail:
            rest = tail.start(1)
            named = next((num for s, _e, num in docs if 0 <= s - rest <= 25), None)
            if named:
                doc = named
            elif _SELF.match(text, rest):
                doc = source_doc
            elif _LAW_WORD.match(text, rest):
                doc = None
        if doc and not (doc == source_doc and dieu == (source_dieu or "").lower()):
            specs.append(RefSpec(doc, dieu, khoan, diem))
    return list(dict.fromkeys(specs))


def row_matches(row, spec: RefSpec) -> bool:
    """Dòng (chunk, văn bản, _) khớp ĐÚNG cặp khoá của spec; Điều ngắn lưu nguyên điều thì tìm Khoản/Điểm trong nội dung."""
    chunk, doc = row[0], row[1]
    info = chunk.section_info or {}
    if spec.doc_number and (doc.doc_number or "").lower() != spec.doc_number.lower():
        return False
    if spec.dieu and str(info.get("dieu") or "").lower() != spec.dieu:
        return False
    if spec.title:
        title = chunking.article_title(chunk.content)
        if not title or _norm_title(title) != _norm_title(spec.title):
            return False
    if spec.khoan:
        chunk_khoan = info.get("khoan")
        if chunk_khoan is not None:
            if str(chunk_khoan) != spec.khoan:
                return False
            if spec.diem and (info.get("diem") or "").lower() not in ("", spec.diem):
                return False
            if spec.diem and not info.get("diem"):
                return chunking.has_clause(chunk.content, spec.khoan, spec.diem)
        elif not chunking.has_clause(chunk.content, spec.khoan, spec.diem):
            return False
    return True


def _like_title(title: str) -> list[str]:
    esc = title.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    # SQLite chỉ so không phân biệt hoa/thường với chữ ASCII: thử cả dạng viết hoa chữ đầu ("Đặt cọc")
    return [f"%Điều %. {v}%" for v in dict.fromkeys([esc, esc[:1].upper() + esc[1:]])]


def lookup(
    db: Session, embedding_model: str, doc_types: list[str] | None, specs: list[RefSpec], per_ref: int = MAX_PER_REF
) -> dict[RefSpec, list]:
    """Tra cả lô specs bằng TỐI ĐA 2 truy vấn (theo Điều, theo tên Điều), lọc đúng khoá ở Python. Giữ nguyên thứ tự chunk."""
    result: dict[RefSpec, list] = {s: [] for s in specs}
    by_article = [s for s in specs if s.dieu and s.doc_number]
    by_title = [s for s in specs if not s.dieu and s.title and s.doc_number]
    pools: list[tuple[list[RefSpec], list]] = []
    if by_article:
        rows = document_repo.retrieval_rows(
            db, embedding_model, doc_types,
            doc_numbers=sorted({s.doc_number for s in by_article}),
            articles=sorted({s.dieu for s in by_article}), limit=FETCH_LIMIT,
        )
        pools.append((by_article, rows))
    if by_title:
        rows = document_repo.retrieval_rows(
            db, embedding_model, doc_types,
            doc_numbers=sorted({s.doc_number for s in by_title}),
            title_patterns=sorted({p for s in by_title for p in _like_title(s.title)}), limit=FETCH_LIMIT,
        )
        pools.append((by_title, rows))
    for group, rows in pools:
        for spec in group:
            result[spec] = [r for r in rows if row_matches(r, spec)][:per_ref]
    return result
