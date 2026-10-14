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
