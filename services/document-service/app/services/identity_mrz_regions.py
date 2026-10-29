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


def detected_band(raw, size):
    """Find three lower-card text rows, tolerating recognition errors in them.

    This chooses pixels only. Recognition and all TD1 checks still run afresh.
    A small lower margin excludes the card edge that otherwise becomes '<'.
    """
    width, height = size
    rows = []
    for line in raw.get("lines", []):
        text = re.sub(r"\s+", "", line.get("text", ""))
        polygon = line.get("polygon")
        if not polygon or len(polygon) != 4 or "<" not in text or not 20 <= len(text) <= 42:
            continue
        x, y = polygon[0]; r, b = polygon[2]
        if 0 <= x < r <= width and height * .45 < y < b <= height and r - x > width * .5:
            rows.append((x, y, r, b))
    rows.sort(key=lambda box: box[1])
    if len(rows) != 3:
        return None
    gaps = [rows[i + 1][1] - rows[i][1] for i in range(2)]
    line_height = statistics.median(b - y for _, y, _, b in rows)
    if min(gaps) <= 0 or max(gaps) > min(gaps) * 1.8 or not .45 * line_height <= min(gaps) <= 2 * line_height:
        return None
    return (0, round(rows[0][1]), width, min(height, round(rows[-1][3] + .25 * line_height)))


def read_bands(pixels, raw, recognize, *, deadline, dedicated_model):
    """Read complete MRZs with the MRZ model, before expensive row fallbacks.

    Work from detected geometry when available, then the lower card area.
    Lighting normalization and bounded scale variants handle photographic
    texture. No character substitution, front values, or cached text is used.
    """
    if not dedicated_model:
        return []
    box = detected_band(raw, pixels.size)
    boxes = [box] if box else []
    fallback = (0, int(pixels.height * .60), pixels.width, pixels.height)
    if fallback not in boxes:
        boxes.append(fallback)
    attempts = []
    for box in boxes:
        band = pixels.crop(box)
        for mode, factor in (("gray", 2), ("normalized", 1.5), ("normalized", 2)):
            if deadline - time.monotonic() <= 4:
                return attempts
            region = (ImageOps.autocontrast(band.convert("L")) if mode == "gray"
                      else normalize_ink(band))
            scale = min(factor, 2400 / region.width)
            region = region.resize((max(1, round(region.width * scale)),
                                    max(1, round(region.height * scale))), Image.Resampling.LANCZOS)
            region = ImageOps.expand(region, border=20, fill=255)
            encoded = io.BytesIO(); region.save(encoded, format="PNG")
            trial = recognize(encoded.getvalue(), {"width": region.width, "height": region.height},
                              6, mrz=True, language="mrz")
            trial.update(side="back", source="mrz_model_band", source_crop=list(box),
                         preprocessing=mode, scale=scale)
            attempts.append(trial)
            if mrz_candidates(trial):
                return attempts
    if deadline - time.monotonic() > 6:
        rectified = rectified_band(pixels)
        if rectified:
            band, geometry = rectified
            for mode, factor in (("gray", 2), ("normalized", 1.5), ("normalized", 2), ("normalized", 1)):
                if deadline - time.monotonic() <= 4:
                    break
                region = ImageOps.autocontrast(band.convert("L")) if mode == "gray" else normalize_ink(band)
                scale = min(factor, 2400 / region.width)
                region = region.resize((max(1, round(region.width * scale)), max(1, round(region.height * scale))),
                                       Image.Resampling.LANCZOS)
                region = ImageOps.expand(region, border=20, fill=255)
                encoded = io.BytesIO(); region.save(encoded, format="PNG")
                trial = recognize(encoded.getvalue(), {"width": region.width, "height": region.height},
                                  6, mrz=True, language="mrz")
                trial.update(side="back", source="mrz_rectified_band", geometry=geometry,
                             preprocessing=mode, scale=scale)
                attempts.append(trial)
                if mrz_candidates(trial):
                    break
    return attempts


def rectified_band(pixels):
    """Locate aligned ink rows when page OCR merged rows with the card border.

    Projection scoring estimates a small photographic skew. Three similarly
    spaced, wide rows locate the MRZ without requiring any recognized text.
    Isolating their ink span keeps the table/card edges out of recognition.
    """
    if pixels.width < 100 or pixels.height < 60:
        return None
    try:
        import numpy as np
    except ImportError:
        return None
    source_crop = (0, round(pixels.height * .55), pixels.width, round(pixels.height * .96))
    band = pixels.crop(source_crop)
    mask = normalize_ink(band)
    small = mask.crop((round(mask.width * .04), 0, round(mask.width * .96), mask.height))
    small = small.resize((800, max(1, round(small.height * 800 / small.width))))
    scores = []
    for quarter in range(-16, 17):
        angle = quarter / 4
        rotated = small.rotate(angle, resample=Image.Resampling.NEAREST, fillcolor=255)
        counts = (np.asarray(rotated) < 128).sum(axis=1).astype(float)
        scores.append((float((counts * counts).sum()), -abs(angle), angle))
    angle = max(scores)[2]
    band = band.rotate(angle, resample=Image.Resampling.BICUBIC, expand=True, fillcolor="white")
    ink = np.asarray(normalize_ink(band)) < 128
    profile = ink[:, round(band.width * .04):round(band.width * .96)].mean(axis=1)
    profile = np.convolve(profile, np.ones(5) / 5, mode="same")

    def groups(values, gap):
        result = []
        for value in values:
            if not result or value - result[-1][-1] > gap:
                result.append([])
            result[-1].append(int(value))
        return result

    boxes = []
    for row in groups(np.flatnonzero(profile > .18), 4):
        if len(row) <= 6:
            continue
        y, b = row[0], row[-1]
        height = b - y
        columns = np.flatnonzero(ink[y:b + 1].sum(axis=0) > height * .25)
        runs = groups(columns, height * .8)
        if not runs:
            continue
        run = max(runs, key=lambda values: values[-1] - values[0])
        if run[-1] - run[0] >= band.width * .55:
            boxes.append((run[0], y, run[-1], b))
    candidates = []
    for i in range(len(boxes) - 2):
        rows = boxes[i:i + 3]
        heights = [b - y for _, y, _, b in rows]
        gaps = [rows[j + 1][1] - rows[j][1] for j in range(2)]
        if (max(heights) > min(heights) * 1.8 or min(gaps) <= 0
                or max(gaps) > min(gaps) * 1.5 or max(gaps) > max(heights) * 2.5):
            continue
        h = max(heights)
        box = (max(0, round(min(row[0] for row in rows) - h * .25)),
               max(0, round(rows[0][1] - h * .5)),
               min(band.width, round(max(row[2] for row in rows) + h * .25)),
               min(band.height, round(rows[-1][3] + h * .6)))
        candidates.append(box)
    if len(candidates) != 1:
        return None
    region = band.crop(candidates[0])
    # A dark card edge can touch all three rows and appear as an extra digit.
    # Only remove broad, nearly continuous vertical ink at the outer margins;
    # text strokes are separated by the white gaps between the three rows.
    columns = (np.asarray(normalize_ink(region)) < 128).mean(axis=0)
    left, right = 0, region.width
    for run in groups(np.flatnonzero(columns > .7), 2):
        if run[-1] - run[0] < region.height * .08:
            continue
        if run[-1] < region.width * .15:
            left = max(left, run[-1] + 3)
        elif run[0] > region.width * .85:
            right = min(right, run[0] - 2)
    region = region.crop((left, 0, right, region.height))
    return region, {"source_crop": list(source_crop), "angle": angle,
                    "rotated_crop": list(candidates[0]), "horizontal_trim": [left, right]}


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
