"""In-place editing, driven through the real window with simulated input."""
import os

import pymupdf
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtTest = pytest.importorskip("PySide6.QtTest")
from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtGui import QKeySequence  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from khmerpdf import app as A  # noqa: E402
from khmerpdf.engine import text_lines  # noqa: E402
from tests.test_recover import LINES, _broken_pdf  # noqa: E402

QTest = QtTest.QTest


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtCore import QStandardPaths
    QStandardPaths.setTestModeEnabled(True)  # recent files etc. don't touch the real settings
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp, tmp_path):
    fn = tmp_path / "broken.pdf"
    _broken_pdf().save(fn)
    w = A.MainWindow()
    w.resize(1000, 800)
    w.show()
    w.open_file(str(fn))
    yield w
    w.dirty = False
    w.close()


def _click_line(w, prefix):
    c = w.canvas
    line = next(ln for ln in c.lines if ln.text.startswith(prefix))
    QTest.mouseClick(c, Qt.LeftButton, pos=QPoint(int((line.rect.x0 + 10) * c.scale),
                                                  int((line.baseline - 3) * c.scale)))
    QApplication.processEvents()
    return line


def test_edit_in_place_shows_recovered_text_and_saves_on_enter(win):
    win.set_mode("edit")
    _click_line(win, LINES[1][:6])
    ed = win.inline["editor"]
    assert ed.toPlainText() == LINES[1]                  # recovered, not the broken text
    assert win.font_box.currentText() == "Khmer OS Battambang"
    ed.setPlainText("ស្រាវជ្រាវអំពីយុត្តិធម៌")
    QTest.keyClick(ed, Qt.Key_Return)
    QApplication.processEvents()
    assert win.inline is None and len(win.undo_stack) == 1
    assert "ស្រាវជ្រាវអំពីយុត្តិធម៌" in [ln.text for ln in text_lines(win.doc[0])]


def test_escape_cancels(win):
    win.set_mode("edit")
    _click_line(win, LINES[0][:6])
    win.inline["editor"].setPlainText("should not be saved")
    QTest.keyClick(win.inline["editor"], Qt.Key_Escape)
    assert win.inline is None and not win.undo_stack


def test_click_elsewhere_saves_and_opens_next_line(win):
    win.set_mode("edit")
    _click_line(win, LINES[0][:6])
    win.inline["editor"].setPlainText("សេចក្តីពន្យល់ (កែ)")
    _click_line(win, LINES[2][:6])
    assert "សេចក្តីពន្យល់ (កែ)" in [ln.text for ln in text_lines(win.doc[0])]
    assert win.inline["editor"].toPlainText() == LINES[2]


def test_add_text_in_place(win):
    win.set_mode("add")
    s = win.canvas.scale
    QTest.mousePress(win.canvas, Qt.LeftButton, pos=QPoint(int(60 * s), int(400 * s)))
    QTest.mouseMove(win.canvas, QPoint(int(400 * s), int(440 * s)))
    QTest.mouseRelease(win.canvas, Qt.LeftButton, pos=QPoint(int(400 * s), int(440 * s)))
    win.inline["editor"].insertPlainText("សួស្តី")
    QTest.keyClick(win.inline["editor"], Qt.Key_Return)
    assert "សួស្តី" in [ln.text for ln in text_lines(win.doc[0])]
    doc = pymupdf.open(stream=win.doc.tobytes())
    assert "សួស្តី" in doc[0].get_text()


# ---- Select tool, paragraphs, Limon, OCR ------------------------------------
def _block_pdf(tmp_path):
    from tests.test_paragraphs import _page_with_block
    fn = tmp_path / "block.pdf"
    _page_with_block().parent.save(fn)
    return fn


@pytest.fixture
def block_win(qapp, tmp_path):
    w = A.MainWindow()
    w.resize(1000, 800)
    w.show()
    w.open_file(str(_block_pdf(tmp_path)))
    yield w
    w.dirty = False
    w.close()


def _para_point(w, para, dx=20):
    c = w.canvas
    return QPoint(int((para.x + dx) * c.scale), int((para.first.baseline - 2) * c.scale))


def test_select_and_drag_moves_the_paragraph(block_win):
    w = block_win
    w.set_mode("select")
    c = w.canvas
    para = c.paras[0]
    p0 = _para_point(w, para)
    QTest.mousePress(c, Qt.LeftButton, pos=p0)
    assert c.selected is para and w.delete_act.isEnabled()
    for step in (QPoint(10, 10), QPoint(40, 60), QPoint(80, 120)):
        QTest.mouseMove(c, p0 + step)
    QTest.mouseRelease(c, Qt.LeftButton, pos=p0 + QPoint(80, 120))
    QApplication.processEvents()
    assert len(w.undo_stack) == 1
    moved = c.selected                                   # still selected after the move
    assert moved is not None and moved.text == para.text
    assert abs(moved.x - (para.x + 80 / c.scale)) < 1
    assert abs(moved.first.baseline - (para.first.baseline + 120 / c.scale)) < 1


def test_side_handle_rewraps_and_delete_removes(block_win):
    w = block_win
    w.set_mode("select")
    c = w.canvas
    para = c.paras[0]
    QTest.mouseClick(c, Qt.LeftButton, pos=_para_point(w, para))
    handle = c._handles(c._box(para))["r"].center().toPoint()
    QTest.mousePress(c, Qt.LeftButton, pos=handle)
    QTest.mouseMove(c, handle + QPoint(20, 0))
    QTest.mouseMove(c, handle + QPoint(int(150 * c.scale), 0))
    QTest.mouseRelease(c, Qt.LeftButton, pos=handle + QPoint(int(150 * c.scale), 0))
    wide = c.selected
    assert wide.text == para.text and len(wide.lines) < len(para.lines)
    w.delete_selection()
    assert not text_lines(w.doc[0]) and len(w.undo_stack) == 2
    w.undo()
    assert len(text_lines(w.doc[0])) == len(wide.lines)


def test_edit_a_whole_paragraph(block_win):
    w = block_win
    w.set_mode("edit")
    c = w.canvas
    para = c.paras[0]
    QTest.mouseClick(c, Qt.LeftButton, pos=_para_point(w, para))
    assert w.inline["kind"] == "para" and w.inline["editor"].toPlainText() == para.text
    w.inline["editor"].setPlainText("សួស្តី " + para.text)
    QTest.keyClick(w.inline["editor"], Qt.Key_Return)
    from khmerpdf.engine import paragraphs
    paras = paragraphs(text_lines(w.doc[0]))
    assert len(paras) == 1 and paras[0].text == "សួស្តី " + para.text


def test_legacy_font_banner_and_conversion(qapp, tmp_path, monkeypatch):
    from tests.test_legacy import _limon_pdf
    fn = tmp_path / "limon.pdf"
    _limon_pdf().save(fn)
    w = A.MainWindow()
    w.show()
    w.open_file(str(fn))
    assert w.banner.isVisible() and w.banner.kind == "limon"
    monkeypatch.setattr(A.QMessageBox, "question", lambda *a, **k: A.QMessageBox.Ok)
    w.convert_limon()
    assert not any(ln.legacy for ln in text_lines(w.doc[0]))
    assert "សៀវភៅ" in w.doc[0].get_text()
    w.dirty = False
    w.close()


def test_ocr_from_the_banner(qapp, tmp_path):
    from khmerpdf import ocr
    if not ocr.available():
        pytest.skip("Tesseract not installed")
    from tests.test_ocr import _scan
    fn = tmp_path / "scan.pdf"
    _scan().save(fn)
    w = A.MainWindow()
    w.show()
    w.open_file(str(fn))
    assert w.banner.isVisible() and w.banner.kind == "scan"
    w.banner.action.click()
    assert len([ln for ln in w.canvas.lines if ln.ocr]) == 3 and w.banner.kind == "check"
    w.dirty = False
    w.close()


def test_clicking_a_table_cell_edits_just_that_cell(qapp, tmp_path):
    from tests.test_paragraphs import CELLS, _table_pdf
    fn = tmp_path / "table.pdf"
    _table_pdf().parent.save(fn)
    w = A.MainWindow()
    w.show()
    w.open_file(str(fn))
    w.set_mode("edit")
    c = w.canvas
    cell = next(ln for ln in c.lines if ln.text == "សុខ ដារា")
    QTest.mouseClick(c, Qt.LeftButton, pos=QPoint(int((cell.rect.x0 + 5) * c.scale), int((cell.baseline - 3) * c.scale)))
    assert w.inline["kind"] == "edit" and w.inline["editor"].toPlainText() == CELLS[1][1]
    w._close_inline(save=False)
    w.dirty = False
    w.close()


# ---- dragging the edit box, making room, pictures ----------------------------
def _layout_win(qapp, tmp_path, html, pics=None):
    from tests.test_layout import _doc
    fn = tmp_path / "layout.pdf"
    _doc(html, pics).save(fn)
    w = A.MainWindow()
    w.resize(1000, 900)
    w.show()
    w.open_file(str(fn))
    return w


def test_drag_the_edit_box_to_move_the_text(qapp, tmp_path):
    w = _layout_win(qapp, tmp_path, "<p>សួស្តី ពិភពលោក</p>")
    w.set_mode("edit")
    c = w.canvas
    line = c.lines[0]
    QTest.mouseClick(c, Qt.LeftButton, pos=QPoint(int((line.rect.x0 + 5) * c.scale), int((line.baseline - 3) * c.scale)))
    grip = w.inline["grip"]
    assert grip.isVisible()
    start = grip.rect().center()
    QTest.mousePress(grip, Qt.LeftButton, pos=start)
    QTest.mouseMove(grip, start + QPoint(40, 100))
    QTest.mouseRelease(grip, Qt.LeftButton, pos=start + QPoint(40, 100))
    QTest.keyClick(w.inline["editor"], Qt.Key_Return)
    (moved,) = text_lines(w.doc[0])
    assert moved.text == line.text
    assert abs(moved.baseline - (line.baseline + 100 / c.scale)) < 1.5
    assert abs(moved.rect.x0 - (line.rect.x0 + 40 / c.scale)) < 1.5
    w.dirty = False
    w.close()


def test_new_lines_push_the_text_below_down(qapp, tmp_path):
    from tests.test_paragraphs import TEXT
    w = _layout_win(qapp, tmp_path, f"<p>{TEXT}</p><p>សួស្តី ពិភពលោក</p><p>សូមអរគុណ។</p>")
    w.set_mode("edit")
    c = w.canvas
    *body, first, below = sorted(c.lines, key=lambda ln: ln.baseline)
    QTest.mouseClick(c, Qt.LeftButton, pos=QPoint(int((first.rect.x0 + 5) * c.scale), int((first.baseline - 3) * c.scale)))
    w.inline["editor"].setPlainText("សួស្តី\nពិភពលោក\nម្តងទៀត")
    QTest.keyClick(w.inline["editor"], Qt.Key_Return)
    lines = sorted(text_lines(w.doc[0]), key=lambda ln: ln.baseline)[len(body):]
    assert [ln.text for ln in lines] == ["សួស្តី", "ពិភពលោក", "ម្តងទៀត", "សូមអរគុណ។"]
    assert lines[3].rect.y0 > lines[2].rect.y1 - 4                      # no overlap
    assert "Made room" in w.statusBar().currentMessage()
    w.undo()
    after = sorted(text_lines(w.doc[0]), key=lambda ln: ln.baseline)
    assert [ln.text for ln in after[-2:]] == [first.text, below.text]
    w.dirty = False
    w.close()


def test_select_move_replace_and_delete_a_picture(qapp, tmp_path, monkeypatch):
    from tests.test_layout import _png
    w = _layout_win(qapp, tmp_path, "<p>សួស្តី</p><img src='pic.png' width='120'/>", pics=_png())
    w.set_mode("select")
    c = w.canvas
    (pic,) = c.pics
    p0 = QPoint(int((pic.rect.x0 + 30) * c.scale), int((pic.rect.y0 + 30) * c.scale))
    QTest.mousePress(c, Qt.LeftButton, pos=p0)
    assert c.selected is pic and w.replace_img_act.isEnabled() and w.delete_act.isEnabled()
    for d in (QPoint(10, 10), QPoint(100, 150)):
        QTest.mouseMove(c, p0 + d)
    QTest.mouseRelease(c, Qt.LeftButton, pos=p0 + QPoint(100, 150))
    (moved,) = c.pics
    assert c.selected is moved and abs(moved.rect.x0 - (pic.rect.x0 + 100 / c.scale)) < 1
    new = tmp_path / "new.png"
    new.write_bytes(_png((0.1, 0.3, 0.9)))
    monkeypatch.setattr(A.QFileDialog, "getOpenFileName", lambda *a, **k: (str(new), ""))
    w.replace_image()
    (replaced,) = c.pics
    assert replaced.xref != pic.xref
    w.delete_selection()
    assert not c.pics and len(w.undo_stack) == 3
    w.dirty = False
    w.close()


# ---- menus, pages, printing --------------------------------------------------
@pytest.fixture
def pages_win(qapp, tmp_path):
    from tests.test_pages import _doc
    fn = tmp_path / "pages.pdf"
    _doc(4).save(fn)
    w = A.MainWindow()
    w.show()
    w.open_file(str(fn))
    yield w
    w.dirty = False
    w.close()


def _page_labels(w):
    from tests.test_pages import _labels
    return _labels(w.doc)


def test_menus_have_print_and_page_tools(pages_win):
    titles = [a.text().replace("&", "") for a in pages_win.menuBar().actions()]
    assert titles == ["File", "Edit", "View", "Pages", "Insert", "Tools", "Help"]
    file_menu = pages_win.menuBar().actions()[0].menu()
    assert "Print…" in [a.text() for a in file_menu.actions()]
    assert pages_win.print_act.shortcut() == QKeySequence(QKeySequence.Print)


def test_page_operations_and_undo(pages_win, monkeypatch):
    w = pages_win
    w.goto(1)
    w.insert_blank_page()
    assert _page_labels(w) == ["ទំព័រ 1", "ទំព័រ 2", "", "ទំព័រ 3", "ទំព័រ 4"] and w.page_no == 2
    assert w.thumbs.count() == 5 and w.page_total.text().strip() == "/ 5"
    w.undo()
    assert w.doc.page_count == 4 and w.thumbs.count() == 4
    w.goto(3)
    w.move_pages(-1)
    assert _page_labels(w) == ["ទំព័រ 1", "ទំព័រ 2", "ទំព័រ 4", "ទំព័រ 3"] and w.page_no == 2
    w.rotate_pages(90)
    assert w.doc[2].rotation == 90
    monkeypatch.setattr(A.QMessageBox, "question", lambda *a, **k: A.QMessageBox.Yes)
    w.thumbs.clearSelection()            # pick pages 1 and 2, as Ctrl-clicks would
    w.thumbs.item(0).setSelected(True)
    w.thumbs.item(1).setSelected(True)
    w.delete_pages()
    assert _page_labels(w) == ["ទំព័រ 4", "ទំព័រ 3"]


def test_drag_thumbnails_to_reorder(pages_win):
    w = pages_win
    it = w.thumbs.takeItem(3)            # what a drop does to the list
    w.thumbs.insertItem(0, it)
    w.thumbs.reordered.emit()
    assert _page_labels(w) == ["ទំព័រ 4", "ទំព័រ 1", "ទំព័រ 2", "ទំព័រ 3"]


def test_merge_and_extract(pages_win, tmp_path, monkeypatch):
    from tests.test_pages import _doc
    w = pages_win
    other = tmp_path / "other.pdf"
    _doc(2).save(other)
    monkeypatch.setattr(A.QFileDialog, "getOpenFileName", lambda *a, **k: (str(other), ""))
    w.insert_from_pdf()                  # after page 1
    assert w.doc.page_count == 6 and _page_labels(w)[1:3] == ["ទំព័រ 1", "ទំព័រ 2"]
    out = tmp_path / "part.pdf"
    monkeypatch.setattr(A.QInputDialog, "getText", lambda *a, **k: ("4-6", True))
    monkeypatch.setattr(A.QFileDialog, "getSaveFileName", lambda *a, **k: (str(out), ""))
    w.extract_pages()
    from tests.test_pages import _labels
    assert _labels(pymupdf.open(out)) == ["ទំព័រ 2", "ទំព័រ 3", "ទំព័រ 4"]


def test_print_pages(pages_win, tmp_path):
    from PySide6.QtPrintSupport import QPrinter
    w = pages_win
    w.rotate_pages(90)                   # a landscape page prints turned to fit the paper
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    out = tmp_path / "printed.pdf"
    printer.setOutputFileName(str(out))
    printer.setResolution(100)
    assert w.paint_pages(printer, [0, 1, 3], progress=False)
    printed = pymupdf.open(out)
    assert printed.page_count == 3
    assert printed[0].get_images()      # each page carries the rendered page


# ---- forms, highlights, notes, signatures ------------------------------------
def _click(w, rect_or_point, button=Qt.LeftButton):
    c = w.canvas
    pt = rect_or_point if isinstance(rect_or_point, pymupdf.Point) else (rect_or_point.tl + rect_or_point.br) / 2
    QTest.mouseClick(c, button, pos=QPoint(int(pt.x * c.scale), int(pt.y * c.scale)))
    QApplication.processEvents()


def test_fill_a_form_by_clicking(qapp, tmp_path, monkeypatch):
    from tests.test_forms_annots import _form
    fn = tmp_path / "form.pdf"
    _form().save(fn)
    w = A.MainWindow()
    w.show()
    w.open_file(str(fn))
    assert w.banner.kind == "form"
    fields = {f.name: f for f in w.canvas.fields}
    _click(w, fields["name"].rect)                                   # View tool: click a text field
    assert w.inline["kind"] == "field"
    w.inline["editor"].setPlainText("សុខ ស្រីនិច្ច")
    QTest.keyClick(w.inline["editor"], Qt.Key_Return)
    _click(w, fields["agree"].rect)                                  # tick the checkbox
    monkeypatch.setattr(w, "_popup", lambda menu, at: next(x for x in menu.actions() if x.text() == "ថ្នាក់ទី៨"))
    _click(w, fields["grade"].rect)                                  # pick from the list
    values = {f.name: f.value for f in w.canvas.fields}
    assert values["name"] == "សុខ ស្រីនិច្ច" and values["agree"] == "Yes" and values["grade"] == "ថ្នាក់ទី៨"
    assert len(w.undo_stack) == 3
    w.dirty = False
    w.close()


def test_highlight_note_and_right_click(win, monkeypatch):
    w = win
    w.set_mode("highlight")
    line = w.canvas.lines[0]
    _click(w, line.rect)                                             # a click highlights the line
    assert [a.kind for a in w.canvas.annots] == ["highlight"]
    w.set_mode("note")
    monkeypatch.setattr(A.QInputDialog, "getMultiLineText", lambda *a, **k: ("សូមពិនិត្យ", True))
    _click(w, pymupdf.Point(400, 600))
    assert sorted(a.kind for a in w.canvas.annots) == ["highlight", "note"]
    shown = []
    monkeypatch.setattr(w, "_popup", lambda menu, at: shown.extend(x.text() for x in menu.actions()))
    w._context_menu(QPoint(0, 0), (line.rect.tl + line.rect.br) / 2)
    assert "Remove Highlight" in shown and "Edit Text" in shown
    w.set_mode("select")
    note = next(a for a in w.canvas.annots if a.kind == "note")
    _click(w, note.rect)
    assert w.canvas.selected.kind == "note"
    w.delete_selection()
    assert [a.kind for a in w.canvas.annots] == ["highlight"]


def test_place_a_signature(win):
    from tests.test_forms_annots import pymupdf as pm
    sig = pm.open().new_page(width=300, height=100)
    sig.draw_line((20, 60), (280, 40), width=3)
    w = win
    w._signature = sig.get_pixmap(dpi=96, alpha=True).tobytes("png")
    w.set_mode("place")
    _click(w, pymupdf.Point(400, 700))
    assert w.canvas.mode == "select" and isinstance(w.canvas.selected, A.Picture)
    assert abs((w.canvas.selected.rect.x0 + w.canvas.selected.rect.x1) / 2 - 400) < 2


# ---- scrolling between pages, tabs --------------------------------------------
def _wheel(w, dy):
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QWheelEvent
    vp = w.scroll.viewport()
    pos = QPointF(vp.width() / 2, vp.height() / 2)
    ev = QWheelEvent(pos, vp.mapToGlobal(pos), QPoint(0, 0), QPoint(0, dy), Qt.NoButton, Qt.NoModifier,
                     Qt.NoScrollPhase, False)
    QApplication.sendEvent(vp, ev)
    QApplication.processEvents()


def test_scrolling_past_the_page_end_turns_the_page(pages_win):
    w = pages_win
    w.set_zoom(2.0)  # taller than the window, so the page scrolls first
    QApplication.processEvents()
    bar = w.scroll.verticalScrollBar()
    assert bar.maximum() > 0
    _wheel(w, -120)
    assert w.page_no == 0 and bar.value() > 0          # scrolls within the page
    bar.setValue(bar.maximum())
    _wheel(w, -120)
    assert w.page_no == 1 and bar.value() == 0          # on to the top of page 2
    _wheel(w, 120)
    assert w.page_no == 0 and bar.value() == bar.maximum()  # back to the bottom of page 1
    w.set_zoom(0.5)  # the whole page fits: each notch turns a page
    QApplication.processEvents()
    _wheel(w, -120)
    _wheel(w, -120)
    assert w.page_no == 2


def test_documents_open_in_tabs(pages_win, tmp_path, monkeypatch):
    w = pages_win
    first = w.path
    second = tmp_path / "second.pdf"
    _broken_pdf().save(second)
    w.goto(2)
    w._snapshot()
    w.doc.delete_page(3)
    w._changed()
    assert w.tab_bar.tabText(0).startswith("• ")
    w.open_file(str(second))
    assert w.tab_bar.count() == 2 and w.path == str(second) and w.doc.page_count == 1
    assert not w.undo_stack and not w.dirty
    w.tab_bar.setCurrentIndex(0)                         # each tab keeps its own document
    assert w.path == first and w.page_no == 2 and w.doc.page_count == 3 and len(w.undo_stack) == 1
    w.open_file(str(second))                             # already open: switch to it
    assert w.tab_bar.count() == 2 and w.path == str(second)
    w.tab_bar.setCurrentIndex(0)
    asked = []
    monkeypatch.setattr(A.QMessageBox, "question", lambda *a, **k: asked.append(a[2]) or A.QMessageBox.Discard)
    assert w.close_tab(0)
    assert asked == ["Save changes to “pages.pdf”?"]
    assert w.tab_bar.count() == 1 and w.path == str(second)
    QTest.mouseClick(w.tab_bar.tabButton(0, A.QTabBar.RightSide), Qt.LeftButton)  # its × button
    assert w.tab_bar.count() == 0 and w.doc is None and w.stack.currentIndex() == 0


# ---- 0.7.0: styled text, ticks, pictures, Organize Pages -----------------------
@pytest.fixture
def styled_win(qapp, tmp_path):
    from khmerpdf.engine import insert_khmer_text
    from khmerpdf.styles import Style
    doc = pymupdf.open()
    runs = [("ឈ្មោះ៖ ", Style()), ("សុខ សាន្ត", Style(bold=True))]
    insert_khmer_text(doc.new_page(), pymupdf.Rect(60, 80, 500, 120), "ឈ្មោះ៖ សុខ សាន្ត", size=16,
                      wrap_text=False, runs=runs)
    fn = tmp_path / "styled.pdf"
    doc.save(fn)
    w = A.MainWindow()
    w.resize(1000, 800)
    w.show()
    w.open_file(str(fn))
    yield w
    w.dirty = False
    w.close()


def _runs(w):
    return [(t, s.bold, s.italic) for t, s in text_lines(w.doc[0])[0].runs]


def test_edit_box_shows_bold_and_ctrl_i_makes_italic(styled_win):
    from PySide6.QtGui import QTextCursor
    w = styled_win
    w.set_mode("edit")
    _click_line(w, "ឈ្មោះ")
    ed = w.inline["editor"]
    cur = ed.textCursor()
    cur.setPosition(len("ឈ្មោះ៖ សុ"))
    ed.setTextCursor(cur)
    assert w.bold_act.isChecked() and w.bold_act.isEnabled()       # the cursor is in the bold name
    cur.setPosition(0)
    cur.setPosition(len("ឈ្មោះ"), QTextCursor.KeepAnchor)
    ed.setTextCursor(cur)
    QTest.keyClick(ed, Qt.Key_I, Qt.ControlModifier)
    assert w.italic_act.isChecked()
    QTest.keyClick(ed, Qt.Key_Return)
    QApplication.processEvents()
    assert _runs(w) == [("ឈ្មោះ", False, True), ("៖ ", False, False), ("សុខ សាន្ត", True, False)]
    assert w.undo_stack


def test_unchanged_styled_text_is_not_rewritten(styled_win):
    w = styled_win
    w.set_mode("edit")
    _click_line(w, "ឈ្មោះ")
    QTest.keyClick(w.inline["editor"], Qt.Key_Return)
    assert not w.undo_stack


def test_toolbar_bold_applies_to_the_selection_only(styled_win):
    from PySide6.QtGui import QTextCursor
    w = styled_win
    w.set_mode("edit")
    _click_line(w, "ឈ្មោះ")
    ed = w.inline["editor"]
    cur = ed.textCursor()
    cur.setPosition(0)
    cur.setPosition(len("ឈ្មោះ"), QTextCursor.KeepAnchor)
    ed.setTextCursor(cur)
    w.bold_act.trigger()
    w._close_inline(save=True)
    assert _runs(w) == [("ឈ្មោះ", True, False), ("៖ ", False, False), ("សុខ សាន្ត", True, False)]


def test_tick_tool_ticks_and_clears_a_drawn_box(qapp, tmp_path):
    from khmerpdf import marks as M
    doc = pymupdf.open()
    doc.new_page().draw_rect(pymupdf.Rect(100, 100, 114, 114), color=(0, 0, 0), width=0.8)
    fn = tmp_path / "box.pdf"
    doc.save(fn)
    w = A.MainWindow()
    w.show()
    w.open_file(str(fn))
    w.set_mode("tick")
    c = w.canvas
    for _ in range(2):
        QTest.mouseClick(c, Qt.LeftButton, pos=QPoint(int(107 * c.scale), int(107 * c.scale)))
        QApplication.processEvents()
    assert len(w.undo_stack) == 2 and not M.is_ticked(w.doc[0], pymupdf.Rect(100, 100, 114, 114))
    w.undo()
    assert M.is_ticked(w.doc[0], pymupdf.Rect(100, 100, 114, 114))
    w.dirty = False
    w.close()


def test_insert_picture(win, tmp_path, monkeypatch):
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 120, 60), False)
    pix.clear_with(90)
    png = tmp_path / "logo.png"
    png.write_bytes(pix.tobytes("png"))
    monkeypatch.setattr(A.QFileDialog, "getOpenFileName", lambda *a, **k: (str(png), ""))
    win.insert_picture()
    assert win.canvas.mode == "place" and "picture" in win.tool_hint.text()
    c = win.canvas
    QTest.mouseClick(c, Qt.LeftButton, pos=QPoint(int(300 * c.scale), int(400 * c.scale)))
    QApplication.processEvents()
    assert len(win.doc[0].get_images()) == 1
    assert win.canvas.mode == "select" and isinstance(win.canvas.selected, A.Picture)


def test_organize_pages(pages_win, monkeypatch):
    w = pages_win
    w.organize_act.trigger()
    assert w._organizing() and w.organizer.grid.count() == 4
    g = w.organizer.grid
    g.clearSelection()
    g.item(1).setSelected(True)
    g.item(2).setSelected(True)
    w.rotate_pages(90)
    assert [p.rotation for p in w.doc] == [0, 90, 90, 0]
    g.clearSelection()
    it = g.takeItem(3)                                   # drag the last page to the front
    g.insertItem(0, it)
    it.setSelected(True)
    g.reordered.emit()
    assert _page_labels(w) == ["ទំព័រ 4", "ទំព័រ 1", "ទំព័រ 2", "ទំព័រ 3"]
    assert w.organizer.selected() == [0]
    monkeypatch.setattr(A.QMessageBox, "question", lambda *a, **k: A.QMessageBox.Yes)
    QTest.keyClick(g, Qt.Key_Delete)
    assert _page_labels(w) == ["ទំព័រ 1", "ទំព័រ 2", "ទំព័រ 3"] and g.count() == 3
    w.undo()
    assert g.count() == 4
    w.organizer.grid.open_page.emit(2)                   # double-click a page: back to it
    assert not w._organizing() and w.page_no == 2


# ---- 0.7.3: the font follows the text being edited ------------------------------
def _select(ed, start, end):
    from PySide6.QtGui import QTextCursor
    cur = ed.textCursor()
    cur.setPosition(start)
    cur.setPosition(end, QTextCursor.KeepAnchor)
    ed.setTextCursor(cur)


def _fonts(w):
    return [(t, s.font) for t, s in text_lines(w.doc[0])[0].runs] or [(text_lines(w.doc[0])[0].text, "")]


def test_a_font_picked_for_a_selection_changes_only_the_selection(styled_win):
    w = styled_win
    w.set_mode("edit")
    _click_line(w, "ឈ្មោះ")
    ed = w.inline["editor"]
    base = w.cur_font
    _select(ed, len("ឈ្មោះ៖ "), len("ឈ្មោះ៖ សុខ"))
    w.font_box.setCurrentText("Khmer OS Muol Light")
    assert w.cur_font == base                       # the rest of the text keeps its font
    _select(ed, 0, 3)
    assert w.font_box.currentText() == base          # the list shows the font where the cursor is
    _select(ed, len("ឈ្មោះ៖ "), len("ឈ្មោះ៖ សុ"))
    assert w.font_box.currentText() == "Khmer OS Muol Light"
    w._close_inline(save=True)
    got = {t: f for t, f in _fonts(w)}
    assert got.get("សុខ") == "Khmer OS Muol Light"
    assert all(f != "Khmer OS Muol Light" for t, f in got.items() if t != "សុខ")


def test_fonts_that_share_a_family_name_are_kept_apart(styled_win):
    w = styled_win                                    # written in Battambang Regular
    w.set_mode("edit")
    _click_line(w, "ឈ្មោះ")
    ed = w.inline["editor"]
    assert w.fonts.families["Battambang Regular"] == w.fonts.families["Battambang Bold"]
    ed.moveCursor(ed.textCursor().MoveOperation.End)
    ed.insertPlainText("ក")
    w._close_inline(save=True)
    assert all(f in ("", "Battambang Regular") for _, f in _fonts(w))   # not saved as Battambang Bold


def test_text_in_an_unavailable_font_gets_the_default_font_not_the_last_one_picked(qapp, tmp_path):
    from khmerpdf.engine import insert_khmer_text
    doc = pymupdf.open()
    page = doc.new_page()
    insert_khmer_text(page, pymupdf.Rect(60, 80, 500, 120), "ឈ្មោះ", size=16, wrap_text=False)
    page.insert_text((60, 300), "Hello there", fontname="Courier", fontsize=14)  # a font we don't have
    fn = tmp_path / "two.pdf"
    doc.save(fn)
    w = A.MainWindow()
    w.resize(1000, 800)
    w.show()
    w.open_file(str(fn))
    try:
        w.set_mode("edit")
        _click_line(w, "ឈ្មោះ")
        w.font_box.setCurrentText("Khmer OS Muol Light")  # all of it: nothing selected
        w._close_inline(save=True)
        _click_line(w, "Hello")
        assert w.cur_font == w.default_font != "Khmer OS Muol Light"
        assert w.font_box.currentText() == w.default_font
    finally:
        w.dirty = False
        w.close()


def test_white_text_on_a_coloured_cell_shows_on_that_colour(qapp, tmp_path):
    from khmerpdf.engine import insert_khmer_text
    doc = pymupdf.open()
    page = doc.new_page()
    page.draw_rect(pymupdf.Rect(50, 60, 400, 130), color=None, fill=(0.1, 0.5, 0.2))  # a green cell
    insert_khmer_text(page, pymupdf.Rect(60, 80, 380, 120), "ក្រុមទី១", size=16, color=(1, 1, 1),
                      wrap_text=False)
    insert_khmer_text(page, pymupdf.Rect(60, 300, 380, 340), "ខ្មៅ", size=16, wrap_text=False)
    fn = tmp_path / "cell.pdf"
    doc.save(fn)
    w = A.MainWindow()
    w.resize(1000, 800)
    w.show()
    w.open_file(str(fn))
    try:
        w.set_mode("edit")
        _click_line(w, "ក្រុម")
        sheet = w.inline["editor"].styleSheet()
        assert "color: #ffffff" in sheet and "background: #ffffff" not in sheet
        bg = A.QColor(sheet.split("background: ")[1][:7])
        assert bg.green() > bg.red() + 40              # the cell's green, as on the page
        w._close_inline(save=False)
        _click_line(w, "ខ្មៅ")                           # black text on white paper: as before
        assert "background: #ffffff" in w.inline["editor"].styleSheet() or \
            "background: #fcfcfc" in w.inline["editor"].styleSheet()
        w._close_inline(save=False)
    finally:
        w.dirty = False
        w.close()


def test_readable_background_never_matches_the_text():
    white, black = A.QColor("white"), A.QColor("black")
    assert A.readable_background(white, white) != white          # white text: not on white
    assert A.readable_background(A.QColor("#1a7f37"), white).name() == "#1a7f37"  # a dark cell: kept
    assert A.readable_background(white, black) == white


def test_a_bold_font_file_shows_bold_and_saves_without_extra_bold(qapp, tmp_path):
    from khmerpdf.engine import insert_khmer_text, load_font
    from khmerpdf.styles import font_path
    doc = pymupdf.open()
    insert_khmer_text(doc.new_page(), pymupdf.Rect(60, 80, 500, 120), "ចំណងជើង", size=16, wrap_text=False,
                      font=load_font(font_path("Battambang Bold")))
    fn = tmp_path / "bold.pdf"
    doc.save(fn)
    w = A.MainWindow()
    w.resize(1000, 800)
    w.show()
    w.open_file(str(fn))
    try:
        w.set_mode("edit")
        _click_line(w, "ចំណង")
        ed = w.inline["editor"]
        assert w.font_box.currentText() == "Battambang Bold"
        assert ed.textCursor().charFormat().property(A.QTextFormat.FontStyleName) == "Bold"
        assert not w.bold_act.isChecked()            # it's the bold font, not bold on top of it
        ed.insertPlainText("ថ្មី")
        w._close_inline(save=True)
        (ln,) = text_lines(w.doc[0])
        assert ln.text == "ចំណងជើងថ្មី" and ln.font == "Battambang Bold"
        assert not any(s.bold for _, s in ln.runs)
    finally:
        w.dirty = False
        w.close()
