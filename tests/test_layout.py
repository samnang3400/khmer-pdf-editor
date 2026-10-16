"""Room for longer text, and pictures that move, resize and get replaced."""
import pymupdf

from khmerpdf.engine import (_bare, edit_text, move_picture, paragraphs, pictures, remove_picture,
                             replace_picture, text_lines)
from tests.test_paragraphs import CELLS, KHMER_OS, TEXT

CSS = ("@font-face{font-family:K;src:url(KhmerOSbattambang.ttf);} body{font-family:K;font-size:15px} "
       "p{margin:0 0 12px 0} td{padding:4px 10px; border:1px solid black}")


def _png(color=(0.9, 0.5, 0.2), w=120, h=80):
    d = pymupdf.open()
    pg = d.new_page(width=w, height=h)
    pg.draw_rect(pg.rect, fill=color)
    return pg.get_pixmap(dpi=72).tobytes("png")


def _doc(html, pics=None):
    arch = pymupdf.Archive(str(KHMER_OS))
    if pics:
        arch.add(pics, "pic.png")
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_htmlbox(pymupdf.Rect(50, 50, 300, 800), html, css=CSS, archive=arch)
    return pymupdf.open(stream=doc.tobytes())


def test_longer_paragraph_pushes_the_next_one_down():
    page = _doc(f"<p>{TEXT}</p><p>សូមអរគុណ។</p><p>{TEXT[:60]}</p>")[0]
    first, thanks, last = paragraphs(text_lines(page), page)
    _, warning, moved = edit_text(page, first, first.text + " " + TEXT, font=None)
    assert warning is None and moved == 2
    after = paragraphs(text_lines(page), page)
    assert len(after) == 3 and _bare(after[1].text) == _bare(thanks.text)
    grown = after[0].rect.y1 - first.rect.y1
    assert grown > 20
    for old, new in ((thanks, after[1]), (last, after[2])):
        assert abs((new.first.baseline - old.first.baseline) - grown) < 2      # moved by the growth
    assert after[0].rect.y1 < after[1].rect.y0 + 4                             # no overlap


def test_shift_enter_in_a_line_makes_room():
    page = _doc("<p>សួស្តី ពិភពលោក</p><p>សូមអរគុណ។</p>")[0]
    line, below = text_lines(page)
    edit_text(page, line, "សួស្តី\nពិភពលោក")
    lines = text_lines(page)
    assert [ln.text for ln in lines] == ["សួស្តី", "ពិភពលោក", "សូមអរគុណ។"]
    assert lines[2].rect.y0 > lines[1].rect.y1 - 4 and lines[2].baseline > below.baseline + 10


def test_a_table_below_doesnt_move():
    from khmerpdf.recover import page_rules
    rows = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in CELLS)
    page = _doc(f"<p>{TEXT[:40]}</p><table style='border-collapse:collapse'>{rows}</table>")[0]
    para = paragraphs(text_lines(page), page)[0]
    last_row = sorted((ln.text, round(ln.baseline, 1)) for ln in text_lines(page) if ln.text in CELLS[-1])
    borders = page_rules(page).h
    _, warning, moved = edit_text(page, para.first, para.first.text + "\n" + TEXT[:40] + "\n" + TEXT[:40])
    assert moved == 0 and warning and "overlap" in warning          # it says so, and nothing moved
    assert page_rules(page).h == borders
    assert sorted((ln.text, round(ln.baseline, 1)) for ln in text_lines(page) if ln.text in CELLS[-1]) == last_row


def test_pictures_move_resize_replace_and_delete():
    doc = _doc(f"<p>{TEXT[:40]}</p><img src='pic.png' width='120'/><p>សូមអរគុណ។</p>", pics=_png())
    page = doc[0]
    (pic,) = pictures(page)
    assert move_picture(page, pic, pymupdf.Rect(300, 400, 420, 480))
    (moved,) = pictures(page)
    assert abs(moved.rect.x0 - 300) < 0.5 and abs(moved.rect.y1 - 480) < 0.5
    assert move_picture(page, moved, pymupdf.Rect(300, 400, 540, 560))      # resized
    (big,) = pictures(page)
    assert abs(big.rect.width - 240) < 0.5
    blue = _png((0.1, 0.3, 0.9))
    assert replace_picture(page, big, blue)
    (new,) = pictures(page)
    r, g, b = page.get_pixmap(clip=new.rect, dpi=36).pixel(4, 4)
    assert b > 150 and r < 80                                               # it's the new picture
    assert remove_picture(page, new) and not pictures(page)
    assert "សូមអរគុណ។" in [ln.text for ln in text_lines(page)]              # text untouched


def test_moving_a_picture_on_one_page_leaves_other_pages_alone():
    doc = _doc("<img src='pic.png' width='120'/>", pics=_png())
    p1 = doc[0]
    form, name = next((x[0], x[1]) for x in p1.get_xobjects())
    p2 = doc.new_page()                         # the same block drawn on another page (like a header)
    p2.wrap_contents()
    doc.xref_set_key(p2.xref, "Resources", f"<< /XObject << /{name} {form} 0 R >> >>")
    c = doc.get_new_xref()
    doc.update_object(c, "<<>>")
    doc.update_stream(c, f"q /{name} Do Q".encode())
    doc.xref_set_key(p2.xref, "Contents", f"{c} 0 R")
    before = pictures(doc[1])[0].rect
    p1 = doc[0]                                 # adding a page invalidates earlier page objects
    assert move_picture(p1, pictures(p1)[0], pymupdf.Rect(300, 500, 420, 580))
    assert pictures(doc[1])[0].rect == before
    assert pictures(doc[0])[0].rect.x0 == 300


def test_picture_below_moves_with_the_text():
    doc = _doc(f"<p>{TEXT[:40]}</p><img src='pic.png' width='120'/>", pics=_png())
    page = doc[0]
    para = paragraphs(text_lines(page), page)[0]
    pic = pictures(page)[0]
    _, _, moved = edit_text(page, para.first, para.first.text + "\n" + TEXT[:40])
    assert moved == 1 and pictures(page)[0].rect.y0 > pic.rect.y0 + 10
