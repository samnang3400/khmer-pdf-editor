"""Page operations: insert, merge, extract (split), duplicate, delete,
rotate and reorder.

Pages are copied with insert_pdf, which gives the copy its own objects. A
page copied any other way would share the Form XObjects of the lines this
app wrote, and editing a line on one page would change it on the other.
"""
from __future__ import annotations

import re

import pymupdf


def parse_ranges(text: str, count: int) -> list[int]:
    """'1-3, 5, 8-' -> [0, 1, 2, 4, 7, ..., count-1] (0-based, in order, no
    repeats). Raises ValueError for anything that isn't a page."""
    out: list[int] = []
    for part in re.split(r"[,\s]+", text.strip()):
        if not part:
            continue
        m = re.fullmatch(r"(\d*)\s*-\s*(\d*)|(\d+)", part)
        if not m:
            raise ValueError(f"“{part}” is not a page or a range like 2-5")
        if m.group(3):
            a = b = int(m.group(3))
        else:
            a = int(m.group(1)) if m.group(1) else 1
            b = int(m.group(2)) if m.group(2) else count
        if not 1 <= a <= b <= count:
            raise ValueError(f"“{part}” is outside pages 1-{count}")
        out += [n - 1 for n in range(a, b + 1) if n - 1 not in out]
    if not out:
        raise ValueError("No pages given")
    return out


def describe(pages: list[int]) -> str:
    """[0, 1, 2, 4] -> '1-3, 5'."""
    out, run = [], []
    for n in sorted(pages) + [None]:
        if run and (n is None or n != run[-1] + 1):
            out.append(f"{run[0] + 1}-{run[-1] + 1}" if len(run) > 1 else str(run[0] + 1))
            run = []
        if n is not None:
            run.append(n)
    return ", ".join(out)


def insert_blank(doc: pymupdf.Document, at: int, like: int | None = None) -> int:
    """A blank page at index `at`, the size of page `like` (default: the page
    before it, or A4). Returns its index."""
    ref = like if like is not None else max(at - 1, 0)
    if doc.page_count:
        r = doc[min(ref, doc.page_count - 1)].rect
        doc.new_page(pno=at, width=r.width, height=r.height)
    else:
        doc.new_page(pno=at)
    return at


def insert_pdf(doc: pymupdf.Document, src: pymupdf.Document, at: int) -> int:
    """Insert all pages of `src` at index `at` (merge). Returns the count."""
    doc.insert_pdf(src, start_at=at)
    return src.page_count


def extract(doc: pymupdf.Document, pages: list[int]) -> pymupdf.Document:
    """A new document with these pages, in this order (split)."""
    out = pymupdf.open()
    for n in pages:
        out.insert_pdf(doc, from_page=n, to_page=n)
    return out


def duplicate(doc: pymupdf.Document, pages: list[int]) -> list[int]:
    """Put a copy of each page right after the last one. Returns the copies'
    indexes."""
    copy = extract(doc, pages)
    at = max(pages) + 1
    doc.insert_pdf(copy, start_at=at)
    return list(range(at, at + len(pages)))


def delete(doc: pymupdf.Document, pages: list[int]) -> None:
    if len(set(pages)) >= doc.page_count:
        raise ValueError("A PDF needs at least one page")
    doc.delete_pages(sorted(set(pages)))


def rotate(doc: pymupdf.Document, pages: list[int], degrees: int) -> None:
    """Turn pages by a multiple of 90 degrees (clockwise)."""
    for n in pages:
        page = doc[n]
        page.set_rotation((page.rotation + degrees) % 360)


def reorder(doc: pymupdf.Document, order: list[int]) -> None:
    """Put the pages in this order (a permutation of all page indexes)."""
    if sorted(order) != list(range(doc.page_count)):
        raise ValueError("The new order must contain every page once")
    if order != list(range(doc.page_count)):
        doc.select(order)


def move(doc: pymupdf.Document, pages: list[int], step: int) -> list[int]:
    """Move pages one place up (step -1) or down (+1), keeping them together.
    Returns their new indexes."""
    order = list(range(doc.page_count))
    sel = sorted(set(pages))
    if step < 0 and sel[0] == 0 or step > 0 and sel[-1] == doc.page_count - 1:
        return sel
    rest = [n for n in order if n not in sel]
    at = rest.index(sel[0] - 1) if step < 0 else rest.index(sel[-1] + 1) + 1
    new = rest[:at] + sel + rest[at:]
    reorder(doc, new)
    return [new.index(n) for n in sel]
