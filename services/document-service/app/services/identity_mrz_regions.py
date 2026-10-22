"""Refine TD1 rows using this image's detected geometry, not identity values.

Each bounded trial reads all rows again from the same source image. No character
repair, filler padding, cached-row stitching or front-side values are used.
"""
import io
import re
import statistics
import time

from PIL import Image, ImageFilter, ImageOps

from app.services.identity_card_match import mrz_candidates


def row_regions(detected, size):
    """Map a coarse MRZ band back to source coordinates, including its offset."""
    crop = detected.get("source_crop")
    scaled = detected.get("ocr_size", {})
    if (not crop or len(crop) != 4 or scaled.get("width", 0) <= 0
            or scaled.get("height", 0) <= 0):
        return None
    left, top, right, bottom = crop
    if not (0 <= left < right <= size[0] and 0 <= top < bottom <= size[1]):
        return None
    boxes = []
    for line in detected.get("lines", []):
        text = re.sub(r"\s+", "", line["text"]).upper()
        if not 15 <= len(text) <= 45 or not re.fullmatch(r"[A-Z0-9<]+", text):
            continue
        polygon = line.get("polygon")
        if not polygon or len(polygon) != 4:
            continue
        x, y = polygon[0]; r, b = polygon[2]
        if not (0 <= x < r <= scaled["width"] and 0 <= y < b <= scaled["height"]):
            continue
        box = (left + x * (right-left) / scaled["width"],
               top + y * (bottom-top) / scaled["height"],
               left + r * (right-left) / scaled["width"],
               top + b * (bottom-top) / scaled["height"])
        boxes.append((text, box))
    boxes.sort(key=lambda item: item[1][1])
    anchors = [i for i, (text, _) in enumerate(boxes) if text.startswith("IDVNM")]
    if len(anchors) != 1 or len(boxes) < anchors[0] + 3:
        return None
    selected = [box for _, box in boxes[anchors[0]:anchors[0]+3]]
    height = statistics.median(b-y for _, y, _, b in selected)
    starts = [y for _, y, _, _ in selected]
    gaps = [starts[i+1]-starts[i] for i in range(2)]
    if (height < 8 or not all(.45*height <= gap <= 1.8*height for gap in gaps)
            or max(gaps) > min(gaps)*1.5):
        return None
    x = statistics.median(box[0] for box in selected)
    r = max(box[2] for box in selected)
    if r-x < height*8 or any(box[2] < x + (r-x)*.6 for box in selected):
        return None
    # The coarse pass can include border/background in overlapping line boxes.
    # Remove a small bottom fringe and give each crop independent white padding.
    second = (x, selected[1][1]-.06*height, r-.12*height, selected[1][3]-.14*height)
    name = (x-.26*height, selected[2][1]+.01*height, r+.08*height, selected[2][3]-.11*height)
    def clamp(box):
        x, y, r, b = box
        return (max(0, round(x)), max(0, round(y)),
                min(size[0], round(r)), min(size[1], round(b)))
    return {"band": tuple(crop), "second": clamp(second), "name": clamp(name),
            "padding": max(12, round(height*.15))}


def normalize_ink(region, radius_fraction=.04):
    """Local illumination normalization keeps dark '<' strokes off card texture."""
    gray = region.convert("L")
    # Use a discrete kernel size for reproducible segmentation across crop sizes.
    radius = max(12, min(96, int(gray.width*radius_fraction)))
    background = gray.filter(ImageFilter.GaussianBlur(radius))
    # The operation is on pixels only, before any text recognition. Pillow-only
    # implementation also works when VietOCR/numpy are not installed.
    ink = bytes(0 if value*200 < max(base, 1)*165 else 255
                for value, base in zip(gray.tobytes(), background.tobytes()))
    return Image.frombytes("L", gray.size, ink)


def read_rows(pixels, detected, recognize, *, deadline, dedicated_model):
    regions = row_regions(detected, pixels.size)
    if not regions or not dedicated_model:
        return []
    attempts = []
    # Two image-processing variants at most, both inside the shared deadline.
    for radius in (.04, .02):
        if deadline-time.monotonic() <= 5:
            break
        trial = {"side": "back", "source": "mrz_local_rows", "radius_fraction": radius,
                 "row_crops": [], "lines": []}
        for index, key in enumerate(("band", "second", "name")):
            if deadline-time.monotonic() <= 1:
                break
            box = regions[key]
            region = pixels.crop(box)
            region = normalize_ink(region, radius) if key == "name" else ImageOps.autocontrast(region.convert("L"))
            if key != "band":
                region = ImageOps.expand(region, border=regions["padding"], fill=255)
            encoded = io.BytesIO(); region.save(encoded, format="PNG")
            read = recognize(encoded.getvalue(), {"width": region.width, "height": region.height}, 6,
                             mrz=True, language="eng" if key == "band" else "mrz",
                             alphabet=("0123456789IDVNM<", "0123456789MFVN<", "ABCDEFGHIJKLMNOPQRSTUVWXYZ<")[index])
            lines = read["lines"]
            if key == "band":
                lines = [line for line in lines if re.sub(r"\s+", "", line["text"]).startswith("IDVNM")]
            if len(lines) != 1:
                break
            trial["lines"].extend(lines)
            trial["row_crops"].append({"source_crop": list(box), "ocr_size": read["ocr_size"]})
        if len(trial["lines"]) != 3:
            continue
        attempts.append(trial)
        if mrz_candidates(trial):
            break
    return attempts
