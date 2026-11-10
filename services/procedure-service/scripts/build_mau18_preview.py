"""Create a display-only copy of the reference PDF without source-page banners."""
from io import BytesIO
from pathlib import Path
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen.canvas import Canvas

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT.parent / 'contract-service/templates/reference-catalog/58eeab1f2bae9b1f.pdf'
TARGET = ROOT / 'database/mau18-reference-clean.pdf'
reader = PdfReader(str(SOURCE))
writer = PdfWriter()
for index, page in enumerate(reader.pages):
    width, height = float(page.mediabox.width), float(page.mediabox.height)
    if abs(width - 595.32) > 1 or abs(height - 842.04) > 1:
        raise ValueError('Unexpected source page dimensions')
    memory = BytesIO()
    canvas = Canvas(memory, pagesize=(width, height))
    canvas.setFillColorRGB(1, 1, 1)
    # Banner and page-number in the supplied PDF, above the national heading.
    if index == 0:
        canvas.rect(0, 750, width, 75, stroke=0, fill=1)
    elif index == 1:
        canvas.rect(0, 792, width, 33, stroke=0, fill=1)
    # Source fanpage watermark; the official form does not use this footer.
    canvas.rect(0, 6, width, 22, stroke=0, fill=1)
    canvas.save()
    memory.seek(0)
    page.merge_page(PdfReader(memory).pages[0])
    writer.add_page(page)
with TARGET.open('wb') as stream:
    writer.write(stream)
print(TARGET)
