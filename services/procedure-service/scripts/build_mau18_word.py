"""Build a Word pilot from the locally packaged Mẫu 18 reference PDF."""
from pathlib import Path
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Cm, Pt
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT.parent / 'contract-service/templates/reference-catalog/58eeab1f2bae9b1f.pdf'
TARGET = ROOT / 'database/mau18-two-person.docx'


def make():
    doc = Document()
    section = doc.sections[0]
    section.top_margin, section.bottom_margin = Cm(1.7), Cm(1.7)
    section.left_margin, section.right_margin = Cm(2), Cm(2)
    normal = doc.styles['Normal']
    normal.font.name, normal.font.size = 'Times New Roman', Pt(11)
    normal.paragraph_format.space_after = Pt(3)

    def paragraph(text, *, center=False, bold=False, italic=False):
        p = doc.add_paragraph()
        if center:
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r = p.add_run(text)
        r.bold, r.italic = bold, italic
        return p

    paragraph('CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM', center=True, bold=True)
    paragraph('Độc lập - Tự do - Hạnh phúc', center=True, bold=True)
    paragraph('ĐƠN ĐĂNG KÝ BIẾN ĐỘNG ĐẤT ĐAI, TÀI SẢN GẮN LIỀN VỚI ĐẤT', center=True, bold=True)
    paragraph('Kính gửi: {{receiving_agency}} (1)', center=True)
    paragraph('I. Người sử dụng đất, chủ sở hữu tài sản gắn liền với đất:', bold=True)
    for text in ('- Tên (2): {{representative_name}}',
                 '- Giấy tờ nhân thân/pháp nhân (3): {{representative_id}}',
                 '- Địa chỉ (4): {{representative_address}}',
                 '- Mã số thuế (nếu có): {{representative_tax}}',
                 '- Điện thoại liên hệ (nếu có): {{phone}}    Hộp thư điện tử (nếu có): {{email}}'):
        paragraph(text)
    paragraph('(Trường hợp có nhiều đồng sử dụng, sở hữu thì kê khai thông tin một người đại diện; đồng thời lập danh sách theo Bảng 18a đính kèm)', italic=True)
    paragraph('II. Nội dung biến động (5):', bold=True)
    paragraph('{{change_content}}')
    paragraph('III. Thông tin về đối tượng được miễn, giảm nghĩa vụ tài chính về đất đai (nếu có) (6):', bold=True)
    paragraph('{{tax_exemption}}')
    paragraph('IV. Giấy tờ liên quan đến nội dung biến động nộp kèm theo đơn này gồm có (7):', bold=True)
    paragraph('{{attached_documents}}')
    paragraph('Cam đoan nội dung kê khai trên đơn là đúng sự thật và chịu trách nhiệm trước pháp luật.')
    p = paragraph('{{place}}, ngày {{day}} tháng {{month}} năm {{year}}\nNgười viết đơn\n(Ký, ghi rõ họ tên và đóng dấu nếu có)')
    p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    doc.add_page_break()
    paragraph('Hướng dẫn kê khai đơn:', bold=True)
    text = PdfReader(str(SOURCE)).pages[1].extract_text()
    text = text.replace('Fanpage QUY HOẠCH QUỐC GIA', '').strip()
    text = text.removeprefix('20').strip().removeprefix('Hướng dẫn kê khai đơn:').strip()
    for line in text.splitlines():
        if line.strip():
            paragraph(line.strip())
    doc.add_page_break()
    paragraph('Bảng 18a: Danh sách những người sử dụng đất, tài sản gắn liền với đất', center=True, bold=True)
    table = doc.add_table(rows=1, cols=7)
    table.style = 'Table Grid'
    headers = ['STT', 'Tên người sử dụng đất, chủ sở hữu tài sản gắn liền với đất, người nhận chuyển quyền', 'Địa chỉ', 'Mã số thuế (nếu có)', 'Giấy tờ pháp nhân/Số hộ chiếu/Số định danh cá nhân', 'Diện tích sử dụng/Tỷ lệ sở hữu (nếu có)', 'Ghi chú (*)']
    for cell, value in zip(table.rows[0].cells, headers):
        cell.text = value
    for i in (1, 2):
        cells = table.add_row().cells
        for cell, value in zip(cells, [str(i), f'{{{{person{i}_name}}}}', f'{{{{person{i}_address}}}}', f'{{{{person{i}_tax}}}}', f'{{{{person{i}_id}}}}', f'{{{{person{i}_share}}}}', f'{{{{person{i}_note}}}}']):
            cell.text = value
    for row in table.rows:
        for cell in row.cells:
            for p in cell.paragraphs:
                for run in p.runs:
                    run.font.size = Pt(8)
    paragraph('(*) Đối với trường hợp quyền sử dụng đất, quyền sở hữu tài sản gắn liền với đất là của vợ và chồng thì thể hiện thông tin “là vợ/chồng”; trường hợp của hộ gia đình thì ghi “là thành viên có chung QSDĐ của hộ.”.', italic=True)
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    doc.save(TARGET)
    print(TARGET)


if __name__ == '__main__':
    make()
