"""Build-only MRZ model fetch: immutable revision, bounded size, fixed SHA-256."""
import hashlib
import json
from pathlib import Path
import urllib.request

REVISION = "1e7adfecda5f3c9ae1fb12cf6b4b8c3958c63e46"
SHA256 = "e44f5b7a6bdd3f382ef3bfa84ee0057f5897946a84a094c26910e0a124f3a9bd"
BASE = f"https://raw.githubusercontent.com/DoubangoTelecom/tesseractMRZ/{REVISION}"


def main():
    with urllib.request.urlopen(BASE + "/tessdata_best/mrz.traineddata", timeout=60) as response:
        data = response.read(12 * 1024 * 1024 + 1)
    if len(data) != 11396382 or hashlib.sha256(data).hexdigest() != SHA256:
        raise RuntimeError("MRZ model size/hash mismatch")
    with urllib.request.urlopen(BASE + "/LICENSE", timeout=30) as response:
        license_text = response.read(16385)
    if len(license_text) > 16384 or b"BSD 3-Clause License" not in license_text:
        raise RuntimeError("MRZ model license missing")
    root = Path("/usr/share/tesseract-ocr/5/tessdata")
    (root / "mrz.traineddata").write_bytes(data)
    notices = Path("/opt/mrz")
    notices.mkdir(parents=True, exist_ok=True)
    (notices / "LICENSE").write_bytes(license_text)
    (notices / "manifest.json").write_text(json.dumps({"revision": REVISION, "sha256": SHA256,
        "source": "DoubangoTelecom/tesseractMRZ", "license": "BSD-3-Clause"}), encoding="utf-8")


if __name__ == "__main__":
    main()
