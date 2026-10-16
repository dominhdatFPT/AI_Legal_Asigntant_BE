"""Bounded admission checks only. Files stay quarantined until the worker checks them."""
from dataclasses import dataclass, field
import hashlib
import io
import json
from pathlib import PurePosixPath
import warnings
import zipfile

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException
from PIL import Image, UnidentifiedImageError
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.core.errors import DocumentError

MAX_FILE = 9437184
MAX_MULTIPART = 10 * 1024 * 1024
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@dataclass(frozen=True)
class Admission:
    filename: str
    purpose: str
    document_type: str
    media_type: str
    uploaded_sha256: str
    request_hash: str
    content: bytes = field(repr=False)


def invalid(code="INVALID_FILE", message="File hỏng hoặc không đúng định dạng.", status=422):
    raise DocumentError(code, message, status)


def inspect_file(filename, declared_type, content, purpose, document_type):
    if (not filename or len(filename) > 255 or filename.strip() in {"", ".", ".."}
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in filename) or "/" in filename or "\\" in filename):
        invalid("VALIDATION_ERROR", "Tên file không hợp lệ.")
    if not content:
        invalid("INVALID_FILE", "File không được rỗng.")
    if len(content) > MAX_FILE:
        invalid("FILE_TOO_LARGE", "File vượt giới hạn 9 MiB.", 413)
    if purpose not in {"contract_template", "supporting_document"} or document_type not in {"contract", "identity_card", "other"}:
        invalid("VALIDATION_ERROR", "Mục đích hoặc loại tài liệu không hợp lệ.")
    if content.startswith(b"%PDF-"):
        media = "application/pdf"
        try:
            reader = PdfReader(io.BytesIO(content), strict=True)
            if reader.is_encrypted:
                invalid("ENCRYPTED_PDF", "Không hỗ trợ PDF mã hóa/khóa.")
            if len(reader.pages) > 100:
                invalid("TOO_MANY_PAGES", "PDF vượt giới hạn 100 trang.")
            if not reader.pages:
                invalid()
            if any(value.get("/FT") == "/Sig" for value in (reader.get_fields() or {}).values()):
                invalid("SIGNED_PDF", "Không hỗ trợ PDF có trường chữ ký số.")
        except (PdfReadError, ValueError, KeyError, TypeError, RecursionError):
            invalid()
    elif content.startswith((b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n")):
        media = "image/jpeg" if content.startswith(b"\xff\xd8\xff") else "image/png"
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(content)) as image:
                    if image.width * image.height > 25_000_000:
                        invalid("UNSAFE_FILE", "Ảnh có kích thước pixel quá lớn.")
                    if image.format != ("JPEG" if media == "image/jpeg" else "PNG"):
                        invalid()
                    image.verify()
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
            invalid()
    elif content.startswith(b"PK\x03\x04"):
        media = DOCX
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                entries = archive.infolist()
                names = [entry.filename for entry in entries]
                if (len(entries) > 2000 or len(set(names)) != len(names)
                        or sum(entry.file_size for entry in entries) > 32 * 1024 * 1024
                        or any(entry.flag_bits & 1 for entry in entries)
                        or any(PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts or "\\" in name for name in names)):
                    invalid("UNSAFE_FILE", "DOCX vượt giới hạn an toàn.")
                if not {"[Content_Types].xml", "word/document.xml"}.issubset(names):
                    invalid("UNSUPPORTED_FILE_TYPE", "ZIP không phải DOCX.", 415)
                if any("vba" in name.lower() for name in names):
                    invalid("UNSAFE_FILE", "Không hỗ trợ tài liệu có macro.")
                types = ElementTree.fromstring(archive.read("[Content_Types].xml"))
                if any("macroenabled" in value.lower() for node in types.iter() for value in node.attrib.values()):
                    invalid("UNSAFE_FILE", "Không hỗ trợ tài liệu có macro.")
                root = ElementTree.fromstring(archive.read("word/document.xml"))
                if root.tag != "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}document":
                    invalid()
        except (zipfile.BadZipFile, KeyError, ValueError, RuntimeError, ElementTree.ParseError, DefusedXmlException):
            invalid()
    else:
        invalid("UNSUPPORTED_FILE_TYPE", "Chỉ hỗ trợ PDF, DOCX, JPEG và PNG.", 415)
    extensions = {"application/pdf": {".pdf"}, DOCX: {".docx"}, "image/jpeg": {".jpg", ".jpeg"}, "image/png": {".png"}}
    if PurePosixPath(filename).suffix.lower() not in extensions[media]:
        invalid("UNSUPPORTED_FILE_TYPE", "Đuôi file không khớp nội dung.", 415)
    if declared_type not in {media, "application/octet-stream", ""}:
        invalid("UNSUPPORTED_FILE_TYPE", "MIME khai báo không khớp nội dung.", 415)
    if purpose == "contract_template" and (document_type != "contract" or media not in {DOCX, "application/pdf"}):
        invalid("UNSUPPORTED_FILE_TYPE", "Mẫu hợp đồng chỉ nhận DOCX/PDF với loại contract.", 415)
    if document_type == "identity_card" and (purpose != "supporting_document" or media == DOCX):
        invalid("UNSUPPORTED_FILE_TYPE", "CCCD chỉ nhận JPEG/PNG/PDF với mục đích supporting_document.", 415)
    digest = hashlib.sha256(content).hexdigest()
    fingerprint = hashlib.sha256(json.dumps([filename, purpose, document_type, media, len(content), digest],
                                           ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    return Admission(filename, purpose, document_type, media, digest, fingerprint, content)
