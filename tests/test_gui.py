"""In-place editing, driven through the real window with simulated input."""
import os

import pymupdf
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtTest = pytest.importorskip("PySide6.QtTest")
from PySide6.QtCore import QPoint, Qt  # noqa: E402
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


def test_limon_banner_and_conversion(qapp, tmp_path, monkeypatch):
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
