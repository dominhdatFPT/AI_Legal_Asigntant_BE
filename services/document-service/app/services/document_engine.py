"""Private subprocess protocol; no network, image/temp files or content logging."""
import base64
import contextlib
import io
import json
import math
import os
from pathlib import Path
import socket
import sys
import zipfile

from defusedxml import ElementTree as ET
from PIL import Image, ImageOps
from pypdf import PdfReader
from app.core.errors import DocumentError
from app.services.file_validation import DOCX, MAX_FILE, inspect_file

NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
MAX_TEXT = 1_000_000


def fail(code="UNSAFE_FILE"):
    raise DocumentError(code, "File không đạt kiểm tra xử lý.", 422)


def sanitize(content, row):
    inspect_file(row["filename"], row["media_type"], content, row["purpose"], row["document_type"])
    if row["media_type"].startswith("image/"):
        with Image.open(io.BytesIO(content)) as image:
            if getattr(image, "n_frames", 1) != 1:
                fail()
            image.load()
            clean = ImageOps.exif_transpose(image).convert("RGB")
            result = io.BytesIO()
            # Fresh pixels only: strip EXIF/GPS/metadata/trailing payload.
            Image.frombytes("RGB", clean.size, clean.tobytes()).save(result,
                format="JPEG" if row["media_type"] == "image/jpeg" else "PNG")
            output = result.getvalue()
            if len(output) > MAX_FILE:
                fail("EXTRACTION_TOO_LARGE")
            return output
    if row["media_type"] == DOCX:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            for entry in archive.infolist():
                name = entry.filename.lower()
                if any(marker in name for marker in ("embeddings/", "activex/", "vbaproject", "customui/")):
                    fail()
                if name.endswith((".xml", ".rels")):
                    root = ET.fromstring(archive.read(entry))
                    if name.endswith(".rels") and any(node.attrib.get("TargetMode", "").lower() == "external" for node in root.iter()):
                        fail()
                    if any(node.tag in {NS+"object", NS+"altChunk", NS+"fldChar", NS+"instrText"} for node in root.iter()):
                        fail()
        return content
    reader = PdfReader(io.BytesIO(content), strict=True)
    # Traverse resolved object graph (not raw byte substrings), with hard bounds.
    pending, seen = [reader.trailer], set()
    forbidden = {"/JavaScript", "/JS", "/Launch", "/EmbeddedFiles", "/OpenAction", "/AA", "/RichMedia",
                 "/XFA", "/SubmitForm", "/ImportData", "/GoToR", "/Sound", "/Movie"}
    for _ in range(100_000):
        if not pending:
            return content
        item = pending.pop()
        item = item.get_object() if hasattr(item, "get_object") else item
        if id(item) in seen:
            continue
        seen.add(id(item))
        if isinstance(item, dict):
            if forbidden & set(item) or item.get("/S") in forbidden:
                fail()
            pending.extend(item.values())
        elif isinstance(item, (list, tuple)):
            pending.extend(item)
    fail()


def bbox(points, width, height):
    if not all(math.isfinite(v) for point in points for v in point) or width <= 0 or height <= 0:
        fail("EXTRACTION_FAILED")
    x0=max(0, min(1, min(p[0] for p in points)/width)); x1=max(0, min(1, max(p[0] for p in points)/width))
    y0=max(0, min(1, min(p[1] for p in points)/height)); y1=max(0, min(1, max(p[1] for p in points)/height))
    if x1 <= x0 or y1 <= y0:
        return None
    return {"x": x0, "y": y0, "width": x1-x0, "height": y1-y0}


class PaddleEngine:
    def __init__(self):
        root = Path(os.environ.get("DOCUMENT_PADDLE_MODEL_DIR", "/opt/document-ocr"))
        if not root.is_absolute() or not all((root/name/"inference.yml").is_file() for name in ("det", "rec")):
            fail("OCR_MODEL_UNAVAILABLE")
        from paddleocr import PaddleOCR
        self.engine = PaddleOCR(text_detection_model_name="PP-OCRv5_mobile_det", text_detection_model_dir=str(root/"det"),
            text_recognition_model_name="latin_PP-OCRv5_mobile_rec", text_recognition_model_dir=str(root/"rec"),
            device="cpu", enable_mkldnn=False, cpu_threads=2, use_doc_orientation_classify=False,
            use_doc_unwarping=False, use_textline_orientation=False)

    def blocks(self, image):
        import numpy as np
        image = image.copy(); image.thumbnail((2400, 2400))
        values = list(self.engine.predict(np.asarray(image)[:, :, ::-1]))
        if len(values) != 1:
            fail("EXTRACTION_FAILED")
        data = values[0].json["res"]
        texts, scores, polys = data["rec_texts"], data["rec_scores"], data["rec_polys"]
        if len(texts) > 5000 or len(texts) != len(scores) or len(texts) != len(polys):
            fail("EXTRACTION_TOO_LARGE")
        blocks = []
        for text, score, polygon in zip(texts, scores, polys):
            if not text.strip():
                continue
            box = bbox(polygon, image.width, image.height)
            if len(text)>10000 or not math.isfinite(score) or not 0 <= score <= 1 or box is None:
                fail("EXTRACTION_TOO_LARGE")
            blocks.append({"text": text, "confidence": float(score), "bbox": box})
        return blocks


def pdf_points(rect, crop, rotation):
    left, bottom, right, top = rect; x0,y0,x1,y1 = crop; w,h = x1-x0,y1-y0
    def rotate(x,y):
        x,y=x-x0,y-y0
        return {0:(x,h-y),90:(y,x),180:(w-x,y),270:(h-y,w-x)}[rotation]
    return [rotate(x,y) for x,y in ((left,bottom),(left,top),(right,top),(right,bottom))]


def extract(content, row, ocr_factory=PaddleEngine):
    pages, pieces, methods, warnings = [], [], set(), []
    engine = None
    def ocr(image):
        nonlocal engine
        if engine is None:
            engine = ocr_factory()
        return engine.blocks(image)
    if row["media_type"] == DOCX:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            root = ET.fromstring(archive.read("word/document.xml"))
            for paragraph in root.iter(NS+"p"):
                text = "".join(node.text or "" if node.tag==NS+"t" else "\t" if node.tag==NS+"tab" else "\n"
                    for node in paragraph.iter() if node.tag in {NS+"t",NS+"tab",NS+"br",NS+"cr"})
                pieces.append(text)
            warnings.append("DOCX_TEXT_REFERENCE_ONLY")
            if any(name.startswith("word/media/") for name in archive.namelist()):
                warnings.append("DOCX_EMBEDDED_IMAGES_NOT_OCR")
        kind = "docx"
    elif row["media_type"].startswith("image/"):
        with Image.open(io.BytesIO(content)) as image:
            blocks=ocr(image.convert("RGB"))
            pages.append({"page":1,"width_pt":float(image.width),"height_pt":float(image.height),
                          "rotation_degrees":0,"method":"ocr","blocks":blocks})
            pieces.append("\n".join(b["text"] for b in blocks))
        kind="image"
    else:
        import pypdfium2 as pdfium
        with pdfium.PdfDocument(content) as pdf:
            if not 1 <= len(pdf) <= 100:
                fail("EXTRACTION_TOO_LARGE")
            for index in range(len(pdf)):
                with contextlib.closing(pdf[index]) as page:
                    width,height=page.get_size(); rotation=page.get_rotation(); crop=page.get_bbox()
                    if not all(math.isfinite(v) and 0<v<=14400 for v in (width,height)):
                        fail("EXTRACTION_TOO_LARGE")
                    with contextlib.closing(page.get_textpage()) as textpage:
                        text=textpage.get_text_range()
                        blocks=[]
                        if text.strip():
                            count=textpage.count_rects()
                            if count>5000 or len(text)>MAX_TEXT:
                                fail("EXTRACTION_TOO_LARGE")
                            for pos in range(count):
                                rect=textpage.get_rect(pos); value=textpage.get_text_bounded(*rect).strip()
                                box=bbox(pdf_points(rect,crop,rotation),width,height)
                                if value and box:
                                    if len(value)>10000: fail("EXTRACTION_TOO_LARGE")
                                    blocks.append({"text":value,"bbox":box,"confidence":None})
                            method="text"
                            # Pages with text plus large raster content may have an
                            # unread scan region; keep native text, disclose limitation.
                            if any(True for _ in page.get_objects(filter=[pdfium.raw.FPDF_PAGEOBJ_IMAGE])):
                                warnings.append("PDF_IMAGE_REGION_NOT_OCR_ON_TEXT_PAGE")
                        else:
                            scale=min(2.0,2400/max(width,height))
                            with contextlib.closing(page.render(scale=scale)) as bitmap:
                                blocks=ocr(bitmap.to_pil().convert("RGB"))
                            text="\n".join(b["text"] for b in blocks); method="ocr"
                    pages.append({"page":index+1,"width_pt":width,"height_pt":height,
                                  "rotation_degrees":rotation,"method":method,"blocks":blocks})
                    pieces.append(text); methods.add(method)
                    if sum(map(len,pieces))>MAX_TEXT: fail("EXTRACTION_TOO_LARGE")
        kind="pdf_text" if methods=={"text"} else "pdf_scan" if methods=={"ocr"} else "pdf_mixed"
    text="\n".join(pieces)
    if len(text)>MAX_TEXT: fail("EXTRACTION_TOO_LARGE")
    warnings=list(dict.fromkeys(warnings))
    if engine is not None: warnings.append("OCR_REVIEW_REQUIRED")
    # Generic documents are text/layout only: no unreviewed inferred identity,
    # phone/email, amounts or Contract field assignments.
    result={"schema_version":"1.0","source_kind":kind,"text":text,"pages":pages,
            "extracted_fields":[],"warnings":warnings,"engine_version":"document-v1/pdfium5.12.1/paddleocr3.7.0-v5-latin"}
    if len(json.dumps(result,ensure_ascii=False).encode())>16*1024*1024-8192:
        fail("EXTRACTION_TOO_LARGE")
    return result


def main():
    output = os.fdopen(os.dup(1), "wb")
    with open(os.devnull,"wb") as native_quiet:
        os.dup2(native_quiet.fileno(),1); os.dup2(native_quiet.fileno(),2)
    # Library logs are discarded, never returned to the parent as protocol/PII.
    with open(os.devnull,"w") as quiet, contextlib.redirect_stdout(quiet), contextlib.redirect_stderr(quiet):
        try:
            if os.name=="posix":
                import resource
                resource.setrlimit(resource.RLIMIT_CPU,(240,240))
                resource.setrlimit(resource.RLIMIT_AS,(8*1024**3,8*1024**3))
            def no_network(*args,**kwargs): raise OSError("Network disabled in parser")
            socket.socket.connect=no_network; socket.socket.connect_ex=no_network; socket.create_connection=no_network
            row=json.loads(sys.stdin.buffer.read(14*1024*1024))
            content=base64.b64decode(row.pop("content"),validate=True)
            if not 1<=len(content)<=MAX_FILE: fail("INVALID_FILE")
            if sys.argv[1]=="sanitize":
                result={"content":base64.b64encode(sanitize(content,row)).decode()}
            elif sys.argv[1]=="extract":
                result=extract(content,row)
            else: fail("INVALID_FILE")
        except DocumentError as exc:
            result={"error":exc.code}
        except Exception:
            result={"error":"EXTRACTION_FAILED"}
    output.write(json.dumps(result,ensure_ascii=False).encode()); output.flush()


if __name__=="__main__": main()
