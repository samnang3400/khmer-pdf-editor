"""Annotations (highlights, comment notes) and signature images.

Annotations are standard PDF annotations, so every viewer shows them and
they can be removed again without touching the page. A signature here is a
picture of a handwritten signature placed on the page, not a cryptographic
digital signature.
"""
from __future__ import annotations

from dataclasses import dataclass

import pymupdf

from .engine import text_lines

HIGHLIGHT = (1.0, 0.86, 0.2)
KINDS = {pymupdf.PDF_ANNOT_HIGHLIGHT: "highlight", pymupdf.PDF_ANNOT_UNDERLINE: "highlight",
         pymupdf.PDF_ANNOT_TEXT: "note", pymupdf.PDF_ANNOT_FREE_TEXT: "note"}
SIGNATURE_WIDTH = 150.0  # pt, when a signature is first placed


@dataclass
class Annot:
    xref: int
    kind: str          # highlight, note, other
    rect: pymupdf.Rect
    text: str          # a note's comment

    @property
    def movable(self) -> bool:
        return self.kind != "highlight"  # a highlight belongs to the text under it


def annotations(page: pymupdf.Page) -> list[Annot]:
    out = []
    for a in page.annots():
        if a.type[0] in (pymupdf.PDF_ANNOT_WIDGET, pymupdf.PDF_ANNOT_LINK, pymupdf.PDF_ANNOT_POPUP):
            continue
        out.append(Annot(a.xref, KINDS.get(a.type[0], "other"), pymupdf.Rect(a.rect), a.info.get("content", "")))
    return out


def _load(page: pymupdf.Page, item: Annot):
    a = page.load_annot(item.xref)
    if a is None:
        raise ValueError("That annotation is no longer on the page")
    return a


def add_highlight(page: pymupdf.Page, rect: pymupdf.Rect, color=HIGHLIGHT) -> Annot:
    """Highlight the text inside `rect`, line by line (or just the area, if
    there is no text there)."""
    rect = pymupdf.Rect(rect)
    quads = []
    for ln in sorted(text_lines(page), key=lambda ln: ln.baseline):
        r = ln.rect
        overlap = min(r.y1, rect.y1) - max(r.y0, rect.y0)
        if overlap > 0.4 * r.height and r.x1 > rect.x0 and r.x0 < rect.x1:
            quads.append(pymupdf.Rect(max(r.x0, rect.x0), r.y0, min(r.x1, rect.x1), r.y1).quad)
    a = page.add_highlight_annot(quads or [rect.quad])
    a.set_colors(stroke=color)
    a.update()
    return Annot(a.xref, "highlight", pymupdf.Rect(a.rect), "")


def add_note(page: pymupdf.Page, point: pymupdf.Point, text: str, author: str = "") -> Annot:
    """A comment note (the small speech-bubble icon viewers open on click)."""
    a = page.add_text_annot(point, text, icon="Comment")
    a.set_info(content=text, title=author)
    a.set_colors(stroke=HIGHLIGHT)
    a.update()
    return Annot(a.xref, "note", pymupdf.Rect(a.rect), text)


def edit_note(page: pymupdf.Page, item: Annot, text: str) -> None:
    a = _load(page, item)
    a.set_info(content=text)
    a.update()


def move_annot(page: pymupdf.Page, item: Annot, rect: pymupdf.Rect) -> None:
    if not item.movable:
        raise ValueError("A highlight stays with its text: delete it and highlight again instead")
    a = _load(page, item)
    a.set_rect(pymupdf.Rect(rect))
    a.update()


def delete_annot(page: pymupdf.Page, item: Annot) -> None:
    page.delete_annot(_load(page, item))


def place_signature(page: pymupdf.Page, png: bytes, at: pymupdf.Point,
                    width: float = SIGNATURE_WIDTH) -> pymupdf.Rect:
    """Put a signature picture on the page, centred on `at`. Returns where;
    afterwards it is an ordinary picture (move, resize, delete it like one)."""
    pix = pymupdf.Pixmap(png)
    h = width * pix.height / max(pix.width, 1)
    r = pymupdf.Rect(at.x - width / 2, at.y - h / 2, at.x + width / 2, at.y + h / 2)
    r = r + (max(0, page.rect.x0 - r.x0) + min(0, page.rect.x1 - r.x1),
             max(0, page.rect.y0 - r.y0) + min(0, page.rect.y1 - r.y1)) * 2  # keep it on the page
    page.insert_image(r, stream=png, keep_proportion=True)
    return r
