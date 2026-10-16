"""OCR thẻ luật sư sau interface LawyerCardOcrProvider: hiện dùng Tesseract local (cùng model/khóa với identity-ocr),
sau này thêm GeminiLawyerCardOcr cùng interface là đổi được, không phải sửa repository / API.

Ảnh vào đã được chuẩn hóa (JPEG mới, không EXIF) ở lawyer_card_images. OCR lỗi không làm hỏng upload:
`read_card_safely` biến mọi lỗi OCR thành kết quả `failed` kèm mã + câu báo tiếng Việt.
"""

import io
import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from app.core.config import ConfigurationError
from app.core.errors import DocumentError
from app.services.lawyer_card_parser import is_readable, parse_lawyer_card
from app.services.local_ocr import MODELS, OcrSettings, _inference_lock, tsv_lines

OCR_DEADLINE_SECONDS = 55
FAILURE_MESSAGES = {
    "OCR_UNREADABLE": "Ảnh thẻ mờ hoặc không đọc được, vui lòng tự nhập số thẻ hoặc chụp lại rõ hơn.",
    "OCR_TIMEOUT": "Đọc ảnh thẻ quá lâu, vui lòng tự nhập số thẻ hành nghề.",
    "OCR_BUSY": "Hệ thống đọc ảnh đang bận, vui lòng tự nhập số thẻ hoặc tải lại ảnh sau.",
    "OCR_UNAVAILABLE": "Chưa đọc được ảnh thẻ tự động, vui lòng tự nhập số thẻ hành nghề.",
    "OCR_MODEL_UNAVAILABLE": "Chưa đọc được ảnh thẻ tự động, vui lòng tự nhập số thẻ hành nghề.",
    "OCR_BAD_RESPONSE": "Chưa đọc được ảnh thẻ tự động, vui lòng tự nhập số thẻ hành nghề.",
}


@dataclass
class CardOcrOutcome:
    status: str                      # completed | failed
    fields: dict | None
    warnings: list[str] = field(default_factory=list)
    error_code: str | None = None
    raw: dict | None = None


class LawyerCardOcrProvider(Protocol):
    engine: str

    def read_card(self, image, *, deadline: float) -> tuple[dict, dict]:
        """Trả (fields, raw JSON của engine). Lỗi thì ném DocumentError với mã OCR_*."""


class LocalLawyerCardOcr:
    """Tesseract 5 vie+eng chạy bằng subprocess, một inference / process (dùng chung khóa với identity-ocr)."""

    engine = "tesseract-local"

    def __init__(self, settings: OcrSettings | None = None):
        self.settings = settings

    def read_card(self, image, *, deadline: float) -> tuple[dict, dict]:
        if self.settings is None:
            try:
                self.settings = OcrSettings.from_env()
            except ConfigurationError:
                raise DocumentError("OCR_UNAVAILABLE", "Cấu hình OCR local không hợp lệ.", 503) from None
        for name in MODELS:
            if not (Path(self.settings.model_root) / (name + ".traineddata")).is_file():
                raise DocumentError("OCR_MODEL_UNAVAILABLE", "Model OCR chưa được cài.", 503)
        executable = shutil.which("tesseract")
        if executable is None:
            raise DocumentError("OCR_MODEL_UNAVAILABLE", "Tesseract chưa được cài.", 503)
        if not _inference_lock.acquire(blocking=False):
            raise DocumentError("OCR_BUSY", "OCR đang bận.", 503)
        try:
            from PIL import Image
            with Image.open(io.BytesIO(image.content)) as pixels:
                pixels = pixels.convert("RGB")
                pixels.thumbnail((2400, 2400))
                size = {"width": pixels.width, "height": pixels.height}
                encoded = io.BytesIO()
                pixels.save(encoded, format="PNG")
            env = _safe_env()
            env["OMP_THREAD_LIMIT"] = str(self.settings.cpu_threads)
            with subprocess.Popen([executable, "stdin", "stdout", "--tessdata-dir", self.settings.model_root,
                                   "-l", "vie+eng", "--oem", "1", "--psm", "3", "tsv"],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                  env=env) as process:
                try:
                    output, _ = process.communicate(encoded.getvalue(),
                                                    timeout=max(.01, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate()
                    raise DocumentError("OCR_TIMEOUT", "OCR quá thời gian xử lý.", 504) from None
            if process.returncode != 0 or len(output) > 1024 * 1024:
                raise DocumentError("OCR_UNAVAILABLE", "Không thể chạy model OCR local.", 503)
            try:
                raw = {"engine": self.engine, "languages": "vie+eng", "ocr_size": size,
                       "lines": tsv_lines(output, size)}
            except (ValueError, KeyError, TypeError):
                raise DocumentError("OCR_BAD_RESPONSE", "Kết quả OCR không hợp lệ.", 502) from None
            return parse_lawyer_card(raw["lines"]), raw
        except OSError:
            raise DocumentError("OCR_UNAVAILABLE", "Không thể chạy OCR local.", 503) from None
        finally:
            _inference_lock.release()


def _safe_env() -> dict:
    # Allowlist giống local_ocr: không cho key/biến loader lọt vào Tesseract.
    return {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP") if key in os.environ}


def read_card_safely(provider: LawyerCardOcrProvider, image) -> CardOcrOutcome:
    """Không bao giờ ném lỗi OCR: ảnh vẫn lưu, người dùng tự nhập khi OCR lỗi."""
    try:
        fields, raw = provider.read_card(image, deadline=time.monotonic() + OCR_DEADLINE_SECONDS)
    except DocumentError as exc:
        code = exc.code if exc.code in FAILURE_MESSAGES else "OCR_UNAVAILABLE"
        return CardOcrOutcome("failed", None, [], code, None)
    if not isinstance(raw, dict) or len(json.dumps(raw, ensure_ascii=False).encode("utf-8")) > 1024 * 1024:
        return CardOcrOutcome("failed", None, [], "OCR_BAD_RESPONSE", None)
    if not is_readable(fields):
        return CardOcrOutcome("failed", None, [], "OCR_UNREADABLE", raw)
    warnings = ["OCR_REQUIRES_USER_REVIEW"]
    if not fields.get("bar_card_number"):
        warnings.append("OCR_BAR_CARD_NUMBER_NOT_FOUND")
    return CardOcrOutcome("completed", fields, warnings, None, raw)
