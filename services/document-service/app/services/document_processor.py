"""Runs untrusted parsers/native OCR in a disposable, bounded child, without secrets."""
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
from app.core.errors import DocumentError
from app.services.file_validation import MAX_FILE


class DocumentProcessor:
    def __init__(self, model_dir="/opt/document-ocr", timeout=240):
        if not 10 <= timeout <= 600:
            raise ValueError("Invalid processing timeout")
        self.model_dir, self.timeout = model_dir, timeout

    def run(self, mode, content, row):
        if mode not in {"sanitize", "extract"} or not 1 <= len(content) <= MAX_FILE:
            raise DocumentError("INVALID_FILE", "Nguồn xử lý ngoài giới hạn.", 422)
        payload = {"content": base64.b64encode(content).decode(), "filename": row["original_filename"],
                   "media_type": row["media_type"], "purpose": row["purpose"], "document_type": row["document_type"]}
        env = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP") if key in os.environ}
        env.update({"PYTHONIOENCODING": "utf-8", "OMP_NUM_THREADS": "2", "OPENBLAS_NUM_THREADS": "2",
                    "HOME": "/tmp" if os.name == "posix" else os.environ.get("TEMP", ""),
                    "DOCUMENT_PADDLE_MODEL_DIR": self.model_dir, "PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK": "True"})
        # Support the repo's trusted common_auth library and isolated validation
        # dependencies. Derive ONLY loaded package roots, never copy caller env.
        roots={str(Path(sys.modules[name].__file__).resolve().parent.parent)
               for name in ("pypdf", "common_auth") if name in sys.modules}
        if roots: env["PYTHONPATH"]=os.pathsep.join(sorted(roots))
        try:
            child = subprocess.Popen([sys.executable, "-m", "app.services.document_engine", mode],
                cwd=str(Path(__file__).resolve().parents[2]), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, env=env)
            try:
                output, _ = child.communicate(json.dumps(payload).encode(), timeout=self.timeout)
            except subprocess.TimeoutExpired:
                child.kill(); child.communicate()
                raise DocumentError("PROCESSING_TIMEOUT", "Xử lý tài liệu vượt thời gian.", 504) from None
            if child.returncode or len(output) > 32*1024*1024:
                raise DocumentError("EXTRACTION_FAILED", "Tiến trình xử lý không hoàn tất.", 422)
            data = json.loads(output)
            if not isinstance(data, dict):
                raise ValueError
            if "error" in data:
                allowed = {"INVALID_FILE", "UNSAFE_FILE", "EXTRACTION_TOO_LARGE", "OCR_MODEL_UNAVAILABLE", "EXTRACTION_FAILED"}
                code = data["error"] if data["error"] in allowed else "EXTRACTION_FAILED"
                raise DocumentError(code, "Không thể xử lý tài liệu với cấu hình hiện tại.", 422)
            if mode == "sanitize":
                result = base64.b64decode(data["content"], validate=True)
                if not 1 <= len(result) <= MAX_FILE:
                    raise ValueError
                return result
            return data
        except (OSError, ValueError, KeyError, TypeError):
            raise DocumentError("EXTRACTION_FAILED", "Tiến trình xử lý trả kết quả không hợp lệ.", 422) from None
