"""Paragraphs: edit, reflow, move, resize and delete several lines as one block."""
import pymupdf

from khmerpdf.engine import (FONT_DIR, _bare, default_font, insert_khmer_text, load_font, paragraphs,
                             text_lines, write_paragraph)
from tests.test_recover import KHMER_OS, _broken_pdf

TEXT = ("សាលាអន្តរជាតិ ហ្វតព្រីនស៍ ផ្តល់ការអប់រំ ដល់សិស្សានុសិស្ស គ្រប់កម្រិតថ្នាក់ "
        "ដោយគ្រូបង្រៀន ដែលមានបទពិសោធន៍ និងចំណេះដឹង ខ្ពស់ក្នុងវិស័យអប់រំ")


def _page_with_block(width=220, justify=False):
    doc = pymupdf.open()
    page = doc.new_page()
    insert_khmer_text(page, pymupdf.Rect(60, 80, 60 + width, 400), TEXT, size=12, justify=justify)
    return page


def test_own_paragraph_keeps_its_exact_text():
    page = _page_with_block()
    lines = text_lines(page)
    assert len(lines) >= 3
    paras = paragraphs(lines)
    assert len(paras) == 1
    p = paras[0]
    assert p.text == TEXT and abs(p.width - 220) < 0.01 and len(p.lines) == len(lines)


def test_reflow_rewraps_inside_the_same_width():
    page = _page_with_block()
    p = paragraphs(text_lines(page))[0]
    new = TEXT + " និងបន្ថែមអត្ថបទថ្មីមួយទៀត ដើម្បីសាកល្បងការបត់បន្ទាត់ឡើងវិញ"
    write_paragraph(page, p, new, font=default_font())
    paras = paragraphs(text_lines(page))
    assert len(paras) == 1 and paras[0].text == new
    assert len(paras[0].lines) > len(p.lines)
    assert all(ln.rect.x1 <= 60 + 220 + 0.5 for ln in paras[0].lines)
    assert abs(paras[0].first.baseline - p.first.baseline) < 0.01   # starts where it did


def test_move_resize_and_scale():
    page = _page_with_block()
    p = paragraphs(text_lines(page))[0]
    write_paragraph(page, p, p.text, dx=40, dy=200)
    moved = paragraphs(text_lines(page))[0]
    assert abs(moved.x - (p.x + 40)) < 0.5 and abs(moved.first.baseline - (p.first.baseline + 200)) < 0.01
    assert [ln.text for ln in moved.lines] == [ln.text for ln in p.lines]   # same line breaks

    write_paragraph(page, moved, moved.text, width=400)                 # wider: fewer lines
    wide = paragraphs(text_lines(page))[0]
    assert len(wide.lines) < len(p.lines) and wide.text == TEXT

    write_paragraph(page, wide, wide.text, scale=1.5)                   # corner drag: bigger
    big = paragraphs(text_lines(page))[0]
    assert big.first.size == 18 and abs(big.width - 600) < 0.5 and big.text == TEXT


def test_justified_paragraph_stays_justified():
    page = _page_with_block(justify=True)
    p = paragraphs(text_lines(page))[0]
    assert p.justify
    right = p.x + p.width
    assert all(abs(ln.rect.x1 - right) < 1 for ln in p.lines[:-1])


def _foreign_paragraph():
    """TEXT as one paragraph, laid out by another tool (MuPDF's HTML writer)."""
    doc = pymupdf.open()
    page = doc.new_page()
    css = "@font-face{font-family:K;src:url(KhmerOSbattambang.ttf);} body{font-family:K;font-size:15px}"
    page.insert_htmlbox(pymupdf.Rect(50, 50, 300, 500), TEXT, css=css, archive=pymupdf.Archive(str(KHMER_OS)))
    return pymupdf.open(stream=doc.tobytes())[0]


def test_lines_of_another_pdf_join_into_a_paragraph():
    page = _foreign_paragraph()
    lines = text_lines(page)
    assert len(lines) >= 3
    paras = paragraphs(lines)
    assert len(paras) == 1
    assert _bare(paras[0].text) == _bare(TEXT)
    # ...and it can be rewritten as one block, in the same font
    write_paragraph(page, paras[0], paras[0].text, font=load_font(str(KHMER_OS / "KhmerOSbattambang.ttf")))
    after = paragraphs(text_lines(page))
    assert len(after) == 1 and _bare(after[0].text) == _bare(TEXT)


def test_separate_lines_stay_separate():
    page = _broken_pdf()[0]                     # three lines far apart
    assert len(paragraphs(text_lines(page))) == 3


def test_one_line_paragraph_keeps_its_width_when_moved():
    doc = pymupdf.open()
    page = doc.new_page()
    font = load_font(str(FONT_DIR / "Battambang-Regular.ttf"))
    insert_khmer_text(page, pymupdf.Rect(50, 50, 500, 100), "សួស្តី ពិភពលោក", font=font, wrap_text=False)
    p = paragraphs(text_lines(page))[0]
    write_paragraph(page, p, p.text, font=font, dx=10, dy=30)
    q = paragraphs(text_lines(page))[0]
    assert len(q.lines) == 1 and abs(q.rect.width - p.rect.width) < 0.5


CELLS = [["ល.រ", "ឈ្មោះសិស្ស", "ថ្នាក់", "ពិន្ទុ"], ["១", "សុខ ដារា", "ទី៧", "៨៥"],
         ["២", "ចាន់ សុភា", "ទី៧", "៩០"], ["៣", "ហេង វិចិត្រ", "ទី៨", "៨៨"]]


def _table_pdf(border=True):
    doc = pymupdf.open()
    page = doc.new_page()
    rows = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in CELLS)
    css = ("@font-face{font-family:K;src:url(KhmerOSbattambang.ttf);} body{font-family:K;font-size:14px} "
           "td{padding:4px 10px" + ("; border:1px solid black}" if border else "}"))
    page.insert_htmlbox(pymupdf.Rect(50, 50, 545, 400), f"<table style='border-collapse:collapse'>{rows}</table>",
                        css=css, archive=pymupdf.Archive(str(KHMER_OS)))
    return pymupdf.open(stream=doc.tobytes())[0]


def test_table_cells_are_separate_lines_and_paragraphs():
    page = _table_pdf()
    lines = text_lines(page)
    assert sorted(_bare(ln.text) for ln in lines) == sorted(_bare(c) for r in CELLS for c in r)
    assert all(len(p.lines) == 1 for p in paragraphs(lines, page))


def test_borderless_table_cells_are_not_joined_into_paragraphs():
    page = _table_pdf(border=False)
    assert all(len(p.lines) == 1 for p in paragraphs(text_lines(page), page))


def test_paragraph_is_not_joined_across_a_rule():
    page = _foreign_paragraph()
    p = paragraphs(text_lines(page), page)[0]
    a, b = p.lines[1], p.lines[2]
    y = (a.baseline + b.rect.y0) / 2
    page.draw_line((40, y), (320, y))                      # a separator between lines 2 and 3
    assert [len(q.lines) for q in paragraphs(text_lines(page), page)] == [2, len(p.lines) - 2]


def test_ragged_khmer_lines_stay_one_paragraph_until_it_ends():
    doc = pymupdf.open()
    page = doc.new_page()
    css = ("@font-face{font-family:K;src:url(KhmerOSbattambang.ttf);} body{font-family:K;font-size:15px} "
           "p{margin:0 0 12px 0}")
    page.insert_htmlbox(pymupdf.Rect(50, 50, 260, 760), f"<p>{TEXT} {TEXT}</p><p>{TEXT}</p><p>សូមអរគុណ។</p>",
                        css=css, archive=pymupdf.Archive(str(KHMER_OS)))
    page = pymupdf.open(stream=doc.tobytes())[0]
    paras = paragraphs(text_lines(page), page)
    assert len(paras) == 3 and _bare(paras[1].text) == _bare(TEXT) and paras[2].text == "សូមអរគុណ។"
