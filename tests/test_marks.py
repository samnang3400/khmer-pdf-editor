"""Ticks in drawn checkboxes, and inserted pictures."""
import pymupdf

from khmerpdf import marks as M


def _form():
    doc = pymupdf.open()
    page = doc.new_page()
    page.draw_rect(pymupdf.Rect(100, 100, 114, 114), color=(0, 0, 0), width=0.8)   # a checkbox
    page.draw_rect(pymupdf.Rect(200, 100, 214, 114), color=(0, 0, 0), width=0.8)   # another
    page.draw_rect(pymupdf.Rect(50, 300, 550, 400), color=(0, 0, 0), width=0.8)    # a table: not a box
    return doc, page


def test_tick_untick_and_tick_again():
    doc, page = _form()
    centre = pymupdf.Point(107, 107)
    assert M.toggle_tick(page, centre)[0] == "ticked"
    assert M.is_ticked(page, pymupdf.Rect(100, 100, 114, 114))
    assert not M.is_ticked(page, pymupdf.Rect(200, 100, 214, 114))
    assert M.toggle_tick(page, centre)[0] == "cleared"          # the tick isn't mistaken for a box
    assert not M.is_ticked(page, pymupdf.Rect(100, 100, 114, 114))
    boxes = [d for d in page.get_drawings() if abs(pymupdf.Rect(d["rect"]).width - 14) < 1.5]
    assert len(boxes) == 2                                        # the boxes themselves stay
    assert M.toggle_tick(page, centre)[0] == "ticked"


def test_a_click_beside_a_box_still_finds_it_and_elsewhere_places_a_tick():
    doc, page = _form()
    assert M.find_box(page, pymupdf.Point(115, 107)) is not None   # just outside the border
    what, r = M.toggle_tick(page, pymupdf.Point(300, 350))         # inside the big table cell
    assert what == "placed" and abs(r.width - M.TICK_SIZE) < 0.01


def test_tick_on_a_rotated_page_lands_in_the_box():
    doc, page = _form()
    page.set_rotation(90)
    shown = pymupdf.Point(107, 107) * page.rotation_matrix  # where the box is on screen
    what, box = M.toggle_tick(page, shown)
    assert what == "ticked" and box.contains(pymupdf.Point(107, 107))
    assert M.is_ticked(page, box)


def test_place_picture_keeps_its_shape_and_stays_on_the_page():
    doc = pymupdf.open()
    page = doc.new_page()
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 400, 200), False)
    pix.clear_with(200)
    r = M.place_picture(page, pix.tobytes("png"), pymupdf.Point(590, 10))  # near the top-right corner
    assert page.rect.contains(r)
    assert abs(r.width / r.height - 2) < 0.01
    assert len(page.get_images()) == 1
    big = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 4000, 1000), False)
    big.clear_with(100)
    r = M.place_picture(page, big.tobytes("png"), pymupdf.Point(300, 400))
    assert r.width <= page.rect.width * 0.5 + 0.01                         # no wider than half the page
