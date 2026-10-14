"""KhmerPDF Editor - desktop GUI (PySide6)."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pymupdf
from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (QAction, QActionGroup, QColor, QFont, QFontDatabase, QIcon,
                           QImage, QKeySequence, QPainter, QPen, QPixmap)
from PySide6.QtWidgets import (QApplication, QColorDialog, QComboBox, QDialog,
                               QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout,
                               QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox,
                               QPlainTextEdit, QPushButton, QScrollArea, QSpinBox, QToolBar,
                               QVBoxLayout, QWidget)

from . import __version__
from .engine import (TextLine, bundled_fonts, insert_khmer_text, load_font,
                     replace_text, search, system_khmer_fonts, text_lines)

APP_NAME = "KhmerPDF Editor"
SCREEN_DPI = 96
UNDO_LIMIT = 30
ZWSP = "​"


def resource_path(*parts: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base.joinpath(*parts)


# --------------------------------------------------------------------------
# Font registry: our engine fonts + the same files registered with Qt
# --------------------------------------------------------------------------
class Fonts:
    def __init__(self):
        self.paths: dict[str, str] = {}
        self.families: dict[str, str] = {}
        for name, path in bundled_fonts().items():
            self.add(name, path)
        for name, path in system_khmer_fonts().items():
            if name not in self.paths:
                self.add(name, path)

    def add(self, name: str, path: str) -> str:
        fid = QFontDatabase.addApplicationFont(path)
        fams = QFontDatabase.applicationFontFamilies(fid) if fid >= 0 else []
        self.paths[name] = path
        self.families[name] = fams[0] if fams else "Sans Serif"
        return name

    def qfont(self, name: str, pt: float) -> QFont:
        f = QFont(self.families.get(name, "Sans Serif"))
        f.setPointSizeF(max(pt, 6))
        return f


# --------------------------------------------------------------------------
# Text entry dialog
# --------------------------------------------------------------------------
class TextDialog(QDialog):
    def __init__(self, parent, fonts: Fonts, *, title: str, text: str = "", font: str,
                 size: float, color: QColor, align: str = "left", show_align=True,
                 original: TextLine | None = None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.fonts = fonts
        self.color = QColor(color)
        lay = QVBoxLayout(self)

        if original is not None:
            msg = QLabel()
            msg.setWordWrap(True)
            if original.garbled:
                msg.setText("<b>The original text can't be read correctly</b> (this PDF has a broken "
                            "Khmer text layer). Type the replacement text below.<br>"
                            f"<span style='color:gray'>Extracted: {original.text}</span>")
            else:
                msg.setText(f"Original: <b>{original.text}</b>")
            lay.addWidget(msg)

        self.edit = QPlainTextEdit(text)
        self.edit.setMinimumSize(520, 160)
        lay.addWidget(self.edit)

        form = QFormLayout()
        row = QHBoxLayout()
        self.font_box = QComboBox()
        self.font_box.addItems(list(fonts.paths))
        self.font_box.setCurrentText(font)
        self.size_box = QDoubleSpinBox()
        self.size_box.setRange(4, 200)
        self.size_box.setValue(size)
        self.color_btn = QPushButton()
        self.color_btn.setFixedWidth(40)
        self.color_btn.clicked.connect(self._pick_color)
        row.addWidget(self.font_box, 1)
        row.addWidget(QLabel("Size"))
        row.addWidget(self.size_box)
        row.addWidget(QLabel("Color"))
        row.addWidget(self.color_btn)
        form.addRow("Font", row)
        self.align_box = QComboBox()
        self.align_box.addItems(["left", "center", "right"])
        self.align_box.setCurrentText(align)
        if show_align:
            form.addRow("Align", self.align_box)
        lay.addLayout(form)

        tools = QHBoxLayout()
        zw = QPushButton("Insert word break (ZWSP)")
        zw.setToolTip("Khmer has no spaces between words. A zero-width space marks where\n"
                      "a line may wrap without showing a visible space.")
        zw.clicked.connect(lambda: self.edit.insertPlainText(ZWSP))
        tools.addWidget(zw)
        tools.addStretch()
        lay.addLayout(tools)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

        self.font_box.currentTextChanged.connect(self._update_preview)
        self.size_box.valueChanged.connect(self._update_preview)
        self._update_preview()
        self.edit.setFocus()

    def _pick_color(self):
        c = QColorDialog.getColor(self.color, self, "Text color")
        if c.isValid():
            self.color = c
            self._update_preview()

    def _update_preview(self):
        self.edit.setFont(self.fonts.qfont(self.font_box.currentText(), self.size_box.value() * 1.2))
        self.color_btn.setStyleSheet(f"background:{self.color.name()}; border:1px solid #888;")
        self.edit.setStyleSheet(f"color:{self.color.name()};")

    def values(self):
        c = self.color
        return dict(text=self.edit.toPlainText(), font=self.font_box.currentText(),
                    size=self.size_box.value(), color=(c.redF(), c.greenF(), c.blueF()),
                    align=self.align_box.currentText())


# --------------------------------------------------------------------------
# Page canvas
# --------------------------------------------------------------------------
class PageCanvas(QWidget):
    rect_drawn = Signal(object)    # pymupdf.Rect in page coordinates
    line_clicked = Signal(object)  # TextLine

    def __init__(self):
        super().__init__()
        self.setMouseTracking(True)
        self.pixmap: QPixmap | None = None
        self.scale = 1.0
        self.mode = "view"
        self.lines: list[TextLine] = []
        self.hover: TextLine | None = None
        self.highlights: list[pymupdf.Rect] = []
        self._drag_start: QPoint | None = None
        self._drag_rect: QRect | None = None

    def set_page(self, pixmap: QPixmap, scale: float, lines: list[TextLine]):
        self.pixmap, self.scale, self.lines = pixmap, scale, lines
        self.hover = None
        size = pixmap.deviceIndependentSize().toSize()
        self.setFixedSize(size)
        self.update()

    def _to_page(self, p: QPoint) -> pymupdf.Point:
        return pymupdf.Point(p.x() / self.scale, p.y() / self.scale)

    def _to_screen(self, r: pymupdf.Rect) -> QRectF:
        s = self.scale
        return QRectF(r.x0 * s, r.y0 * s, r.width * s, r.height * s)

    def paintEvent(self, _):
        if not self.pixmap:
            return
        p = QPainter(self)
        p.drawPixmap(0, 0, self.pixmap)
        p.setRenderHint(QPainter.Antialiasing)
        for r in self.highlights:
            p.fillRect(self._to_screen(r), QColor(255, 200, 0, 90))
        if self.mode == "edit":
            p.setPen(QPen(QColor(0, 120, 215, 70), 1, Qt.DashLine))
            for line in self.lines:
                p.drawRect(self._to_screen(line.rect))
            if self.hover:
                p.setPen(QPen(QColor(0, 120, 215), 2))
                p.fillRect(self._to_screen(self.hover.rect), QColor(0, 120, 215, 40))
                p.drawRect(self._to_screen(self.hover.rect))
        if self._drag_rect:
            p.setPen(QPen(QColor(0, 150, 80), 1.5, Qt.DashLine))
            p.fillRect(self._drag_rect, QColor(0, 150, 80, 30))
            p.drawRect(self._drag_rect)

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        if self.mode == "add":
            self._drag_start = e.position().toPoint()
            self._drag_rect = QRect(self._drag_start, QSize(0, 0))
        elif self.mode == "edit" and self.hover:
            self.line_clicked.emit(self.hover)

    def mouseMoveEvent(self, e):
        pos = e.position().toPoint()
        if self.mode == "add" and self._drag_start is not None:
            self._drag_rect = QRect(self._drag_start, pos).normalized()
            self.update()
        elif self.mode == "edit":
            pt = self._to_page(pos)
            hit = next((ln for ln in self.lines if pt in ln.rect), None)
            if hit is not self.hover:
                self.hover = hit
                self.setCursor(Qt.PointingHandCursor if hit else Qt.ArrowCursor)
                self.update()

    def mouseReleaseEvent(self, e):
        if self.mode != "add" or self._drag_start is None:
            return
        r = self._drag_rect or QRect()
        self._drag_start, self._drag_rect = None, None
        self.update()
        x0, y0 = r.left() / self.scale, r.top() / self.scale
        if r.width() < 15 or r.height() < 10:  # a click: default box
            rect = pymupdf.Rect(x0, y0, x0 + 300, y0 + 60)
        else:
            rect = pymupdf.Rect(x0, y0, r.right() / self.scale, r.bottom() / self.scale)
        self.rect_drawn.emit(rect)


# --------------------------------------------------------------------------
# Main window
# --------------------------------------------------------------------------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.fonts = Fonts()
        self.doc: pymupdf.Document | None = None
        self.path: str | None = None
        self.page_no = 0
        self.zoom = 1.0
        self.undo_stack: list[tuple[bytes, int]] = []
        self.redo_stack: list[tuple[bytes, int]] = []
        self.dirty = False
        self.cur_font = "Battambang Regular" if "Battambang Regular" in self.fonts.paths else next(iter(self.fonts.paths))
        self.cur_size = 14.0
        self.cur_color = QColor(0, 0, 0)
        self.search_hits: list[tuple[int, pymupdf.Rect]] = []

        self.canvas = PageCanvas()
        self.canvas.rect_drawn.connect(self.add_text_at)
        self.canvas.line_clicked.connect(self.edit_line)
        scroll = QScrollArea()
        scroll.setWidget(self.canvas)
        scroll.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        scroll.setStyleSheet("QScrollArea { background: #5a5a5a; }")
        self.setCentralWidget(scroll)
        self.placeholder = QLabel("Open a PDF (Ctrl+O) or drag one here")
        self._build_toolbar()
        self.setAcceptDrops(True)
        self.resize(1100, 850)
        self._update_title()
        icon = resource_path("assets", "icon.ico")
        if icon.exists():
            self.setWindowIcon(QIcon(str(icon)))

    # ---- UI -------------------------------------------------------------
    def _build_toolbar(self):
        tb = QToolBar("Main")
        tb.setMovable(False)
        self.addToolBar(tb)

        def act(text, slot, shortcut=None, tip=None):
            a = QAction(text, self)
            a.triggered.connect(slot)
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            a.setToolTip(tip or text)
            tb.addAction(a)
            return a

        act("Open", self.open_dialog, QKeySequence.Open)
        act("Save", self.save, QKeySequence.Save)
        act("Save As", self.save_as, QKeySequence.SaveAs)
        tb.addSeparator()
        self.undo_act = act("Undo", self.undo, QKeySequence.Undo)
        self.redo_act = act("Redo", self.redo, QKeySequence.Redo)
        tb.addSeparator()
        act("◀", lambda: self.goto(self.page_no - 1), "PgUp", "Previous page")
        self.page_spin = QSpinBox()
        self.page_spin.setMinimum(1)
        self.page_spin.valueChanged.connect(lambda v: self.goto(v - 1))
        tb.addWidget(self.page_spin)
        self.page_total = QLabel(" / 0 ")
        tb.addWidget(self.page_total)
        act("▶", lambda: self.goto(self.page_no + 1), "PgDown", "Next page")
        tb.addSeparator()
        act("−", lambda: self.set_zoom(self.zoom / 1.2), QKeySequence.ZoomOut, "Zoom out")
        self.zoom_label = QLabel("100%")
        tb.addWidget(self.zoom_label)
        act("+", lambda: self.set_zoom(self.zoom * 1.2), QKeySequence.ZoomIn, "Zoom in")
        tb.addSeparator()

        group = QActionGroup(self)
        self.mode_actions: dict[str, QAction] = {}
        for mode, label, key, tip in (("view", "View", "Esc", "Browse the document"),
                                      ("add", "Add Text", "T", "Drag a box (or click) to add Khmer text"),
                                      ("edit", "Edit Text", "E", "Click a line of text to replace it")):
            a = QAction(label, self, checkable=True)
            a.setShortcut(key)
            a.setToolTip(f"{tip} ({key})")
            a.triggered.connect(lambda _=False, m=mode: self.set_mode(m))
            group.addAction(a)
            tb.addAction(a)
            self.mode_actions[mode] = a
            if mode == "view":
                a.setChecked(True)
        tb.addSeparator()

        self.font_box = QComboBox()
        self.font_box.addItems(list(self.fonts.paths))
        self.font_box.addItem("Add font file…")
        self.font_box.setCurrentText(self.cur_font)
        self.font_box.currentTextChanged.connect(self._font_changed)
        tb.addWidget(self.font_box)
        self.size_spin = QDoubleSpinBox()
        self.size_spin.setRange(4, 200)
        self.size_spin.setValue(self.cur_size)
        self.size_spin.valueChanged.connect(lambda v: setattr(self, "cur_size", v))
        tb.addWidget(self.size_spin)
        self.color_btn = QPushButton()
        self.color_btn.setFixedWidth(28)
        self.color_btn.setToolTip("Text color")
        self.color_btn.clicked.connect(self._pick_color)
        self._paint_color_btn()
        tb.addWidget(self.color_btn)
        tb.addSeparator()

        self.find_box = QLineEdit()
        self.find_box.setPlaceholderText("Find (Khmer or English)…")
        self.find_box.setClearButtonEnabled(True)
        self.find_box.setMaximumWidth(220)
        self.find_box.returnPressed.connect(self.find_next)
        tb.addWidget(self.find_box)
        f = QAction(self)
        f.setShortcut(QKeySequence.Find)
        f.triggered.connect(lambda: (self.find_box.setFocus(), self.find_box.selectAll()))
        self.addAction(f)
        self._refresh_undo_actions()

    def _paint_color_btn(self):
        self.color_btn.setStyleSheet(f"background:{self.cur_color.name()}; border:1px solid #888;")

    def _pick_color(self):
        c = QColorDialog.getColor(self.cur_color, self, "Text color")
        if c.isValid():
            self.cur_color = c
            self._paint_color_btn()

    def _font_changed(self, name: str):
        if name != "Add font file…":
            self.cur_font = name
            return
        path, _ = QFileDialog.getOpenFileName(self, "Add a Khmer font", "", "Fonts (*.ttf *.otf)")
        self.font_box.blockSignals(True)
        if path:
            try:
                load_font(path)
                name = self.fonts.add(Path(path).stem.replace("-", " "), path)
                self.font_box.insertItem(self.font_box.count() - 1, name)
                self.cur_font = name
            except Exception as exc:
                QMessageBox.warning(self, APP_NAME, f"Could not load font:\n{exc}")
        self.font_box.setCurrentText(self.cur_font)
        self.font_box.blockSignals(False)

    def set_mode(self, mode: str):
        self.mode_actions[mode].setChecked(True)
        self.canvas.mode = mode
        self.canvas.hover = None
        self.canvas.setCursor(Qt.CrossCursor if mode == "add" else Qt.ArrowCursor)
        hints = {"view": "", "add": "Drag a box where the text should go, or click for a default box.",
                 "edit": "Click a line of text to replace it."}
        self.statusBar().showMessage(hints[mode])
        self.canvas.update()

    # ---- document -------------------------------------------------------
    def open_dialog(self):
        if not self._confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Open PDF", "", "PDF files (*.pdf)")
        if path:
            self.open_file(path)

    def open_file(self, path: str):
        try:
            data = Path(path).read_bytes()
            doc = pymupdf.open(stream=data, filetype="pdf")  # in memory: no file lock on Windows
            if doc.needs_pass:
                QMessageBox.warning(self, APP_NAME, "Password-protected PDFs are not supported yet.")
                return
        except Exception as exc:
            QMessageBox.critical(self, APP_NAME, f"Could not open the file:\n{exc}")
            return
        self.doc, self.path, self.page_no = doc, path, 0
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.dirty = False
        self.page_spin.blockSignals(True)
        self.page_spin.setMaximum(doc.page_count)
        self.page_spin.blockSignals(False)
        self.page_total.setText(f" / {doc.page_count} ")
        self.render()
        self._refresh_undo_actions()

    def render(self):
        if not self.doc:
            return
        page = self.doc[self.page_no]
        dpr = self.devicePixelRatioF()
        scale = self.zoom * SCREEN_DPI / 72
        pix = page.get_pixmap(matrix=pymupdf.Matrix(scale * dpr, scale * dpr), alpha=False)
        img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888).copy()
        qpix = QPixmap.fromImage(img)
        qpix.setDevicePixelRatio(dpr)
        self.canvas.highlights = [r for n, r in self.search_hits if n == self.page_no]
        self.canvas.set_page(qpix, scale, text_lines(page))
        self.page_spin.blockSignals(True)
        self.page_spin.setValue(self.page_no + 1)
        self.page_spin.blockSignals(False)
        self.zoom_label.setText(f"{round(self.zoom * 100)}%")
        self._update_title()

    def goto(self, n: int):
        if self.doc and 0 <= n < self.doc.page_count and n != self.page_no:
            self.page_no = n
            self.render()

    def set_zoom(self, z: float):
        self.zoom = min(max(z, 0.25), 6.0)
        self.render()

    # ---- editing --------------------------------------------------------
    def _snapshot(self):
        self.undo_stack.append((self.doc.tobytes(), self.page_no))
        del self.undo_stack[:-UNDO_LIMIT]
        self.redo_stack.clear()

    def _changed(self):
        self.dirty = True
        self.search_hits = []
        self.render()
        self._refresh_undo_actions()

    def add_text_at(self, rect: pymupdf.Rect):
        if not self.doc:
            return
        dlg = TextDialog(self, self.fonts, title="Add text", font=self.cur_font,
                         size=self.cur_size, color=self.cur_color)
        if dlg.exec() != QDialog.Accepted:
            return
        v = dlg.values()
        if not v["text"].strip():
            return
        self._remember_style(v)
        self._snapshot()
        try:
            insert_khmer_text(self.doc[self.page_no], rect, v["text"], font=load_font(self.fonts.paths[v["font"]]),
                              size=v["size"], color=v["color"], align=v["align"])
        except Exception as exc:
            self._restore(self.undo_stack.pop())
            QMessageBox.critical(self, APP_NAME, f"Could not add text:\n{exc}")
            return
        self._changed()

    def edit_line(self, line: TextLine):
        c = QColor.fromRgbF(*line.color)
        dlg = TextDialog(self, self.fonts, title="Edit text", text="" if line.garbled else line.text,
                         font=line.font if line.font in self.fonts.paths else self.cur_font,
                         size=line.size, color=c, show_align=False, original=line)
        if dlg.exec() != QDialog.Accepted:
            return
        v = dlg.values()
        self._remember_style(v, size=False)
        self._snapshot()
        try:
            replace_text(self.doc[self.page_no], line, v["text"], font=load_font(self.fonts.paths[v["font"]]),
                         size=v["size"], color=v["color"])
        except Exception as exc:
            self._restore(self.undo_stack.pop())
            QMessageBox.critical(self, APP_NAME, f"Could not edit text:\n{exc}")
            return
        self._changed()

    def _remember_style(self, v, size=True):
        self.cur_font = v["font"]
        self.font_box.setCurrentText(v["font"])
        if size:
            self.size_spin.setValue(v["size"])
        self.cur_color = QColor.fromRgbF(*v["color"])
        self._paint_color_btn()

    def _restore(self, state):
        data, page_no = state
        self.doc = pymupdf.open(stream=data, filetype="pdf")
        self.page_no = min(page_no, self.doc.page_count - 1)

    def undo(self):
        if self.undo_stack:
            self.redo_stack.append((self.doc.tobytes(), self.page_no))
            self._restore(self.undo_stack.pop())
            self._changed()

    def redo(self):
        if self.redo_stack:
            self.undo_stack.append((self.doc.tobytes(), self.page_no))
            self._restore(self.redo_stack.pop())
            self._changed()

    def _refresh_undo_actions(self):
        self.undo_act.setEnabled(bool(self.undo_stack))
        self.redo_act.setEnabled(bool(self.redo_stack))

    # ---- search ---------------------------------------------------------
    def find_next(self):
        q = self.find_box.text().strip()
        if not self.doc or not q:
            self.search_hits = []
            self.render()
            return
        self.search_hits = [(i, r) for i in range(self.doc.page_count) for r in search(self.doc[i], q)]
        if not self.search_hits:
            self.statusBar().showMessage(f"“{q}” not found")
            self.render()
            return
        pages = sorted({n for n, _ in self.search_hits})
        nxt = next((n for n in pages if n > self.page_no), pages[0]) if self.page_no in pages and len(pages) > 1 \
            else (self.page_no if self.page_no in pages else pages[0])
        self.page_no = nxt
        self.statusBar().showMessage(f"{len(self.search_hits)} match(es) on {len(pages)} page(s)")
        self.render()

    # ---- saving ---------------------------------------------------------
    def save(self):
        if not self.doc:
            return False
        if not self.path:
            return self.save_as()
        return self._write(self.path)

    def save_as(self):
        if not self.doc:
            return False
        start = self.path or "document.pdf"
        path, _ = QFileDialog.getSaveFileName(self, "Save PDF", start, "PDF files (*.pdf)")
        if not path:
            return False
        if not path.lower().endswith(".pdf"):
            path += ".pdf"
        return self._write(path)

    def _write(self, path: str) -> bool:
        try:
            fd, tmp = tempfile.mkstemp(suffix=".pdf", dir=str(Path(path).parent))
            os.close(fd)
            self.doc.save(tmp, garbage=3, deflate=True)
            os.replace(tmp, path)
        except Exception as exc:
            QMessageBox.critical(self, APP_NAME, f"Could not save:\n{exc}")
            return False
        self.path, self.dirty = path, False
        self._update_title()
        self.statusBar().showMessage(f"Saved {path}", 4000)
        return True

    def _confirm_discard(self) -> bool:
        if not self.dirty:
            return True
        r = QMessageBox.question(self, APP_NAME, "Save changes to this document?",
                                 QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if r == QMessageBox.Save:
            return self.save()
        return r == QMessageBox.Discard

    def _update_title(self):
        name = Path(self.path).name if self.path else "No document"
        self.setWindowTitle(f"{'• ' if self.dirty else ''}{name} — {APP_NAME} {__version__}")

    # ---- window events --------------------------------------------------
    def closeEvent(self, e):
        e.accept() if self._confirm_discard() else e.ignore()

    def dragEnterEvent(self, e):
        if any(u.toLocalFile().lower().endswith(".pdf") for u in e.mimeData().urls()):
            e.acceptProposedAction()

    def dropEvent(self, e):
        for u in e.mimeData().urls():
            if u.toLocalFile().lower().endswith(".pdf") and self._confirm_discard():
                self.open_file(u.toLocalFile())
                break


def main():
    QApplication.setApplicationName(APP_NAME)
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    if len(sys.argv) > 1 and Path(sys.argv[1]).is_file():
        win.open_file(sys.argv[1])
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
