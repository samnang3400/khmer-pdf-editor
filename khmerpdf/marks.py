"""Marks put on a page by clicking: ticks in checkboxes, and pictures.

Most printed forms draw their checkboxes as plain squares (or a ☐ character)
rather than as PDF form fields, so the form filler can't tick them. A tick
here is a vector check mark sized to the box under the click; clicking a box
that already has a mark in it (ours or the PDF's own) clears it.
"""
from __future__ import annotations

import pymupdf

BOX_MIN, BOX_MAX = 4.0, 40.0   # pt: what counts as a checkbox
BOX_CHARS = "☐□▢❑❒◻⬜"
TICK_SIZE = 10.0               # pt: a tick placed where there is no box


def _squareish(r: pymupdf.Rect) -> bool:
    w, h = r.width, r.height
    return BOX_MIN <= w <= BOX_MAX and BOX_MIN <= h <= BOX_MAX and abs(w - h) <= 0.35 * max(w, h)


def find_box(page: pymupdf.Page, at: pymupdf.Point) -> pymupdf.Rect | None:
    """The checkbox under (or right next to) a click: a small drawn square,
    or a box character. The smallest one wins (boxes inside table cells)."""
    near = pymupdf.Rect(at.x - 2, at.y - 2, at.x + 2, at.y + 2)
    found: list[pymupdf.Rect] = []
    for d in page.get_drawings():
        r = pymupdf.Rect(d["rect"])
        if d.get("color") is None and d.get("type") == "f":
            continue  # a filled shape without an outline isn't an empty box
        items = d.get("items", [])
        closed = d.get("closePath") or any(it[0] in ("re", "qu") for it in items) or len(items) >= 4
        if closed and _squareish(r) and r.intersects(near):  # (a tick is an open line: not a box)
            found.append(r)
    for b in page.get_text("rawdict")["blocks"]:
        for ln in b.get("lines", []):
            for sp in ln["spans"]:
                for c in sp["chars"]:
                    if c["c"] in BOX_CHARS:
                        r = pymupdf.Rect(c["bbox"])
                        # the glyph's box is taller than the square drawn in it
                        side = min(r.width, r.height)
                        sq = pymupdf.Rect(r.x0, r.y1 - side - 0.2 * side, r.x0 + side, r.y1 - 0.2 * side)
                        if sq.intersects(near) or r.intersects(near):
                            found.append(sq)
    return min(found, key=lambda r: r.width * r.height) if found else None


def _inside(r: pymupdf.Rect) -> pymupdf.Rect:
    k = 0.12 * min(r.width, r.height)
    return pymupdf.Rect(r.x0 + k, r.y0 + k, r.x1 - k, r.y1 - k)


def is_ticked(page: pymupdf.Page, box: pymupdf.Rect) -> bool:
    """Something is drawn inside the box (a tick or a cross)."""
    inner = _inside(box)
    for d in page.get_drawings():
        r = pymupdf.Rect(d["rect"])
        if not r.is_empty and inner.contains(r) or (r.width < 0.5 or r.height < 0.5) and inner.intersects(r):
            return True
    return False


def draw_tick(page: pymupdf.Page, box: pymupdf.Rect,
              color: tuple[float, float, float] = (0, 0, 0)) -> pymupdf.Rect:
    """A check mark filling the box. (Like find_box, in the page's own,
    unrotated coordinates - the ones get_drawings uses.)"""
    w, h = box.width, box.height
    pts = [pymupdf.Point(box.x0 + 0.2 * w, box.y0 + 0.52 * h),
           pymupdf.Point(box.x0 + 0.42 * w, box.y0 + 0.76 * h),
           pymupdf.Point(box.x0 + 0.82 * w, box.y0 + 0.24 * h)]
    shape = page.new_shape()
    shape.draw_polyline(pts)
    shape.finish(color=color, width=max(0.8, 0.11 * min(w, h)), closePath=False, lineCap=1, lineJoin=1)
    shape.commit()
    return box


def clear_box(page: pymupdf.Page, box: pymupdf.Rect) -> None:
    """Remove what is drawn inside the box, leaving the box itself."""
    page.add_redact_annot(_inside(box))
    page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE,
                          graphics=pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED,
                          text=pymupdf.PDF_REDACT_TEXT_NONE)


def toggle_tick(page: pymupdf.Page, at: pymupdf.Point,
                color: tuple[float, float, float] = (0, 0, 0)) -> tuple[str, pymupdf.Rect]:
    """Tick the box under the click (a point on the page as displayed, i.e.
    rotated), or clear it if it is ticked. With no box there, a tick of
    TICK_SIZE is placed at the click. Returns ('ticked' | 'cleared' | 'placed',
    rect in the page's own unrotated coordinates)."""
    if page.rotation:
        at = at * page.derotation_matrix
    box = find_box(page, at)
    if box is None:
        s = TICK_SIZE
        r = pymupdf.Rect(at.x - s / 2, at.y - s / 2, at.x + s / 2, at.y + s / 2)
        return "placed", draw_tick(page, r, color)
    if is_ticked(page, box):
        clear_box(page, box)
        return "cleared", box
    return "ticked", draw_tick(page, box, color)


def place_picture(page: pymupdf.Page, data: bytes, at: pymupdf.Point) -> pymupdf.Rect:
    """Put a picture on the page, centred on the click: at its own size
    (96 dpi) but no wider than half the page or taller than half of it, and
    kept on the page. Afterwards it is an ordinary picture to move or resize."""
    pix = pymupdf.Pixmap(data)
    w, h = pix.width * 72 / 96, pix.height * 72 / 96
    k = min(1.0, page.rect.width * 0.5 / max(w, 1), page.rect.height * 0.5 / max(h, 1))
    w, h = w * k, h * k
    r = pymupdf.Rect(at.x - w / 2, at.y - h / 2, at.x + w / 2, at.y + h / 2)
    r = r + (max(0, page.rect.x0 - r.x0) + min(0, page.rect.x1 - r.x1),
             max(0, page.rect.y0 - r.y0) + min(0, page.rect.y1 - r.y1)) * 2
    page.insert_image(r, stream=data, keep_proportion=True)
    return r
