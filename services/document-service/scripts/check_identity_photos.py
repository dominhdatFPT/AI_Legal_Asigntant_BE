"""Offline regression for consented front/back photos; never prints identity data.

Run in the OCR image with --network none and mount a directory containing
<case>-front.jpg and <case>-back.jpg. --variants also resizes and rotates each
pair, comparing the independently recognized identity with the original.
Photos/results are not fixtures and must not be committed to the repository.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image
from app.core.errors import DocumentError
from app.services.identity_images import IdentityImage
from app.services.identity_card_match import ensure_card_pair
from app.services.identity_parser import folded
from app.services.local_ocr import LocalOcr, OcrSettings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--variants", action="store_true")
    parser.add_argument("--diagnostics", type=Path)
    args = parser.parse_args()
    photos = sorted(args.directory.glob("*-front.jpg"))
    if not photos:
        parser.error("No *-front.jpg / *-back.jpg pairs found")
    failures = 0
    for front_path in photos:
        label = front_path.name.removesuffix("-front.jpg")
        original = None
        for variant in (["original", "resized", "rotated"] if args.variants else ["original"]):
            engine = LocalOcr(OcrSettings(vietocr_enabled=True))
            captured, fields = {}, {}
            start = time.monotonic()
            result = {"case": label, "variant": variant}
            try:
                for side in ("front", "back"):
                    content = (args.directory / (label + "-" + side + ".jpg")).read_bytes()
                    if variant != "original":
                        with Image.open(io.BytesIO(content)) as image:
                            if variant == "resized":
                                image = image.resize((round(image.width * .85), round(image.height * .85)), Image.Resampling.LANCZOS)
                            else:
                                image = image.rotate(.8 if side == "front" else -.8,
                                                     resample=Image.Resampling.BICUBIC, expand=True, fillcolor="white")
                            output = io.BytesIO(); image.save(output, format="JPEG", quality=95)
                            content = output.getvalue()
                    photo = IdentityImage(side, "image/jpeg", hashlib.sha256(content).hexdigest(), content)
                    fields[side] = engine.read_image(photo, deadline=start + 55,
                        on_response=lambda raw, side=side: captured.update({side: raw}))
                pair = ensure_card_pair(fields["front"], captured["front"], captured["back"])
                resolved = {**fields["front"], **pair.get("corrections", {})}
                if not resolved.get("identity_number") or not resolved.get("full_name"):
                    raise AssertionError("required_identity_missing")
                identity = {key: resolved.get(key) for key in ("identity_number", "full_name", "date_of_birth")}
                identity["full_name"] = folded(identity["full_name"])
                if variant == "original":
                    original = identity
                elif original is not None and any(value and identity[key] != value for key, value in original.items()):
                    raise AssertionError("variant_identity_changed")
                result.update(status="PASS", match=pair["status"],
                              present_fields=[key for key, value in resolved.items() if value])
            except (DocumentError, AssertionError) as exc:
                failures += 1
                result.update(status="FAIL", code=exc.code if isinstance(exc, DocumentError) else str(exc))
            result["seconds"] = round(time.monotonic() - start, 2)
            print(json.dumps(result), flush=True)
            if args.diagnostics:
                args.diagnostics.mkdir(exist_ok=True, parents=True)
                (args.diagnostics / (label + "-" + variant + ".json")).write_text(json.dumps(captured), encoding="utf-8")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
