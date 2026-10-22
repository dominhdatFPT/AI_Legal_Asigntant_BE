"""Read-only classification catalogue for source documents; no DB or user records."""
import json
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def document_classifications():
    data = json.loads((Path(__file__).resolve().parents[1] / 'reference_classifications.json').read_text(encoding='utf-8'))
    return data
