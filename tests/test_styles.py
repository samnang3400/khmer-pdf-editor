"""Styled text (bold, italic, other fonts, colours), list items, letters a
font lacks, and PDFs made by browsers."""
import pymupdf
import pytest

from khmerpdf import engine as E
from khmerpdf import styles as ST
from khmerpdf.recover import MappedDecoder, find_lines
from khmerpdf.styles import Style

BOLD = Style(bold=True)
RED = Style(color=(1.0, 0.0, 0.0))


# ---- run helpers -------------------------------------------------------------
def test_slice_merge_and_json_round_trip():
    runs = [("ឈ្មោះ៖ ", ST.PLAIN), ("សុខ", BOLD), (" ", BOLD), ("សាន្ត", Style(italic=True))]
    assert ST.merge(runs)[1] == ("សុខ ", BOLD)
    assert ST.slice_runs(runs, 4, 9) == [("ះ៖ ", ST.PLAIN), ("សុ", BOLD)]
    text = ST.runs_text(runs)
    assert ST.from_json(ST.to_json(runs), text) == ST.merge(runs)
    assert ST.from_json(ST.to_json(runs), text + "x") == []  # doesn't describe this text


def test_align_runs_follows_small_text_changes():
    runs = [("ក ខ", ST.PLAIN), ("គ​ឃ", BOLD)]
    assert ST.align_runs(runs, "ក ខគឃ") == [("ក ខ", ST.PLAIN), ("គឃ", BOLD)]   # word break gone
    assert ST.align_runs(runs, "ក ខ គ​ឃង") == [("ក ខ ", ST.PLAIN), ("គ​ឃង", BOLD)]


def test_plain_means_no_styling_at_all():
    assert ST.plain(None) and ST.plain([("ក", ST.PLAIN)])
    assert not ST.plain([("ក", Style(color=(1, 0, 0)))])   # one colour for all of it is still a style
    assert not ST.plain([("ក", Style(font="Moul"))])


def test_bold_font_files_are_found_by_family():
    assert ST.bold_path("Battambang Regular").endswith("Battambang-Bold.ttf")
    assert ST.bold_path("Khmer OS Siemreap") is None  # no bold file: drawn as a thickened outline
    assert ST.family("NotoSansKhmer-Bold") == "NotoSansKhmer"


# ---- writing and reading styled text ------------------------------------------
def _styled_doc():
    doc = pymupdf.open()
    page = doc.new_page()
    runs = [("ឈ្មោះ៖ ", ST.PLAIN), ("សុខ សាន្ត", BOLD), (" ពណ៌ក្រហម", RED), (" ទ្រេត", Style(italic=True))]
    E.insert_khmer_text(page, pymupdf.Rect(50, 50, 500, 200), ST.runs_text(runs), size=14, runs=runs)
    return doc, runs


def test_styled_paragraph_round_trip():
    doc, runs = _styled_doc()
    page = doc[0]
    (para,) = E.paragraphs(E.text_lines(page), page)
    assert para.text == ST.runs_text(runs)
    got = [(t, s.bold, s.italic, s.color) for t, s in para.runs]
    assert ("សុខ សាន្ត", True, False, None) in got
    assert (" ពណ៌ក្រហម", False, False, (1.0, 0.0, 0.0)) in got
    assert (" ទ្រេត", False, True, None) in got
    assert "ឈ្មោះ៖ សុខ សាន្ត" in page.get_text()  # the text layer is plain, searchable text


def _glyph_names(doc):
    reg = doc.xref_get_key(doc.pdf_catalog(), "KhmerPDFGlyphs")[1]
    return doc.xref_get_keys(int(reg.split()[0]))


def test_bold_uses_the_bold_font_file_or_a_thickened_outline():
    for name, synthetic in (("Battambang Regular", False), ("Khmer OS Siemreap", True)):
        doc = pymupdf.open()
        font = E.load_font(ST.font_path(name))
        runs = [("ក ", ST.PLAIN), ("ខ", BOLD)]
        E.insert_khmer_text(doc.new_page(), pymupdf.Rect(50, 50, 500, 100), "ក ខ", font=font, runs=runs,
                            wrap_text=False)
        keys = _glyph_names(doc)
        assert any(k.endswith("b") for k in keys) is synthetic, name
        bold_file = E._cached_font(ST.bold_path(name)) if not synthetic else None
        if bold_file:
            assert any(k.startswith(f"G{bold_file.digest}_") for k in keys)


def test_editing_keeps_the_styles_of_the_words_left():
    doc, _ = _styled_doc()
    page = doc[0]
    (para,) = E.paragraphs(E.text_lines(page), page)
    E.edit_text(page, para, para.text.replace("សាន្ត", "សុភា"))  # runs follow the text
    (para,) = E.paragraphs(E.text_lines(page), page)
    assert ("សុខ សុភា", True) in [(t, s.bold) for t, s in para.runs]
    E.edit_text(page, para, para.text, runs=[(para.text, ST.PLAIN)])  # all styling taken off
    (para,) = E.paragraphs(E.text_lines(page), page)
    assert not para.runs


def test_letters_a_font_lacks_use_the_fallback_font():
    font = E.load_font(ST.font_path("Siemreap"))  # Google's Siemreap: Khmer and digits only
    assert not font.hb_font.get_nominal_glyph(ord("A"))
    doc = pymupdf.open()
    page = doc.new_page()
    E.insert_khmer_text(page, pymupdf.Rect(50, 50, 500, 100), "Toyota ខ្មែរ", font=font, wrap_text=False)
    fb = E._cached_font(str(E.FALLBACK_FONT))
    keys = _glyph_names(doc)
    assert any(k.startswith(f"G{fb.digest}_") for k in keys)       # Latin from Khmer OS
    assert any(k.startswith(f"G{font.digest}_") for k in keys)     # Khmer from Siemreap
    assert E.text_lines(page)[0].text == "Toyota ខ្មែរ"


# ---- browser PDFs: bold drawn as shapes, invisible text on top -----------------
def _browser_bold(invisible: str):
    """'Name: ' as text, then 'BOLD' drawn as a filled shape with invisible
    text over it - the way Chrome prints synthetic bold."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((50, 100), "Name: ", fontname="helv", fontsize=12)
    x = 50 + pymupdf.get_text_length("Name: ", "helv", 12)
    w = pymupdf.get_text_length("BOLD", "helv", 12)
    # a letter-like outline (many points), not a box
    pts = [pymupdf.Point(x + w * i / 10, 100 - (9 if i % 2 else 1)) for i in range(11)] + \
          [pymupdf.Point(x + w, 102), pymupdf.Point(x, 102)]
    sh = page.new_shape()
    sh.draw_polyline(pts)
    sh.finish(fill=(0.2, 0.2, 0.2), closePath=True, width=0)
    sh.commit()
    kw = dict(render_mode=3) if invisible == "mode3" else dict(fill_opacity=0)
    page.insert_text((x, 100), "BOLD", fontname="helv", fontsize=12, **kw)
    r = pymupdf.Rect(pts[0], pts[0])
    for p in pts:
        r |= p
    return doc, r


@pytest.mark.parametrize("invisible", ["mode3", "opacity0"])
def test_text_drawn_as_shapes_reads_as_bold_and_is_removed_on_edit(invisible):
    doc, shape_rect = _browser_bold(invisible)
    page = doc[0]
    (line,) = find_lines(page)
    assert line.text == "Name: BOLD"
    assert [(t, s.bold) for t, s in line.runs] == [("Name: ", False), ("BOLD", True)]
    assert line.shapes
    (tl,) = E.text_lines(page)
    E.edit_text(page, tl, "Name: BOLDER")

    def shape_left():
        return any(abs(pymupdf.Rect(d["rect"]).x0 - shape_rect.x0) < 0.01
                   and abs(pymupdf.Rect(d["rect"]).y0 - shape_rect.y0) < 0.01 for d in page.get_drawings())
    assert not shape_left()  # the old letters are gone, not left under the new ones
    (tl,) = E.text_lines(page)
    assert tl.text == "Name: BOLDER" and ("BOLDER", True) in [(t, s.bold) for t, s in tl.runs]


def test_hidden_text_without_shapes_stays_an_ocr_layer():
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((50, 100), "hidden", fontname="helv", fontsize=12, render_mode=3)
    assert find_lines(page) == []


def test_blank_glyphs_take_their_neighbours_id_shift():
    md = MappedDecoder.__new__(MappedDecoder)
    md.mapping = {460: (461,), 470: (471,), 100: (100,)}
    assert md._shift_near(466) == 1
    md.mapping[468] = (468,)
    assert md._shift_near(466) is None  # the two sides disagree


# ---- list items ----------------------------------------------------------------
def _lines(page, items):
    for x, y, text in items:
        E.insert_khmer_text(page, pymupdf.Rect(x, y - 14, 560, y + 6), text, size=11, wrap_text=False,
                            baseline=y)


def test_each_bullet_is_its_own_paragraph_with_its_hanging_lines():
    doc = pymupdf.open()
    page = doc.new_page()
    long1 = "- ច្បាប់ដើមលតាបត្រត្រួតពិនិត្យលក្ខណៈបច្ចេកទេសយានជំនិះ"
    _lines(page, [(50, 100, long1), (58, 118, "(មានបិទជាប់នៅកញ្ចក់រថយន្ត)"),
                  (50, 136, "- ច្បាប់ដើមវិញ្ញាបនបត្រ"), (50, 154, "១. ធាតុទីមួយ"), (50, 172, "២. ធាតុទីពីរ")])
    for ln in E.text_lines(page):  # (as if another program had written them)
        doc.xref_set_key(ln.xref, "KhmerPDFLine", "false")
    paras = E.paragraphs([E.TextLine(rect=pymupdf.Rect(f.rect), text=f.text, size=f.size, font=f.font,
                                     color=f.color, baseline=f.baseline, garbled=False)
                          for f in find_lines(page)], page)
    texts = [p.text for p in paras]
    assert len(paras) == 4, texts
    first = paras[0]
    assert len(first.lines) == 2 and first.indent < 0  # the bullet hangs out in front of the text
    assert [p.text[:2] for p in paras[1:]] == ["- ", "១.", "២."]


@pytest.mark.parametrize("text,item", [("- ក", True), ("• ក", True), ("1. ក", True), ("១. ក", True),
                                       ("ក. ខ", True), ("(a) x", True), ("១.ក", True),
                                       ("-៥", False), ("ក្រុម", False), ("2026 ឆ្នាំ", False)])
def test_list_item_markers(text, item):
    assert E.is_list_item(text) is item


def test_hanging_indent_is_kept_when_rewritten():
    doc = pymupdf.open()
    page = doc.new_page()
    def line(y):
        return E.TextLine(rect=pymupdf.Rect(50, y - 10, 300, y + 4), text="x", size=11, font="", color=(0, 0, 0),
                          baseline=y, garbled=False)
    para = E.Paragraph([line(100), line(116)], "x x", 60, 240, 16, False, indent=-10)
    text = "- " + "ពាក្យ " * 30
    E.draw_paragraph(page, para, text.strip(), size=11)
    lines = sorted(E.text_lines(page), key=lambda ln: ln.baseline)
    assert len(lines) > 1
    assert abs(lines[0].rect.x0 - 50) < 1 and all(abs(ln.rect.x0 - 60) < 1 for ln in lines[1:])
    (p,) = E.paragraphs(E.text_lines(page), page)
    assert abs(p.indent + 10) < 0.1  # remembered for the next edit
