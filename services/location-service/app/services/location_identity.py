"""Compare a street address without confusing nearby houses or ward centroids."""
import re
import unicodedata


def text_key(value):
    text = unicodedata.normalize("NFD", value.casefold().replace("đ", "d"))
    return " ".join(re.findall(r"[a-z0-9]+", "".join(c for c in text if not unicodedata.combining(c))))


def street_identity(address):
    """Keep house suffixes, ranges, alley numbers and street word order significant."""
    if not isinstance(address, str):
        return None
    segments = address.split(",")
    for index, segment in enumerate(segments):
        segment = unicodedata.normalize("NFD", segment.casefold().replace("đ", "d"))
        segment = "".join(c for c in segment if not unicodedata.combining(c))
        segment = re.sub(r"^\s*so\s+", "", segment.strip())
        if re.fullmatch(r"\d+[a-z]?(?:[/\-]\d+[a-z]?)*", segment) and index + 1 < len(segments):
            segment += " " + segments[index + 1].strip()
        match = re.match(r"^(\d+[a-z]?(?:\s*[/\-]\s*\d+[a-z]?)*|k\d+[a-z0-9]*(?:[/\-][a-z0-9]+)*)\s+(.+)$", segment)
        if not match:
            continue
        house = re.sub(r"\s+", "", match[1])
        house = re.sub(r"\d+", lambda m: str(int(m[0])), house)
        street = re.sub(r"^(?:duong|pho|d)\s+", "", text_key(match[2]))
        # Reject a postcode, a bare house number or a district-only address.
        if not street or not re.search(r"[a-z]", street):
            continue
        return house, street
    return None


def same_street_address(expected, actual):
    identity = street_identity(expected)
    return identity is not None and identity == street_identity(actual)
