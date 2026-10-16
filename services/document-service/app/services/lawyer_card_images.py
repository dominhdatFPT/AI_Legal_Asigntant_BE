"""Chuẩn hóa một ảnh thẻ luật sư: chỉ JPEG/PNG ≤ 4 MiB, giải mã đầy đủ, xoay theo EXIF rồi mã hóa lại thành JPEG mới
(pixel mới: không EXIF/GPS/ICC/comment, không giữ byte gốc). Bản này lưu thẳng vào safe/ để cấp link xem cho admin.
Không thay đổi identity_images (API CCCD giữ nguyên hành vi)."""

import hashlib
import io
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError

from app.core.errors import DocumentError
from app.services.file_validation import inspect_file
from app.services.identity_images import MAX_IDENTITY_IMAGE, MAX_IDENTITY_PIXELS, IdentityImage, _BoundedOutput

MAX_LAWYER_CARD_IMAGE = MAX_IDENTITY_IMAGE


def prepare_lawyer_card_image(filename: str, media_type: str, content: bytes) -> tuple[IdentityImage, str]:
    """Trả (ảnh đã chuẩn hóa, fingerprint của byte gốc cho Idempotency-Key)."""
    if len(content) > MAX_LAWYER_CARD_IMAGE:
        raise DocumentError("FILE_TOO_LARGE", "Ảnh thẻ luật sư tối đa 4 MiB.", 413)
    if media_type not in {"image/jpeg", "image/png"} or not content.startswith((b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n")):
        raise DocumentError("UNSUPPORTED_FILE_TYPE", "Ảnh thẻ luật sư phải là JPEG hoặc PNG.", 415)
    inspect_file(filename, media_type, content, "supporting_document", "other")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content)) as image:
                if image.width * image.height > MAX_IDENTITY_PIXELS:
                    raise DocumentError("UNSAFE_FILE", "Ảnh có kích thước pixel quá lớn.", 422)
                if getattr(image, "n_frames", 1) != 1:
                    raise DocumentError("UNSAFE_FILE", "Không hỗ trợ ảnh nhiều khung hình.", 422)
                image.load()
                oriented = ImageOps.exif_transpose(image)
                rgba = oriented.convert("RGBA")
                clean = Image.new("RGB", rgba.size, "white")
                try:
                    clean.paste(rgba, mask=rgba.getchannel("A"))
                    with _BoundedOutput() as output:
                        clean.save(output, format="JPEG", quality=95, subsampling=0)
                        data = output.getvalue()
                finally:
                    clean.close()
                    rgba.close()
                    if oriented is not image:
                        oriented.close()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise DocumentError("INVALID_FILE", "Ảnh thẻ hỏng hoặc không giải mã được.", 422) from None
    fingerprint = hashlib.sha256(b"lawyer-card-v1\0" + hashlib.sha256(content).digest()).hexdigest()
    return IdentityImage("lawyer_card", "image/jpeg", hashlib.sha256(data).hexdigest(), data), fingerprint
