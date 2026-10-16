"""Bounded CPU subprocess. Models must already exist; never download in requests.

Only one inference per API process. A timeout kills and reaps the child, rather
than leaving CPU work running behind a timed-out thread. No image temp files.
"""
from dataclasses import dataclass
import csv
import io
import json
import math
import os
import re
from pathlib import Path
import shutil
import subprocess
import threading
import time

from app.core.config import ConfigurationError
from app.core.errors import DocumentError
from app.services.identity_parser import parse_fields, parse_additional_fields
from app.services.identity_card_match import mrz_candidates

MODELS = ("vie", "eng")
_inference_lock = threading.Lock()


@dataclass(frozen=True)
class OcrSettings:
    model_root: str = "/usr/share/tesseract-ocr/5/tessdata"
    cpu_threads: int = 2
    vietocr_enabled: bool = False
    vietocr_root: str = "/opt/vietocr"

    @classmethod
    def from_env(cls):
        root = os.getenv("DOCUMENT_OCR_MODEL_DIR", "/usr/share/tesseract-ocr/5/tessdata")
        try:
            threads = int(os.getenv("DOCUMENT_OCR_CPU_THREADS", "2"))
        except ValueError:
            raise ConfigurationError("DOCUMENT_OCR_CPU_THREADS phải là số nguyên 1–4.") from None
        if not 1 <= threads <= 4 or not Path(root).is_absolute():
            raise ConfigurationError("OCR cần đường dẫn model tuyệt đối và CPU threads 1–4.")
        enabled = os.getenv("DOCUMENT_VIETOCR_ENABLED", "0")
        vietocr_root = os.getenv("DOCUMENT_VIETOCR_MODEL_DIR", "/opt/vietocr")
        if enabled not in {"0","1"} or not Path(vietocr_root).is_absolute():
            raise ConfigurationError("VietOCR cần flag 0/1 và đường dẫn model tuyệt đối.")
        return cls(root, threads, enabled == "1", vietocr_root)


class LocalOcr:
    def __init__(self, settings=None):
        self.settings = settings

    @property
    def request_namespace(self):
        enabled = self.settings.vietocr_enabled if self.settings else os.getenv("DOCUMENT_VIETOCR_ENABLED","0") == "1"
        return "tesseract5-vietocr-seq2seq-parser14-pair2-" + ("on" if enabled else "off")

    def read_image(self, image, *, deadline, on_response):
        if self.settings is None:
            try:
                self.settings = OcrSettings.from_env()
            except ConfigurationError:
                raise DocumentError("CONFIGURATION_ERROR", "Cấu hình OCR local không hợp lệ.", 503) from None
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise DocumentError("OCR_TIMEOUT", "OCR quá thời gian xử lý.", 504)
        for name in MODELS:
            if not (Path(self.settings.model_root) / (name + ".traineddata")).is_file():
                raise DocumentError("OCR_MODEL_UNAVAILABLE", "Model OCR chưa được cài; build lại Document image.", 503)
        executable = shutil.which("tesseract")
        if executable is None:
            raise DocumentError("OCR_MODEL_UNAVAILABLE", "Tesseract chưa được cài; build lại Document image.", 503)
        if not _inference_lock.acquire(blocking=False):
            raise DocumentError("OCR_BUSY", "OCR đang bận; thử lại bằng phiên mới sau.", 503)
        try:
            # Allowlist, not a secret-name blacklist: arbitrary provider/API keys
            # and Python/native loader injection must not reach Tesseract.
            env = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP") if key in os.environ}
            env["OMP_THREAD_LIMIT"] = str(self.settings.cpu_threads)
            from PIL import Image, ImageOps
            with Image.open(io.BytesIO(image.content)) as pixels:
                pixels = pixels.convert("RGB")
                source_size = {"width": pixels.width, "height": pixels.height}
                pixels.thumbnail((2400, 2400))
                ocr_size = {"width": pixels.width, "height": pixels.height}
                encoded = io.BytesIO()
                pixels.save(encoded, format="PNG")
            def recognize(content, size, psm, *, mrz=False, alphabet=None):
                with subprocess.Popen([executable, "stdin", "stdout", "--tessdata-dir", self.settings.model_root,
                                   "-l", "eng" if mrz else "vie+eng", "--oem", "1", "--psm", str(psm),
                                   *(["-c", "tessedit_char_whitelist=" + (alphabet or "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789<")] if mrz else []), "tsv"],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                  env=env, cwd=str(Path(__file__).resolve().parents[2])) as process:
                    try:
                        output, _ = process.communicate(content, timeout=max(.01, deadline - time.monotonic()))
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.communicate()
                        raise DocumentError("OCR_TIMEOUT", "OCR quá thời gian xử lý.", 504) from None
                if process.returncode != 0 or len(output) > 1024 * 1024:
                    raise DocumentError("OCR_UNAVAILABLE", "Không thể chạy model OCR local.", 503)
                return {"side": image.side, "psm": psm, "ocr_size": size, "lines": tsv_lines(output, size)}
            try:
                primary = recognize(encoded.getvalue(), ocr_size, 3)
                raw = {"engine": "tesseract-local", "engine_version": "5", "parser_version": "10",
                       "languages": "vie+eng", "side": image.side, "source_size": source_size, "ocr_size": ocr_size,
                       "lines": primary["lines"], "attempts": []}
                fields = parse_fields(raw)
                extra = parse_additional_fields(raw)
                needed = ("full_name", "identity_number", "date_of_birth", "permanent_address") if image.side == "front" else ("issue_date", "issue_place")
                if any(not fields[key] for key in needed) or (image.side == "front" and not all(extra.values())):
                    enhanced = ImageOps.autocontrast(pixels.convert("L"))
                    scale = min(2, 2400 / max(enhanced.size))
                    enhanced = enhanced.resize((max(1, round(enhanced.width * scale)), max(1, round(enhanced.height * scale))), Image.Resampling.LANCZOS)
                    enhanced_size = {"width": enhanced.width, "height": enhanced.height}
                    buffer = io.BytesIO()
                    enhanced.save(buffer, format="PNG")
                    for psm in ((3, 11, 6) if image.side == "front" else (3,)):
                        if deadline - time.monotonic() <= 1:
                            break
                        raw["attempts"].append(recognize(buffer.getvalue(), enhanced_size, psm))
                        fields = parse_fields(raw)
                        extra = parse_additional_fields(raw)
                        if all(fields[key] for key in needed) and (image.side != "front" or all(extra.values())):
                            break
                if image.side == "back" and not mrz_candidates(raw) and deadline - time.monotonic() > 1:
                    # Read three detected rows from this one image with a raw
                    # line recognizer. Different alphabets reflect TD1 fields,
                    # but no recognized characters/digits are manually repaired.
                    rows = [line for line in raw["lines"] if "<" in line["text"]
                            and 25 <= len(re.sub(r"\s+", "", line["text"])) <= 40
                            and line["polygon"][0][1] > pixels.height * .45]
                    if len(rows) == 3:
                        attempt = {"side": "back", "source": "mrz_rows_psm13", "lines": []}
                        alphabets = ("0123456789IDVNM<", "0123456789MFVN<", "ABCDEFGHIJKLMNOPQRSTUVWXYZ<")
                        for row, alphabet in zip(rows, alphabets):
                            if deadline - time.monotonic() <= 1:
                                break
                            x, y = row["polygon"][0]; right, bottom = row["polygon"][2]
                            region = pixels.convert("L").crop((max(0,x-6), max(0,y-6), min(pixels.width,right+6), min(pixels.height,bottom+6)))
                            region = ImageOps.autocontrast(region)
                            scale = min(2, 2400 / region.width)
                            region = region.resize((round(region.width*scale), round(region.height*scale)), Image.Resampling.LANCZOS)
                            buffer = io.BytesIO(); region.save(buffer,format="PNG")
                            recognized = recognize(buffer.getvalue(), {"width":region.width,"height":region.height}, 13, mrz=True, alphabet=alphabet)
                            attempt["lines"].extend(recognized["lines"])
                        if len(attempt["lines"]) == 3:
                            raw["attempts"].append(attempt)
                if image.side == "back" and not mrz_candidates(raw) and deadline - time.monotonic() > 1:
                    # Read all three MRZ rows in one pass; never stitch different
                    # OCR attempts or repair digits to make a checksum succeed.
                    region = ImageOps.autocontrast(pixels.convert("L"))
                    region = region.crop((0, int(region.height * .60), region.width, region.height))
                    scale = min(2, 2400 / region.width)
                    region = region.resize((round(region.width * scale), round(region.height * scale)), Image.Resampling.LANCZOS)
                    buffer = io.BytesIO(); region.save(buffer, format="PNG")
                    raw["attempts"].append(recognize(buffer.getvalue(), {"width": region.width, "height": region.height}, 6, mrz=True))
                if image.side == "back":
                    from app.services.identity_parser import folded
                    text = " ".join(folded(line["text"]) for attempt in [raw]+raw["attempts"] for line in attempt["lines"])
                    if "of issue" in text and "of expiry" in text:
                        # Cropped, landscape 2024 card: three printed value
                        # regions. Residence and registered birth place retain
                        # their own semantics, not permanent address/origin.
                        regions = (("residence_address", (.055,.092,.47,.147)),
                                   ("registered_birth_place", (.055,.235,.47,.29)),
                                   ("issue_place", (.28,.48,.75,.56)))
                        for key, (left,top,right,bottom) in regions:
                            if deadline - time.monotonic() <= 1:
                                break
                            region = pixels.crop((round(left*pixels.width),round(top*pixels.height),round(right*pixels.width),round(bottom*pixels.height)))
                            if key in {"residence_address", "registered_birth_place"}:
                                # Security-pattern background and clipped border
                                # fragments interfere with the address baseline.
                                # Isolate dark ink, then pad the single text row.
                                region = region.resize((region.width*3,region.height*3),Image.Resampling.LANCZOS)
                                region = region.convert("L")
                                ink_source = region
                                region = region.point(lambda value: 255 if value > 140 else 0)
                                region = ImageOps.expand(region,border=18,fill="white")
                            else:
                                region = ImageOps.autocontrast(region.convert("L"))
                                region = region.resize((region.width*2,region.height*2),Image.Resampling.LANCZOS)
                            buffer = io.BytesIO();region.save(buffer,format="PNG")
                            attempt = recognize(buffer.getvalue(),{"width":region.width,"height":region.height},7)
                            if key in {"residence_address", "registered_birth_place"}:
                                score = min((line["confidence"] for line in attempt["lines"]),default=0)
                                if score < .75 and deadline - time.monotonic() > 1:
                                    alternate = ImageOps.expand(ink_source.point(lambda value:255 if value>120 else 0),border=18,fill="white")
                                    buffer = io.BytesIO();alternate.save(buffer,format="PNG")
                                    second = recognize(buffer.getvalue(),{"width":alternate.width,"height":alternate.height},7)
                                    if min((line["confidence"] for line in second["lines"]),default=0) > score:
                                        attempt = second
                            if key == "residence_address":
                                from app.services.vietocr_local import residence_detail_box
                                box = residence_detail_box(raw, pixels.width, pixels.height)
                                if box and deadline - time.monotonic() > 1:
                                    detail = pixels.crop(tuple(round(v) for v in box))
                                    detail = detail.resize((detail.width*3,detail.height*3),Image.Resampling.LANCZOS)
                                    detail = ImageOps.expand(detail.convert("L").point(lambda v:255 if v>140 else 0),border=18,fill="white")
                                    buffer = io.BytesIO();detail.save(buffer,format="PNG")
                                    detail_attempt = recognize(buffer.getvalue(),{"width":detail.width,"height":detail.height},7)
                                    if detail_attempt["lines"] and all(l["confidence"] >= .75 for l in detail_attempt["lines"]):
                                        attempt["lines"] = detail_attempt["lines"] + attempt["lines"]
                            attempt.update(source="new_card_region",field=key)
                            raw["attempts"].append(attempt)
                if self.settings.vietocr_enabled:
                    from app.services.vietocr_local import read_regions
                    attempts = read_regions(pixels,raw,root=self.settings.vietocr_root,
                                            threads=self.settings.cpu_threads,deadline=deadline)
                    raw["attempts"].extend(attempts)
                    raw["vietocr"] = {"model":"vgg_seq2seq","accepted_fields":[a["field"] for a in attempts]}
                if len(json.dumps(raw, ensure_ascii=False).encode("utf-8")) > 1024 * 1024:
                    raise ValueError
                on_response(raw)
                fields = parse_fields(raw)
            except (ValueError, KeyError, TypeError, csv.Error):
                raise DocumentError("OCR_BAD_RESPONSE", "Kết quả OCR không hợp lệ.", 502) from None
            return fields
        except OSError:
            raise DocumentError("OCR_UNAVAILABLE", "Không thể chạy OCR local.", 503) from None
        finally:
            _inference_lock.release()


def tsv_lines(output, size):
    reader = csv.DictReader(io.StringIO(output.decode("utf-8")), delimiter="\t", quoting=csv.QUOTE_NONE)
    required = {"level", "page_num", "block_num", "par_num", "line_num", "left", "top", "width", "height", "conf", "text"}
    if reader.fieldnames is None or not required <= set(reader.fieldnames):
        raise ValueError
    grouped = {}
    for index, row in enumerate(reader):
        if index >= 10000:
            raise ValueError
        if row["level"] != "5" or not row["text"]:
            continue
        text = row["text"]
        score = float(row["conf"]) / 100
        left, top, width, height = (int(row[key]) for key in ("left", "top", "width", "height"))
        if width == 0 or height == 0:
            # Tesseract emits zero-size boxes for specks/noise on real photos; skip them, not the whole page.
            continue
        if (len(text) > 2000 or not math.isfinite(score) or not 0 <= score <= 1 or left < 0 or top < 0
                or width < 0 or height < 0 or left + width > size["width"] or top + height > size["height"]):
            raise ValueError
        key = tuple(row[name] for name in ("page_num", "block_num", "par_num", "line_num"))
        grouped.setdefault(key, []).append((left, top, width, height, text, score))
    if len(grouped) > 400:
        raise ValueError
    lines = []
    for words in grouped.values():
        words.sort(key=lambda word: word[0])
        text = " ".join(word[4] for word in words)
        if len(text) > 2000:
            raise ValueError
        left, top = min(w[0] for w in words), min(w[1] for w in words)
        right, bottom = max(w[0]+w[2] for w in words), max(w[1]+w[3] for w in words)
        lines.append({"text": text, "confidence": min(w[5] for w in words),
                      "polygon": [[left, top], [right, top], [right, bottom], [left, bottom]],
                      "words": [{"text": w[4], "confidence": w[5], "bbox": [w[0],w[1],w[2],w[3]]} for w in words]})
    lines.sort(key=lambda line: (line["polygon"][0][1], line["polygon"][0][0]))
    return lines
