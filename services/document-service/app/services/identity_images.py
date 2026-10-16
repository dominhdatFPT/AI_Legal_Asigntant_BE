"""Provider-independent preparation of two CCCD images; no network or persistence.

These application limits fit the existing 10 MiB Gateway multipart limit. They
are application limits, independent of the local OCR model.
Admission/metadata removal does not establish authenticity or replace scanning.
"""
from dataclasses import dataclass, field
import hashlib
import io
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError

from app.core.errors import DocumentError
from app.services.file_validation import inspect_file

MAX_IDENTITY_IMAGE = 4 * 1024 * 1024
MAX_IDENTITY_PIXELS = 25_000_000


@dataclass(frozen=True)
class IdentityImage:
    side: str
    media_type: str
    sha256: str
    content: bytes = field(repr=False)


class _BoundedOutput(io.BytesIO):
    def write(self, data):
        if self.tell() + len(data) > MAX_IDENTITY_IMAGE:
            raise DocumentError("FILE_TOO_LARGE", "Ảnh chuẩn hóa vượt giới hạn 4 MiB.", 413)
        return super().write(data)


def prepare_identity_image(side, filename, media_type, content):
    if side not in {"front", "back"}:
        raise DocumentError("VALIDATION_ERROR", "Mặt ảnh phải là front hoặc back.", 422)
    if len(content) > MAX_IDENTITY_IMAGE:
        raise DocumentError("FILE_TOO_LARGE", "Mỗi ảnh CCCD tối đa 4 MiB.", 413)
    # Do not parse PDF/ZIP here. This input is strictly two raster images.
    if media_type not in {"image/jpeg", "image/png"}:
        raise DocumentError("UNSUPPORTED_FILE_TYPE", "Ảnh CCCD phải là JPEG hoặc PNG.", 415)
    if not content.startswith((b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n")):
        raise DocumentError("UNSUPPORTED_FILE_TYPE", "Nội dung ảnh CCCD không phải JPEG hoặc PNG.", 415)
    inspect_file(filename, media_type, content, "supporting_document", "identity_card")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content)) as image:
                if image.width * image.height > MAX_IDENTITY_PIXELS:
                    raise DocumentError("UNSAFE_FILE", "Ảnh có kích thước pixel quá lớn.", 422)
                if getattr(image, "n_frames", 1) != 1:
                    raise DocumentError("UNSAFE_FILE", "Không hỗ trợ ảnh CCCD nhiều khung hình.", 422)
                image.load()  # Fully decode; verify() alone cannot detect all truncated pixel data.
                oriented = ImageOps.exif_transpose(image)
                rgba = oriented.convert("RGBA")
                clean = Image.new("RGB", rgba.size, "white")
                try:
                    clean.paste(rgba, mask=rgba.getchannel("A"))
                    with _BoundedOutput() as output:
                        # Fresh pixels only: EXIF/GPS/XMP/ICC/comments and trailing data are not copied.
                        clean.save(output, format="JPEG", quality=95, subsampling=0)
                        data = output.getvalue()
                finally:
                    clean.close()
                    rgba.close()
                    if oriented is not image:
                        oriented.close()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError,
            Image.DecompressionBombWarning):
        raise DocumentError("INVALID_FILE", "Ảnh CCCD hỏng hoặc không giải mã được.", 422) from None
    return IdentityImage(side, "image/jpeg", hashlib.sha256(data).hexdigest(), data)


def prepare_identity_pair(front, back):
    """Each input is (filename, declared MIME, bytes), ordered front then back.

    Fingerprint original bytes and order, so a later idempotency layer cannot
    silently replay a different upload after normalization removed metadata.
    """
    first = prepare_identity_image("front", *front)
    second = prepare_identity_image("back", *back)
    if first.sha256 == second.sha256:
        raise DocumentError("VALIDATION_ERROR", "Hai mặt CCCD không được là cùng một ảnh.", 422)
    fingerprint = hashlib.sha256(b"identity-pair-v1\0" + hashlib.sha256(front[2]).digest()
                                 + hashlib.sha256(back[2]).digest()).hexdigest()
    return first, second, fingerprint
