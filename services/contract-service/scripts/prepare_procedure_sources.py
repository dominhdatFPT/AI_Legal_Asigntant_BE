"""Prepare local PDF working copies of unique Procedure sources without changing originals.

Run inside the Contract image, with the Procedure source mounted read-only and a
separate local output directory. This is a conversion/audit step, not catalog import.
"""

from __future__ import annotations

import argparse
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory

import pymupdf
from pypdf import PdfReader


FORMATS = {".doc", ".docx", ".pdf", ".png"}
MIRROR_DIRS = {"IMG đất đai", "Template kinh doanh"}
DISPLAY_NAMES = {
    "Thủ tục đề nghị cấp lại Giấy chứng nhận đăng ký doanh nghiệp.docx":
        "Giấy đề nghị cấp lại Giấy chứng nhận đăng ký hộ kinh doanh",
    "Thủ tục đăng ký thay đổi nội dung Đăng ký doanh nghiệp.docx":
        "Giấy đề nghị đăng ký thay đổi nội dung đăng ký hộ kinh doanh",
    "Thủ tục thông báo tạm ngừng - Cấp đổi thông tin ĐKDN.docx":
        "Giấy đề nghị đăng ký hoạt động chi nhánh/văn phòng đại diện hoặc thông báo địa điểm kinh doanh của hợp tác xã",
}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def unique_sources(root: Path):
    seen = set()
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in FORMATS or any(part in MIRROR_DIRS for part in path.parts):
            continue
        data = path.read_bytes()
        source_sha = digest(data)
        if source_sha in seen:
            continue
        seen.add(source_sha)
        yield path, data, source_sha


def png_to_pdf(data: bytes) -> bytes:
    with pymupdf.open(stream=data, filetype="png") as image:
        rect = image[0].rect
        if rect.is_empty or rect.width > 10000 or rect.height > 10000:
            raise ValueError("Invalid source image")
        a4 = pymupdf.Rect(0, 0, 595.28, 841.89)
        scale = min(a4.width / rect.width, a4.height / rect.height)
        width, height = rect.width * scale, rect.height * scale
        target = pymupdf.Rect((a4.width - width) / 2, (a4.height - height) / 2,
                              (a4.width + width) / 2, (a4.height + height) / 2)
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=a4.width, height=a4.height)
        page.insert_image(target, stream=data)
        return pdf.tobytes(garbage=4, deflate=True)


def word_to_pdf(path: Path) -> bytes:
    executable = shutil.which("libreoffice")
    if not executable:
        raise RuntimeError("LibreOffice is required in the Contract image")
    with TemporaryDirectory(prefix="procedure-source-") as temp:
        work = Path(temp)
        copy = work / ("source" + path.suffix.lower())
        shutil.copyfile(path, copy)
        profile = (work / "profile").as_uri()
        subprocess.run(
            [executable, "--headless", "--nologo", "--nodefault", "--norestore",
             "-env:UserInstallation=" + profile, "--convert-to", "pdf:writer_pdf_Export",
             "--outdir", str(work), str(copy)],
            check=True, timeout=90, stdin=subprocess.DEVNULL, capture_output=True,
            env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": temp,
                 "TMPDIR": temp, "LANG": "C.UTF-8"},
        )
        result = work / "source.pdf"
        if not result.is_file():
            raise RuntimeError("LibreOffice did not create a PDF")
        return result.read_bytes()


def verify_pdf(data: bytes) -> int:
    reader = PdfReader(BytesIO(data), strict=True)
    if reader.is_encrypted or not 1 <= len(reader.pages) <= 100:
        raise ValueError("Unsupported or encrypted PDF")
    if any(field.get("/FT") == "/Sig" for field in (reader.get_fields() or {}).values()):
        raise ValueError("Signed PDF must not be transformed")
    with pymupdf.open(stream=data, filetype="pdf") as pdf:
        if pdf.page_count != len(reader.pages):
            raise ValueError("PDF parser page-count mismatch")
    return len(reader.pages)


def prepare(root: Path, output: Path) -> list[dict]:
    if not root.is_dir() or not output.is_dir():
        raise ValueError("Both source and output directories must exist")
    records = []
    for path, source, source_sha in unique_sources(root):
        record = {
            "source": str(path.relative_to(root)).replace("\\", "/"),
            "source_sha256": source_sha,
            "source_format": path.suffix.lower().lstrip("."),
            "display_name": DISPLAY_NAMES.get(path.name, path.stem),
            "status": "conversion_pending",
        }
        try:
            target = output / (source_sha[:16] + ".pdf")
            if target.exists():
                pdf = target.read_bytes()
            elif path.suffix.lower() == ".pdf":
                pdf = source
            elif path.suffix.lower() == ".png":
                pdf = png_to_pdf(source)
            else:
                pdf = word_to_pdf(path)
            record["pages"] = verify_pdf(pdf)
            record["prepared_sha256"] = digest(pdf)
            record["prepared_file"] = target.name
            if not target.exists():
                with target.open("xb") as stream:
                    stream.write(pdf)
            record["status"] = "prepared_not_mapped"
        except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError,
                subprocess.TimeoutExpired) as error:
            record["error_type"] = type(error).__name__
        records.append(record)
    return records


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source_root", type=Path)
    parser.add_argument("output_dir", type=Path)
    arguments = parser.parse_args()
    records = prepare(arguments.source_root, arguments.output_dir)
    report = arguments.output_dir / "manifest.json"
    encoded = (json.dumps(records, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if report.exists() and report.read_bytes() != encoded:
        raise ValueError("Existing manifest differs; review before replacing")
    if not report.exists():
        with report.open("xb") as stream:
            stream.write(encoded)
    print(json.dumps({"total": len(records), "prepared": sum(r["status"] == "prepared_not_mapped" for r in records),
                      "failed": [r["source"] for r in records if r["status"] != "prepared_not_mapped"]}, ensure_ascii=False))
    return 0 if all(r["status"] == "prepared_not_mapped" for r in records) else 1


if __name__ == "__main__":
    raise SystemExit(main())
