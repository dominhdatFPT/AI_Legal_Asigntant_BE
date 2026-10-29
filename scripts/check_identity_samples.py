"""Offline smoke check for the fictional two-face fixtures in ocr-test-samples."""
import json
import argparse
from pathlib import Path
import time

from app.core.errors import DocumentError
from app.services.identity_images import prepare_identity_pair
from app.services.identity_parser import field_diagnostics, merge_fields, parse_additional_fields
from app.services.identity_card_match import ensure_card_pair
from app.services.local_ocr import LocalOcr


ROOT = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser()
parser.add_argument("--front", type=Path, default=ROOT / "ocr-test-samples" / "cccd-mau-front.png")
parser.add_argument("--back", type=Path, default=ROOT / "ocr-test-samples" / "cccd-mau-back.png")
parser.add_argument("--raw", action="store_true")
parser.add_argument("--brief", action="store_true")
parser.add_argument("--save-raw", type=Path)
args = parser.parse_args()
pair = prepare_identity_pair(*[
    (path.name, "image/png" if path.suffix.lower() == ".png" else "image/jpeg", path.read_bytes())
    for path in (args.front, args.back)
])
provider = LocalOcr()
raw = {}
deadline = time.monotonic() + 55
readings = [provider.read_image(image, deadline=deadline,
            on_response=lambda result, side=image.side: raw.__setitem__(side, result))
            for image in pair[:2]]
if args.save_raw:
    args.save_raw.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
fields, warnings = merge_fields(*readings)
extra = {key: parse_additional_fields(raw["front"]).get(key)
         or parse_additional_fields(raw["back"]).get(key)
         for key in parse_additional_fields(raw["front"])}
if args.raw:
    for side in ("front", "back"):
        print(side, json.dumps([{"source": attempt.get("source"), "field": attempt.get("field"),
                                 "lines": [line["text"] for line in attempt.get("lines", [])]}
                                for attempt in [raw[side]] + raw[side].get("attempts", [])], ensure_ascii=False))
try:
    pair_status = ensure_card_pair(readings[0], raw["front"], raw["back"])["status"]
except DocumentError as error:
    pair_status = error.code
result = {"fields": fields, "additional_fields": extra, "card_pair_status": pair_status,
          "field_diagnostics": field_diagnostics(raw["front"], raw["back"], {**fields, **extra}),
          "warnings": warnings}
if args.brief:
    result.pop("field_diagnostics")
print(json.dumps(result, ensure_ascii=False, indent=2))
