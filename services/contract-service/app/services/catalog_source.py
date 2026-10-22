"""Server-reviewed system DOCX snapshots, separate from owner-bound C8 imports."""
from datetime import date
from io import BytesIO
import re
from uuid import UUID
from zipfile import ZipFile, ZIP_DEFLATED
from copy import deepcopy

from lxml import etree
import pymupdf as fitz

from app.core.errors import ContractError
from app.services.personal_renderer import PersonalRenderer, inspect_docx, validate_pdf, rectangle, W


def catalog_manifest(version):
    manifest=(version.get("extraction_snapshot") or {}).get("catalog_import")
    if not isinstance(manifest,dict) or type(manifest.get("schema_version")) is not int or manifest["schema_version"]!=1:
        return None
    return manifest if version.get("kind")=="system" and version.get("source_format") in {"docx","pdf"} else None


def source_key_valid(version):
    try:
        template=str(UUID(str(version["template_id"])))
        revision=str(UUID(str(version["id"])))
        suffix=version["source_format"]
        if suffix not in {"docx","pdf"}:
            return False
        return bool(re.fullmatch(r"snapshots/"+template+"/"+revision+r"/[0-9a-f-]{36}\."+suffix,version["snapshot_storage_key"]))
    except (KeyError,TypeError,ValueError):
        return False


def validate_catalog(version, values, *, final):
    manifest=catalog_manifest(version)
    if not manifest or not source_key_valid(version):
        raise ContractError("SOURCE_RENDERER_NOT_SUPPORTED","Snapshot kho mẫu chưa hợp lệ.",422)
    if manifest.get("publication_blockers") or manifest.get("visual_qa_passed") is not True:
        raise ContractError("TEMPLATE_REVIEW_REQUIRED","Mẫu chưa được duyệt nội dung/bố cục.",422)
    for name,choices in manifest.get("choices",{}).items():
        if values.get(name) is not None and values[name] not in choices:
            raise ContractError("VALIDATION_ERROR","Giá trị lựa chọn không hợp lệ.",422,details=[{"field":"field_values."+name,"message":"Chọn giá trị cho phép của mẫu."}])
    if manifest.get("code")=="business-premises-lease":
        # Constraints apply to final export, while preview supports partial drafts.
        if not final:
            return
        def fail():
            raise ContractError("VALIDATION_ERROR","Kiểm tra ngày, tiền, thời hạn và số bản hợp đồng.",422)
        try:
            for name,low,high in [("f01",1,31),("f02",1,12),("f03",1900,9999),("f22",1,31),("f24",2,100),("f25",1,50)]:
                value=values[name]
                if type(value) not in {int,float} or not float(value).is_integer() or not low<=value<=high:
                    fail()
            signed=date(int(values["f03"]),int(values["f02"]),int(values["f01"]))
            start,end=date.fromisoformat(values["f18"]),date.fromisoformat(values["f19"])
            if end<start or date.fromisoformat(values["f07"])>signed or values["f24"]!=2*values["f25"]:
                fail()
            if not re.fullmatch(r"[1-9][0-9]{0,3}",values["f17"]):
                fail()
            for name in ("f20","f23"):
                value=values[name]
                if type(value) not in {int,float} or not 0<=value<=10**15 or not float(value).is_integer():
                    fail()
        except (KeyError,TypeError,ValueError,OverflowError):
            fail()


class CatalogSourceRenderer:
    """Native Word reflow is allowed; no reconstruction from flattened text.

    Runtime checks guard file type, mapped-token coverage, input size and PDF pages.
    Human QA is still required for each newly registered system source.
    """
    def prepare_pdf(self,data,fields):
        """Clear only reviewer-mapped dotted blanks; never erase printed labels."""
        prepared=deepcopy(fields)
        with validate_pdf(data) as doc:
            for field in prepared:
                for placement in field.get("placements",[]):
                    if placement.get("kind")=="pdf_box":
                        continue
                    if placement.get("kind")!="pdf_dots" or type(placement.get("page")) is not int or not 1<=placement["page"]<=len(doc):
                        raise ContractError("INVALID_MAPPING","Vị trí điền PDF không hợp lệ.",422)
                    page=doc[placement["page"]-1]
                    rect=rectangle(placement,page)
                    if rect.is_empty or not page.rect.contains(rect):
                        raise ContractError("INVALID_MAPPING","Vị trí điền PDF không hợp lệ.",422)
                    def touches_drawing(item):
                        drawn=fitz.Rect(item["rect"])
                        stroke=max(1,float(item.get("width",0)))
                        return fitz.Rect(drawn.x0-stroke,drawn.y0-stroke,drawn.x1+stroke,drawn.y1+stroke).intersects(rect)
                    if any(fitz.Rect(item["bbox"]).intersects(rect) for item in page.get_image_info()) or any(
                        touches_drawing(item) for item in page.get_drawings()
                    ):
                        raise ContractError("INVALID_MAPPING","Ô điền PDF chạm hình hoặc đường in sẵn.",422)
                    hit=0
                    for block in page.get_text("rawdict")["blocks"]:
                        for line in block.get("lines",[]):
                            for span in line["spans"]:
                                for char in span["chars"]:
                                    if fitz.Rect(char["bbox"]).intersects(rect):
                                        if char["c"] not in ".…·_ ":
                                            raise ContractError("INVALID_MAPPING","Ô điền PDF chạm nội dung in sẵn.",422)
                                        if char["c"]!=" ":
                                            hit+=1
                    if not hit:
                        raise ContractError("INVALID_MAPPING","Ô điền PDF không nằm trên dòng chấm.",422)
                    page.draw_rect(rect,color=None,fill=(1,1,1),overlay=True)
                    placement["kind"]="pdf_box"
            return doc.tobytes(garbage=4,deflate=True),prepared

    def render(self,data,fields,values,format,preview=False):
        if data.startswith(b"%PDF-"):
            if format!="pdf":
                raise ContractError("SOURCE_FORMAT_NOT_SUPPORTED","Mẫu PDF chỉ xuất PDF giữ nền, không tự chuyển sang Word.",422)
            renderer=PersonalRenderer()
            prepared,mapped=self.prepare_pdf(data,fields)
            renderer.validate_mapping(mapped,{"anchors":{}},"pdf",prepared)
            return renderer.render(prepared,"pdf",mapped,values,"pdf",preview)
        parts=inspect_docx(data)
        expected=set()
        for part,root in parts.items():
            for index,p in enumerate(root.iter(W+"p")):
                text="".join(t.text or "" for t in p.iter(W+"t"))
                for match in re.finditer(r"\{\{([a-z][a-z0-9_]*)\}\}",text):
                    expected.add((f"{part}:{index}:{match.start()}:{match.end()}",match[1]))
        supplied=[]
        for field in fields:
            for placement in field.get("placements",[]):
                if placement.get("kind")!="docx_anchor":
                    raise ContractError("INVALID_INPUT","Mapping kho mẫu không hợp lệ.",422)
                supplied.append((placement["locator"],field["name"]))
        if set(supplied)!=expected or len(supplied)!=len(expected):
            raise ContractError("INVALID_INPUT","Mapping kho mẫu không khớp nguồn.",422)
        renderer=PersonalRenderer()
        display=dict(values)
        for field in fields:
            value=display.get(field["name"])
            if field["type"]=="date" and isinstance(value,str) and value:
                try:
                    display[field["name"]]=date.fromisoformat(value).strftime("%d/%m/%Y")
                except ValueError:
                    raise ContractError("INVALID_INPUT","Ngày điền không hợp lệ.",422) from None
            elif field["type"]=="number" and type(value) in {int,float} and float(value).is_integer():
                display[field["name"]]=(f"{int(value):,}".replace(",",".") if "đồng" in field["label"].lower() else str(int(value)))
        filled=renderer.fill_docx(data,fields,display)
        if preview:
            with ZipFile(BytesIO(filled)) as archive:
                root=etree.fromstring(archive.read("word/document.xml"))
                p=etree.Element(W+"p");r=etree.SubElement(p,W+"r")
                props=etree.SubElement(r,W+"rPr");etree.SubElement(props,W+"b")
                etree.SubElement(r,W+"t").text="BẢN XEM TRƯỚC - CHƯA HOÀN THIỆN"
                root.find(W+"body").insert(0,p)
                out=BytesIO()
                with ZipFile(out,"w",ZIP_DEFLATED) as target:
                    for info in archive.infolist():
                        target.writestr(deepcopy(info),etree.tostring(root,xml_declaration=True,encoding="UTF-8",standalone=True) if info.filename=="word/document.xml" else archive.read(info.filename))
                filled=out.getvalue()
        pdf=renderer.convert(filled)
        return filled if format=="docx" else pdf
