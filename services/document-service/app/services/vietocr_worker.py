"""One bounded offline CPU inference child; protocol carries crops, not secrets."""
import base64
import hashlib
import io
import json
import math
import os
from pathlib import Path
import sys


def main():
    request = sys.stdin.buffer.read(8 * 1024 * 1024 + 1)
    if len(request) > 8 * 1024 * 1024:
        raise ValueError("Oversized crop request")
    items = json.loads(request)
    if not isinstance(items, list) or not 1 <= len(items) <= 24:
        raise ValueError("Invalid crop batch")
    root = Path(os.environ["DOCUMENT_VIETOCR_MODEL_DIR"])
    import yaml
    import torch
    from PIL import Image
    from vietocr.tool.translate import build_model, process_input, translate
    torch.set_num_threads(int(os.environ.get("OMP_THREAD_LIMIT", "2")))
    torch.set_num_interop_threads(1)
    config = yaml.safe_load((root / "config.yml").read_text(encoding="utf-8"))
    config["device"] = "cpu"
    config["cnn"]["pretrained"] = False
    weights = root / "vgg_seq2seq.pth"
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    digest = hashlib.sha256()
    with weights.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    if digest.hexdigest() != manifest["sha256"]:
        raise ValueError("Model integrity check failed")
    model, vocab = build_model(config)
    model.load_state_dict(torch.load(weights, map_location="cpu", weights_only=True))
    model.eval()
    output = []
    for item in items:
        if not isinstance(item, dict) or set(item) != {"id", "image"} or not isinstance(item["id"], int):
            raise ValueError("Invalid crop item")
        data = base64.b64decode(item["image"], validate=True)
        if len(data) > 1024 * 1024:
            raise ValueError("Oversized crop")
        with Image.open(io.BytesIO(data)) as image:
            if image.width * image.height > 2_000_000:
                raise ValueError("Oversized crop pixels")
            tensor = process_input(image, config["dataset"]["image_height"],
                                   config["dataset"]["image_min_width"], config["dataset"]["image_max_width"])
            with torch.inference_mode():
                sequence, probability = translate(tensor, model)
            text = vocab.decode(sequence[0].tolist()).strip()
            score = float(probability[0])
        if len(text) > 2000 or not math.isfinite(score) or not 0 <= score <= 1:
            raise ValueError("Invalid inference result")
        output.append({"id":item["id"], "text":text, "confidence":score})
    sys.stdout.write(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Never expose model logs, crops, user text, paths or configuration.
        sys.exit(2)
