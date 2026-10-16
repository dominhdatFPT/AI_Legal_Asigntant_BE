"""Build-time only: bake official VietOCR weights/config into the image."""
import hashlib
import json
from pathlib import Path
import urllib.request
import yaml

REVISION = "fe8c3a7fc714aec57ab81cec844eb3adf0c1636c"
WEIGHTS_URL = "https://vocr.vn/data/vietocr/vgg_seq2seq.pth"
EXPECTED_SHA256 = "0921503a41375a0584268e23ef3d414ea478a8fe8777865c7745d38f2d0bc5db"
ROOT = Path("/opt/vietocr")


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    base = f"https://raw.githubusercontent.com/pbcquoc/vietocr/{REVISION}/config/"
    config = {}
    for name in ("base.yml", "vgg-seq2seq.yml"):
        with urllib.request.urlopen(base + name, timeout=30) as response:
            config.update(yaml.safe_load(response.read(65536)))
    config.update(device="cpu", weights=str(ROOT / "vgg_seq2seq.pth"), quiet=True)
    config["cnn"]["pretrained"] = False
    config["predictor"]["beamsearch"] = False
    digest, size = hashlib.sha256(), 0
    with urllib.request.urlopen(WEIGHTS_URL, timeout=60) as response, (ROOT / "vgg_seq2seq.pth").open("wb") as output:
        while chunk := response.read(1024 * 1024):
            size += len(chunk)
            if size > 128 * 1024 * 1024:
                raise RuntimeError("VietOCR model exceeds build limit")
            digest.update(chunk)
            output.write(chunk)
    if size < 10 * 1024 * 1024:
        raise RuntimeError("VietOCR model response too small")
    if digest.hexdigest() != EXPECTED_SHA256:
        raise RuntimeError("VietOCR model checksum differs from pinned artifact")
    (ROOT / "config.yml").write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    manifest = {"model":"vgg_seq2seq", "config_revision":REVISION, "weights_url":WEIGHTS_URL,
                "sha256":digest.hexdigest(), "size_bytes":size}
    (ROOT / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    print(json.dumps(manifest))


if __name__ == "__main__":
    main()
