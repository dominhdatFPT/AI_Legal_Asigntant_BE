"""Conservative label-based CCCD extraction. Never verify identity or guess digits."""
from datetime import date, datetime
import re
import unicodedata

FIELDS = ("full_name", "identity_number", "date_of_birth", "issue_date", "issue_place", "permanent_address")
ADDITIONAL_FIELDS = ("sex", "nationality", "place_of_origin", "expiry_date", "residence_address", "registered_birth_place")
LABELS = {
    "full_name": ("ho, chu dem va ten khai sinh", "ho va ten", "full name"),
    "identity_number": ("so dinh danh ca nhan", "personal identification number", "so", "no", "identity number"),
    "date_of_birth": ("ngay, thang, nam sinh", "ngay sinh", "date of birth"),
    "issue_date": ("ngay, thang, nam cap", "ngay cap", "date of issue", "issued on"),
    "issue_place": ("noi cap", "place of issue"),
    "permanent_address": ("dia chi thuong tru", "noi thuong tru", "place of residence", "permanent address"),
    "sex": ("gioi tinh", "sex"),
    "nationality": ("quoc tich", "nationality"),
    "place_of_origin": ("que quan", "place of origin"),
    "expiry_date": ("co gia tri den", "ngay, thang, nam het han", "ngay het han", "date of expiry", "expiry date"),
    "residence_address": ("noi cu tru", "place of residence"),
    "registered_birth_place": ("noi dang ky khai sinh", "place of birth"),
}


def has_label(text, key):
    return any(re.search(r"(?<![a-z])" + re.escape(label) + r"(?=$|[\s:/.,-])", folded(text)) for label in LABELS[key])


def following_values(lines, index):
    box = _bounds(lines[index])
    if not box:
        return []
    left, top, right, bottom = box
    height = max(1, bottom - top)
    result = []
    for line in lines[index + 1:index + 4]:
        other = _bounds(line)
        if not other or other[1] - bottom > 3 * height:
            break
        if other[1] >= top and other[0] <= right and other[2] >= left:
            if label_of(line["text"]):
                break
            result.append(line)
    return result


def _bounds(line):
    polygon = line.get("polygon")
    return (*polygon[0], *polygon[2]) if polygon and len(polygon) == 4 else None


def _strip_labels(text, key):
    value = unicodedata.normalize("NFC", text)
    for label in sorted(LABELS[key], key=len, reverse=True):
        match = re.search(re.escape(label), folded(value))
        if match:
            value = value[:match.start()] + value[match.end():]
    return value.strip(" :/.-")


def _date_value(text, *, future=False):
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y-%m-%d"):
        try:
            candidate = datetime.strptime(text, fmt).date()
            if date(1900, 1, 1) <= candidate <= (date(2200, 1, 1) if future else date.today()):
                return candidate.isoformat()
        except ValueError:
            pass
    return None


def _dates(line, *, future=False):
    # Score only the date word: a weak English label must not discard it.
    words = line.get("words") or [{"text": line["text"], "confidence": line["confidence"]}]
    values = []
    for word in words:
        if word["confidence"] < .75:
            continue
        for token in re.findall(r"(?<!\d)(?:\d{1,2}[/.-]\d{1,2}[/.-]\d{4}|\d{4}-\d{2}-\d{2})(?!\d)", word["text"]):
            parsed = _date_value(token, future=future)
            if parsed:
                values.append(parsed)
    return values


def _address(lines, index, key):
    heading = lines[index]
    box = _bounds(heading)
    words = heading.get("words", [])
    label_scores = []
    for label in LABELS[key]:
        label_tokens = label.split()
        tokens = [re.sub(r"[^a-z]", "", folded(word["text"])) for word in words]
        for start in range(len(tokens) - len(label_tokens) + 1):
            if tokens[start:start+len(label_tokens)] == label_tokens:
                label_scores.append(min(word["confidence"] for word in words[start:start+len(label_tokens)]))
    if max(label_scores, default=heading["confidence"]) < .75:
        return None
    value = _strip_labels(heading["text"], key)
    # Sparse OCR can split 'Place of origin' across lines. It is a label,
    # never the user's address. A weak inline remainder is not a value either.
    fragments = {"place", "place of", "of origin", "origin", "place of origin",
                 "of residence", "residence", "place of residence", "of birth", "birth", "place of birth"}
    if folded(value).strip(" :/.-") in fragments or heading["confidence"] < .75:
        value = ""
    parts = [value]
    if box:
        left, top, right, bottom = box
        height = max(1, bottom - top)
        for following in lines[index + 1:]:
            other = _bounds(following)
            if not other:
                break
            x, y, _, end = other
            if y - bottom > 3 * height:
                break
            # Expiry below the portrait is a separate column, not an address.
            if x < left - 2 * height:
                continue
            if folded(following["text"]).strip(" :/.-") in fragments:
                continue
            if abs(x - left) > 3 * height or label_of(following["text"]) or following["confidence"] < .75:
                break
            parts.append(following["text"].strip())
            bottom = end
    elif not parts[0] and index + 1 < len(lines):
        following = lines[index + 1]
        if following["confidence"] >= .75 and label_of(following["text"]) is None:
            parts.append(following["text"].strip())
    value = " ".join(part for part in parts if part)
    # A damaged heading can leave only decoration such as "_" or "-".
    # It is not an address and must not outrank an independently read region.
    return value if (value and len(value) <= 2000
        and re.search(r"[^\W\d_]", value)
        and not any(ord(ch) < 32 or ord(ch) == 127 for ch in value)) else None


def folded(value):
    return "".join(ch for ch in unicodedata.normalize("NFD", value.lower().replace("đ", "d"))
                   if unicodedata.category(ch) != "Mn")


def normalize_issue_place(value):
    # Restore accents only for a complete known name. Do not fuzzy-match,
    # expand abbreviations or replace a different/incomplete issuing authority.
    authorities = ("CỤC CẢNH SÁT QUẢN LÝ HÀNH CHÍNH VỀ TRẬT TỰ XÃ HỘI",)
    tokens = re.findall(r"[a-z0-9]+", folded(value))
    for authority in authorities:
        if tokens == re.findall(r"[a-z0-9]+", folded(authority)):
            return authority
    return value


def _nationality_value(value):
    """Accept a nationality value, never a misplaced printed field label."""
    value = value.strip()
    if (not value or len(value) > 100
            or any(has_label(value, key) for key in LABELS)
            or not re.search(r"[^\W\d_]", value)
            or any(not (ch.isalpha() or ch in " '-") for ch in value)):
        return None
    return value


def label_of(text):
    text = folded(text).strip()
    text = re.sub(r"^[0-9il][.)]?\s+", "", text)
    for key, labels in LABELS.items():
        for label in labels:
            if re.match(r"^" + re.escape(label) + r"(?=$|[\s:/.-])", text):
                # 'Place of residence' alone can be current, not permanent.
                # Remove it from bilingual headings, but never infer its semantics.
                if key == "permanent_address" and label == "place of residence":
                    continue
                return key
    # Printed bilingual labels may have a damaged Vietnamese prefix.
    for key, english in (("full_name", "full name"), ("identity_number", "personal identification number")):
        if re.search(r"\b" + re.escape(english) + r"\b", text):
            return key
    return None


def _parse_single(raw):
    # Ignore whitespace-only lines emitted by Tesseract between a label and value.
    lines = [line for line in raw["lines"] if line["text"].strip()]
    found = {key: [] for key in FIELDS}
    if raw.get("source") in {"new_card_region", "vietocr_field", "numeric_field"} and raw.get("field") in FIELDS:
        field = raw["field"]
        value = region_value(raw)
        if value:
            found[field].append(value)
    for i, line in enumerate(lines):
        key = label_of(line["text"])
        if key == "identity_number":
            # A weak bilingual label must not discard a clearly read number.
            numbers = {word["text"] for word in line.get("words", [])
                       if re.fullmatch(r"[0-9]{12}", word["text"]) and word["confidence"] >= .9}
            if len(numbers) == 1:
                found[key].extend(numbers)
        if key not in FIELDS or line["confidence"] < .75:
            continue
        # Remove only known labels from bilingual headings, not arbitrary OCR text.
        value = unicodedata.normalize("NFC", line["text"])
        if key == "full_name":
            # The damaged Vietnamese prefix is still a heading when a complete
            # English label follows it; do not turn the leftover prefix into a name.
            ends = [match.end() for label in LABELS[key]
                    for match in re.finditer(re.escape(label), folded(value))]
            if ends:
                value = value[max(ends):]
        for label in sorted(LABELS[key], key=len, reverse=True):
            match = re.search(re.escape(label), folded(value))
            if match:
                value = value[:match.start()] + value[match.end():]
        value = value.strip(" :/.-")
        if not value and i + 1 < len(lines):
            following = lines[i + 1]
            if following["confidence"] >= .75 and label_of(following["text"]) is None:
                value = following["text"].strip()
        if not value or len(value) > {"full_name": 200, "issue_place": 500, "permanent_address": 2000}.get(key, 32):
            continue
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
            continue
        if key == "identity_number":
            if not re.fullmatch(r"[0-9]{12}", value):
                continue
        elif key in {"date_of_birth", "issue_date"}:
            parsed = None
            for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"):
                try:
                    candidate = datetime.strptime(value, fmt).date()
                    if date(1900, 1, 1) <= candidate <= date.today():
                        parsed = candidate.isoformat()
                    break
                except ValueError:
                    pass
            if not parsed:
                continue
            value = parsed
        elif key == "full_name":
            if len(value.split()) < 2 or any(not (ch.isalpha() or ch in " '-") for ch in value):
                continue
        found[key].append(value)
    for i, line in enumerate(lines):
        key = label_of(line["text"])
        if has_label(line["text"], "date_of_birth"):
            found["date_of_birth"].extend(_dates(line))
            for following in following_values(lines, i):
                found["date_of_birth"].extend(_dates(following))
        # Older CCCD back cards print 'Ngày, tháng, năm / Date, month, year'.
        issue_heading = raw.get("side") == "back" and re.match(r"^ngay\s*,?\s*thang\s*,?\s*nam\b", folded(line["text"]))
        if has_label(line["text"], "issue_date") or issue_heading:
            found["issue_date"].extend(_dates(line))
            for following in following_values(lines, i):
                found["issue_date"].extend(_dates(following))
        if raw.get("side") == "back" and has_label(line["text"], "issue_place"):
            for following in following_values(lines, i):
                if following["confidence"] >= .75 and len(following["text"]) <= 500:
                    found["issue_place"].append(following["text"].strip())
                    break
        if raw.get("side") == "back" and re.search(r"\bbo cong an\b", folded(line["text"])):
            authority_words = [w for w in line.get("words", []) if re.sub(r"[^a-z]", "", folded(w["text"])) in {"bo", "cong", "an"}]
            if len(authority_words) >= 3 and min(w["confidence"] for w in authority_words) >= .75:
                found["issue_place"].append("BỘ CÔNG AN")
        if key == "permanent_address":
            address = _address(lines, i, key)
            if address:
                # Replace the incomplete first-line candidate, not append a conflict.
                short = _strip_labels(line["text"], key)
                found[key] = [v for v in found[key] if v != short]
                found[key].append(address)
        if raw.get("side") == "back" and issue_heading:
            # Extract the printed issuing authority, excluding the officer title
            # and signature. Never insert a default authority absent from OCR.
            authority = []
            for following in lines[i + 1:i + 4]:
                text = following["text"].strip()
                normalized = folded(text)
                if "cuc canh sat" in normalized:
                    start = normalized.index("cuc canh sat")
                    words = following.get("words", [])
                    scores = [word["confidence"] for word in words if folded(word["text"].strip(" :/.-")) in {"cuc", "canh", "sat"}]
                    if min(scores, default=following["confidence"]) >= .75:
                        authority.append(text[start:].strip(" :/.-"))
                elif authority and normalized.startswith("quan ly hanh chinh") and following["confidence"] >= .75:
                    authority.append(text)
                elif authority:
                    break
            value = " ".join(authority)
            if value and len(value) <= 500 and not any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
                found["issue_place"].append(value)
    # The front-card number immediately precedes the full-name heading. Use the
    # confident digit word when the bilingual number label is misread. Never
    # substitute digits or scan arbitrary numbers elsewhere/on the back card.
    if raw.get("side") == "front":
        for i, heading in enumerate(lines):
            if i == 0 or label_of(heading["text"]) != "full_name":
                continue
            previous = lines[i - 1]
            if label_of(previous["text"]) not in (None, "identity_number"):
                continue
            polygon = heading.get("polygon", [])
            if len(polygon) != 4:
                continue
            left, top = polygon[0]
            right, bottom = polygon[2]
            height = bottom - top
            for word in previous.get("words", []):
                if word["confidence"] < .9 or not re.fullmatch(r"[0-9]{12}", word["text"]):
                    continue
                x, y, width, word_height = word["bbox"]
                # A short name heading is narrower than the printed number.
                # Bound the number relative to heading height, not heading width.
                if (height > 0 and word_height > 0 and left - height <= x <= left + 8 * height
                        and 0 < width <= 16 * max(height, word_height)
                        and y < top and 0 <= top - (y + word_height) <= 3 * max(height, word_height)):
                    found["identity_number"].append(word["text"])
    found["issue_place"] = [normalize_issue_place(value) for value in found["issue_place"]]
    return {key: values[0] if values and len(set(values)) == 1 else None for key, values in found.items()}


def merge_fields(front, back):
    result, warnings = {}, []
    for key in FIELDS:
        if front[key] and back[key] and front[key] != back[key]:
            result[key] = None
            warnings.append("CONFLICT_" + key.upper())
        else:
            result[key] = front[key] or back[key]
        if not result[key]:
            warnings.append("REVIEW_" + key.upper())
    return result, warnings


def _parse_additional_single(raw):
    """OCR hints only; confirmed Document profile keeps its existing six fields."""
    lines = [line for line in raw.get("lines", []) if line["text"].strip()]
    found = {key: [] for key in ADDITIONAL_FIELDS}
    if raw.get("source") in {"new_card_region", "vietocr_field"} and raw.get("field") in ADDITIONAL_FIELDS:
        value = region_value(raw)
        if value:
            found[raw["field"]].append(value)
    for i, line in enumerate(lines):
        text = line["text"]
        key = label_of(text)
        if has_label(text, "expiry_date"):
            key = "expiry_date"
            found[key].extend(_dates(line, future=True))
            for following in following_values(lines, i):
                found[key].extend(_dates(following, future=True))
        for address_key in ("place_of_origin", "residence_address", "registered_birth_place"):
            if has_label(text, address_key):
                value = _address(lines, i, address_key)
                if value:
                    found[address_key].append(value)
        # Sex and nationality share a single row on the old card.
        for field in ("sex", "nationality"):
            pattern = "|".join(re.escape(label) for label in LABELS[field])
            matches = list(re.finditer(r"(?:" + pattern + r")(?=$|[\s:/.-])", folded(text)))
            if not matches:
                continue
            start = matches[-1].end()
            end = len(text)
            if field == "sex":
                next_label = re.search(r"quoc tich|nationality", folded(text)[start:])
                if next_label:
                    end = start + next_label.start()
            value = text[start:end].strip(" :/.-")
            if not value or len(value) > 100:
                continue
            words = line.get("words", [])
            # Match word offsets in this value segment; the word 'Nam' can
            # occur both as sex and in 'Việt Nam' elsewhere on the same row.
            value_words, cursor = [], 0
            for word in words:
                position = text.find(word["text"], cursor)
                if position < 0:
                    continue
                cursor = position + len(word["text"])
                if position >= start and cursor <= end and word["text"].strip(" :/.-"):
                    value_words.append(word)
            score = min((w["confidence"] for w in value_words), default=line["confidence"])
            if score < .75:
                continue
            if field == "sex":
                value = {"nam": "male", "male": "male", "nu": "female", "female": "female"}.get(folded(value))
            elif field == "nationality":
                value = _nationality_value(value)
            if value:
                found[field].append(value)
        for following in following_values(lines, i):
            if has_label(text, "sex"):
                for word in following.get("words", []):
                    sex = {"nam":"male", "nu":"female", "male":"male", "female":"female"}.get(folded(word["text"]).strip(" :/.-"))
                    if sex and word["confidence"] >= .75:
                        found["sex"].append(sex)
            if has_label(text, "nationality") and following["confidence"] >= .75:
                value = _nationality_value(following["text"])
                if value:
                    found["nationality"].append(value)
                break
    return {key: values[0] if values and len(set(values)) == 1 else None for key, values in found.items()}


def _with_attempts(raw, parser):
    result = parser(raw)
    attempts = [parser(attempt) for attempt in raw.get("attempts", [])]
    for key in result:
        if result[key] is None:
            candidates = {attempt[key] for attempt in attempts if attempt[key] is not None}
            if len(candidates) == 1:
                result[key] = candidates.pop()
    # A heading failure can leave only the administrative suffix. Prefer a
    # separately read street+suffix ONLY if all readings agree on that suffix.
    # Never copy street information from birthplace or a different address.
    complete_residence = [(a, values["residence_address"]) for a,values in zip(raw.get("attempts", []), attempts)
        if a.get("includes_inline_detail") and values.get("residence_address")
        and a.get("source") in {"vietocr_field", "new_card_region"}]
    if "residence_address" in result and complete_residence:
        def tokens(value):
            return re.findall(r"[a-z0-9]+", folded(value))
        readings = [v["residence_address"] for v in attempts if v.get("residence_address")]
        if result.get("residence_address"):
            readings.append(result["residence_address"])
        for a,value in sorted(complete_residence, key=lambda item: min(l["confidence"] for l in item[0]["lines"]), reverse=True):
            full = tokens(value)
            if all(full[-len(tokens(other)):] == tokens(other) for other in readings):
                result["residence_address"] = value
                break
    for attempt, values in zip(raw.get("attempts", []), attempts):
        if attempt.get("source") != "vietocr_field":
            continue
        for key,value in values.items():
            if not value:
                continue
            normalized = re.sub(r"[^a-z0-9]", "", folded(value))
            others = [result[key]] if result[key] else [item[key] for item in attempts if item[key]]
            if all(re.sub(r"[^a-z0-9]", "", folded(other)) == normalized for other in others):
                regional = [(a, item[key]) for a, item in zip(raw.get("attempts", []), attempts)
                            if a.get("source") == "new_card_region" and item[key]
                            and all(l["confidence"] >= .9 for l in a.get("lines", []))]
                if regional:
                    result[key] = restore_missing_tones(regional[0][1], value)
                    continue
                if result[key] and any(c.isalpha() and ord(c)>127 for c in result[key]) and result[key] != value:
                    # Letter agreement alone cannot prove a different Vietnamese tone.
                    if key == "full_name" and all(l["confidence"] >= .88 for l in attempt.get("lines", [])):
                        result[key] = value
                    elif key in {"permanent_address", "place_of_origin", "residence_address", "registered_birth_place"}:
                        result[key] = restore_missing_tones(result[key], value, punctuation=True)
                    continue
                # Restore Vietnamese accents/case when readings agree on the
                # actual letters, or fill a field the other engine missed.
                result[key] = value
    for key in ("permanent_address", "place_of_origin", "residence_address", "registered_birth_place"):
        if result.get(key):
            result[key] = normalize_address_tones(result[key])
    return result


def normalize_address_tones(value):
    """Standardize Vietnamese tone placement and a known admin-label OCR slip."""
    value = unicodedata.normalize("NFC", value)
    equivalents = {"oà": "òa", "oá": "óa", "oả": "ỏa", "oã": "õa", "oạ": "ọa",
                   "oè": "òe", "oé": "óe", "oẻ": "ỏe", "oẽ": "õe", "oẹ": "ọe"}
    def word(match):
        token = match.group(0)
        for old, new in equivalents.items():
            if token.lower().endswith(old):
                ending = new.upper() if token[-2:].isupper() else new
                return token[:-2] + ending
        return token
    value = re.sub(r"[^\W\d_]+", word, value)
    # "thị trần" is not an administrative unit; card glare commonly turns
    # the final sắc in "thị trấn" into a huyền mark. Preserve letter case.
    def township(match):
        source = match.group(0)
        if source.isupper():
            return "THỊ TRẤN"
        return "Thị trấn" if source[0].isupper() else "thị trấn"
    return re.sub(r"\bthị\s+trần\b", township, value, flags=re.IGNORECASE)


def restore_missing_tones(value, candidate, punctuation=False):
    """Fill unaccented words only; never replace an existing printed-reading tone."""
    tokens = list(re.finditer(r"[^\W_]+", value))
    other = re.findall(r"[^\W_]+", candidate)
    if len(tokens) != len(other) or any(folded(a.group()) != folded(b) for a,b in zip(tokens,other)):
        return value
    if punctuation:
        positions = list(re.finditer(r"[^\W_]+", candidate))
        for original, position in reversed(list(zip(tokens, positions))):
            if not original.group().isascii():
                candidate = candidate[:position.start()] + original.group() + candidate[position.end():]
        return candidate
    for token, replacement in reversed(list(zip(tokens,other))):
        if token.group().isascii() and token.group().isalpha() and not replacement.isascii():
            value = value[:token.start()] + replacement + value[token.end():]
    return value


def format_residence_lines(lines):
    """A printed line break is whitespace, not an administrative comma.

    Keep observed commas; never create a boundary between the street and the
    first administrative word merely because the card wraps onto another row.
    """
    values = [line.strip() for line in lines]
    return " ".join(values)


def region_value(raw):
    lines = [line for line in raw.get("lines", []) if line["text"].strip()]
    if not lines or any(line["confidence"] < .75 for line in lines):
        return None
    if (raw.get("source") == "new_card_region" and raw.get("field") == "residence_address"
            and raw.get("includes_inline_detail") and len(lines) < 2):
        return None  # A successfully read street alone is not a complete address.
    if raw.get("field") in {"date_of_birth", "issue_date", "expiry_date"}:
        dates = {value for line in lines for value in _dates(line, future=raw.get("field") == "expiry_date")}
        return dates.pop() if len(dates) == 1 else None
    value = " ".join(line["text"].strip() for line in lines)
    if raw.get("field") == "residence_address":
        value = format_residence_lines([line["text"] for line in lines])
    if len(value) > 2000 or any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        return None
    if raw["field"] in {"permanent_address", "place_of_origin", "residence_address", "registered_birth_place"}:
        # These are VALUE-only crops. A misplaced crop of the bilingual
        # heading is not an address and must not conflict with a true value.
        if any(has_label(value, key) for key in
                ("permanent_address", "place_of_origin", "residence_address", "registered_birth_place")):
            return None
        if re.search(r"\b(?:place|origin|residence|birth)\b", folded(value)):
            return None
        if not re.search(r"[^\W\d_]", value):
            return None
    if raw["field"] == "issue_place":
        if re.search(r"\bbo cong an\b", folded(value)):
            return "BỘ CÔNG AN"
        if "cuc canh sat" in folded(value):
            complete = "CỤC CẢNH SÁT QUẢN LÝ HÀNH CHÍNH VỀ TRẬT TỰ XÃ HỘI"
            if re.findall(r"[a-z]+",folded(value)) != re.findall(r"[a-z]+",folded(complete)):
                return None  # A partial heading is not a complete issuer.
            return complete
        return value if len(value) <= 500 else None
    if raw["field"] == "full_name":
        return value if len(value)<=200 and len(value.split())>=2 and all(ch.isalpha() or ch in " '-" for ch in value) else None
    if raw["field"] == "sex":
        return {"nam": "male", "male": "male", "nu": "female", "female": "female"}.get(folded(value))
    if raw["field"] == "nationality":
        return _nationality_value(value)
    return value


def residence_components(raw, value):
    """Form mapping from two independently read residence rows, never birthplace.

    Preserve the raw address string. Interpret a numeric comma inside the
    separately located street row as a separator, not a province boundary.
    """
    if not value or raw.get("side") != "back":
        return None
    normalize = lambda text: re.sub(r"[^a-z0-9]", "", folded(text))
    candidates=[]
    for attempt in raw.get("attempts", []):
        rows=attempt.get("address_lines", [])
        lines=attempt.get("lines", [])
        if (attempt.get("field") != "residence_address" or not attempt.get("includes_inline_detail")
                or attempt.get("source") not in {"new_card_region", "vietocr_field"}
                or len(rows) != 2 or not lines or any(l["confidence"] < .75 for l in lines)):
            continue
        if normalize(format_residence_lines(rows)) != normalize(value):
            continue
        suffix=[part.strip() for part in rows[1].split(",")]
        if len(suffix) < 2 or any(not part for part in suffix):
            continue
        detail=re.sub(r"^(\d+[A-Za-z]?)\s*,\s*(?=\S)", r"\1 ", rows[0].strip())
        parts={key:normalize_address_tones(text) for key,text in
               {"detail":detail,"ward":", ".join(suffix[:-1]),"province":suffix[-1]}.items()}
        if any(not text or len(text)>2000 for text in parts.values()):
            continue
        candidates.append((min(l["confidence"] for l in lines),parts))
    if not candidates:
        return None
    if len({tuple(normalize(p[key]) for key in ("detail","ward","province")) for _,p in candidates}) != 1:
        return None
    return max(candidates,key=lambda item:item[0])[1]


def parse_fields(raw):
    result = _with_attempts(raw, _parse_single)
    if raw.get("side") == "back":
        # Owner identity is printed on the front; MRZ consistency is checked
        # separately. Back-side background/officer text is not another owner.
        for key in ("full_name", "identity_number", "date_of_birth"):
            result[key] = None
    return result


def parse_additional_fields(raw):
    result = _with_attempts(raw, _parse_additional_single)
    # If both independently printed addresses name the same administrative
    # suffix, retain the stronger regional reading's tones in that suffix.
    names = ("residence_address", "registered_birth_place")
    if all(result.get(key) for key in names):
        parts = {key: [p.strip() for p in result[key].split(",")] for key in names}
        if all(len(parts[key]) >= 3 for key in names):
            tails = {key: ", ".join(parts[key][-3:]) for key in names}
            for key in names:
                regions = [a for a in raw.get("attempts", []) if a.get("source") == "new_card_region"
                           and a.get("field") == key and a.get("lines")]
                if regions:
                    strongest = max(regions, key=lambda a: a["lines"][-1]["confidence"])
                    printed_tail = normalize_address_tones(strongest["lines"][-1]["text"].strip())
                    if len(printed_tail.split(",")) == 3 and folded(result[key]).endswith(folded(printed_tail)):
                        tails[key] = printed_tail
            if folded(tails[names[0]]) == folded(tails[names[1]]):
                scores = {key: max((a["lines"][-1]["confidence"] if a.get("lines") else 0
                    for a in raw.get("attempts", []) if a.get("source") == "new_card_region" and a.get("field") == key), default=0)
                    for key in names}
                winner = max(names, key=lambda key: scores[key])
                if scores[winner] >= .9 and scores[names[0]] != scores[names[1]]:
                    for key in names:
                        if folded(result[key]).endswith(folded(tails[key])):
                            result[key] = result[key][:-len(tails[key])] + tails[winner]
    return result
