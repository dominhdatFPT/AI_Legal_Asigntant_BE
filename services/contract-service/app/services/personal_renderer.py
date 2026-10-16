"""File-preserving C8 renderer. Explicit DOCX markers or user-selected blank PDF boxes."""
from io import BytesIO
from pathlib import Path, PurePosixPath
import posixpath
import re
import shutil
import subprocess
from tempfile import TemporaryDirectory
import zipfile

from defusedxml import ElementTree as SafeXml
from lxml import etree
import pymupdf as fitz
from pypdf import PdfReader

from app.core.errors import ContractError
from app.services.contract_renderer import plain

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
MAX_SOURCE = 9437184
FONT = "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf"


def invalid(code="UNSAFE_FILE"):
    return ContractError(code, "File hoặc vị trí điền chưa được hỗ trợ; hãy kiểm tra mẫu.", 422)


def inspect_docx(data):
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            infos = archive.infolist()
            names = [i.filename for i in infos]
            if (len(names)>2000 or len(set(names))!=len(names) or sum(i.file_size for i in infos)>32*1024*1024
                or any(i.flag_bits & 1 for i in infos)
                or any(PurePosixPath(n).is_absolute() or ".." in PurePosixPath(n).parts or "\\" in n for n in names)
                or not {"[Content_Types].xml","word/document.xml"}.issubset(names)
                or any(any(s in n.lower() for s in ("vba","embedding","activex")) for n in names)):
                raise invalid()
            parts = {}
            for name in names:
                if name.endswith((".xml", ".rels")):
                    raw = archive.read(name)
                    root = SafeXml.fromstring(raw)
                    if any("macroenabled" in v.lower() for node in root.iter() for v in node.attrib.values()):
                        raise invalid()
                    if name.endswith(".rels") and any(node.get("TargetMode", "").lower()=="external" for node in root):
                        raise invalid()
                    if name.endswith(".rels"):
                        part_dir=str(PurePosixPath(name).parent.parent)
                        for node in root:
                            target=node.get("Target","")
                            resolved=posixpath.normpath(posixpath.join(part_dir,target))
                            if ":" in target or "\\" in target or target.startswith("/") or resolved==".." or resolved.startswith("../"):
                                raise invalid()
                    # Do not execute linked fields, altChunk, tracked edits or embedded objects in LibreOffice.
                    if any(node.tag in {W+k for k in ("instrText","fldSimple","altChunk","object","del","ins","dataBinding")} for node in root.iter()):
                        raise invalid()
                    if re.fullmatch(r"word/(document|header[0-9]+|footer[0-9]+)\.xml", name):
                        parts[name] = etree.fromstring(raw, etree.XMLParser(resolve_entities=False,no_network=True))
            if parts["word/document.xml"].tag != W+"document":
                raise invalid()
            return parts
    except ContractError:
        raise
    except Exception:
        raise invalid() from None


def validate_pdf(data):
    try:
        reader=PdfReader(BytesIO(data),strict=True)
        if reader.is_encrypted or not 1<=len(reader.pages)<=100:
            raise invalid("ENCRYPTED_PDF")
        if any(f.get("/FT")=="/Sig" for f in (reader.get_fields() or {}).values()):
            raise invalid("SIGNED_PDF")
        for page in reader.pages:
            if any(a.get_object().get("/FT")=="/Sig" for a in page.get("/Annots",[])):
                raise invalid("SIGNED_PDF")
        doc=fitz.open(stream=data,filetype="pdf")
        if not 1<=doc.page_count<=100 or any(not 72<=p.rect.width<=2000 or not 72<=p.rect.height<=2000 for p in doc) or sum(p.rect.width*p.rect.height*2.25 for p in doc)>100000000:
            doc.close()
            raise invalid()
        return doc
    except ContractError:
        raise
    except Exception:
        raise invalid() from None


def box(rect,page):
    return {"kind":"pdf_box","page":page.number+1,"x":rect.x0/page.rect.width,"y":rect.y0/page.rect.height,
        "width":rect.width/page.rect.width,"height":rect.height/page.rect.height,"font_size":12,"multiline":False}


def rectangle(placement,page):
    return fitz.Rect(placement["x"]*page.rect.width,placement["y"]*page.rect.height,
        (placement["x"]+placement["width"])*page.rect.width,(placement["y"]+placement["height"])*page.rect.height)


class PersonalRenderer:
    def convert(self,data):
        if not shutil.which("libreoffice"):
            raise ContractError("RENDERER_UNAVAILABLE","Chạy image Docker Contract có LibreOffice.")
        with TemporaryDirectory(prefix="contract-source-") as temp:
            folder=Path(temp)
            source=folder/"source.docx"
            source.write_bytes(data)
            try:
                subprocess.run([shutil.which("libreoffice"),"--headless","--nologo","--nodefault","--norestore",
                    "-env:UserInstallation="+(folder/"profile").as_uri(),"--convert-to","pdf:writer_pdf_Export","--outdir",temp,str(source)],
                    check=True,timeout=60,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                    env={"PATH":"/usr/local/bin:/usr/bin:/bin","HOME":temp,"TMPDIR":temp,"LANG":"C.UTF-8"})
                pdf=(folder/"source.pdf").read_bytes()
                if len(pdf)>52428800:
                    raise invalid()
                with validate_pdf(pdf):
                    pass
                return pdf
            except subprocess.TimeoutExpired:
                raise invalid("RENDER_TIMEOUT") from None
            except (OSError,subprocess.CalledProcessError):
                raise invalid("RENDER_FAILED") from None

    def prepare(self,data,format):
        if not 1<=len(data)<=MAX_SOURCE:
            raise invalid()
        parts=inspect_docx(data) if format=="docx" else {}
        pdf=self.convert(data) if format=="docx" else self.render(data,"pdf",[],{},"pdf")
        anchors={}
        fields=[]
        with validate_pdf(pdf) as doc:
            if format=="docx":
                occurrences={}
                for part,root in sorted(parts.items()):
                    for index,p in enumerate(root.iter(W+"p")):
                        # Restrict to native text paragraphs, including table cells, header/footer.
                        text="".join(t.text or "" for t in p.iter(W+"t"))
                        if len(text)>200000:
                            raise invalid()
                        for match in re.finditer(r"\{\{([a-z][a-z0-9_]{0,63})\}\}",text):
                            locator=f"{part}:{index}:{match.start()}:{match.end()}"
                            anchors[locator]={"token":match[0],"field_name":match[1]}
                            occurrences.setdefault(match[1],[]).append(locator)
                if len(anchors)>2000 or len(occurrences)>100:
                    raise invalid()
                for name,locators in occurrences.items():
                    hits=[(p,r) for p in doc for r in p.search_for("{{"+name+"}}")]
                    # Ambiguous/repeated header/line-wrapped anchors are not guessed.
                    if len(hits)!=len(locators) or len(locators)>20 or len({loc.rsplit(":",3)[0] for loc in locators})>1:
                        continue
                    placements=[]
                    for locator,(page,rect) in zip(locators,hits):
                        b=box(rect,page)
                        anchors[locator]["preview_boxes"]=[b]
                        placements.append({"kind":"docx_anchor","locator":locator,"preview_boxes":[b]})
                    fields.append({"name":name,"label":name,"type":"text","required":False,"identity_key":None,
                        "confidence":None,"placements":placements})
            manifest={"anchors":anchors,"pages":[{"width":p.rect.width,"height":p.rect.height} for p in doc],
                "fields":fields,"warnings":["USER_MAPPING_REVIEW_REQUIRED"]}
            if format=="pdf":
                manifest["warnings"].append("PDF_SELECT_BLANK_BOXES_MANUALLY")
            elif len(fields)!=len({a["field_name"] for a in anchors.values()}):
                manifest["warnings"].append("DOCX_ANCHOR_PREVIEW_AMBIGUOUS")
        return pdf,manifest

    def validate_mapping(self,fields,manifest,format,pdf):
        used=[]
        with validate_pdf(pdf) as doc:
            for field in fields:
                for placement in field["placements"]:
                    if format=="docx":
                        if placement["kind"]!="docx_anchor":
                            raise invalid("INVALID_MAPPING")
                        anchor=manifest["anchors"].get(placement["locator"])
                        if not anchor or not anchor.get("preview_boxes") or placement["preview_boxes"]!=anchor["preview_boxes"]:
                            raise invalid("INVALID_MAPPING")
                        ident=placement["locator"]
                        if ident in used:
                            raise invalid("INVALID_MAPPING")
                        used.append(ident)
                    else:
                        if placement["kind"]!="pdf_box" or placement["page"]>len(doc):
                            raise invalid("INVALID_MAPPING")
                        page=doc[placement["page"]-1]
                        rect=rectangle(placement,page)
                        if rect.width<8 or rect.height<placement["font_size"]*1.2:
                            raise invalid("TEXT_OVERFLOW")
                        if any(n==page.number and rect.intersects(r) for n,r in used):
                            raise invalid("INVALID_MAPPING")
                        # Fail closed on visible marks: no writing over source text or scan ink.
                        pix=page.get_pixmap(matrix=fitz.Matrix(1,1),clip=rect,colorspace=fitz.csGRAY,alpha=False)
                        if any(v<245 for v in pix.samples):
                            raise invalid("INVALID_MAPPING")
                        used.append((page.number,rect))
            if format=="docx" and set(used)!=set(manifest["anchors"]):
                raise invalid("INVALID_MAPPING")

    def fill_docx(self,data,fields,values):
        parts=inspect_docx(data)
        edits={}
        for field in fields:
            for placement in field["placements"]:
                part,index,start,end=placement["locator"].rsplit(":",3)
                edits.setdefault((part,int(index)),[]).append((int(start),int(end),plain(values.get(field["name"]))))
        for (part,index),changes in edits.items():
            p=list(parts[part].iter(W+"p"))[index]
            for start,end,value in sorted(changes,reverse=True):
                nodes=list(p.iter(W+"t"))
                full="".join(n.text or "" for n in nodes)
                if not re.fullmatch(r"\{\{[a-z][a-z0-9_]{0,63}\}\}",full[start:end]) or any(ord(c)<32 for c in value) or len(value)>10000:
                    raise invalid("INVALID_INPUT")
                pos=0
                for node in nodes:
                    text=node.text or ""
                    a,b=pos,pos+len(text)
                    if b>start and a<end:
                        node.text=text[:max(0,start-a)]+(value if a<=start<b else "")+text[max(0,end-a):]
                        node.set("{http://www.w3.org/XML/1998/namespace}space","preserve")
                    pos=b
        output=BytesIO()
        changed_parts={part for part,index in edits}
        with zipfile.ZipFile(BytesIO(data)) as archive,zipfile.ZipFile(output,"w",zipfile.ZIP_DEFLATED) as target:
            for info in archive.infolist():
                raw=etree.tostring(parts[info.filename],xml_declaration=True,encoding="UTF-8",standalone=True) if info.filename in changed_parts else archive.read(info.filename)
                target.writestr(info,raw)
        return output.getvalue()

    def render(self,data,source_format,fields,values,format,preview=False):
        if source_format=="pdf" and format=="docx":
            raise invalid("SOURCE_FORMAT_NOT_SUPPORTED")
        docx=self.fill_docx(data,fields,values) if source_format=="docx" else None
        pdf=self.convert(docx) if docx else data
        if source_format=="docx":
            # DOCX can legitimately reflow, but not silently add pages after filling.
            with validate_pdf(self.convert(data)) as original, validate_pdf(pdf) as filled:
                if len(filled)!=len(original):
                    raise invalid("TEXT_OVERFLOW")
                font=fitz.Font(fontfile=FONT)
                for field in fields:
                    value=plain(values.get(field["name"]))
                    for placement in field["placements"]:
                        for b in placement["preview_boxes"]:
                            if font.text_length(value,fontsize=14)>b["width"]*original[b["page"]-1].rect.width:
                                raise invalid("TEXT_OVERFLOW")
        if format=="docx":
            return docx
        with validate_pdf(pdf) as source,fitz.open() as target:
            # Rasterize background to remove actions, attachments/links and normalize crop/rotation.
            for page in source:
                dest=target.new_page(width=page.rect.width,height=page.rect.height)
                pix=page.get_pixmap(matrix=fitz.Matrix(1.5,1.5),alpha=False)
                dest.insert_image(dest.rect,stream=pix.tobytes("png"))
            if source_format=="pdf":
                for field in fields:
                    value=plain(values.get(field["name"]))
                    if len(value)>10000 or any(ord(c)<32 and c not in "\n\t" for c in value):
                        raise invalid("INVALID_INPUT")
                    for placement in field["placements"]:
                        page=target[placement["page"]-1]
                        if not placement["multiline"] and "\n" in value:
                            raise invalid("TEXT_OVERFLOW")
                        rect=rectangle(placement,page)
                        font=fitz.Font(fontfile=FONT)
                        if not placement["multiline"] and font.text_length(value,fontsize=placement["font_size"])>rect.width:
                            raise invalid("TEXT_OVERFLOW")
                        spare=page.insert_textbox(rect,value,fontname="contractfont",fontfile=FONT,fontsize=placement["font_size"])
                        if spare<0:
                            raise invalid("TEXT_OVERFLOW")
            if preview:
                # Add a separate band, never overlay the source's topmost text.
                with fitz.open() as marked:
                    for page in target:
                        output_page=marked.new_page(width=page.rect.width,height=page.rect.height+24)
                        output_page.show_pdf_page(fitz.Rect(0,24,page.rect.width,page.rect.height+24),target,page.number)
                        output_page.insert_text((12,16),"BẢN XEM TRƯỚC - CHƯA HOÀN THIỆN",fontname="contractfont",fontfile=FONT,fontsize=10,color=(0.8,0,0))
                    output=marked.tobytes(garbage=4,deflate=True)
            else:
                output=target.tobytes(garbage=4,deflate=True)
            if len(output)>52428800:
                raise invalid("FILE_TOO_LARGE")
            return output
