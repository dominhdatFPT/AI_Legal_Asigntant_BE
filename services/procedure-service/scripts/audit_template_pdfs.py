"""Script audit và báo cáo kiểm tra chất lượng font tiếng Việt của các file PDF biểu mẫu trong cơ sở dữ liệu.

Chế độ hoạt động:
- Read-only an toàn 100%: Quét và chẩn đoán toàn diện, không tự ý sửa đổi hay xóa dữ liệu.
- Đối chiếu chéo Word-PDF: Khi bản ghi có cả file Word và PDF, phân tích sự tương thích ký tự tiếng Việt Unicode
  để phát hiện tài liệu bị rớt font bảng mã (WinAnsi/Type1) mà không lo xóa nhầm tài liệu hợp lệ.
- Xuất file kiểm duyệt (--export-defective <DIR>): Xuất các file bị lỗi hoặc nghi vấn ra thư mục để kiểm tra trực tiếp.
"""
import argparse
import io
import json
import os
import re
import sys
from pathlib import Path

import psycopg
from docx import Document
from pypdf import PdfReader

VIETNAMESE_PATTERN = re.compile(
    r"[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]",
    re.IGNORECASE,
)
VN_NON_WINANSI = re.compile(
    r"[ạảầấậẩẫăằắặẳẵẹẻẽềếệểễịỉĩọỏồốộổỗơờớợởỡụủũưừứựửữỳỵỷỹđ]",
    re.IGNORECASE,
)
IN_WORD_DOT = re.compile(r"[a-zA-Z0-9\u00C0-\u024F]·|·[a-zA-Z0-9\u00C0-\u024F]|\uFFFD")


def resolve_db_url(cli_url: str | None = None) -> str:
    if cli_url:
        return cli_url
    url = os.getenv("PROCEDURE_DATABASE_URL")
    if url:
        return url
    for p in [Path(__file__).resolve().parents[2] / ".env", Path(__file__).resolve().parents[3] / ".env"]:
        if p.is_file():
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("PROCEDURE_DATABASE_URL="):
                    url = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
        if url:
            break
    return url or ""


def audit_pdf_bytes(pdf_data: bytes, word_data: bytes | None = None) -> dict:
    if not pdf_data or not pdf_data.startswith(b"%PDF-"):
        return {
            "valid_pdf": False,
            "status": "CORRUPTED_PDF",
            "detail": "Tệp không bắt đầu bằng header %PDF-",
            "page_count": 0,
            "text_len": 0,
            "dot_count": 0,
            "in_word_dots": 0,
            "pdf_non_winansi": 0,
            "word_non_winansi": 0,
            "fonts": [],
        }

    try:
        reader = PdfReader(io.BytesIO(pdf_data))
        page_count = len(reader.pages)
        if page_count == 0:
            return {
                "valid_pdf": False,
                "status": "CORRUPTED_PDF",
                "detail": "Tệp PDF không chứa trang nào",
                "page_count": 0,
                "text_len": 0,
                "dot_count": 0,
                "in_word_dots": 0,
                "pdf_non_winansi": 0,
                "word_non_winansi": 0,
                "fonts": [],
            }

        full_text = ""
        fonts = set()
        for page in reader.pages:
            extracted = page.extract_text() or ""
            full_text += extracted + "\n"
            if "/Resources" in page and "/Font" in page["/Resources"]:
                font_dict = page["/Resources"]["/Font"]
                for f_key in font_dict:
                    try:
                        f_obj = font_dict[f_key]
                        base_font = f_obj.get("/BaseFont", "")
                        if base_font:
                            fonts.add(str(base_font))
                    except Exception:
                        pass
    except Exception as exc:
        return {
            "valid_pdf": False,
            "status": "CORRUPTED_PDF",
            "detail": f"Lỗi đọc cấu trúc PDF: {exc}",
            "page_count": 0,
            "text_len": 0,
            "dot_count": 0,
            "in_word_dots": 0,
            "pdf_non_winansi": 0,
            "word_non_winansi": 0,
            "fonts": [],
        }

    text_len = len(full_text.strip())
    dot_count = full_text.count("·") + full_text.count("\ufffd")
    in_word_dots = len(IN_WORD_DOT.findall(full_text))
    pdf_non_winansi = len(VN_NON_WINANSI.findall(full_text))

    # Đọc thông tin từ file Word đối chiếu (nếu có)
    word_non_winansi = 0
    if word_data:
        try:
            doc = Document(io.BytesIO(word_data))
            w_paras = [p.text for p in doc.paragraphs if p.text]
            w_cells = [c.text for t in doc.tables for r in t.rows for c in r.cells if c.text]
            w_text = "\n".join(w_paras + w_cells)
            word_non_winansi = len(VN_NON_WINANSI.findall(w_text))
        except Exception:
            pass

    # Phân loại trạng thái với độ tin cậy cao:
    if text_len < 50:
        status = "SCANNED_DOCUMENT"
        detail = "Tài liệu scan hoặc ảnh không có text layer"
    elif word_data and word_non_winansi >= 10 and pdf_non_winansi <= 2 and (dot_count >= 8 or in_word_dots >= 4):
        status = "CONFIRMED_DEFECTIVE_WITH_WORD"
        detail = (
            f"Đối chiếu Word xác nhận lỗi: Word có {word_non_winansi} ký tự tiếng Việt chuẩn "
            f"nhưng PDF chỉ trích xuất được {pdf_non_winansi} và chứa {dot_count} dấu thay thế ({in_word_dots} dấu trong từ)."
        )
    elif in_word_dots >= 4 and pdf_non_winansi <= 2:
        status = "CONFIRMED_DEFECTIVE_NO_WORD"
        detail = f"Phát hiện {in_word_dots} ký tự lỗi font nằm ngay trong từ và thiếu ký tự tiếng Việt Unicode chuẩn."
    elif dot_count >= 10 and (dot_count / text_len) > 0.02 and pdf_non_winansi <= 2:
        status = "SUSPICIOUS_DEFECTIVE"
        detail = f"Tỷ lệ ký tự thay thế cao ({dot_count}/{text_len}) nhưng thiếu ký tự tiếng Việt Unicode."
    else:
        status = "VERIFIED_CLEAN"
        detail = "Văn bản PDF toàn vẹn, font tiếng Việt Unicode đầy đủ."

    return {
        "valid_pdf": True,
        "status": status,
        "detail": detail,
        "page_count": page_count,
        "text_len": text_len,
        "dot_count": dot_count,
        "in_word_dots": in_word_dots,
        "pdf_non_winansi": pdf_non_winansi,
        "word_non_winansi": word_non_winansi,
        "dot_ratio": round((dot_count / text_len) if text_len > 0 else 0.0, 4),
        "fonts": sorted(list(fonts)),
    }


AUDIT_BATCH_BYTES = 8 * 1024 * 1024
AUDIT_BATCH_IDS = 100


def _audit_rows(cur):
    """Fetch metadata cheaply, then load document bytes in bounded ID batches."""
    batch = []
    batch_bytes = 0

    def fetch_batch(rows):
        cur.execute(
            "SELECT id, reference_pdf, octet_length(reference_pdf), word_data "
            "FROM public.custom_form_templates WHERE id = ANY(%s) "
            "AND reference_pdf IS NOT NULL AND deleted_at IS NULL",
            ([row[0] for row in rows],),
        )
        documents = {row[0]: row[1:] for row in cur.fetchall()}
        for row in rows:
            if row[0] in documents:
                yield row[:8], documents[row[0]]

    offset = 0
    while True:
        cur.execute("""
            SELECT id, name, domain, status, word_filename, reference_pdf_filename,
                   reference_pdf IS NOT NULL, word_data IS NOT NULL,
                   COALESCE(octet_length(reference_pdf), 0) + COALESCE(octet_length(word_data), 0)
            FROM public.custom_form_templates
            WHERE reference_pdf IS NOT NULL AND deleted_at IS NULL
            ORDER BY updated_at DESC, id
            LIMIT %s OFFSET %s
        """, (AUDIT_BATCH_IDS, offset))
        metadata = cur.fetchall()
        if not metadata:
            break
        offset += len(metadata)
        for row in metadata:
            size = row[8]
            if batch and (len(batch) >= AUDIT_BATCH_IDS or batch_bytes + size > AUDIT_BATCH_BYTES):
                yield from fetch_batch(batch)
                batch, batch_bytes = [], 0
            batch.append(row)
            batch_bytes += size
    if batch:
        yield from fetch_batch(batch)


def run_audit(db_url: str, export_dir: str | None = None) -> list[dict]:
    clean_url = db_url.replace("postgresql+psycopg://", "postgresql://", 1)
    if "sslmode" not in clean_url and "localhost" not in clean_url and "127.0.0.1" not in clean_url:
        clean_url += ("&" if "?" in clean_url else "?") + "sslmode=require"

    records_report = []
    export_path = Path(export_dir).resolve() if export_dir else None
    if export_path:
        export_path.mkdir(parents=True, exist_ok=True)

    with psycopg.connect(clean_url, connect_timeout=15) as conn:
        with conn.cursor() as cur:
            print("=" * 80)
            print("KIỂM TRA CHẤT LƯỢNG PDF TRONG: custom_form_templates")
            print("=" * 80)

            for r, document in _audit_rows(cur):
                tid, name, domain, status, w_fn, pdf_fn, has_pdf, has_word = r
                pdf_data, pdf_size, word_data = document
                analysis = audit_pdf_bytes(pdf_data, word_data=word_data)

                entry = {
                    "table": "custom_form_templates",
                    "id": str(tid),
                    "name": name,
                    "domain": domain,
                    "status": status,
                    "pdf_filename": pdf_fn,
                    "pdf_size": pdf_size,
                    "has_word": bool(has_word),
                    "analysis": analysis,
                }
                records_report.append(entry)

                st = analysis["status"]
                flag = "🔴" if "DEFECTIVE" in st else ("⚠️" if "CORRUPTED" in st else ("⚪" if st == "SCANNED_DOCUMENT" else "🟢"))
                print(f"[{flag}] {name[:45]:<45} | Size: {pdf_size:>7} | Status: {st}")
                print(f"     ID: {tid} | Dots: {analysis['dot_count']} (In-word: {analysis['in_word_dots']}) | PDF VN: {analysis['pdf_non_winansi']} | Word VN: {analysis['word_non_winansi']}")
                if analysis.get("detail"):
                    print(f"     ℹ️ {analysis['detail']}")

                if export_path and ("DEFECTIVE" in st or "CORRUPTED" in st):
                    safe_tid = str(tid)[:8]
                    pdf_out = export_path / f"{safe_tid}_{pdf_fn or 'document.pdf'}"
                    pdf_out.write_bytes(pdf_data)
                    print(f"     💾 Đã xuất PDF lỗi kiểm tra: {pdf_out}")
                    if word_data:
                        w_out = export_path / f"{safe_tid}_{w_fn or 'document.docx'}"
                        w_out.write_bytes(word_data)
                        print(f"     💾 Đã xuất file Word gốc: {w_out}")

    return records_report


def main():
    parser = argparse.ArgumentParser(description="Audit an toàn chất lượng font PDF biểu mẫu trong Database")
    parser.add_argument("--db-url", type=str, default="", help="PostgreSQL Database URL")
    parser.add_argument("--fix", action="store_true", help="(Đã hủy bỏ) Không còn tự động xóa reference_pdf để tránh mất dữ liệu")
    parser.add_argument("--export-defective", type=str, default=None, help="Thư mục xuất các file lỗi để kiểm tra thủ công")
    parser.add_argument("--json", action="store_true", help="Xuất báo cáo định dạng JSON")
    args = parser.parse_args()

    if args.fix:
        print("⚠️ LƯU Ý BẢO VỆ DỮ LIỆU:")
        print("Tính năng tự động xóa reference_pdf=NULL bằng cờ --fix đã bị loại bỏ.")
        print("Lý do: Fallback ReportLab có thể làm sai lệch bố cục biểu mẫu hành chính.")
        print("Giải pháp khuyến nghị: Tải lại file PDF chuẩn (xuất có Embed Unicode Fonts) qua API quản trị.\n")

    db_url = resolve_db_url(args.db_url)
    if not db_url:
        print("Lỗi: Không tìm thấy PROCEDURE_DATABASE_URL trong biến môi trường hoặc file .env")
        sys.exit(1)

    report = run_audit(db_url, export_dir=args.export_defective)

    defective_count = sum(1 for r in report if "DEFECTIVE" in r["analysis"]["status"])
    corrupted_count = sum(1 for r in report if "CORRUPTED" in r["analysis"]["status"])
    clean_count = sum(1 for r in report if r["analysis"]["status"] == "VERIFIED_CLEAN")
    scanned_count = sum(1 for r in report if r["analysis"]["status"] == "SCANNED_DOCUMENT")

    print("\n" + "=" * 80)
    print(f"TỔNG KẾT RÀ SOÁT: {len(report)} tệp PDF")
    print(f"  🟢 Hợp lệ (VERIFIED_CLEAN):               {clean_count}")
    print(f"  ⚪ Ảnh quét (SCANNED_DOCUMENT):            {scanned_count}")
    print(f"  ⚠️ Hỏng cấu trúc (CORRUPTED_PDF):          {corrupted_count}")
    print(f"  🔴 Lỗi font (CONFIRMED_DEFECTIVE):         {defective_count}")
    print("=" * 80)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
