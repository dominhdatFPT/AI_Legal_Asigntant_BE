"""Combine rendered page PNGs into small local visual-review sheets."""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw


def build(directory: Path, columns: int = 4, rows: int = 3) -> int:
    images = sorted(directory.glob("[0-9][0-9]-[0-9][0-9].png"))
    width, height = 300, 445
    per_sheet = columns * rows
    for start in range(0, len(images), per_sheet):
        sheet = Image.new("RGB", (columns * width, rows * height), "#dddddd")
        draw = ImageDraw.Draw(sheet)
        for offset, path in enumerate(images[start:start + per_sheet]):
            with Image.open(path) as source:
                source.thumbnail((width - 12, height - 32))
                x = (offset % columns) * width + (width - source.width) // 2
                y = (offset // columns) * height + 24
                sheet.paste(source, (x, y))
            draw.text(((offset % columns) * width + 6, (offset // columns) * height + 4),
                      path.stem, fill="#111111")
        sheet.save(directory / f"sheet-{start // per_sheet + 1:02d}.png")
    return (len(images) + per_sheet - 1) // per_sheet


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    print(f"sheets={build(parser.parse_args().directory)}")
