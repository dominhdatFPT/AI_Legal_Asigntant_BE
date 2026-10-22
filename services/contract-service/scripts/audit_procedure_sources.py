"""Render a complete contact-sheet audit of locally prepared Procedure PDFs.

The sheets are review aids only; they do not certify field mappings or legal text.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pymupdf


def audit(directory: Path, qa_dir: Path) -> dict:
    records = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if not qa_dir.is_dir():
        raise ValueError("QA output directory must exist")
    rows = []
    for index, record in enumerate(records, start=1):
        source = directory / record["prepared_file"]
        with pymupdf.open(source) as document:
            if len(document) != record["pages"]:
                raise ValueError(f"Page count changed: {record['source']}")
            for page_index, page in enumerate(document, start=1):
                label = f"{index:02d}-{page_index:02d}"
                pix = page.get_pixmap(matrix=pymupdf.Matrix(0.7, 0.7), alpha=False)
                pix.save(qa_dir / f"{label}.png")
                rows.append({
                    "template": index, "page": page_index,
                    "image": f"{label}.png", "text_chars": len(page.get_text().strip()),
                    "source": record["source"],
                })
    report = {"templates": len(records), "pages": len(rows), "pages_without_text":
              [item for item in rows if not item["text_chars"]], "pages_detail": rows}
    (qa_dir / "audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"templates": len(records), "pages": len(rows),
            "pages_without_text": len(report["pages_without_text"])}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("prepared_dir", type=Path)
    parser.add_argument("qa_dir", type=Path)
    args = parser.parse_args()
    print(json.dumps(audit(args.prepared_dir, args.qa_dir), ensure_ascii=False))


if __name__ == "__main__":
    main()
