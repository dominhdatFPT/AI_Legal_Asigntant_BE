"""Crop Vietnamese text, then run an offline VietOCR child with a hard timeout."""
import base64
from difflib import SequenceMatcher
import io
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from app.core.errors import DocumentError
from app.services.identity_parser import LABELS, folded, label_of, has_label, resolve_label, format_residence_lines, parse_fields, parse_additional_fields

TEXT_FIELDS = {"full_name", "date_of_birth", "issue_date", "expiry_date", "sex", "permanent_address", "place_of_origin", "nationality", "issue_place",
               "residence_address", "registered_birth_place"}


def supplemental_regions(raw, width, height, excluded):
    """Locate omitted values across passes, using weak labels only as geometry.

    Tesseract confidence is NOT promoted to a value confidence. The independent
    VietOCR result still has to pass the normal threshold and field validation.
    No card coordinates or identity-specific text are used here.
    """
    candidates = {}
    for attempt in [raw] + [a for a in raw.get("attempts", []) if not a.get("source")]:
        size = attempt.get("ocr_size", raw.get("ocr_size"))
        if not size or not size.get("width") or not size.get("height"):
            continue
        sx, sy = width / size["width"], height / size["height"]
        lines = attempt.get("lines", [])
        for index, line in enumerate(lines):
            text = line.get("text", "")
            keys = [key for key in sorted(TEXT_FIELDS) if has_label(text, key)]
            legacy_date = (raw.get("side") == "back" and
                re.search(r"\bmonth\b", folded(text)) and re.search(r"\byear\b", folded(text)))
            authority = raw.get("side") == "back" and any(
                label in folded(text) for label in ("cuc canh sat", "bo cong an"))
            if authority and "issue_place" not in keys:
                keys.append("issue_place")
            if legacy_date and "issue_date" not in keys:
                keys.append("issue_date")
            for key in keys:
                if key in excluded or key == "full_name":
                    continue  # Keep the existing independently validated name path.
                if raw.get("side") == "back" and key not in {"issue_date", "issue_place", "residence_address", "registered_birth_place"}:
                    continue
                words = line.get("words", [])
                ends = [m.end() for label in LABELS[key]
                    for m in re.finditer(re.escape(label), folded(text))]
                start = max(ends, default=len(text))
                translation = {"registered_birth_place": "place of birth",
                               "permanent_address": "place of residence",
                               "residence_address": "place of residence",
                               "place_of_origin": "place of origin"}.get(key)
                if translation:
                    bilingual = re.search(re.escape(translation), folded(text))
                    if bilingual:
                        start = max(start, bilingual.end())
                if not ends:
                    match = resolve_label(text)
                    if match["field"] == key:
                        tokens = list(re.finditer(r"\w+", text))
                        count = match["label_token_count"]
                        if len(tokens) >= count:
                            start = tokens[count - 1].end()
                if key == "issue_place" and authority:
                    start = max(folded(text).rfind(label) for label in ("cuc canh sat", "bo cong an"))
                stop = len(text)
                if key == "sex":
                    stops = [m.start() for label in LABELS["nationality"]
                        for m in re.finditer(re.escape(label), folded(text)) if m.start() >= start]
                    stop = min(stops, default=stop)
                values, cursor = [], 0
                for word in words:
                    token = word.get("text", "")
                    pos = text.find(token, cursor)
                    if pos < 0:
                        continue
                    cursor = pos + len(token)
                    if not word.get("bbox"):
                        continue
                    if key in {"date_of_birth", "issue_date", "expiry_date"}:
                        use = bool(re.fullmatch(r"\d{1,2}[/.-]\d{1,2}[/.-]\d{4}", token))
                    else:
                        use = pos >= start and cursor <= stop and any(c.isalnum() for c in token)
                        if key == "sex":
                            use = use and folded(token).strip(" :/.-") in {"nam", "nu", "male", "female"}
                    if use:
                        values.append(word)
                boxes = []
                if values:
                    boxes.append((min(w["bbox"][0] for w in values), min(w["bbox"][1] for w in values),
                        max(w["bbox"][0]+w["bbox"][2] for w in values), max(w["bbox"][1]+w["bbox"][3] for w in values)))
                polygon = line.get("polygon")
                if polygon and key in {"permanent_address", "place_of_origin", "nationality", "residence_address", "registered_birth_place", "issue_place"}:
                    left, top = polygon[0]; right, bottom = polygon[2]
                    line_height = max(1, bottom-top)
                    for following in lines[index+1:index+13]:
                        box = following.get("polygon")
                        if not box:
                            break
                        x, y = box[0]; r, b = box[2]
                        if y < top+line_height*.4 or b-y < line_height*.25:
                            continue
                        if y-bottom > 3*line_height:
                            break
                        if abs(x-left)>3*line_height or r-x > (right-left)*1.5:
                            continue  # Ignore interleaved decoration in another column.
                        if len(following.get("words") or following["text"].split()) < 2:
                            continue
                        if label_of(following["text"]):
                            break
                        boxes.append((x,y,r,b));bottom=b
                        if key in {"nationality", "issue_place"}:
                            break
                if not boxes:
                    continue
                # Prefer complete line layout over sparse fragments, then the
                # pass with stronger heading recognition. These are crop hints.
                rank = (attempt.get("psm") == 3, line.get("confidence", 0), len(boxes))
                if key not in candidates or rank > candidates[key][0]:
                    candidates[key] = (rank, [(x*sx-4,y*sy-4,r*sx+4,b*sy+4) for x,y,r,b in boxes])
    return {key: boxes for key, (_, boxes) in candidates.items()}


def birth_date_box(raw, width, height):
    """Use the birth heading to locate the printed date, including a clipped year."""
    passes = [raw] + [a for a in raw.get("attempts", []) if not a.get("source")]
    candidates, anchor = [], None
    for attempt in passes:
        size = attempt.get("ocr_size", raw.get("ocr_size"))
        if not size:
            continue
        sx,sy = width/size["width"],height/size["height"]
        for line in attempt.get("lines", []):
            for word in line.get("words", []):
                if not re.fullmatch(r"\d{1,2}[/.-]\d{1,2}[/.-]\d{1,4}",word["text"]):
                    continue
                x,y,w,h = word["bbox"]
                box=(x*sx,y*sy,(x+w)*sx,(y+h)*sy)
                candidates.append(box)
                if has_label(line["text"],"date_of_birth"):
                    anchor=box
    if not anchor:
        return None
    x,y,r,b=anchor;h=max(1,b-y)
    same=[box for box in candidates if abs(box[0]-x)<h and abs(box[1]-y)<h/2]
    left=min(box[0] for box in same); right=max(box[2] for box in same)
    # Page OCR often stops its bbox after the first year digits even though
    # the printed date continues. Extend only toward the remaining same row.
    return (max(0,left-5),max(0,min(box[1] for box in same)-5),
            min(width,right+max(20,round((right-left)*.4))),
            min(height,max(box[3] for box in same)+5))


def residence_detail_box(raw, width, height, *, pixels=None):
    """Locate the inline value after the printed residence heading, not a fixed address."""
    for attempt in [raw] + raw.get("attempts", []):
        if attempt.get("source"):
            continue
        size = attempt.get("ocr_size", raw.get("ocr_size"))
        if not size:
            continue
        for line in attempt.get("lines", []):
            words = line.get("words", [])
            for index, word in enumerate(words):
                if "residence" not in folded(word["text"]):
                    continue
                values = [w for w in words[index+1:] if w.get("bbox") and any(c.isalnum() for c in w["text"])]
                if len(values) < 2:
                    continue
                sx, sy = width / size["width"], height / size["height"]
                return (min(w["bbox"][0] for w in values)*sx-3,
                        min(w["bbox"][1] for w in values)*sy-3,
                        max(w["bbox"][0]+w["bbox"][2] for w in values)*sx+3,
                        max(w["bbox"][1]+w["bbox"][3] for w in values)*sy+3)
    # A damaged bilingual label must not drop the entire inline street. Use
    # the supported new-back printed layout only after independent date labels
    # identify it. Scan dark ink to extend to long street names, not a fixed
    # house/name dictionary. No fallback for front/portrait/unknown layouts.
    text = " ".join(folded(l["text"]) for a in [raw]+raw.get("attempts", [])
                    if not a.get("source") for l in a.get("lines", []))
    if (pixels is None or raw.get("side") != "back" or not 1.45 <= width/height <= 1.75
            or "of issue" not in text or "of expiry" not in text):
        return None
    left, top, right, bottom = round(width*.30), round(height*.045), round(width*.95), round(height*.092)
    strip = pixels.crop((left,top,right,bottom)).convert("L")
    if strip.height < 8:
        return None
    ink = strip.load()
    columns = [x for x in range(strip.width)
               if sum(ink[x,y] < 120 for y in range(strip.height)) >= max(2, round(strip.height*.16))]
    if not columns:
        return None
    end = columns[0]
    for x in columns[1:]:
        if x-end > strip.height*1.5:
            break
        end = x
    if end-columns[0] < 8:
        return None
    # Preserve stable whitespace around a short value. Tight horizontal
    # trimming can turn the colon/last label strokes into a phantom word.
    # Still extend beyond that minimum width for a genuinely long ink line.
    return (left,top,max(round(width*.65),min(right,left+end+5)),bottom)
    return None


def residence_value_regions(raw, width, height):
    """Two printed value rows anchored to the residence heading on this image.

    A suffix word missed by page OCR can start before its reported bbox. Give
    that row left context; do not extend into the next/birthplace heading.
    """
    if raw.get("side") != "back":
        return None
    for attempt in [raw] + raw.get("attempts", []):
        if attempt.get("source"):
            continue
        size = attempt.get("ocr_size", raw.get("ocr_size"))
        if not size or not size.get("width") or not size.get("height"):
            continue
        sx, sy = width/size["width"], height/size["height"]
        lines = attempt.get("lines", [])
        for index, heading in enumerate(lines):
            if not heading.get("polygon"):
                continue
            words = heading.get("words", [])
            labels = [i for i,w in enumerate(words) if "residence" in folded(w["text"])]
            # The Vietnamese heading can be unreadable while the English
            # "of residence" survives with one damaged word. Only use that
            # word as a crop anchor when its preceding "of" is still present.
            if not labels:
                labels = [i for i,w in enumerate(words) if i > 0
                          and folded(words[i-1]["text"]).strip(" :/.-") == "of"
                          and SequenceMatcher(None, folded(w["text"]).strip(" :/.-"),
                                              "residence").ratio() >= .75]
            if not has_label(heading["text"], "residence_address") and not labels:
                continue
            if not labels:
                continue
            values = [w for w in words[labels[-1]+1:] if w.get("bbox") and any(c.isalnum() for c in w["text"])]
            if len(values) < 2:
                continue
            x = min(w["bbox"][0] for w in values); y = min(w["bbox"][1] for w in values)
            r = max(w["bbox"][0]+w["bbox"][2] for w in values)
            b = max(w["bbox"][1]+w["bbox"][3] for w in values); h = max(1,b-y)
            for suffix in lines[index+1:index+3]:
                polygon = suffix.get("polygon")
                if not polygon or label_of(suffix["text"]):
                    break
                left,top = polygon[0];right,bottom = polygon[2]; row_height=bottom-top
                if top < y+.4*h:
                    continue
                if top-b > 1.5*h or row_height < .4*h or "," not in suffix["text"]:
                    break
                padding=max(4,round(row_height*.11))
                left=min(left-2*row_height,heading["polygon"][0][0])
                return {"detail":(x*sx-6,y*sy-6,r*sx+6,b*sy+6),
                        "suffix":(max(0,left*sx),max(0,(top-padding)*sy),
                                  min(width,(right+padding)*sx),min(height,(bottom+padding)*sy))}
    return None


def crop_regions(pixels, raw):
    regions = []
    def add(field, box, order=0):
        left, top, right, bottom = box
        left, top = max(0, int(left)), max(0, int(top))
        right, bottom = min(pixels.width, int(right)), min(pixels.height, int(bottom))
        if right-left < 8 or bottom-top < 8 or len(regions) >= 24:
            return
        crop = pixels.crop((left,top,right,bottom))
        encoded = io.BytesIO();crop.save(encoded,format="PNG")
        content = encoded.getvalue()
        if len(content) <= 1024 * 1024:
            regions.append({"field":field,"order":order,"image":base64.b64encode(content).decode("ascii")})
    attempts = [raw] + [a for a in raw.get("attempts", []) if a.get("psm") in {3,11} and not a.get("source")]
    # Choose usable headings, not the pass with the most background fragments.
    selected = max(attempts, key=lambda a: (
        int(raw.get("side") == "front" and a.get("psm") == 11 and
            any(label_of(l["text"]) == "full_name" for l in a.get("lines", []))),
        sum(label_of(l["text"]) in {"full_name","permanent_address","place_of_origin","nationality"}
            for l in a.get("lines",[])),
        -sum(len(l["text"].strip()) < 3 for l in a.get("lines",[]))))
    lines = [line for line in selected.get("lines",[]) if line["text"].strip() and "<" not in line["text"]]
    size = selected.get("ocr_size",raw["ocr_size"])
    sx, sy = pixels.width/size["width"], pixels.height/size["height"]
    for i, heading in enumerate(lines):
        if raw.get("side") == "back":
            break
        field = label_of(heading["text"])
        if field not in {"full_name","permanent_address","place_of_origin","nationality"}:
            continue
        polygon = heading.get("polygon")
        if not polygon or len(polygon) != 4:
            continue
        left, top = polygon[0];right,bottom = polygon[2];height = max(1,bottom-top)
        ends = [match.end() for label in LABELS[field]
                for match in re.finditer(re.escape(label),folded(heading["text"]))]
        end = max(ends,default=len(heading["text"]))
        words,cursor = [],0
        for word in heading.get("words",[]):
            position = heading["text"].find(word["text"],cursor)
            cursor = position+len(word["text"])
            label_fragment = re.sub(r"[^a-z]","",folded(word["text"])) in {
                "full","ful","name","fullname","place","of","origin","residence","nationality",
                "placeof","oforigin","ofresidence","lorigins","origins"}
            if position >= end and not label_fragment and word["confidence"] >= .75 and any(ch.isalnum() for ch in word["text"]):
                words.append(word)
        order = 0
        if words and (field != "full_name" or len(words) >= 2):
            x = min(word["bbox"][0] for word in words)
            add(field,(x*sx-2,top*sy-2,right*sx+2,bottom*sy+2),order)
            order += 1
        for following in lines[i+1:i+4]:
            box = following.get("polygon")
            if not box:
                break
            x,y = box[0];r,b = box[2]
            if y-bottom > 3*height:
                break
            if x < left-2*height:
                continue
            if label_of(following["text"]):
                break
            if field == "full_name":
                # Sparse OCR often inserts a tiny portrait/QR fragment between
                # the printed Full name heading and the actual name row. Skip
                # such fragments and keep looking; the first plausible line
                # must align with the heading and contain at least two words.
                words = [word for word in following.get("words", [])
                         if any(ch.isalpha() for ch in word.get("text", ""))]
                word_count = len(words) if following.get("words") else len(
                    re.findall(r"[^\W\d_]+", following.get("text", "")))
                if abs(x-left)>3*height or r-x<2*height or word_count<2:
                    continue
            elif abs(x-left)>3*height:
                break
            add(field,(x*sx-2,y*sy-2,r*sx+2,b*sy+2),order)
            order += 1
            bottom = b
            if field in {"full_name","nationality"}:
                break
    if raw.get("side") == "front":
        box = birth_date_box(raw, pixels.width, pixels.height)
        if box:
            add("date_of_birth", box)
    if raw.get("side") == "back":
        anchored = residence_value_regions(raw, pixels.width, pixels.height)
        detail = anchored["detail"] if anchored else residence_detail_box(raw, pixels.width, pixels.height, pixels=pixels)
        if detail:
            first = len(regions)
            add("residence_address", detail, 0)
            for region in regions[first:]:
                region["includes_inline_detail"] = True
        dynamic = supplemental_regions(raw, pixels.width, pixels.height, set())
        for field in ("residence_address", "registered_birth_place", "issue_place"):
            boxes = ([anchored["suffix"]] if field == "residence_address" and anchored
                     else dynamic.get(field, []))
            for index, box in enumerate(boxes):
                add(field, box, index + int(field == "residence_address" and bool(detail)))
    excluded = {region["field"] for region in regions}
    # Address crops selected from sparse layout can contain only a label.
    # Keep a complete-line alternative as an independent reading, not append
    # it to the same address (which would duplicate or concatenate values).
    alternative_fields = {"permanent_address", "place_of_origin"} if raw.get("side") == "front" else set()
    for field, boxes in supplemental_regions(raw, pixels.width, pixels.height,
            excluded - alternative_fields).items():
        if len(regions) + len(boxes) > 24:
            continue  # Never return a truncated multi-line address as complete.
        first = len(regions)
        for order, box in enumerate(boxes):
            add(field, box, order)
        if field in excluded:
            for region in regions[first:]:
                region["variant"] = "complete_line"
    # Printed-layout fallback, NOT saved identity data. Only admit a closely
    # cropped old CCCD identified by its two address headings. New cards and
    # portrait/uncropped images must not use these layout regions.
    old_layout_labels = {key for key in
        ("full_name", "date_of_birth", "nationality", "permanent_address")
        if any(has_label(line["text"], key) for a in attempts for line in a.get("lines", []))}
    explicit_old_headings = all(any(has_label(line["text"], key)
        for a in attempts for line in a.get("lines", []))
        for key in ("place_of_origin", "permanent_address"))
    has_origin_heading = any(has_label(line["text"], "place_of_origin")
        for a in attempts for line in a.get("lines", []))
    new_layout_labels = all(any(has_label(line["text"], key)
        for a in attempts for line in a.get("lines", []))
        for key in ("residence_address", "registered_birth_place"))
    # The origin/expiry headings are the two easiest old-card labels to lose
    # under glare. Identify the printed layout from four other independent
    # headings instead of requiring the very heading whose value we must rescue.
    old_layout = (explicit_old_headings or len(old_layout_labels) == 4
        or (has_origin_heading and len(old_layout_labels) >= 3)) and not new_layout_labels
    if raw.get("side") == "front" and old_layout and 1.45 <= pixels.width/pixels.height <= 1.7:
        available = {**parse_fields(raw), **parse_additional_fields(raw)}
        # A value row can be cut off by Tesseract's short bbox. Replace its
        # incomplete crops with a full printed row while keeping the card
        # layout gate above; partial readings must not block this fallback.
        incomplete_addresses = {field for field in ("place_of_origin", "permanent_address")
            if len([part for part in (available.get(field) or "").split(",") if part.strip()]) < 3}
        inline_residence = [match.group(1).split() for attempt in attempts
            for line in attempt.get("lines", [])
            if (match := re.search(r"place of residence\s*:\s*(.+)$", folded(line.get("text", ""))))]
        if any(words and (len(words) <= 2 or len(words[-1].strip(" .,:;")) == 1)
               for words in inline_residence):
            incomplete_addresses.add("permanent_address")
        replace_regions = incomplete_addresses | ({"nationality"} if not available.get("nationality") else set())
        regions = [region for region in regions if region["field"] not in replace_regions]
        excluded = {region["field"] for region in regions}
        templates = {
            "nationality": [(.81,.64,.995,.73)],
            "place_of_origin": [(.29,.77,.97,.84)],
            # Include lower diacritics/descenders, not only the text baseline.
            "permanent_address": [(.66,.84,.98,.905),(.29,.895,.97,.965)],
            "expiry_date": [(.14,.855,.30,.925)],
        }
        for field, boxes in templates.items():
            if (field not in replace_regions and (available.get(field) or field in excluded)
                    or len(regions)+len(boxes)>24):
                continue
            first = len(regions)
            for order, box in enumerate(boxes):
                add(field, tuple(value*dimension for value,dimension in zip(box,
                    (pixels.width,pixels.height,pixels.width,pixels.height))), order)
            for region in regions[first:]:
                region["variant"] = "old_card_layout"
    return regions


def read_regions(pixels, raw, *, root, threads, deadline):
    if any(not (Path(root)/name).is_file() for name in ("config.yml","manifest.json","vgg_seq2seq.pth")):
        raise DocumentError("OCR_MODEL_UNAVAILABLE","Model VietOCR chưa được cài; build lại Document image.",503)
    regions = crop_regions(pixels,raw)
    if not regions:
        return []
    payload = json.dumps([{"id":i,"image":region["image"]} for i,region in enumerate(regions)]).encode("utf-8")
    if len(payload)>8*1024*1024:
        raise DocumentError("OCR_UNAVAILABLE","Vùng ảnh VietOCR vượt giới hạn.",503)
    remaining = deadline-time.monotonic()
    if remaining<=0:
        raise DocumentError("OCR_TIMEOUT","OCR quá thời gian xử lý.",504)
    env = {key:os.environ[key] for key in ("PATH","SYSTEMROOT","WINDIR","TEMP","TMP") if key in os.environ}
    env.update(DOCUMENT_VIETOCR_MODEL_DIR=root,OMP_THREAD_LIMIT=str(threads),OPENBLAS_NUM_THREADS=str(threads))
    with subprocess.Popen([sys.executable,"-m","app.services.vietocr_worker"],stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,env=env,cwd=str(Path(__file__).resolve().parents[2])) as process:
        try:
            output,_ = process.communicate(payload,timeout=remaining)
        except subprocess.TimeoutExpired:
            process.kill();process.communicate()
            raise DocumentError("OCR_TIMEOUT","VietOCR quá thời gian xử lý.",504) from None
    if process.returncode!=0 or len(output)>128*1024:
        raise DocumentError("OCR_UNAVAILABLE","Không thể chạy VietOCR local.",503)
    try:
        result = json.loads(output)
        if not isinstance(result,list) or len(result)!=len(regions):
            raise ValueError
        groups,seen,detail_groups = {},set(),set()
        for item in result:
            index = item["id"]
            if type(index) is not int or index in seen or not 0<=index<len(regions):
                raise ValueError
            seen.add(index)
            text,score = item["text"],item["confidence"]
            if not isinstance(text,str) or len(text)>2000 or type(score) not in (int,float) or not math.isfinite(score) or not 0<=score<=1:
                raise ValueError
            region = regions[index]
            group = (region["field"],region.get("variant","primary"))
            groups.setdefault(group,[]).append((region["order"],text,score))
            if region.get("includes_inline_detail"):
                detail_groups.add(group)
        attempts = []
        registered_heading = any(resolve_label(line.get("text", ""))["field"] == "registered_birth_place"
            for attempt in [raw] + raw.get("attempts", []) for line in attempt.get("lines", []))
        for (field,variant),values in groups.items():
            values.sort()
            if any(not text.strip() or score<.75 for _,text,score in values):
                continue
            # A wrapped printed line is whitespace, not evidence of a comma.
            separator = " "
            text = separator.join(value[1].strip() for value in values)
            if field == "residence_address":
                text = format_residence_lines([value[1] for value in values])
            if field == "registered_birth_place" and raw["side"] == "back" and registered_heading:
                text = re.sub(r"^\s*place\s+of\s+birth\s*[:/.-]*\s*", "", text, flags=re.IGNORECASE)
            attempts.append({"side":raw["side"],"source":"vietocr_field","field":field,
                "variant":variant,"model":"vgg_seq2seq","lines":[{"text":text,"confidence":min(value[2] for value in values)}]})
            if (field,variant) in detail_groups and len(values)>=2:
                attempts[-1]["includes_inline_detail"] = True
                attempts[-1]["address_lines"] = [value[1].strip() for value in values]
        return attempts
    except (ValueError,KeyError,TypeError):
        raise DocumentError("OCR_BAD_RESPONSE","Kết quả VietOCR không hợp lệ.",502) from None
