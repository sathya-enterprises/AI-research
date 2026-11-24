"""List blank and near-blank pages in a report PDF (offline).

usage: python scripts/pdf_blank_pages.py report.pdf [...]

A page counts as blank when, after removing the footer furniture (running title, page number, ARN line, firm
name), it has almost no text and no images or chart drawings. "Sparse" pages (a heading or a few lines and
otherwise empty) are listed separately: they usually mean a block was pushed to the next page.
"""
from __future__ import annotations

import re
import sys

import fitz  # PyMuPDF

_FURNITURE = re.compile(r"(?im)^\s*(?:\d{1,3}|ARN-\S.*|Growth Gradual LLP|.{0,60}\.\.\.|.{0,60}…)\s*$")


def page_report(path: str) -> dict:
    doc = fitz.open(path)
    blank, sparse = [], []
    for index, page in enumerate(doc):
        height = page.rect.height
        body_blocks = [b for b in page.get_text("blocks") if b[1] < height * 0.92 and b[3] > height * 0.03]
        text = "\n".join(str(b[4]) for b in body_blocks)
        text = _FURNITURE.sub("", text).strip()
        has_visual = bool(page.get_images()) or len(page.get_drawings()) > 12
        chars = len(re.sub(r"\s+", "", text))
        if chars < 40 and not has_visual:
            blank.append(index + 1)
        elif chars < 400 and not has_visual and index > 0:
            sparse.append((index + 1, chars))
    return {"pages": len(doc), "blank": blank, "sparse": sparse}


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        print(arg, page_report(arg))
