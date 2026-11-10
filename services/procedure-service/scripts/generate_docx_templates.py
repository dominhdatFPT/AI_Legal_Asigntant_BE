import os
import io
import pymupdf
import docx
from docx.shared import Inches
from PIL import Image

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BIZ_DIR = os.path.join(BASE_DIR, "Template các thủ tục kinh doanh")
LAND_DIR = os.path.join(BASE_DIR, "Template các thủ tục đất đai")

def convert_pdf_to_docx(pdf_path: str, docx_path: str):
    print(f"Converting PDF: {os.path.basename(pdf_path)} -> {os.path.basename(docx_path)}")
    doc_pdf = pymupdf.open(pdf_path)
    doc_word = docx.Document()
    
    # Configure A4 page
    section = doc_word.sections[0]
    section.page_width = Inches(8.27)
    section.page_height = Inches(11.69)
    section.top_margin = Inches(0.2)
    section.bottom_margin = Inches(0.2)
    section.left_margin = Inches(0.2)
    section.right_margin = Inches(0.2)

    for i, page in enumerate(doc_pdf):
        # 150 DPI provides sharp text while keeping file size reasonable
        pix = page.get_pixmap(dpi=150)
        img_data = pix.tobytes("png")
        if i > 0:
            doc_word.add_page_break()
        doc_word.add_picture(io.BytesIO(img_data), width=Inches(7.87))
        
    doc_word.save(docx_path)
    print(f"  Created {docx_path} ({os.path.getsize(docx_path):,} bytes)")

def convert_png_to_docx(png_path: str, docx_path: str):
    print(f"Converting PNG: {os.path.basename(png_path)} -> {os.path.basename(docx_path)}")
    doc_word = docx.Document()
    section = doc_word.sections[0]
    section.page_width = Inches(8.27)
    section.page_height = Inches(11.69)
    section.top_margin = Inches(0.2)
    section.bottom_margin = Inches(0.2)
    section.left_margin = Inches(0.2)
    section.right_margin = Inches(0.2)
    
    doc_word.add_picture(png_path, width=Inches(7.87))
    doc_word.save(docx_path)
    print(f"  Created {docx_path} ({os.path.getsize(docx_path):,} bytes)")

def main():
    targets = [
        # Business PDFs
        (BIZ_DIR, "Thủ tục đăng ký thành lập Công ty Cổ phần.pdf"),
        (BIZ_DIR, "Thủ tục đăng ký thành lập Công ty Hợp danh.pdf"),
        (BIZ_DIR, "Thủ tục đăng ký thành lập Công ty Trách nhiệm hữu hạn.pdf"),
        (BIZ_DIR, "Thủ tục đăng ký thành lập Doanh nghiệp tư nhân.pdf"),
        # Land PDFs & PNG
        (LAND_DIR, "Thủ tục chuyển mục đích sử dụng đất - Đơn đề nghị chuyển mục đích sử dụng đất.pdf"),
        (LAND_DIR, "Thủ tục tách thửa hoặc hợp thửa đất.pdf"),
        (LAND_DIR, "Thủ tục đăng ký biến động quyền sử dụng đất do chuyển nhượng.pdf"),
        (LAND_DIR, "Thủ tục đăng ký đất đai, cấp Giấy chứng nhận lần đầu.pdf"),
        (LAND_DIR, "Thủ tục giao đất, cho thuê đất - Đơn xin giao đất.png"),
    ]
    
    for folder, fname in targets:
        src = os.path.join(folder, fname)
        if not os.path.exists(src):
            print(f"Warning: File not found {src}")
            continue
        base_name, ext = os.path.splitext(fname)
        dest = os.path.join(folder, f"{base_name}.docx")
        if ext.lower() == ".pdf":
            convert_pdf_to_docx(src, dest)
        elif ext.lower() == ".png":
            convert_png_to_docx(src, dest)

if __name__ == "__main__":
    main()
