"""Safe attachment filenames; never put untrusted text directly into headers."""
import re
import unicodedata
from urllib.parse import quote


def download_filename(value):
    value = value.replace("\\", "/").rsplit("/", 1)[-1]
    value = "".join(ch for ch in value if unicodedata.category(ch) not in {"Cc", "Cf", "Cs"})
    return value.strip(" .")[:200] or "document"


def content_disposition(value):
    name = download_filename(value)
    fallback = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    fallback = re.sub(r"[^A-Za-z0-9._ -]", "_", fallback).strip(" .") or "document"
    return f'attachment; filename="{fallback}"; filename*=UTF-8\'\'{quote(name, safe="")}'
