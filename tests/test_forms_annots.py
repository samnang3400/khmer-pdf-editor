"""Form filling (with shaped Khmer), annotations and signature pictures."""
import pymupdf
import pytest

from khmerpdf import annots as AN
from khmerpdf import forms as F
from khmerpdf.engine import FONT_DIR, insert_khmer_text, load_font, pictures

FONT = load_font(str(FONT_DIR / "khmeros" / "KhmerOSbattambang.ttf"))


def _form():
    doc = pymupdf.open()
    page = doc.new_page()
    for kind, rect, name, flags, choices in (
            (pymupdf.PDF_WIDGET_TYPE_TEXT, (50, 50, 300, 75), "name", 0, None),
            (pymupdf.PDF_WIDGET_TYPE_TEXT, (50, 200, 300, 260), "note", pymupdf.PDF_TX_FIELD_IS_MULTILINE, None),
            (pymupdf.PDF_WIDGET_TYPE_CHECKBOX, (50, 90, 65, 105), "agree", 0, None),
            (pymupdf.PDF_WIDGET_TYPE_COMBOBOX, (50, 120, 200, 140), "grade", 0, ["ថ្នាក់ទី៧", "ថ្នាក់ទី៨"]),
            (pymupdf.PDF_WIDGET_TYPE_TEXT, (50, 300, 300, 325), "locked", pymupdf.PDF_FIELD_IS_READ_ONLY, None)):
        w = pymupdf.Widget()
        w.field_type, w.rect, w.field_name, w.field_flags = kind, pymupdf.Rect(rect), name, flags
        if choices:
            w.choice_values = choices
        page.add_widget(w)
    return pymupdf.open(stream=doc.tobytes())


def _fields(page):
    return {f.name: f for f in F.fields(page)}


def test_fill_a_form_with_khmer():
    doc = _form()
    page = doc[0]
    fs = _fields(page)
    assert (fs["name"].kind, fs["note"].multiline, fs["agree"].kind, fs["grade"].options) == \
        ("text", True, "checkbox", ["ថ្នាក់ទី៧", "ថ្នាក់ទី៨"])
    F.fill_text(page, fs["name"], "សុខ ស្រីនិច្ច", FONT)
    F.fill_text(page, fs["note"], "សិស្សពូកែ ចូលរៀនទៀងទាត់ និងមានសីលធម៌ល្អ", FONT)
    assert F.toggle(page, fs["agree"]) is True
    F.choose(page, fs["grade"], "ថ្នាក់ទី៨", FONT)
    with pytest.raises(ValueError):
        F.fill_text(page, fs["locked"], "x")
    page = pymupdf.open(stream=doc.tobytes())[0]                  # survives saving
    fs = _fields(page)
    assert fs["name"].value == "សុខ ស្រីនិច្ច" and fs["agree"].checked and fs["grade"].value == "ថ្នាក់ទី៨"
    # it is drawn (shaped glyph outlines), not left blank
    pix = page.get_pixmap(clip=fs["name"].rect, dpi=72, colorspace=pymupdf.csGRAY)
    assert min(pix.samples) < 100
    assert F.toggle(page, _fields(page)["agree"]) is False          # and unticked again


def test_highlight_follows_the_text_lines():
    doc = pymupdf.open()
    page = doc.new_page()
    insert_khmer_text(page, pymupdf.Rect(50, 50, 300, 300), "សួស្តី ពិភពលោក\nអ្នកទាំងអស់គ្នា", font=FONT, size=14)
    h = AN.add_highlight(page, pymupdf.Rect(40, 40, 400, 120))
    a = page.load_annot(h.xref)
    assert len(a.vertices) == 8                                   # two lines = two quads
    (item,) = AN.annotations(page)
    assert item.kind == "highlight" and not item.movable
    with pytest.raises(ValueError):
        AN.move_annot(page, item, pymupdf.Rect(0, 0, 10, 10))
    AN.delete_annot(page, item)
    assert not AN.annotations(page)


def test_notes_can_be_moved_edited_and_deleted():
    doc = pymupdf.open()
    page = doc.new_page()
    n = AN.add_note(page, pymupdf.Point(100, 100), "សូមពិនិត្យ", author="Teacher")
    AN.move_annot(page, n, pymupdf.Rect(300, 400, 320, 420))
    AN.edit_note(page, n, "បានពិនិត្យរួច")
    page = pymupdf.open(stream=doc.tobytes())[0]
    (item,) = AN.annotations(page)
    assert item.kind == "note" and item.text == "បានពិនិត្យរួច" and abs(item.rect.x0 - 300) < 1
    AN.delete_annot(page, item)
    assert not AN.annotations(page)


def test_signature_is_a_picture_on_the_page():
    sig = pymupdf.open().new_page(width=300, height=100)
    sig.draw_bezier((20, 70), (100, 0), (180, 120), (280, 30), width=3)
    png = sig.get_pixmap(dpi=96, alpha=True).tobytes("png")
    doc = pymupdf.open()
    page = doc.new_page()
    r = AN.place_signature(page, png, pymupdf.Point(400, 700))
    assert abs(r.width - AN.SIGNATURE_WIDTH) < 0.5 and abs(r.height - 50) < 1
    (pic,) = pictures(page)
    assert abs(pic.rect.x0 - r.x0) < 0.5
    r2 = AN.place_signature(page, png, pymupdf.Point(590, 830))   # near the corner: kept on the page
    assert r2.x1 <= page.rect.x1 + 0.01 and r2.y1 <= page.rect.y1 + 0.01
