"""Cắt văn bản pháp luật Việt Nam thành các đoạn (chunk) để tạo embedding.

Nguyên tắc:
- Mỗi Điều là 1 chunk (đơn vị trích dẫn tự nhiên: "Điều 188, Luật Đất đai 2024").
- Điều dài quá `max_chars` thì tách theo Khoản; Khoản vẫn dài thì tách theo Điểm;
  vẫn dài nữa thì cắt theo dòng.
- Chunk con luôn được thêm dòng "Điều X. <tên điều>" ở đầu (và câu dẫn của Khoản khi tách theo Điểm)
  để đoạn đứng một mình vẫn hiểu được và Gemini trích dẫn đúng.
- Văn bản không có cấu trúc "Điều ..." thì cắt theo đoạn văn (dòng trống).

Phần trước Điều đầu tiên (Quốc hiệu, "Căn cứ Hiến pháp...") bị bỏ qua vì không có giá trị tra cứu.
"""

import re
import unicodedata
from dataclasses import dataclass, field

DEFAULT_MAX_CHARS = 2500  # ~ 600–900 token tiếng Việt, dưới giới hạn 2048 token của gemini-embedding-001

# "Chương I", "CHƯƠNG II", "Chương 3"
_CHUONG = re.compile(r"^\s*chương\s+([ivxlcdm]+|\d+)\b", re.IGNORECASE)
# "Mục 1. ..." (nhóm Điều trong một Chương) — không lưu, chỉ để không bị dính vào Điều trước
_MUC = re.compile(r"^\s*mục\s+\d+\b", re.IGNORECASE)
# "Điều 188. Tên điều" / "Điều 10a: ..." — bắt buộc có dấu . hoặc : sau số
# để không nhầm câu kiểu "Điều 5 của Luật này quy định..."
_DIEU = re.compile(r"^\s*điều\s+(\d+[a-zđ]?)\s*[.:]\s*(.*)$", re.IGNORECASE)
# "1. Nội dung khoản"
_KHOAN = re.compile(r"^\s*(\d+)\.\s+\S")
# "a) Nội dung điểm", "đ) ..."
_DIEM = re.compile(r"^\s*([a-zđ])\)\s+\S")


@dataclass
class Chunk:
    content: str
    section_info: dict = field(default_factory=dict)  # {"chuong", "dieu", "khoan", "diem"}


@dataclass
class _Article:
    chuong: str | None
    dieu: str
    heading_line: str  # "Điều 188. Điều kiện thực hiện..."
    body: list[str]


def normalize_text(text: str) -> str:
    """NFC (gõ dấu tổ hợp vs dựng sẵn), bỏ khoảng trắng thừa cuối dòng, gộp dòng trống."""
    text = unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace(" ", " ")
    lines = [line.rstrip() for line in text.split("\n")]
    out: list[str] = []
    for line in lines:
        if line.strip() == "" and (not out or out[-1] == ""):
            continue
        out.append(line)
    return "\n".join(out).strip()


def _info(chuong=None, dieu=None, khoan=None, diem=None) -> dict:
    return {"chuong": chuong, "dieu": dieu, "khoan": khoan, "diem": diem}


def _split_articles(lines: list[str]) -> list[_Article]:
    articles: list[_Article] = []
    chuong: str | None = None
    current: _Article | None = None
    for line in lines:
        m_chuong = _CHUONG.match(line)
        if m_chuong:
            chuong = m_chuong.group(1).upper()
            current = None  # tên chương không thuộc Điều nào
            continue
        if _MUC.match(line):
            current = None
            continue
        m_dieu = _DIEU.match(line)
        if m_dieu:
            current = _Article(chuong, m_dieu.group(1), line.strip(), [])
            articles.append(current)
            continue
        if current is not None:
            current.body.append(line)
        # dòng trước Điều đầu tiên hoặc tên Chương/Mục: bỏ qua
    return articles


def _group_by(lines: list[str], pattern: re.Pattern) -> tuple[list[str], list[tuple[str, list[str]]]]:
    """Tách lines thành (phần mở đầu, [(số khoản/điểm, các dòng), ...])."""
    intro: list[str] = []
    groups: list[tuple[str, list[str]]] = []
    for line in lines:
        m = pattern.match(line)
        if m:
            groups.append((m.group(1), [line]))
        elif groups:
            groups[-1][1].append(line)
        else:
            intro.append(line)
    return intro, groups


def _join(*parts: list[str] | str) -> str:
    lines: list[str] = []
    for p in parts:
        lines.extend([p] if isinstance(p, str) else p)
    return "\n".join(l for l in lines if l.strip()).strip()


def _hard_split(prefix: str, lines: list[str], max_chars: int) -> list[str]:
    """Cắt theo dòng cho vừa max_chars; dòng đơn quá dài thì cắt theo ký tự."""
    budget = max(200, max_chars - len(prefix) - 1)
    pieces: list[str] = []
    buf: list[str] = []
    size = 0
    for line in (l for l in lines if l.strip()):
        while len(line) > budget:
            if buf:
                pieces.append("\n".join(buf))
                buf, size = [], 0
            cut = line.rfind(" ", 0, budget)
            cut = cut if cut > budget // 2 else budget
            pieces.append(line[:cut])
            line = line[cut:].lstrip()
        if size + len(line) + 1 > budget and buf:
            pieces.append("\n".join(buf))
            buf, size = [], 0
        buf.append(line)
        size += len(line) + 1
    if buf:
        pieces.append("\n".join(buf))
    return [_join(prefix, p) for p in pieces]


def _chunk_article(a: _Article, max_chars: int) -> list[Chunk]:
    whole = _join(a.heading_line, a.body)
    if len(whole) <= max_chars:
        return [Chunk(whole, _info(a.chuong, a.dieu))]

    intro, khoans = _group_by(a.body, _KHOAN)
    if not khoans:
        return [Chunk(t, _info(a.chuong, a.dieu)) for t in _hard_split(_join(a.heading_line), a.body, max_chars)]

    header = _join(a.heading_line, intro)  # câu mở đầu của Điều (nếu có) đi kèm mọi khoản
    chunks: list[Chunk] = []
    for khoan_no, khoan_lines in khoans:
        text = _join(header, khoan_lines)
        if len(text) <= max_chars:
            chunks.append(Chunk(text, _info(a.chuong, a.dieu, khoan_no)))
            continue
        k_intro, diems = _group_by(khoan_lines, _DIEM)
        k_header = _join(header, k_intro)  # "1. ... khi có các điều kiện sau đây:"
        if not diems or len(k_header) > max_chars // 2:
            for t in _hard_split(header, khoan_lines, max_chars):
                chunks.append(Chunk(t, _info(a.chuong, a.dieu, khoan_no)))
            continue
        for diem_ch, diem_lines in diems:
            text = _join(k_header, diem_lines)
            if len(text) <= max_chars:
                chunks.append(Chunk(text, _info(a.chuong, a.dieu, khoan_no, diem_ch)))
            else:
                for t in _hard_split(k_header, diem_lines, max_chars):
                    chunks.append(Chunk(t, _info(a.chuong, a.dieu, khoan_no, diem_ch)))
    return chunks


def _chunk_paragraphs(text: str, max_chars: int) -> list[Chunk]:
    """Văn bản không có 'Điều ...': gom các đoạn văn (cách nhau dòng trống) cho vừa max_chars."""
    chunks: list[Chunk] = []
    buf = ""
    for para in (p.strip() for p in text.split("\n\n")):
        if not para:
            continue
        if len(para) > max_chars:
            if buf:
                chunks.append(Chunk(buf, _info()))
                buf = ""
            chunks.extend(Chunk(t, _info()) for t in _hard_split("", para.split("\n"), max_chars))
            continue
        if buf and len(buf) + 2 + len(para) > max_chars:
            chunks.append(Chunk(buf, _info()))
            buf = ""
        buf = f"{buf}\n\n{para}" if buf else para
    if buf:
        chunks.append(Chunk(buf, _info()))
    return chunks


def chunk_legal_text(text: str, max_chars: int = DEFAULT_MAX_CHARS) -> list[Chunk]:
    text = normalize_text(text)
    if not text:
        return []
    articles = _split_articles(text.split("\n"))
    if not articles:
        return _chunk_paragraphs(text, max_chars)
    chunks: list[Chunk] = []
    for a in articles:
        chunks.extend(_chunk_article(a, max_chars))
    return chunks


def embedding_text(doc_title: str, chunk: Chunk) -> str:
    """Chuỗi gửi đi tạo embedding: thêm tên văn bản + chương để vector mang đủ ngữ cảnh."""
    chuong = chunk.section_info.get("chuong")
    head = f"{doc_title} - Chương {chuong}" if chuong else doc_title
    return f"{head}\n{chunk.content}"
