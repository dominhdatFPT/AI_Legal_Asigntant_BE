"""Kiểm đếm dữ liệu luật đã nạp vào kho RAG: có thiếu, trùng, lỗi hay không. Script CHỈ ĐỌC (SELECT), không sửa database,
không tạo embedding, không gọi Gemini. Đây là bước 1 trong việc kiểm tra độ tin cậy; đối chiếu bản gốc và kiểm hiệu lực pháp lý
là việc làm tay (script KHÔNG nhận định về hiệu lực và KHÔNG đoán số Điều đúng của từng luật, chỉ in số Điều lớn nhất tìm được).

Cách chạy (ở thư mục gốc legal-assistant-ai, đã có venv, .env có RAG_DATABASE_URL, giống ingest_batch.py):

    .venv\\Scripts\\python -X utf8 services/rag-service/scripts/audit_corpus.py                       # tất cả văn bản
    .venv\\Scripts\\python -X utf8 services/rag-service/scripts/audit_corpus.py --doc-number 31/2024/QH15
    .venv\\Scripts\\python -X utf8 services/rag-service/scripts/audit_corpus.py --sample 30 --seed 7  # thêm mẫu để đối chiếu tay
    .venv\\Scripts\\python -X utf8 services/rag-service/scripts/audit_corpus.py --out bao_cao.md      # ghi thêm ra file .md

Không có --out thì chỉ in ra màn hình, không tạo file nào. Mã thoát 0 nếu không có LỖI, 1 nếu có LỖI (hoặc không đọc được database).

Cách xếp mức (do script tự đặt, để biết nên xem cái nào trước):
- LỖI = dữ liệu hỏng hoặc mất gần như chắc chắn: văn bản không có đoạn nào, Điều có trong toàn văn mà không có đoạn nào, đoạn rỗng,
  chunk_index trùng, embedding_model rỗng hoặc lẫn nhiều model, chunk_count lưu lệch số đoạn thật, ký tự lỗi mã hoá (U+FFFD, "Ã", "Ä"...).
- CẢNH BÁO = nghi vấn cần người xem (Điều lạ, Điều trùng, nhảy số Điều, đoạn quá ngắn/dài, độ phủ thấp, file nguồn khác database...).
- OK = không có LỖI hay CẢNH BÁO nào.

Phần E tự tìm file .txt nguồn: theo tên file trong danh_sach_luat.csv (nếu có) hoặc danh_sach_luat.mau.csv, hoặc theo tên luật
(vd "Luật Đất đai 2024" -> luat_dat_dai_2024.txt), trong các thư mục data/ gần repo. Không thấy thì bỏ qua và ghi một dòng.
"""

import argparse
import csv
import random
import re
import sys
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parents[1]  # services/rag-service
ROOT_DIR = SERVICE_DIR.parents[1]                  # legal-assistant-ai
sys.path.insert(0, str(SERVICE_DIR))

from app.services.chunking import (  # noqa: E402  (cần sys.path ở trên)
    DEFAULT_MAX_CHARS, _CHUONG, _DIEM, _DIEU, _KHOAN, _MUC, _split_articles, normalize_text,
)

ERROR, WARNING, OK = "LỖI", "CẢNH BÁO", "OK"
MAX_EXAMPLES = 10
SHORT_CHARS = 50
LONG_TOLERANCE = 1.2  # chunk dài quá max_chars hơn 20% mới coi là quá dài (bộ cắt cố tình cho dài hơn một chút khi không cắt được)
COVERAGE_WARN = 0.95
SECTION_KEYS = ("chuong", "dieu", "khoan", "diem")
# Thứ tự hậu tố chữ của số Điều (10a, 10b...): bảng chữ cái tiếng Việt như số thứ tự điểm trong luật
SUFFIX_ORDER = "abcdđeghiklmnopqrstuvxy"
# Điều viết lệch: dòng bắt đầu bằng "Điều" + số, KHÔNG cần dấu chấm sau số (bộ cắt bắt buộc có "." hoặc ":")
_LOOSE_DIEU = re.compile(r"^\s*điều\s+(\d+[a-zđ]?)\b", re.IGNORECASE)
_DIEU_NUMBER = re.compile(r"(\d+)([a-zđ]?)")
# Mojibake tiếng Việt: chữ UTF-8 bị đọc như Latin-1/cp1252 ("Äiá»u" thay cho "Điều"). Chữ "Ã", "Â" đứng riêng là chữ Việt hợp lệ
# nên chỉ bắt khi theo sau là ký hiệu lạ (U+0080-U+00BF, dấu ngoặc/gạch cong...), thứ không bao giờ đứng sau chữ cái tiếng Việt.
_MOJIBAKE = re.compile("[ÃÂÄÅÆ][\u0080-¿–-›ŒœŠšŸŽžˆ˜]|á[¡-¿]")
_PAGE_LINE = re.compile(
    r"^\s*(?:(?:trang|page)\s*\d+(?:\s*(?:/|trên|of)\s*\d+)?|\d{1,4}|[-–—]\s*\d{1,4}\s*[-–—])\s*$", re.IGNORECASE
)
_TOC_LINE = re.compile(r"^\s*(?:mục\s+lục|muc\s+luc)\s*:?\s*$", re.IGNORECASE)
# Các Điều hay được trích: luôn đưa vào mẫu đối chiếu tay (--sample)
CITED_ARTICLES = {"31/2024/QH15": ["188", "30", "45"], "59/2020/QH14": ["51", "206"]}


# ── Kiểu dữ liệu ────────────────────────────────────────────────────────────

@dataclass
class DocRow:
    doc_number: str | None
    title: str
    doc_type: str
    status: str
    issued_date: date | None
    effective_date: date | None
    is_processed: bool
    chunk_count: int
    content: str


@dataclass
class ChunkRow:
    index: int
    content: str
    section_info: dict | None
    embedding_model: str | None


@dataclass
class Finding:
    level: str
    text: str
    examples: list[str] = field(default_factory=list)


@dataclass
class DocReport:
    doc: DocRow
    profile: list[str] = field(default_factory=list)
    info: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    n_chunks: int = 0
    n_articles: int = 0
    max_article: str | None = None
    coverage: float | None = None

    @property
    def verdict(self) -> str:
        if any(f.level == ERROR for f in self.findings):
            return ERROR
        return WARNING if self.findings else OK


# ── Hàm thuần: số Điều ──────────────────────────────────────────────────────

def dieu_sort_key(dieu: str) -> tuple[int, int, str]:
    """"10" < "10a" < "10b" < "11". Chuỗi không đúng dạng số đứng cuối."""
    m = _DIEU_NUMBER.fullmatch(dieu.lower())
    if not m:
        return (10**9, 0, dieu)
    suffix = m.group(2)
    rank = 0 if not suffix else (SUFFIX_ORDER.index(suffix) + 1 if suffix in SUFFIX_ORDER else 100 + ord(suffix))
    return (int(m.group(1)), rank, "")


def find_gaps(numbers) -> list[str]:
    """Số Điều bị nhảy: có 44 và 46 mà không có 45 -> ["45"]; có 10 và 10b mà không có 10a -> ["10a"];
    có 10a mà không có 10 -> ["10"]. Chỉ xét từ số nhỏ nhất đến số lớn nhất đang có."""
    parsed = []
    for n in set(numbers):
        m = _DIEU_NUMBER.fullmatch(n.lower())
        if m:
            parsed.append((int(m.group(1)), m.group(2)))
    if not parsed:
        return []
    bases = {b for b, _ in parsed}
    missing = [str(b) for b in range(min(bases), max(bases) + 1) if b not in bases]
    for base in sorted(bases):
        suffixes = {s for b, s in parsed if b == base and s}
        if not suffixes:
            continue
        if (base, "") not in parsed:
            missing.append(str(base))
        known = [SUFFIX_ORDER.index(s) for s in suffixes if s in SUFFIX_ORDER]
        if known:
            missing += [f"{base}{c}" for c in SUFFIX_ORDER[: max(known) + 1] if c not in suffixes]
    return sorted(set(missing), key=dieu_sort_key)


def find_duplicates(numbers: list[str]) -> dict[str, int]:
    """Số Điều xuất hiện từ hai lần trở lên (theo thứ tự số Điều)."""
    counts = Counter(n.lower() for n in numbers)
    return {k: counts[k] for k in sorted((k for k, v in counts.items() if v > 1), key=dieu_sort_key)}


def analyze_articles(full_text: str, chunks: list[ChunkRow]) -> dict:
    """Đối chiếu số Điều giữa toàn văn (đúng bộ nhận diện của chunking.py, thêm một lượt quét lỏng) và các chunk."""
    lines = normalize_text(full_text).split("\n")
    articles = _split_articles(lines)
    strict = [(a.chuong, a.dieu.lower()) for a in articles]
    numbers = [d for _, d in strict]
    in_text = set(numbers)
    in_chunks = {
        str(c.section_info["dieu"]).lower()
        for c in chunks if isinstance(c.section_info, dict) and c.section_info.get("dieu")
    }
    per_chuong: Counter = Counter()
    order: list = []
    for chuong, _ in strict:
        if chuong not in per_chuong:
            order.append(chuong)
        per_chuong[chuong] += 1
    loose = [
        (i, line.strip()[:80])
        for i, line in enumerate(lines, start=1) if _LOOSE_DIEU.match(line) and not _DIEU.match(line)
    ]
    ordered = sorted(in_text, key=dieu_sort_key)
    return {
        "numbers": numbers, "count": len(numbers), "first": ordered[0] if ordered else None,
        "max": ordered[-1] if ordered else None, "duplicates": find_duplicates(numbers), "gaps": find_gaps(in_text),
        "per_chuong": [(c, per_chuong[c]) for c in order], "loose": loose,
        "missing_in_chunks": sorted(in_text - in_chunks, key=dieu_sort_key),
        "strange_in_chunks": sorted(in_chunks - in_text, key=dieu_sort_key),
    }


# ── Hàm thuần: chất lượng từng chunk ────────────────────────────────────────

def chunk_label(row: ChunkRow) -> str:
    dieu = row.section_info.get("dieu") if isinstance(row.section_info, dict) else None
    return f"chunk_index={row.index}" + (f", Điều {dieu}" if dieu else "")


def classify_chunks(rows: list[ChunkRow], max_chars: int = DEFAULT_MAX_CHARS) -> dict:
    """Chia chunk vào các nhóm lỗi. Giá trị là danh sách ChunkRow (hoặc dict/list đơn giản cho chỉ số, model)."""
    empty = [r for r in rows if not (r.content or "").strip()]
    short = [r for r in rows if 0 < len((r.content or "").strip()) < SHORT_CHARS]
    long = [r for r in rows if len(r.content or "") > max_chars * LONG_TOLERANCE]
    bad_info = [
        r for r in rows
        if not isinstance(r.section_info, dict) or any(k not in r.section_info for k in SECTION_KEYS)
    ]
    no_dieu = [r for r in rows if isinstance(r.section_info, dict) and not r.section_info.get("dieu")]
    index_counts = Counter(r.index for r in rows)
    index_duplicates = {i: n for i, n in sorted(index_counts.items()) if n > 1}
    present = sorted(index_counts)
    index_missing = [i for i in range(0, present[-1] + 1) if i not in index_counts] if present else []
    models = Counter((r.embedding_model or "").strip() or "(trống)" for r in rows)
    by_content: dict[str, list[int]] = {}
    for r in rows:
        text = (r.content or "").strip()
        if text:
            by_content.setdefault(text, []).append(r.index)
    return {
        "empty": empty, "short": short, "long": long, "bad_info": bad_info, "no_dieu": no_dieu,
        "index_duplicates": index_duplicates, "index_missing": index_missing, "models": dict(models),
        "empty_model": [r for r in rows if not (r.embedding_model or "").strip()],
        "duplicate_content": [sorted(v) for v in by_content.values() if len(v) > 1],
    }


def scan_text_issues(text: str, blank_run: int = 2) -> dict[str, list[str]]:
    """Dấu hiệu lỗi nguồn trong một đoạn chữ. Trả {loại: [ví dụ ngắn]}, chỉ có khoá của loại tìm thấy.
    blank_run = số dòng trống liên tiếp tối thiểu để coi là bất thường."""
    found: dict[str, list[str]] = {}

    def add(kind: str, example: str) -> None:
        found.setdefault(kind, []).append(example.strip()[:60])

    if "�" in text:
        i = text.index("�")
        add("ký tự thay thế U+FFFD", text[max(0, i - 15): i + 15])
    for m in _MOJIBAKE.finditer(text):
        add("lỗi mã hoá tiếng Việt (mojibake)", text[max(0, m.start() - 10): m.end() + 10])
        break
    if unicodedata.normalize("NFC", text) != text:
        add("chưa chuẩn NFC", "(chữ có dấu ở dạng tổ hợp)")
    for line in text.split("\n"):
        if _PAGE_LINE.match(line):
            add("dòng số trang", line)
        elif _TOC_LINE.match(line):
            add("dòng MỤC LỤC", line)
    if re.search(r"\n(?:[ \t]*\n){%d,}" % blank_run, text):
        add("nhiều dòng trống liên tiếp", f"({blank_run}+ dòng trống)")
    return found


def repeated_lines(texts: list[str], min_chunks: int = 4) -> list[tuple[str, int]]:
    """Dòng ngắn lặp lại ở nhiều chunk mà không phải tiêu đề cấu trúc: nghi chân trang/đầu trang lặp theo từng trang.
    Bỏ qua dòng "Điều/Chương/Mục/Khoản/Điểm" (bộ cắt cố ý lặp tiêu đề Điều ở các chunk con) và dòng kết thúc bằng dấu câu."""
    counts: Counter = Counter()
    for text in texts:
        for line in {l.strip() for l in text.split("\n")}:
            if not 8 <= len(line) <= 120 or line[-1] in ".;:,":
                continue
            if any(p.match(line) for p in (_DIEU, _CHUONG, _MUC, _KHOAN, _DIEM)):
                continue
            counts[line] += 1
    return sorted(((l, n) for l, n in counts.items() if n >= min_chunks), key=lambda x: (-x[1], x[0]))


# ── Hàm thuần: độ phủ, đối chiếu nguồn, mẫu ─────────────────────────────────

def _non_space(text: str) -> int:
    return sum(1 for c in text if not c.isspace())


def coverage_ratio(chunks: list[ChunkRow], full_text: str) -> tuple[float | None, int, int]:
    """(tỉ lệ, số ký tự chunk, số ký tự toàn văn từ Điều đầu tiên). Đếm ký tự không phải khoảng trắng. Chunk con của cùng một Điều
    bị bỏ dòng tiêu đề "Điều X." thêm vào ở đầu. Không có Điều nào hoặc toàn văn rỗng thì tỉ lệ là None."""
    lines = normalize_text(full_text).split("\n")
    first = next((i for i, line in enumerate(lines) if _DIEU.match(line)), None)
    if first is None:
        return None, 0, 0
    source_chars = _non_space("\n".join(lines[first:]))
    if source_chars == 0:
        return None, 0, 0
    chunk_chars, previous = 0, None
    for c in sorted(chunks, key=lambda r: r.index):
        info = c.section_info if isinstance(c.section_info, dict) else {}
        key = (info.get("chuong"), info.get("dieu"))
        body = c.content or ""
        if previous is not None and key == previous and key[1]:
            head, _, rest = body.partition("\n")
            if _DIEU.match(head):
                body = rest
        chunk_chars += _non_space(body)
        previous = key
    return chunk_chars / source_chars, chunk_chars, source_chars


def compare_texts(db_text: str, source_text: str) -> dict:
    """So hai văn bản sau khi chuẩn hoá (normalize_text). Đếm dòng chỉ có ở một bên, không in đoạn dài."""
    a, b = normalize_text(db_text).split("\n"), normalize_text(source_text).split("\n")
    if a == b:
        return {"same": True, "only_db": 0, "only_source": 0, "examples_db": [], "examples_source": []}
    only_db, only_source = Counter(a) - Counter(b), Counter(b) - Counter(a)
    return {
        "same": False, "only_db": sum(only_db.values()), "only_source": sum(only_source.values()),
        "examples_db": [l[:80] for l in list(only_db)[:3]], "examples_source": [l[:80] for l in list(only_source)[:3]],
    }


def pick_sample(pool: list[dict], n: int, seed: int, required: dict[str, list[str]] = CITED_ARTICLES) -> list[dict]:
    """pool: [{"doc_number", "dieu", "index", ...}]. Trả các chunk của Điều hay được trích (theo `required`) rồi n chunk ngẫu nhiên
    khác; cùng seed thì ra đúng mẫu cũ. Kết quả xếp theo (số hiệu, chunk_index) trong từng nhóm."""
    ordered = sorted(pool, key=lambda c: (c["doc_number"] or "", c["index"]))
    cited = [c for c in ordered if str(c.get("dieu") or "").lower() in {d.lower() for d in required.get(c["doc_number"], [])}]
    cited_ids = {(c["doc_number"], c["index"]) for c in cited}
    rest = [c for c in ordered if (c["doc_number"], c["index"]) not in cited_ids]
    picked = random.Random(seed).sample(rest, min(max(n, 0), len(rest)))
    return cited + sorted(picked, key=lambda c: (c["doc_number"] or "", c["index"]))


# ── File nguồn (E) ──────────────────────────────────────────────────────────

def slugify(title: str) -> str:
    text = unicodedata.normalize("NFD", title.lower().replace("đ", "d"))
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")


def source_dirs() -> list[Path]:
    candidates = [ROOT_DIR / "data", ROOT_DIR.parent / "data", ROOT_DIR, SERVICE_DIR / "scripts", Path.cwd() / "data"]
    seen, out = set(), []
    for d in candidates:
        if d.is_dir() and d.resolve() not in seen:
            seen.add(d.resolve())
            out.append(d)
    return out


def manifest_names(dirs: list[Path]) -> dict[str, list[str]]:
    """{số hiệu: [tên file]} đọc từ danh_sach_luat*.csv (cột file, doc_number). Lỗi đọc thì bỏ qua file đó."""
    names: dict[str, list[str]] = {}
    for d in dirs:
        for path in sorted(d.glob("danh_sach_luat*.csv")):
            try:
                with path.open(encoding="utf-8-sig", newline="") as f:
                    for row in csv.DictReader(f):
                        number, file = (row.get("doc_number") or "").strip(), (row.get("file") or "").strip()
                        if number and file and not file.startswith("#"):
                            names.setdefault(number, []).append(Path(file).name)
            except (OSError, csv.Error, UnicodeDecodeError):
                continue
    return names


def find_source_file(doc: DocRow, dirs: list[Path], names: dict[str, list[str]]) -> Path | None:
    candidates = list(names.get(doc.doc_number or "", [])) + [f"{slugify(doc.title)}.txt"]
    for d in dirs:
        for name in candidates:
            if (d / name).is_file():
                return d / name
    return None


# ── Kiểm một văn bản ────────────────────────────────────────────────────────

def _examples(rows: list[ChunkRow]) -> list[str]:
    return [chunk_label(r) for r in rows[:MAX_EXAMPLES]]


def audit_document(doc: DocRow, chunks: list[ChunkRow], max_chars: int = DEFAULT_MAX_CHARS,
                   source: tuple[Path | None, str | None] | None = None) -> DocReport:
    """source: (đường dẫn file nguồn, nội dung) hoặc (None, None) khi không tìm thấy; None = không kiểm phần E."""
    report = DocReport(doc=doc, n_chunks=len(chunks))
    add = lambda level, text, examples=None: report.findings.append(Finding(level, text, examples or []))

    # A. Hồ sơ văn bản
    report.profile = [
        f"Số hiệu: {doc.doc_number or '(trống)'} | Loại: {doc.doc_type} | Trạng thái: {doc.status}",
        f"Tên: {doc.title}",
        f"Ngày ban hành: {doc.issued_date or '(trống)'} | Ngày hiệu lực: {doc.effective_date or '(trống)'} | is_processed: {doc.is_processed}",
        f"chunk_count đã lưu: {doc.chunk_count} | số chunk thật: {len(chunks)} | độ dài toàn văn: {len(doc.content or '')} ký tự",
    ]
    if not (doc.doc_number or "").strip():
        add(WARNING, "Số hiệu (doc_number) để trống.")
    if doc.effective_date is None:
        add(WARNING, "Ngày hiệu lực để trống.")
    if doc.status != "active":
        add(WARNING, f"Trạng thái là '{doc.status}', không phải 'active' (tìm kiếm chỉ lấy văn bản 'active').")
    if not doc.is_processed:
        add(WARNING, "is_processed là false (tìm kiếm bỏ qua văn bản này).")
    if not chunks:
        add(ERROR, "Văn bản không có chunk nào.")
    elif doc.chunk_count != len(chunks):
        add(ERROR, f"chunk_count lưu là {doc.chunk_count} nhưng số chunk thật là {len(chunks)}.")

    # B. Đối chiếu số Điều giữa toàn văn và chunk
    art = analyze_articles(doc.content or "", chunks)
    report.n_articles, report.max_article = art["count"], art["max"]
    if art["count"] == 0:
        add(WARNING, "Bộ nhận diện không thấy 'Điều N.' nào trong toàn văn (văn bản sẽ bị cắt theo đoạn văn).")
    else:
        report.info.append(
            f"Số Điều trong toàn văn: {art['count']} | Điều nhỏ nhất: {art['first']} | Điều lớn nhất: {art['max']} "
            "(đối chiếu tay với số Điều thật của luật)"
        )
        report.info.append("Số Điều theo Chương: " + ", ".join(
            f"{'(trước Chương I)' if c is None else 'Chương ' + c}: {n}" for c, n in art["per_chuong"]))
        if art["first"] != "1":
            add(WARNING, f"Điều nhỏ nhất trong toàn văn là {art['first']}, không phải 1 (có thể mất phần đầu).")
    if art["missing_in_chunks"]:
        add(ERROR, f"{len(art['missing_in_chunks'])} Điều có trong toàn văn nhưng không có chunk nào (thiếu).",
            art["missing_in_chunks"][:MAX_EXAMPLES * 2])
    if art["strange_in_chunks"]:
        add(WARNING, f"{len(art['strange_in_chunks'])} Điều có trong chunk nhưng không có trong toàn văn (lạ).",
            art["strange_in_chunks"][:MAX_EXAMPLES * 2])
    if art["duplicates"]:
        add(WARNING, f"{len(art['duplicates'])} Điều xuất hiện từ hai lần trở lên trong toàn văn (nghi trùng hoặc lẫn phụ lục).",
            [f"Điều {d} ({n} lần)" for d, n in list(art["duplicates"].items())[:MAX_EXAMPLES]])
    if art["gaps"]:
        add(WARNING, f"{len(art['gaps'])} chỗ nhảy số Điều (có số trước và sau mà thiếu số giữa).", [f"Điều {g}" for g in art["gaps"][:MAX_EXAMPLES * 2]])
    if art["loose"]:
        add(WARNING, f"{len(art['loose'])} dòng bắt đầu bằng 'Điều <số>' nhưng bộ cắt không coi là tiêu đề Điều "
            "(thiếu dấu '.' hoặc ':' sau số; cũng có thể chỉ là câu văn thường).",
            [f"dòng {i}: {s}" for i, s in art["loose"][:MAX_EXAMPLES]])

    # C. Chất lượng từng chunk
    cls = classify_chunks(chunks, max_chars)
    if cls["empty"]:
        add(ERROR, f"{len(cls['empty'])} chunk rỗng.", _examples(cls["empty"]))
    if cls["short"]:
        add(WARNING, f"{len(cls['short'])} chunk quá ngắn (dưới {SHORT_CHARS} ký tự).", _examples(cls["short"]))
    if cls["long"]:
        add(WARNING, f"{len(cls['long'])} chunk quá dài (vượt {max_chars} ký tự quá {round((LONG_TOLERANCE - 1) * 100)}%).", _examples(cls["long"]))
    if cls["bad_info"]:
        add(WARNING, f"{len(cls['bad_info'])} chunk có section_info thiếu khoá hoặc không phải từ điển.", _examples(cls["bad_info"]))
    if cls["no_dieu"] and art["count"] > 0:
        add(WARNING, f"{len(cls['no_dieu'])} chunk thiếu số Điều trong section_info.", _examples(cls["no_dieu"]))
    if cls["index_duplicates"]:
        add(ERROR, f"{len(cls['index_duplicates'])} giá trị chunk_index bị trùng.", [f"chunk_index={i} ({n} lần)" for i, n in list(cls["index_duplicates"].items())[:MAX_EXAMPLES]])
    if cls["index_missing"]:
        add(WARNING, f"chunk_index bị nhảy: thiếu {len(cls['index_missing'])} số trong dãy 0..{max(r.index for r in chunks)}.", [f"chunk_index={i}" for i in cls["index_missing"][:MAX_EXAMPLES]])
    if cls["empty_model"]:
        add(ERROR, f"{len(cls['empty_model'])} chunk có embedding_model rỗng.", _examples(cls["empty_model"]))
    if len(cls["models"]) > 1:
        add(ERROR, "Các chunk dùng nhiều embedding_model khác nhau.", [f"{m}: {n} chunk" for m, n in cls["models"].items()])
    elif cls["models"]:
        report.info.append(f"embedding_model: {next(iter(cls['models']))}")
    if cls["duplicate_content"]:
        add(WARNING, f"{len(cls['duplicate_content'])} nhóm chunk trùng nội dung hoàn toàn.", [f"chunk_index {g}" for g in cls["duplicate_content"][:MAX_EXAMPLES]])

    # C.5 dấu hiệu lỗi nguồn trong chunk
    issue_chunks: dict[str, list[ChunkRow]] = {}
    snippets: dict[str, list[str]] = {}
    for c in chunks:
        for kind, examples in scan_text_issues(c.content or "", blank_run=2).items():
            issue_chunks.setdefault(kind, []).append(c)
            snippets.setdefault(kind, []).append(f"{chunk_label(c)}: {examples[0]!r}")
    for kind, rows in issue_chunks.items():
        level = ERROR if kind.startswith(("ký tự thay thế", "lỗi mã hoá")) else WARNING
        add(level, f"{len(rows)} chunk dính '{kind}'.", snippets[kind][:5])
    repeated = repeated_lines([c.content or "" for c in chunks])
    if repeated:
        add(WARNING, f"{len(repeated)} dòng ngắn lặp lại ở nhiều chunk (nghi chân trang/đầu trang).", [f"{n} chunk: {l[:60]!r}" for l, n in repeated[:5]])
    raw_issues = scan_text_issues(doc.content or "", blank_run=3)
    for kind in ("ký tự thay thế U+FFFD", "lỗi mã hoá tiếng Việt (mojibake)"):
        if kind in raw_issues:
            add(ERROR, f"Toàn văn gốc (cột content) dính '{kind}'.", raw_issues[kind][:3])
    for kind in ("chưa chuẩn NFC", "nhiều dòng trống liên tiếp"):
        if kind in raw_issues:
            report.info.append(f"Toàn văn gốc có dấu hiệu '{kind}' (bộ cắt đã tự chuẩn hoá nên chunk không bị ảnh hưởng).")

    # D. Độ phủ
    ratio, chunk_chars, source_chars = coverage_ratio(chunks, doc.content or "")
    report.coverage = ratio
    if ratio is None:
        report.info.append("Độ phủ: không tính được (toàn văn không có Điều nào hoặc rỗng).")
    else:
        report.info.append(f"Độ phủ: chunk {chunk_chars} ký tự / toàn văn từ Điều đầu {source_chars} ký tự = {ratio:.1%} (đếm không tính khoảng trắng)")
        if ratio < COVERAGE_WARN:
            add(WARNING, f"Độ phủ {ratio:.1%} thấp hơn {COVERAGE_WARN:.0%}: có thể mất nội dung.")

    # E. So với file .txt nguồn
    if source is not None:
        path, text = source
        if path is None or text is None:
            report.info.append("File nguồn: không tìm thấy file .txt tương ứng, bỏ qua phần so sánh.")
        else:
            diff = compare_texts(doc.content or "", text)
            if diff["same"]:
                report.info.append(f"File nguồn {path.name}: GIỐNG cột content (sau khi chuẩn hoá).")
            else:
                add(WARNING, f"File nguồn {path.name} KHÁC cột content: {diff['only_db']} dòng chỉ có trong database, {diff['only_source']} dòng chỉ có trong file.",
                    [f"chỉ trong database: {l!r}" for l in diff["examples_db"]] + [f"chỉ trong file: {l!r}" for l in diff["examples_source"]])
    return report


# ── Báo cáo ─────────────────────────────────────────────────────────────────

def format_sample(pool: list[dict], picked: list[dict]) -> list[str]:
    lines = ["## Mẫu để đối chiếu tay với bản gốc", f"({len(picked)} chunk: các Điều hay được trích trước, rồi mẫu ngẫu nhiên cố định theo seed)", ""]
    for c in picked:
        info = c.get("section_info") or {}
        parts = [f"Điều {info['dieu']}" if info.get("dieu") else "(không có Điều)"]
        if info.get("khoan"):
            parts.append(f"Khoản {info['khoan']}")
        if info.get("diem"):
            parts.append(f"Điểm {info['diem']}")
        lines += [f"### {c['title']} ({c['doc_number']}) — {', '.join(parts)}, chunk_index {c['index']}", c["content"], ""]
    return lines


def build_report(reports: list[DocReport], sample_lines: list[str] | None = None) -> str:
    out = ["# Kiểm đếm dữ liệu luật trong kho RAG", "",
           "Script chỉ đọc database. Đây là kiểm đếm bằng quy tắc: chưa đối chiếu bản gốc và chưa kiểm hiệu lực pháp lý (làm tay).", "",
           "## Bảng tổng hợp", "",
           "| Số hiệu | Tên | Chunk | Số Điều | Điều lớn nhất | Độ phủ | Kết luận |", "|---|---|---|---|---|---|---|"]
    for r in reports:
        cov = f"{r.coverage:.1%}" if r.coverage is not None else "-"
        out.append(f"| {r.doc.doc_number or '(trống)'} | {r.doc.title[:45]} | {r.n_chunks} | {r.n_articles} | {r.max_article or '-'} | {cov} | {r.verdict} |")
    counts = Counter(r.verdict for r in reports)
    out += ["", f"Tổng: {len(reports)} văn bản — OK {counts[OK]}, CẢNH BÁO {counts[WARNING]}, LỖI {counts[ERROR]}.", "", "## Chi tiết từng văn bản"]
    for r in reports:
        out += ["", f"### {r.doc.doc_number or '(trống)'} — {r.doc.title}: {r.verdict}", *[f"- {l}" for l in r.profile]]
        out += [f"- {l}" for l in r.info]
        if not r.findings:
            out.append("- Không có cảnh báo hay lỗi.")
        for f in sorted(r.findings, key=lambda f: f.level != ERROR):
            out.append(f"- [{f.level}] {f.text}")
            if f.examples:
                out.append("    ví dụ: " + "; ".join(str(e) for e in f.examples))
    if sample_lines:
        out += [""] + sample_lines
    return "\n".join(out) + "\n"


# ── Đọc database (chỉ SELECT) ───────────────────────────────────────────────

def _load_env() -> None:
    """Nạp .env đúng cách ingest_batch.py làm; không in gì ra. Tách thành hàm để test không đụng .env thật."""
    from dotenv import load_dotenv

    load_dotenv(ROOT_DIR / ".env")


def fetch_corpus(db, doc_number: str | None) -> list[tuple[DocRow, list[ChunkRow]]]:
    """Chỉ SELECT. Không lấy cột embedding (nặng, không cần). Lỗi database ném SQLAlchemyError cho nơi gọi xử lý."""
    from sqlalchemy import select

    from app.models import DocumentChunk, LegalDocument

    query = select(LegalDocument).order_by(LegalDocument.doc_number, LegalDocument.title)
    if doc_number:
        query = query.where(LegalDocument.doc_number == doc_number)
    result = []
    for d in db.execute(query).scalars().all():
        rows = db.execute(
            select(DocumentChunk.chunk_index, DocumentChunk.content, DocumentChunk.section_info, DocumentChunk.embedding_model)
            .where(DocumentChunk.document_id == d.id).order_by(DocumentChunk.chunk_index)
        ).all()
        result.append((
            DocRow(d.doc_number, d.title, d.doc_type, d.status, d.issued_date, d.effective_date, d.is_processed, d.chunk_count, d.content or ""),
            [ChunkRow(i, c, s, m) for i, c, s, m in rows],
        ))
    return result


def main() -> int:
    p = argparse.ArgumentParser(description="Kiểm đếm dữ liệu luật đã nạp vào kho RAG (chỉ đọc)")
    p.add_argument("--doc-number", help="chỉ kiểm một văn bản theo số hiệu, vd 31/2024/QH15")
    p.add_argument("--sample", nargs="?", type=int, const=30, default=None, metavar="N",
                   help="in thêm N chunk ngẫu nhiên (mặc định 30) cùng các Điều hay được trích để đối chiếu tay")
    p.add_argument("--seed", type=int, default=42, help="seed của mẫu ngẫu nhiên (mặc định 42, cố định để chạy lại ra đúng mẫu cũ)")
    p.add_argument("--out", type=Path, help="ghi thêm báo cáo ra file .md này (không có thì chỉ in màn hình)")
    p.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS, help="max_chars khi nạp (mặc định 2500)")
    args = p.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    from sqlalchemy.exc import SQLAlchemyError

    _load_env()
    try:
        from app.core.database import get_session

        db = get_session()
    except RuntimeError:
        print("Không đọc được database: chưa cấu hình RAG_DATABASE_URL trong .env.")
        return 1
    try:
        db.autoflush = False
        corpus = fetch_corpus(db, args.doc_number)
    except SQLAlchemyError as e:  # không in e: thông báo lỗi có thể chứa thông tin kết nối
        print(f"Không kết nối hoặc không đọc được database ({type(e).__name__}). Kiểm tra RAG_DATABASE_URL và mạng.")
        return 1
    finally:
        db.rollback()
        db.close()
    if not corpus:
        print(f"Không có văn bản nào{' với số hiệu ' + args.doc_number if args.doc_number else ''} trong kho.")
        return 1

    dirs = source_dirs()
    names = manifest_names(dirs)
    reports, pool = [], []
    for doc, chunks in corpus:
        path = find_source_file(doc, dirs, names)
        text = None
        if path is not None:
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                path = None
        reports.append(audit_document(doc, chunks, args.max_chars, source=(path, text)))
        pool += [{"doc_number": doc.doc_number, "title": doc.title, "dieu": (c.section_info or {}).get("dieu") if isinstance(c.section_info, dict) else None,
                  "section_info": c.section_info if isinstance(c.section_info, dict) else {}, "index": c.index, "content": c.content} for c in chunks]
    sample_lines = format_sample(pool, pick_sample(pool, args.sample, args.seed)) if args.sample is not None else None
    report = build_report(reports, sample_lines)
    print(report)
    if args.out:
        args.out.write_text(report, encoding="utf-8")
        print(f"Đã ghi báo cáo ra {args.out}")
    return 1 if any(r.verdict == ERROR for r in reports) else 0


if __name__ == "__main__":
    sys.exit(main())
