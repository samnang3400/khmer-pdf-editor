"""KhmerPDF Editor - desktop GUI (PySide6)."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pymupdf
from PySide6.QtCore import QEvent, QPoint, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (QAction, QActionGroup, QColor, QFont, QFontDatabase, QFontMetricsF,
                           QIcon, QImage, QKeySequence, QPainter, QPen, QPixmap)
from PySide6.QtWidgets import (QApplication, QColorDialog, QComboBox, QDoubleSpinBox,
                               QFileDialog, QLabel, QLineEdit, QMainWindow, QMessageBox,
                               QPlainTextEdit, QPushButton, QScrollArea, QSpinBox, QToolBar,
                               QWidget)

from . import __version__
from .engine import (TextLine, bundled_fonts, insert_khmer_text, load_font, match_font,
                     replace_text, search, system_khmer_fonts, text_lines)

APP_NAME = "KhmerPDF Editor"
SCREEN_DPI = 96
UNDO_LIMIT = 30
ZWSP = "\u200b"


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
            # skip installed copies of fonts we already ship (e.g. Khmer OS)
            if name not in self.paths and not match_font(self.paths, Path(path).name):
                self.add(name, path)

    def add(self, name: str, path: str) -> str:
        fid = QFontDatabase.addApplicationFont(path)
        fams = QFontDatabase.applicationFontFamilies(fid) if fid >= 0 else []
        self.paths[name] = path
        self.families[name] = fams[0] if fams else "Sans Serif"
        return name

    def qfont(self, name: str, px: float) -> QFont:
        f = QFont(self.families.get(name, "Sans Serif"))
        f.setPixelSize(max(round(px), 6))
        return f


# --------------------------------------------------------------------------
# In-place text editor (sits on the page, over the line being edited)
# --------------------------------------------------------------------------
class InlineEditor(QPlainTextEdit):
    """Enter = save, Shift+Enter = new line, Esc = cancel."""
    commit = Signal()
    cancel = Signal()

    def __init__(self, parent: QWidget, wrap_width: float | None):
        super().__init__(parent)
        self.wrap_width = wrap_width  # px; None = single line that grows to the right
        self.min_width = 60.0         # px; at least the width of the line being covered
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setLineWrapMode(QPlainTextEdit.WidgetWidth if wrap_width else QPlainTextEdit.NoWrap)
        self.document().setDocumentMargin(1)
        self.textChanged.connect(self.fit)

    def event(self, e):
        # keep Esc/Enter for us instead of the window's shortcuts
        if e.type() == QEvent.ShortcutOverride and e.key() in (Qt.Key_Escape, Qt.Key_Return, Qt.Key_Enter):
            e.accept()
            return True
        return super().event(e)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.cancel.emit()
            return
        if e.key() in (Qt.Key_Return, Qt.Key_Enter) and not e.modifiers() & Qt.ShiftModifier:
            self.commit.emit()
            return
        super().keyPressEvent(e)

    def fit(self):
        """Grow with the text so the editor never shows scrollbars."""
        fm = QFontMetricsF(self.font())
        pad = 10
        if self.wrap_width:
            # QPlainTextEdit's document height is measured in (wrapped) lines
            n = max(1, int(self.document().size().height()))
            w, h = self.wrap_width + pad, n * fm.lineSpacing() + pad
        else:
            lines = self.toPlainText().split("\n")
            w = max(fm.horizontalAdvance(t) for t in lines) + fm.averageCharWidth() * 3 + pad
            h = len(lines) * fm.lineSpacing() + pad
        self.resize(int(max(w, self.min_width)), int(h))


# --------------------------------------------------------------------------
# Page canvas
# --------------------------------------------------------------------------
class PageCanvas(QWidget):
    rect_drawn = Signal(object)    # pymupdf.Rect in page coordinates
    line_clicked = Signal(object)  # TextLine
    pressed = Signal()             # any click on the page (saves an open in-place editor)

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

    def hit(self, pos: QPoint) -> TextLine | None:
        pt = self._to_page(pos)
        return next((ln for ln in self.lines if pt in ln.rect), None)

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        self.pressed.emit()  # may save an open editor and re-render the page
        pos = e.position().toPoint()
        if self.mode == "add":
            self._drag_start = pos
            self._drag_rect = QRect(self._drag_start, QSize(0, 0))
        elif self.mode == "edit":
            line = self.hit(pos)  # fresh hit-test: the page may have just changed
            if line:
                self.line_clicked.emit(line)

    def mouseMoveEvent(self, e):
        pos = e.position().toPoint()
        if self.mode == "add" and self._drag_start is not None:
            self._drag_rect = QRect(self._drag_start, pos).normalized()
            self.update()
        elif self.mode == "edit":
            hit = self.hit(pos)
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
        self.inline: dict | None = None  # the open in-place editor, if any

        self.canvas = PageCanvas()
        self.canvas.rect_drawn.connect(self.add_text_at)
        self.canvas.line_clicked.connect(self.edit_line)
        self.canvas.pressed.connect(lambda: self._close_inline(save=True))
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
        self.size_spin.valueChanged.connect(self._size_changed)
        tb.addWidget(self.size_spin)
        self.color_btn = QPushButton()
        self.color_btn.setFixedWidth(28)
        self.color_btn.setToolTip("Text color")
        self.color_btn.clicked.connect(self._pick_color)
        self._paint_color_btn()
        tb.addWidget(self.color_btn)
        self.insert_zwsp_act = act("Word break", self.insert_zwsp, "Ctrl+Shift+Space",
                                   "Insert an invisible Khmer word break (ZWSP) where a line may wrap")
        self.insert_zwsp_act.setEnabled(False)
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
            self._style_inline()
        if self.inline:
            self.inline["editor"].setFocus()

    def _size_changed(self, v: float):
        self.cur_size = v
        self._style_inline()

    def _font_changed(self, name: str):
        if name != "Add font file…":
            self.cur_font = name
            self._style_inline()
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
        self._style_inline()

    def set_mode(self, mode: str):
        self._close_inline(save=True)
        self.mode_actions[mode].setChecked(True)
        self.canvas.mode = mode
        self.canvas.hover = None
        self.canvas.setCursor(Qt.CrossCursor if mode == "add" else Qt.ArrowCursor)
        hints = {"view": "", "add": "Drag a box (or click) and type. Enter = save, Shift+Enter = new line.",
                 "edit": "Click a line of text to edit it in place."}
        self.statusBar().showMessage(hints[mode])
        self.canvas.update()

    # ---- document -------------------------------------------------------
    def open_dialog(self):
        self._close_inline(save=True)
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
        self._close_inline(save=True)
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

    # ---- in-place editing -------------------------------------------------
    def add_text_at(self, rect: pymupdf.Rect):
        if self.doc:
            self._open_inline("add", None, rect, "", self.cur_font, self.cur_size, self.cur_color, wrap=True)

    def edit_line(self, line: TextLine):
        font = match_font(self.fonts.paths, line.font)
        if line.garbled:
            self.statusBar().showMessage("This line's text can't be read from the PDF (its text layer is broken "
                                         "and no matching font was found). Type the replacement text.")
        elif line.approx:
            self.statusBar().showMessage("⚠ This PDF's font isn't available, so the text was rebuilt with Khmer "
                                         "spelling rules and may contain mistakes. Please check it before saving.")
        elif line.recovered:
            self.statusBar().showMessage(f"Text recovered from the glyphs ({font or line.font}). "
                                         "Enter = save, Shift+Enter = new line, Esc = cancel.")
        else:
            self.statusBar().showMessage("Enter = save, Shift+Enter = new line, Esc = cancel.")
        if not font:
            font = self.cur_font
            if line.font and not (line.approx or line.garbled):
                self.statusBar().showMessage(f"The PDF's font “{line.font}” isn't available; using {font}. "
                                             "Pick another font in the toolbar if you like.")
        self._open_inline("edit", line, line.rect, "" if line.garbled else line.text,
                          font, line.size, QColor.fromRgbF(*line.color), wrap=False)

    def _open_inline(self, kind, line, rect, text, font, size, color, wrap):
        self._close_inline(save=True)
        if kind == "edit":
            line = next((ln for ln in self.canvas.lines if ln.rect == line.rect), line)
        # the toolbar shows (and controls) the style of the text being edited
        for w in (self.font_box, self.size_spin):
            w.blockSignals(True)
        self.font_box.setCurrentText(font)
        self.size_spin.setValue(size)
        for w in (self.font_box, self.size_spin):
            w.blockSignals(False)
        self.cur_font, self.cur_size, self.cur_color = font, size, QColor(color)
        self._paint_color_btn()

        s = self.canvas.scale
        ed = InlineEditor(self.canvas, rect.width * s if wrap else None)
        ed.min_width = rect.width * s + 12  # always cover the original line completely
        ed.setPlaceholderText("Type the replacement text…" if line is not None and line.garbled else "Type here…")
        ed.setPlainText(text)
        ed.commit.connect(lambda: self._close_inline(save=True))
        ed.cancel.connect(lambda: self._close_inline(save=False))
        self.inline = dict(editor=ed, kind=kind, line=line, rect=pymupdf.Rect(rect),
                           start=(text, font, size, QColor(color).name()))
        self._style_inline()
        top = (line.baseline - size * 1.0) * s if line is not None else rect.y0 * s
        ed.move(int(rect.x0 * s) - 5, int(min(top, rect.y0 * s)) - 5)
        ed.show()
        ed.setFocus()
        ed.moveCursor(ed.textCursor().MoveOperation.End)
        self.insert_zwsp_act.setEnabled(True)

    def _style_inline(self):
        if not self.inline:
            return
        ed = self.inline["editor"]
        ed.setFont(self.fonts.qfont(self.cur_font, self.cur_size * self.canvas.scale))
        line = self.inline.get("line")
        border = "#d98200" if line is not None and (line.approx or line.garbled) else "#0078d7"
        ed.setStyleSheet(f"QPlainTextEdit {{ color: {self.cur_color.name()}; background: white; "
                         f"border: 2px solid {border}; border-radius: 3px; }}")
        ed.fit()

    def _close_inline(self, save: bool) -> None:
        """Save (or discard) the open in-place editor, if any."""
        if not getattr(self, "inline", None):
            return
        st, self.inline = self.inline, None
        ed = st["editor"]
        text = ed.toPlainText()
        ed.hide()
        ed.deleteLater()
        self.insert_zwsp_act.setEnabled(False)
        self.canvas.setFocus()
        if not save or not self.doc:
            return
        unchanged = (text, self.cur_font, self.cur_size, self.cur_color.name()) == st["start"]
        if st["kind"] == "add" and not text.strip():
            return
        if st["kind"] == "edit" and (unchanged or (st["line"].garbled and not text.strip())):
            return
        c = self.cur_color
        font = load_font(self.fonts.paths[self.cur_font])
        self._snapshot()
        try:
            page = self.doc[self.page_no]
            if st["kind"] == "add":
                insert_khmer_text(page, st["rect"], text, font=font, size=self.cur_size,
                                  color=(c.redF(), c.greenF(), c.blueF()))
            else:
                replace_text(page, st["line"], text, font=font, size=self.cur_size,
                             color=(c.redF(), c.greenF(), c.blueF()),
                             fit=st["start"][1:3] == (self.cur_font, self.cur_size))
        except Exception as exc:
            self._restore(self.undo_stack.pop())
            QMessageBox.critical(self, APP_NAME, f"Could not save the text:\n{exc}")
            return
        self._changed()

    def insert_zwsp(self):
        if self.inline:
            self.inline["editor"].insertPlainText(ZWSP)
            self.inline["editor"].setFocus()

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
        self._close_inline(save=True)
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
        self._close_inline(save=True)
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
