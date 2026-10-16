"""Crop Vietnamese text, then run an offline VietOCR child with a hard timeout."""
import base64
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
from app.services.identity_parser import LABELS, folded, label_of, format_residence_lines

TEXT_FIELDS = {"full_name", "permanent_address", "place_of_origin", "nationality", "issue_place",
               "residence_address", "registered_birth_place"}


def residence_detail_box(raw, width, height):
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
    attempts = [raw] + [a for a in raw.get("attempts", []) if a.get("psm") == 3 and not a.get("source")]
    selected = max(attempts, key=lambda a:sum(bool(l["text"].strip()) for l in a.get("lines",[])))
    lines = [line for line in selected.get("lines",[]) if line["text"].strip() and "<" not in line["text"]]
    size = selected.get("ocr_size",raw["ocr_size"])
    sx, sy = pixels.width/size["width"], pixels.height/size["height"]
    for i, heading in enumerate(lines):
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
            if label_of(following["text"]) or abs(x-left)>3*height:
                break
            add(field,(x*sx-2,y*sy-2,r*sx+2,b*sy+2),order)
            order += 1
            bottom = b
            if field in {"full_name","nationality"}:
                break
    text = " ".join(folded(l["text"]) for a in attempts for l in a.get("lines",[]))
    if raw.get("side") == "back" and "of issue" in text and "of expiry" in text:
        detail = residence_detail_box(raw, pixels.width, pixels.height)
        if detail:
            add("residence_address", detail, 0)
        for field, (left,top,right,bottom) in (
            ("residence_address",(.055,.092,.47,.147)),
            ("registered_birth_place",(.055,.235,.47,.29)),
            ("issue_place",(.28,.48,.75,.56))):
            add(field,(left*pixels.width,top*pixels.height,right*pixels.width,bottom*pixels.height),
                1 if field == "residence_address" and detail else 0)
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
        groups,seen = {},set()
        for item in result:
            index = item["id"]
            if type(index) is not int or index in seen or not 0<=index<len(regions):
                raise ValueError
            seen.add(index)
            text,score = item["text"],item["confidence"]
            if not isinstance(text,str) or len(text)>2000 or type(score) not in (int,float) or not math.isfinite(score) or not 0<=score<=1:
                raise ValueError
            region = regions[index]
            groups.setdefault(region["field"],[]).append((region["order"],text,score))
        attempts = []
        for field,values in groups.items():
            values.sort()
            if any(not text.strip() or score<.75 for _,text,score in values):
                continue
            # A wrapped printed line is whitespace, not evidence of a comma.
            separator = " "
            text = separator.join(value[1].strip().strip(",") for value in values)
            if field == "residence_address":
                text = format_residence_lines([value[1] for value in values])
            attempts.append({"side":raw["side"],"source":"vietocr_field","field":field,
                "model":"vgg_seq2seq","lines":[{"text":text,"confidence":min(value[2] for value in values)}]})
        return attempts
    except (ValueError,KeyError,TypeError):
        raise DocumentError("OCR_BAD_RESPONSE","Kết quả VietOCR không hợp lệ.",502) from None
