"""Pair consistency for Vietnamese chip-card TD1 MRZ, not authenticity/KYC.

ICAO Doc 9303 Part 5 defines TD1 positions and check digits. The supported
Vietnamese layout repeats the full 12-digit citizen number in optional data.
No OCR character substitutions or identity-number guessing are permitted.
"""
import re
from app.core.errors import DocumentError
from app.services.identity_parser import folded, parse_additional_fields


def check_digit(value):
    return str(sum((int(ch) if ch.isdigit() else 0 if ch == "<" else ord(ch) - 55)
                  * (7, 3, 1)[i % 3] for i, ch in enumerate(value)) % 10)


def _parse_td1(rows):
    first, second, name = rows
    if not re.fullmatch(r"IDVNM[0-9]{10}[0-9]{12}<<[0-9]", first):
        return None
    if not re.fullmatch(r"[0-9]{7}[MF<][0-9]{7}VNM[A-Z0-9<]{11}[0-9]", second):
        return None
    if not re.fullmatch(r"[A-Z<]{30}", name) or "<<" not in name:
        return None
    if first[5:14] != first[18:27]:
        return None
    checks = ((first[5:14], first[14]), (second[:6], second[6]),
              (second[8:14], second[14]),
              (first[5:30] + second[:7] + second[8:15] + second[18:29], second[29]))
    if any(check_digit(value) != digit for value, digit in checks):
        return None
    return {"number": first[15:27], "birth": second[:6], "expiry": second[8:14],
            "sex": second[7], "name": " ".join(name.replace("<", " ").split())}


def mrz_candidates(raw):
    result = []
    for attempt in [raw] + raw.get("attempts", []):
        lines = [re.sub(r"\s+", "", line["text"]).upper() for line in attempt.get("lines", [])]
        lines = [line for line in lines if re.fullmatch(r"[A-Z0-9<]{30}", line)]
        for i in range(len(lines) - 2):
            candidate = _parse_td1(lines[i:i + 3])
            if candidate and candidate not in result:
                result.append(candidate)
    return result


def ensure_card_pair(front_fields, front_raw, back_raw):
    candidates = mrz_candidates(back_raw)
    if not candidates or not front_fields.get("identity_number"):
        raise DocumentError("IDENTITY_PAIR_UNVERIFIABLE",
            "Chưa đối chiếu được hai mặt CCCD. Hãy chụp rõ vùng MRZ (ba dòng chữ phía dưới mặt sau) và thử lại.", 422)
    extra = parse_additional_fields(front_raw)
    # A visual expiry OCR can misread one year digit. Reconcile only when a
    # separate visual reading agrees with the checksum-valid MRZ; never invent
    # a date from the MRZ alone or bypass a citizen-number mismatch.
    expiry_readings = {value for attempt in [front_raw] + front_raw.get("attempts", [])
                       if (value := parse_additional_fields(attempt).get("expiry_date"))}
    corrections = {}
    for candidate in candidates:
        mismatch = candidate["number"] != front_fields["identity_number"]
        name = front_fields.get("full_name")
        if name:
            mismatch |= " ".join(folded(name).upper().split()) != candidate["name"]
        for field, mrz_field in (("date_of_birth", "birth"),):
            value = front_fields.get(field)
            if value:
                mismatch |= value[2:4] + value[5:7] + value[8:10] != candidate[mrz_field]
        if extra.get("expiry_date"):
            value = extra["expiry_date"]
            if value[2:4] + value[5:7] + value[8:10] != candidate["expiry"]:
                agreement = [reading for reading in expiry_readings
                    if reading[2:4] + reading[5:7] + reading[8:10] == candidate["expiry"]]
                if len(agreement) == 1:
                    corrections["expiry_date"] = agreement[0]
                else:
                    mismatch = True
        if extra.get("sex") and candidate["sex"] != "<":
            mismatch |= {"male": "M", "female": "F"}[extra["sex"]] != candidate["sex"]
        if mismatch:
            raise DocumentError("IDENTITY_PAIR_MISMATCH",
                "Thông tin hai mặt CCCD không khớp. Hãy chọn mặt trước và mặt sau của cùng một thẻ.", 422)
    result = {"status": "matched", "method": "td1_mrz_consistency", "version": "1"}
    if corrections:
        result["corrections"] = corrections
    return result
