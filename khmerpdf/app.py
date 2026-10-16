"""KhmerPDF Editor - desktop GUI (PySide6)."""
from __future__ import annotations

import os
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pymupdf
from PySide6.QtCore import QEvent, QEventLoop, QPoint, QPointF, QRect, QRectF, QSettings, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (QAction, QActionGroup, QColor, QFont, QFontDatabase, QFontMetricsF,
                           QGuiApplication, QIcon, QImage, QKeySequence, QPainter, QPen, QPixmap)
from PySide6.QtWidgets import (QApplication, QColorDialog, QComboBox, QDoubleSpinBox, QFileDialog,
                               QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QProgressDialog,
                               QPushButton, QScrollArea, QSizePolicy, QSpinBox, QStackedWidget,
                               QStyleFactory, QToolBar, QToolButton, QVBoxLayout, QWidget)

from . import __version__, ocr
from .recover import page_rules
from . import theme as T
from .engine import (Paragraph, Picture, TextLine, add_ocr_layer, bundled_fonts, convert_legacy,
                     delete_paragraph, delete_picture, edit_text, insert_khmer_text, load_font, match_font,
                     move_picture, paragraphs, pictures, replace_picture, search, system_khmer_fonts,
                     text_lines, write_paragraph)

APP_NAME = "KhmerPDF Editor"
SCREEN_DPI = 96
UNDO_LIMIT = 30
ZWSP = "​"
RECENT_MAX = 8
THUMB_W = 112


def resource_path(*parts: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base.joinpath(*parts)


IMAGE_FILES = "Images (*.png *.jpg *.jpeg *.bmp *.gif *.tif *.tiff)"


def block_rect(para) -> pymupdf.Rect:
    """The paragraph's box: its wrap width, and the height of its lines.
    (A picture's box is just its rectangle.)"""
    if isinstance(para, Picture):
        return pymupdf.Rect(para.rect)
    r = para.rect
    return pymupdf.Rect(min(para.x, r.x0), r.y0, max(para.x + para.width, r.x1), r.y1)


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
# In-place text editor (sits on the page, over the text being edited)
# --------------------------------------------------------------------------
class InlineEditor(QPlainTextEdit):
    """Enter = save, Shift+Enter = new line, Esc = cancel."""
    commit = Signal()
    cancel = Signal()

    def __init__(self, parent: QWidget, wrap_width: float | None):
        super().__init__(parent)
        self.wrap_width = wrap_width  # px; None = single line that grows to the right
        self.min_width = 60.0         # px; at least the width of the text being covered
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setLineWrapMode(QPlainTextEdit.WidgetWidth if wrap_width else QPlainTextEdit.NoWrap)
        self.document().setDocumentMargin(1)
        self.textChanged.connect(self.fit)

    def event(self, e):
        # keep Esc/Enter/Delete for us instead of the window's shortcuts
        if e.type() == QEvent.ShortcutOverride and e.key() in (Qt.Key_Escape, Qt.Key_Return, Qt.Key_Enter,
                                                                 Qt.Key_Delete, Qt.Key_Backspace):
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
            # QPlainTextEdit's document height is measured in (wrapped) lines,
            # laid out at the current width: set the width first
            w = max(self.wrap_width + pad, self.min_width)
            if self.width() != int(w):
                self.resize(int(w), self.height())
            n = max(1, int(self.document().size().height()))
            h = n * fm.lineSpacing() + pad
        else:
            lines = self.toPlainText().split("\n")
            w = max(fm.horizontalAdvance(t) for t in lines) + fm.averageCharWidth() * 3 + pad
            h = len(lines) * fm.lineSpacing() + pad
        self.resize(int(max(w, self.min_width)), int(h))


class EditorGrip(QLabel):
    """A tab on the in-place editor: drag it to move the text elsewhere."""

    def __init__(self, parent: QWidget, editor: InlineEditor):
        super().__init__("⠿  Move", parent)
        self.editor = editor
        self.setCursor(Qt.SizeAllCursor)
        self.setToolTip("Drag to move the text")
        self._last: QPoint | None = None

    def place(self):
        self.adjustSize()
        y = self.editor.y() - self.height() + 2
        if y < 0:  # no room above: hang it below the editor
            y = self.editor.y() + self.editor.height() - 2
        self.move(self.editor.x() + 6, y)
        self.raise_()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._last = e.globalPosition().toPoint()

    def mouseMoveEvent(self, e):
        if self._last is not None:
            p = e.globalPosition().toPoint()
            self.editor.move(self.editor.pos() + (p - self._last))
            self._last = p
            self.place()

    def mouseReleaseEvent(self, e):
        self._last = None
        self.editor.setFocus()


# --------------------------------------------------------------------------
# Page canvas
# --------------------------------------------------------------------------
_HANDLE_CURSORS = {"l": Qt.SizeHorCursor, "r": Qt.SizeHorCursor, "tl": Qt.SizeFDiagCursor,
                   "br": Qt.SizeFDiagCursor, "tr": Qt.SizeBDiagCursor, "bl": Qt.SizeBDiagCursor}


class PageCanvas(QWidget):
    rect_drawn = Signal(object)          # pymupdf.Rect in page coordinates
    para_clicked = Signal(object)        # Paragraph (Edit tool)
    pressed = Signal()                   # any click on the page (saves an open in-place editor)
    selection_changed = Signal(object)   # Paragraph | None (Select tool)
    move_requested = Signal(object, float, float)       # paragraph, dx, dy (page units)
    resize_requested = Signal(object, object, bool)     # paragraph, new box (page), corner (= scale)
    edit_requested = Signal(object)      # double click on a paragraph

    def __init__(self, theme: T.Theme):
        super().__init__()
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.ClickFocus)
        self.theme = theme
        self.pixmap: QPixmap | None = None
        self.scale = 1.0
        self.mode = "view"
        self.lines: list[TextLine] = []
        self.paras: list[Paragraph] = []
        self.pics: list[Picture] = []
        self.hover: Paragraph | Picture | None = None
        self.selected: Paragraph | Picture | None = None
        self.highlights: list[pymupdf.Rect] = []
        self._drag: dict | None = None

    def set_page(self, pixmap: QPixmap, scale: float, lines: list[TextLine], paras: list[Paragraph],
                 pics: list[Picture] = ()):
        self.pixmap, self.scale, self.lines, self.paras, self.pics = pixmap, scale, lines, paras, list(pics)
        self.hover = None
        self.selected = None
        self._drag = None
        self.setFixedSize(pixmap.deviceIndependentSize().toSize())
        self.update()

    def _to_page(self, p) -> pymupdf.Point:
        return pymupdf.Point(p.x() / self.scale, p.y() / self.scale)

    def _to_screen(self, r: pymupdf.Rect) -> QRectF:
        s = self.scale
        return QRectF(r.x0 * s, r.y0 * s, r.width * s, r.height * s)

    def _box(self, para: Paragraph) -> QRectF:
        return self._to_screen(block_rect(para)).adjusted(-3, -3, 3, 3)

    @staticmethod
    def _handles(r: QRectF) -> dict[str, QRectF]:
        pts = {"tl": r.topLeft(), "tr": r.topRight(), "bl": r.bottomLeft(), "br": r.bottomRight(),
               "l": QPointF(r.left(), r.center().y()), "r": QPointF(r.right(), r.center().y())}
        return {k: QRectF(p.x() - 4, p.y() - 4, 8, 8) for k, p in pts.items()}

    def hit(self, pos) -> Paragraph | Picture | None:
        """Text under the mouse - or, with the Select tool, a picture (the one
        drawn last, i.e. on top)."""
        pt = self._to_page(pos)
        para = next((p for p in self.paras if pt in block_rect(p)), None)
        if para is None and self.mode == "select":
            para = next((p for p in reversed(self.pics) if pt in p.rect), None)
        return para

    def _handle_at(self, pos: QPointF) -> str | None:
        if not self.selected:
            return None
        return next((k for k, h in self._handles(self._box(self.selected)).items()
                     if h.adjusted(-4, -4, 4, 4).contains(pos)), None)

    # ---- painting -------------------------------------------------------
    def paintEvent(self, _):
        if not self.pixmap:
            return
        t = self.theme
        accent, warn = QColor(t.accent), QColor(t.warning)
        p = QPainter(self)
        p.drawPixmap(0, 0, self.pixmap)
        p.setRenderHint(QPainter.Antialiasing)
        for r in self.highlights:
            p.fillRect(self._to_screen(r), QColor(t.highlight))

        def tint(c: QColor, a: int) -> QColor:
            c = QColor(c)
            c.setAlpha(a)
            return c

        if self.mode in ("edit", "select"):
            for para in self.paras:
                col = warn if para.needs_check else accent
                p.setPen(QPen(tint(col, 150 if para.needs_check else 70), 1, Qt.DashLine))
                p.drawRoundedRect(self._box(para), 3, 3)
            if self.mode == "select":
                p.setPen(QPen(tint(QColor(t.muted), 110), 1, Qt.DotLine))
                for pic in self.pics:
                    p.drawRect(self._box(pic))
            if self.hover and self.hover is not self.selected:
                col = warn if getattr(self.hover, "needs_check", False) else accent
                p.setPen(QPen(col, 1.5))
                p.setBrush(tint(col, 28))
                p.drawRoundedRect(self._box(self.hover), 3, 3)
                p.setBrush(Qt.NoBrush)
        if self.mode == "select" and self.selected:
            box = self._box(self.selected)
            d = self._drag
            if d and d.get("moved"):
                # live preview: the selected text follows the mouse
                src = d["orig"]
                dpr = self.pixmap.devicePixelRatio()
                piece = self.pixmap.copy(QRect(int(src.x() * dpr), int(src.y() * dpr),
                                               int(src.width() * dpr), int(src.height() * dpr)))
                p.fillRect(src, tint(QColor(t.canvas), 140))
                if d["kind"] in ("move", "tl", "tr", "bl", "br"):
                    p.setOpacity(0.85)
                    p.drawPixmap(d["rect"], piece, QRectF(piece.rect()))
                    p.setOpacity(1.0)
                box = d["rect"]
            p.setPen(QPen(accent, 1.5))
            p.drawRoundedRect(box, 3, 3)
            p.setBrush(QColor("white"))  # handles sit on the (white) page in both themes
            for h in self._handles(box).values():
                p.drawRoundedRect(h, 2, 2)
            p.setBrush(Qt.NoBrush)
        if self.mode == "add" and self._drag:
            r = self._drag["rect"]
            p.setPen(QPen(accent, 1.5, Qt.DashLine))
            p.fillRect(r, tint(accent, 30))
            p.drawRect(r)

    # ---- mouse ----------------------------------------------------------
    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        self.pressed.emit()  # may save an open editor and re-render the page
        pos = e.position()
        if self.mode == "add":
            self._drag = dict(kind="add", start=pos, rect=QRectF(pos, QSize(0, 0)))
        elif self.mode == "edit":
            para = self.hit(pos)  # fresh hit-test: the page may have just changed
            if para:
                self.para_clicked.emit(para)
        elif self.mode == "select":
            handle = self._handle_at(pos)
            if handle:
                box = self._box(self.selected)
                self._drag = dict(kind=handle, start=pos, orig=box, rect=QRectF(box))
                return
            para = self.hit(pos)
            if para is not self.selected:
                self.selected = para
                self.selection_changed.emit(para)
            if para:
                box = self._box(para)
                self._drag = dict(kind="move", start=pos, orig=box, rect=QRectF(box))
            self.update()

    def mouseDoubleClickEvent(self, e):
        if self.mode == "select" and e.button() == Qt.LeftButton:
            para = self.hit(e.position())
            if para:
                self._drag = None
                self.edit_requested.emit(para)

    def _drag_rect(self, d: dict, pos: QPointF) -> QRectF:
        o, dx, dy = d["orig"], pos.x() - d["start"].x(), pos.y() - d["start"].y()
        k = d["kind"]
        if k == "move":
            return o.translated(dx, dy)
        if k == "l":
            return QRectF(QPointF(min(o.left() + dx, o.right() - 24), o.top()), o.bottomRight())
        if k == "r":
            return QRectF(o.topLeft(), QPointF(max(o.right() + dx, o.left() + 24), o.bottom()))
        # corners scale the whole paragraph about the opposite corner
        w = max(o.width() + (dx if k in ("tr", "br") else -dx), 24)
        h = o.height() * w / o.width()
        x = o.left() if k in ("tr", "br") else o.right() - w
        y = o.top() if k in ("bl", "br") else o.bottom() - h
        return QRectF(x, y, w, h)

    def mouseMoveEvent(self, e):
        pos = e.position()
        d = self._drag
        if d and self.mode == "add":
            d["rect"] = QRectF(d["start"], pos).normalized()
            self.update()
            return
        if d and self.mode == "select":
            if not d.get("moved") and (pos - d["start"]).manhattanLength() < 4:
                return
            d["moved"] = True
            d["rect"] = self._drag_rect(d, pos)
            self.update()
            return
        if self.mode in ("edit", "select"):
            hit = self.hit(pos)
            handle = self._handle_at(pos) if self.mode == "select" else None
            if handle:
                self.setCursor(_HANDLE_CURSORS[handle])
            elif self.mode == "select" and hit is not None and hit is self.selected:
                self.setCursor(Qt.SizeAllCursor)
            else:
                self.setCursor(Qt.PointingHandCursor if hit else Qt.ArrowCursor)
            if hit is not self.hover:
                self.hover = hit
                self.update()

    def mouseReleaseEvent(self, e):
        d, self._drag = self._drag, None
        if not d:
            return
        self.update()
        if self.mode == "add":
            r = d["rect"]
            x0, y0 = r.left() / self.scale, r.top() / self.scale
            if r.width() < 15 or r.height() < 10:  # a click: default box
                rect = pymupdf.Rect(x0, y0, x0 + 300, y0 + 60)
            else:
                rect = pymupdf.Rect(x0, y0, r.right() / self.scale, r.bottom() / self.scale)
            self.rect_drawn.emit(rect)
            return
        if self.mode != "select" or not d.get("moved") or not self.selected:
            return
        s, r = self.scale, d["rect"]
        if d["kind"] == "move":
            self.move_requested.emit(self.selected, (r.left() - d["orig"].left()) / s,
                                     (r.top() - d["orig"].top()) / s)
        else:
            r = r.adjusted(3, 3, -3, -3)  # back from the drawn frame to the paragraph's box
            self.resize_requested.emit(self.selected, pymupdf.Rect(r.left() / s, r.top() / s,
                                                                   r.right() / s, r.bottom() / s),
                                       d["kind"] not in ("l", "r"))

    def leaveEvent(self, _):
        if self.hover:
            self.hover = None
            self.update()


class PageFrame(QWidget):
    """Holds the page canvas and paints a soft shadow around it."""

    def __init__(self, canvas: PageCanvas):
        super().__init__()
        self.canvas = canvas
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 32)
        lay.addWidget(canvas, 0, Qt.AlignHCenter | Qt.AlignTop)

    def paintEvent(self, _):
        g = self.canvas.geometry()
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(self.canvas.theme.canvas))
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        dark = self.canvas.theme.dark
        for i in range(8, 0, -1):
            p.setBrush(QColor(0, 0, 0, (14 if dark else 7) + (8 - i)))
            p.drawRoundedRect(QRectF(g).adjusted(-i, -i + 2, i, i + 3), i, i)


# --------------------------------------------------------------------------
# Welcome screen and info banner
# --------------------------------------------------------------------------
class Welcome(QWidget):
    open_clicked = Signal()
    recent_clicked = Signal(str)

    def __init__(self):
        super().__init__()
        outer = QVBoxLayout(self)
        outer.addStretch(2)
        card = QFrame(objectName="card")
        card.setMaximumWidth(520)
        card.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(40, 36, 40, 32)
        lay.setSpacing(10)
        self.logo = QLabel(alignment=Qt.AlignHCenter)
        lay.addWidget(self.logo)
        title = QLabel(APP_NAME, objectName="title", alignment=Qt.AlignHCenter)
        lay.addWidget(title)
        sub = QLabel("Edit Khmer PDFs with correct shaping. Text stays searchable and copyable.\n"
                     "Fixes broken Word PDFs, converts Limon text and reads scans.",
                     objectName="muted", alignment=Qt.AlignHCenter, wordWrap=True)
        lay.addWidget(sub)
        lay.addSpacing(10)
        btn = QPushButton("Open PDF…", objectName="primary")
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(self.open_clicked)
        lay.addWidget(btn, 0, Qt.AlignHCenter)
        lay.addWidget(QLabel("or drag a PDF file here  ·  Ctrl+O", objectName="hint", alignment=Qt.AlignHCenter))
        self.recent_title = QLabel("Recent", objectName="muted")
        lay.addSpacing(12)
        lay.addWidget(self.recent_title)
        self.recent_box = QVBoxLayout()
        self.recent_box.setSpacing(0)
        lay.addLayout(self.recent_box)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(card, 4)
        row.addStretch(1)
        outer.addLayout(row)
        outer.addStretch(3)

    def set_recent(self, paths: list[str]):
        while self.recent_box.count():
            w = self.recent_box.takeAt(0).widget()
            if w:
                w.deleteLater()
        names = [Path(p).name for p in paths]
        for p in paths:
            name = Path(p).name
            if names.count(name) > 1:  # same name in different folders: say which
                name += f"   —   {Path(p).parent.name or Path(p).parent}"
            b = QPushButton(name, objectName="link")
            b.setToolTip(p)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, p=p: self.recent_clicked.emit(p))
            self.recent_box.addWidget(b)
        self.recent_title.setVisible(bool(paths))

    def set_theme(self, t: T.Theme):
        self.logo.setPixmap(T.icon_pixmap("document", t.accent, 56, self.devicePixelRatioF() or 2))


class Banner(QFrame):
    """A one-line message above the page, with an optional action."""

    def __init__(self):
        super().__init__(objectName="banner")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 6, 8, 6)
        self.icon = QLabel()
        self.text = QLabel(wordWrap=True)
        self.text.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.action = QPushButton()
        self.action.setCursor(Qt.PointingHandCursor)
        self.close_btn = QToolButton(autoRaise=True)
        self.close_btn.setToolTip("Hide")
        lay.addWidget(self.icon)
        lay.addSpacing(6)
        lay.addWidget(self.text)
        lay.addWidget(self.action)
        lay.addWidget(self.close_btn)
        self.kind = ""
        self._slot = None
        self.action.clicked.connect(lambda: self._slot and self._slot())
        self.hide()

    def show_message(self, kind: str, text: str, action: str | None, slot, warning: bool, t: T.Theme):
        self.kind, self._slot = kind, slot
        self.setProperty("kind", "warning" if warning else "info")
        self.style().unpolish(self)
        self.style().polish(self)
        name = {"scan": "ocr", "limon": "convert"}.get(kind, "edit")
        self.icon.setPixmap(T.icon_pixmap(name, t.warning if warning else t.accent))
        self.text.setText(text)
        self.action.setVisible(bool(action))
        self.action.setText(action or "")
        self.close_btn.setIcon(T.icon("close", t))
        self.show()


# --------------------------------------------------------------------------
# Main window
# --------------------------------------------------------------------------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.theme = T.system_theme()
        self.fonts = Fonts()
        self.settings = QSettings("KhmerPDF", APP_NAME)
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
        self._icons: list[tuple[object, str]] = []  # (action/button, icon name) to re-tint on theme change
        self._select_key: tuple[float, float] | None = None
        self._dismissed: set[str] = set()
        self._thumb_queue: list[int] = []

        self.canvas = PageCanvas(self.theme)
        self.canvas.rect_drawn.connect(self.add_text_at)
        self.canvas.para_clicked.connect(self.edit_paragraph)
        self.canvas.pressed.connect(lambda: self._close_inline(save=True))
        self.canvas.selection_changed.connect(self._selection_changed)
        self.canvas.move_requested.connect(self.move_item)
        self.canvas.resize_requested.connect(self.resize_item)
        self.canvas.edit_requested.connect(self._edit_from_select)

        self.scroll = QScrollArea(objectName="pages")
        self.scroll.setWidget(PageFrame(self.canvas))
        self.scroll.setWidgetResizable(True)
        self.scroll.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self.scroll.viewport().installEventFilter(self)

        self.thumbs = QListWidget(objectName="thumbs")
        self.thumbs.setViewMode(QListWidget.IconMode)
        self.thumbs.setFlow(QListWidget.TopToBottom)
        self.thumbs.setWrapping(False)
        self.thumbs.setMovement(QListWidget.Static)
        self.thumbs.setIconSize(QSize(THUMB_W, int(THUMB_W * 1.42)))
        self.thumbs.setFixedWidth(THUMB_W + 44)
        self.thumbs.setSpacing(2)
        self.thumbs.currentRowChanged.connect(lambda r: r >= 0 and self.goto(r))
        self._thumb_timer = QTimer(self, interval=5, timeout=self._render_thumbs)

        self.banner = Banner()
        self.banner.close_btn.clicked.connect(self._dismiss_banner)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)
        rl.addWidget(self.banner)
        rl.addWidget(self.scroll)
        doc_view = QWidget()
        dl = QHBoxLayout(doc_view)
        dl.setContentsMargins(0, 0, 0, 0)
        dl.setSpacing(0)
        dl.addWidget(self.thumbs)
        dl.addWidget(right)

        self.welcome = Welcome()
        self.welcome.open_clicked.connect(self.open_dialog)
        self.welcome.recent_clicked.connect(self._open_recent)
        self.stack = QStackedWidget(objectName="central")
        self.stack.addWidget(self.welcome)
        self.stack.addWidget(doc_view)
        self.setCentralWidget(self.stack)

        self._build_toolbar()
        self._build_statusbar()
        self.setAcceptDrops(True)
        self.resize(1200, 860)
        self.setMinimumSize(760, 520)
        self._update_title()
        self.welcome.set_recent(self._recent())
        icon = resource_path("assets", "icon.ico")
        if icon.exists():
            self.setWindowIcon(QIcon(str(icon)))
        self.apply_theme(self.theme)
        try:
            QGuiApplication.styleHints().colorSchemeChanged.connect(lambda _: self.apply_theme(T.system_theme()))
        except AttributeError:
            pass
        self._doc_controls(False)

    # ---- UI -------------------------------------------------------------
    def _act(self, text, slot, icon=None, shortcut=None, tip=None, tb=None, checkable=False):
        a = QAction(text, self, checkable=checkable)
        a.triggered.connect(slot)
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        sc = a.shortcut().toString(QKeySequence.NativeText)
        a.setToolTip(f"{tip or text} ({sc})" if sc else (tip or text))
        if icon:
            self._icons.append((a, icon))
        if tb is not None:
            tb.addAction(a)
        self.addAction(a)  # shortcuts work wherever the action is shown
        return a

    def _build_toolbar(self):
        tb = QToolBar("Main", objectName="mainBar")
        tb.setMovable(False)
        tb.setIconSize(QSize(20, 20))
        tb.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self.addToolBar(tb)
        self.sidebar_act = self._act("Pages", self._toggle_sidebar, "sidebar", "F4", "Show or hide the page list",
                                     tb, checkable=True)
        self.sidebar_act.setChecked(True)
        tb.addSeparator()
        self._act("Open", self.open_dialog, "open", QKeySequence.Open, "Open a PDF", tb)
        self.save_act = self._act("Save", self.save, "save", QKeySequence.Save, "Save", None)
        self.save_as_act = self._act("Save As…", self.save_as, None, QKeySequence.SaveAs, "Save as a new file")
        save_btn = QToolButton()
        save_btn.setDefaultAction(self.save_act)
        menu = QMenu(save_btn)
        menu.addAction(self.save_as_act)
        save_btn.setMenu(menu)
        save_btn.setPopupMode(QToolButton.MenuButtonPopup)
        tb.addWidget(save_btn)
        tb.addSeparator()
        self.undo_act = self._act("Undo", self.undo, "undo", QKeySequence.Undo, tb=tb)
        self.redo_act = self._act("Redo", self.redo, "redo", QKeySequence.Redo, tb=tb)
        tb.addSeparator()

        group = QActionGroup(self)
        self.mode_actions: dict[str, QAction] = {}
        for mode, label, icon, key, tip in (
                ("view", "View", "view", "Esc", "Browse the document"),
                ("select", "Select", "select", "V", "Select text to move, resize (drag the handles) or delete it"),
                ("edit", "Edit Text", "edit", "E", "Click a paragraph or line to edit it in place"),
                ("add", "Add Text", "add", "T", "Drag a box (or click) to add Khmer text")):
            a = self._act(label, lambda _=False, m=mode: self.set_mode(m), icon, key, tip, tb, checkable=True)
            group.addAction(a)
            self.mode_actions[mode] = a
            btn = tb.widgetForAction(a)
            btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.mode_actions["view"].setChecked(True)
        self.delete_act = self._act("Delete", self.delete_selection, "delete", QKeySequence.Delete,
                                    "Delete the selected text", tb)
        self.delete_act.setEnabled(False)
        self.replace_img_act = self._act("Replace Image", self.replace_image, "image", None,
                                         "Put another picture in place of the selected one", tb)
        tb.widgetForAction(self.replace_img_act).setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.replace_img_act.setEnabled(False)
        tb.addSeparator()

        self.ocr_act = self._act("Recognize Text", self.ocr_page, "ocr", "Ctrl+Shift+R",
                                 "Read scanned text with OCR so it can be searched and edited", None)
        self.ocr_all_act = self._act("Recognize All Pages", self.ocr_all, None, None,
                                     "Run OCR on every page of the document")
        ocr_btn = QToolButton()
        ocr_btn.setDefaultAction(self.ocr_act)
        m = QMenu(ocr_btn)
        m.addAction(self.ocr_act)
        m.addAction(self.ocr_all_act)
        ocr_btn.setMenu(m)
        ocr_btn.setPopupMode(QToolButton.MenuButtonPopup)
        ocr_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        tb.addWidget(ocr_btn)
        self.limon_act = self._act("Convert Limon", self.convert_limon, "convert", None,
                                   "Rewrite text in the legacy Limon font as Unicode Khmer", tb)
        tb.widgetForAction(self.limon_act).setToolButtonStyle(Qt.ToolButtonTextBesideIcon)

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(spacer)
        self.find_box = QLineEdit()
        self.find_box.setPlaceholderText("Find Khmer or English…")
        self.find_box.setClearButtonEnabled(True)
        self.find_box.setFixedWidth(240)
        self.find_box.returnPressed.connect(self.find_next)
        self.find_icon = self.find_box.addAction(QIcon(), QLineEdit.LeadingPosition)
        tb.addWidget(self.find_box)
        f = QAction(self)
        f.setShortcut(QKeySequence.Find)
        f.triggered.connect(lambda: (self.find_box.setFocus(), self.find_box.selectAll()))
        self.addAction(f)

        # second row: text style
        self.addToolBarBreak()
        fb = QToolBar("Text", objectName="formatBar")
        fb.setMovable(False)
        fb.setIconSize(QSize(18, 18))
        self.addToolBar(fb)
        fb.addWidget(QLabel("  Font  ", objectName="muted"))
        self.font_box = QComboBox()
        self.font_box.setMinimumWidth(200)
        self.font_box.addItems(list(self.fonts.paths))
        self.font_box.addItem("Add font file…")
        self.font_box.setCurrentText(self.cur_font)
        self.font_box.currentTextChanged.connect(self._font_changed)
        fb.addWidget(self.font_box)
        fb.addWidget(QLabel("  Size  ", objectName="muted"))
        self.size_spin = QDoubleSpinBox()
        self.size_spin.setRange(4, 200)
        self.size_spin.setDecimals(1)
        self.size_spin.setSuffix(" pt")
        self.size_spin.setValue(self.cur_size)
        self.size_spin.valueChanged.connect(self._size_changed)
        fb.addWidget(self.size_spin)
        fb.addWidget(QLabel("  Colour  ", objectName="muted"))
        self.color_btn = QPushButton()
        self.color_btn.setFixedSize(28, 24)
        self.color_btn.setToolTip("Text colour")
        self.color_btn.setCursor(Qt.PointingHandCursor)
        self.color_btn.clicked.connect(self._pick_color)
        fb.addWidget(self.color_btn)
        fb.addSeparator()
        self.insert_zwsp_act = self._act("Word Break", self.insert_zwsp, "wordbreak", "Ctrl+Shift+Space",
                                         "Insert an invisible Khmer word break (ZWSP) where a line may wrap", fb)
        fb.widgetForAction(self.insert_zwsp_act).setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.insert_zwsp_act.setEnabled(False)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        fb.addWidget(spacer)
        self.tool_hint = QLabel("", objectName="hint")
        fb.addWidget(self.tool_hint)
        fb.addWidget(QLabel("  "))
        self._refresh_undo_actions()

    def _build_statusbar(self):
        sb = self.statusBar()
        sb.setSizeGripEnabled(False)

        def btn(icon, tip, slot):
            b = QToolButton(autoRaise=True)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            b.setIconSize(QSize(16, 16))
            self._icons.append((b, icon))
            return b

        self.prev_btn = btn("prev", "Previous page (PgUp)", lambda: self.goto(self.page_no - 1))
        self.next_btn = btn("next", "Next page (PgDown)", lambda: self.goto(self.page_no + 1))
        for key, step in (("PgUp", -1), ("PgDown", 1)):
            a = QAction(self)
            a.setShortcut(key)
            a.triggered.connect(lambda _=False, s=step: self.goto(self.page_no + s))
            self.addAction(a)
        self.page_spin = QSpinBox()
        self.page_spin.setMinimum(1)
        self.page_spin.setFixedWidth(64)
        self.page_spin.setAlignment(Qt.AlignCenter)
        self.page_spin.valueChanged.connect(lambda v: self.goto(v - 1))
        self.page_total = QLabel(" / 0 ")
        zoom_out = btn("zoomout", "Zoom out (Ctrl+−)", lambda: self.set_zoom(self.zoom / 1.2))
        zoom_in = btn("zoomin", "Zoom in (Ctrl++)", lambda: self.set_zoom(self.zoom * 1.2))
        for seq, f in ((QKeySequence.ZoomIn, 1.2), (QKeySequence.ZoomOut, 1 / 1.2)):
            a = QAction(self)
            a.setShortcut(seq)
            a.triggered.connect(lambda _=False, f=f: self.set_zoom(self.zoom * f))
            self.addAction(a)
        self.zoom_label = QToolButton(autoRaise=True)
        self.zoom_label.setText("100%")
        self.zoom_label.setMinimumWidth(56)
        self.zoom_label.setPopupMode(QToolButton.InstantPopup)
        zm = QMenu(self.zoom_label)
        zm.addAction("Fit width", self.fit_width)
        zm.addSeparator()
        for z in (50, 75, 100, 125, 150, 200, 300):
            zm.addAction(f"{z}%", lambda z=z: self.set_zoom(z / 100))
        self.zoom_label.setMenu(zm)
        fit = btn("fit", "Fit width", self.fit_width)
        self._nav = [self.prev_btn, self.page_spin, self.page_total, self.next_btn, zoom_out, self.zoom_label,
                     zoom_in, fit]
        for w in self._nav[:4]:
            sb.addPermanentWidget(w)
        gap = QLabel("   ")
        self._nav_gaps = [gap]
        sb.addPermanentWidget(gap)
        for w in self._nav[4:]:
            sb.addPermanentWidget(w)

    def apply_theme(self, t: T.Theme):
        self.theme = t
        app = QApplication.instance()
        app.setStyle(QStyleFactory.create("Fusion"))
        app.setPalette(T.palette(t))
        app.setStyleSheet(T.stylesheet(t))
        for w, name in self._icons:
            w.setIcon(T.icon(name, t))
        self.find_icon.setIcon(T.icon("search", t))
        self.canvas.theme = t
        self.canvas.update()
        self.welcome.set_theme(t)
        self._paint_color_btn()
        if self.banner.isVisible() and self.doc:
            self._update_banner()

    def _doc_controls(self, on: bool):
        for a in (self.save_act, self.save_as_act, self.ocr_act, self.ocr_all_act, self.limon_act,
                  *self.mode_actions.values()):
            a.setEnabled(on)
        for w in self._nav + self._nav_gaps:
            w.setVisible(on)
        self.find_box.setEnabled(on)
        self.stack.setCurrentIndex(1 if on else 0)

    def _toggle_sidebar(self):
        self.thumbs.setVisible(self.sidebar_act.isChecked())

    def _paint_color_btn(self):
        self.color_btn.setStyleSheet(f"QPushButton {{ background: {self.cur_color.name()}; border: 2px solid "
                                     f"{self.theme.raised}; outline: 1px solid {self.theme.border}; "
                                     f"border-radius: 5px; padding: 0; }}")

    def _pick_color(self):
        c = QColorDialog.getColor(self.cur_color, self, "Text colour")
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

    HINTS = {"view": "", "select": "Drag to move · handles resize · Delete removes · double-click edits",
             "edit": "Click text to edit it in place", "add": "Drag a box (or click) and type"}

    def set_mode(self, mode: str):
        self._close_inline(save=True)
        self.mode_actions[mode].setChecked(True)
        self.canvas.mode = mode
        self.canvas.hover = None
        if mode != "select" and self.canvas.selected:
            self.canvas.selected = None
            self._selection_changed(None)
        self.canvas.setCursor(Qt.CrossCursor if mode == "add" else Qt.ArrowCursor)
        self.tool_hint.setText(self.HINTS[mode])
        hints = {"view": "", "select": "Click text to select it. Drag it to move it; drag a side handle to change "
                                       "its width, a corner to make it bigger or smaller.",
                 "add": "Drag a box (or click) and type. Enter = save, Shift+Enter = new line.",
                 "edit": "Click a paragraph or line to edit it in place."}
        self.statusBar().showMessage(hints[mode])
        self.canvas.update()

    # ---- document -------------------------------------------------------
    def _recent(self) -> list[str]:
        v = self.settings.value("recent", []) or []
        v = [v] if isinstance(v, str) else list(v)
        return [p for p in v if Path(p).is_file()][:RECENT_MAX]

    def _add_recent(self, path: str):
        paths = [str(Path(path))] + [p for p in self._recent() if Path(p) != Path(path)]
        self.settings.setValue("recent", paths[:RECENT_MAX])
        self.welcome.set_recent(paths[:RECENT_MAX])

    def _open_recent(self, path: str):
        if self._confirm_discard():
            self.open_file(path)

    def open_dialog(self):
        self._close_inline(save=True)
        if not self._confirm_discard():
            return
        start = str(Path(self._recent()[0]).parent) if self._recent() else ""
        path, _ = QFileDialog.getOpenFileName(self, "Open PDF", start, "PDF files (*.pdf)")
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
        self.search_hits = []
        self._dismissed.clear()
        self._doc_controls(True)
        self.page_spin.blockSignals(True)
        self.page_spin.setMaximum(doc.page_count)
        self.page_spin.blockSignals(False)
        self.page_total.setText(f" / {doc.page_count} ")
        self._add_recent(path)
        # start at 100%, or narrower if the page wouldn't fit
        self.zoom = 1.0
        vw = self.scroll.viewport().width() - 64
        pw = doc[0].rect.width * SCREEN_DPI / 72
        if self.isVisible() and vw > 200 and pw > vw:
            self.zoom = max(vw / pw, 0.25)
        self._rebuild_thumbs()
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
        key = self._select_key or (self._key(self.canvas.selected) if self.canvas.selected else None)
        self._select_key = None
        self.canvas.highlights = [r for n, r in self.search_hits if n == self.page_no]
        rules = page_rules(page)  # table borders keep cells apart
        lines = text_lines(page, rules)
        self.canvas.set_page(qpix, scale, lines, paragraphs(lines, rules=rules), pictures(page, lines))
        if key and self.canvas.mode == "select":
            self.canvas.selected = next((p for p in self.canvas.paras + self.canvas.pics
                                         if self._key(p)[0] == key[0] and abs(self._key(p)[1] - key[1]) < 2
                                         and abs(self._key(p)[2] - key[2]) < 1.5), None)
        self._selection_changed(self.canvas.selected)
        self.page_spin.blockSignals(True)
        self.page_spin.setValue(self.page_no + 1)
        self.page_spin.blockSignals(False)
        self.prev_btn.setEnabled(self.page_no > 0)
        self.next_btn.setEnabled(self.page_no < self.doc.page_count - 1)
        self.zoom_label.setText(f"{round(self.zoom * 100)}%")
        if self.thumbs.currentRow() != self.page_no:
            self.thumbs.blockSignals(True)
            self.thumbs.setCurrentRow(self.page_no)
            self.thumbs.blockSignals(False)
        self._update_banner(page)
        self._update_title()

    def goto(self, n: int):
        if self.doc and 0 <= n < self.doc.page_count and n != self.page_no:
            self.page_no = n
            self.canvas.selected = None
            self.render()
            self.scroll.verticalScrollBar().setValue(0)

    def set_zoom(self, z: float):
        self.zoom = min(max(z, 0.25), 6.0)
        self.render()

    def fit_width(self):
        if self.doc:
            pw = self.doc[self.page_no].rect.width * SCREEN_DPI / 72
            self.set_zoom((self.scroll.viewport().width() - 64) / pw)

    def eventFilter(self, obj, e):
        if obj is self.scroll.viewport() and e.type() == QEvent.Wheel and e.modifiers() & Qt.ControlModifier:
            self.set_zoom(self.zoom * (1.1 if e.angleDelta().y() > 0 else 1 / 1.1))
            return True
        return super().eventFilter(obj, e)

    # ---- thumbnails -----------------------------------------------------
    def _rebuild_thumbs(self):
        self.thumbs.blockSignals(True)
        self.thumbs.clear()
        blank: dict[tuple[int, int], QIcon] = {}
        for i in range(self.doc.page_count):
            it = QListWidgetItem(str(i + 1))
            it.setTextAlignment(Qt.AlignHCenter)
            r = self.doc[i].rect
            size = (THUMB_W, max(int(THUMB_W * r.height / r.width), 1))
            if size not in blank:  # a white page until its picture is ready, so the list doesn't jump
                pm = QPixmap(*size)
                pm.fill(QColor("white"))
                blank[size] = QIcon(pm)
            it.setIcon(blank[size])
            it.setSizeHint(QSize(THUMB_W + 16, int(THUMB_W * 1.42) + 30))
            self.thumbs.addItem(it)
        self.thumbs.setCurrentRow(self.page_no)
        self.thumbs.blockSignals(False)
        self._thumb_queue = list(range(self.doc.page_count))
        self._thumb_timer.start()

    def _refresh_thumb(self, n: int):
        if n not in self._thumb_queue:
            self._thumb_queue.insert(0, n)
        self._thumb_timer.start()

    def _render_thumbs(self):
        if not self.doc or not self._thumb_queue:
            self._thumb_timer.stop()
            return
        # one page per tick, ones in view first, so the window stays responsive
        view = self.thumbs.viewport().rect()
        n = next((n for n in self._thumb_queue if n < self.thumbs.count()
                  and self.thumbs.visualItemRect(self.thumbs.item(n)).intersects(view)), self._thumb_queue[0])
        self._thumb_queue.remove(n)
        if n < self.doc.page_count and n < self.thumbs.count():
            page = self.doc[n]
            dpr = self.devicePixelRatioF()
            z = THUMB_W * dpr / page.rect.width
            pix = page.get_pixmap(matrix=pymupdf.Matrix(z, z), alpha=False)
            img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888).copy()
            pm = QPixmap.fromImage(img)
            pm.setDevicePixelRatio(dpr)
            ic = QIcon(pm)
            ic.addPixmap(pm, QIcon.Selected)  # no selection tint over the page picture
            self.thumbs.item(n).setIcon(ic)

    # ---- banner -----------------------------------------------------------
    def _update_banner(self, page: pymupdf.Page | None = None):
        page = page or self.doc[self.page_no]
        t = self.theme
        if "scan" not in self._dismissed and not any(ln.ocr for ln in self.canvas.lines) and ocr.is_scanned(page):
            self.banner.show_message("scan", "This page is a scanned image. Recognize its text to search, copy "
                                     "and edit it.", "Recognize Text", self.ocr_page, False, t)
        elif "limon" not in self._dismissed and any(ln.legacy for ln in self.canvas.lines):
            self.banner.show_message("limon", "This page uses the legacy Limon font. Its text is shown as "
                                     "Unicode when you edit it; convert it so it can be searched and copied.",
                                     "Convert to Unicode", self.convert_limon, False, t)
        elif "check" not in self._dismissed and any(p.needs_check for p in self.canvas.paras):
            self.banner.show_message("check", "Text with an orange outline was read by OCR or rebuilt without "
                                     "its font. Check it before you rely on it.", None, None, True, t)
        else:
            self.banner.hide()

    def _dismiss_banner(self):
        self._dismissed.add(self.banner.kind)
        self.banner.hide()

    # ---- editing --------------------------------------------------------
    def _snapshot(self):
        self.undo_stack.append((self.doc.tobytes(), self.page_no))
        del self.undo_stack[:-UNDO_LIMIT]
        self.redo_stack.clear()

    def _changed(self, pages: list[int] | None = None):
        self.dirty = True
        self.search_hits = []
        self.render()
        for n in pages if pages is not None else [self.page_no]:
            self._refresh_thumb(n)
        self._refresh_undo_actions()

    @staticmethod
    def _key(item) -> tuple[str, float, float]:
        """Finds the same paragraph or picture again after the page is redrawn."""
        if isinstance(item, Picture):
            return "pic", item.rect.x0, item.rect.y0
        return "text", item.x, item.first.baseline

    def _font_for(self, pdf_font: str) -> str:
        return match_font(self.fonts.paths, pdf_font) or self.cur_font

    def _selection_changed(self, para):
        self.delete_act.setEnabled(para is not None and self.canvas.mode == "select")
        self.replace_img_act.setEnabled(isinstance(para, Picture) and self.canvas.mode == "select")
        if isinstance(para, Picture):
            r = para.rect
            self.statusBar().showMessage(f"Picture selected · {r.width:.0f} × {r.height:.0f} pt · drag to move, "
                                         "handles resize, double-click to replace")
        elif para is not None:
            if para.garbled:
                self.statusBar().showMessage("This text can't be read from the PDF, so it can't be moved. "
                                             "You can delete it, or edit it and type its text.")
            else:
                n = len(para.lines)
                self.statusBar().showMessage(f"{n} line{'s' if n > 1 else ''} selected · {para.first.font} "
                                             f"{para.first.size:g} pt")

    def _rewrite(self, para: Paragraph, text: str, font: str | None = None, **kw) -> bool:
        """Rewrite a paragraph (edited, moved or resized) as one undo step."""
        if para.garbled:
            QMessageBox.information(self, APP_NAME, "This text can't be read from the PDF, so it can't be moved "
                                                    "or resized. Edit it and type its text first.")
            return False
        name = font or self._font_for(para.first.font)
        self._snapshot()
        try:
            write_paragraph(self.doc[self.page_no], para, text, font=load_font(self.fonts.paths[name]), **kw)
        except Exception as exc:
            self._restore(self.undo_stack.pop())
            QMessageBox.critical(self, APP_NAME, f"Could not change the text:\n{exc}")
            return False
        first, scale = para.first, kw.get("scale", 1.0)
        self._select_key = ("text", para.x + kw.get("dx", 0.0), first.baseline + kw.get("dy", 0.0))
        self._changed()
        if not match_font(self.fonts.paths, first.font) and not first.xref:
            self.statusBar().showMessage(f"The PDF's font “{first.font}” isn't available, so the text was "
                                         f"redrawn in {name}.")
        elif scale != 1.0:
            self.statusBar().showMessage(f"Text size: {first.size * scale:.1f} pt")
        return True

    def _picture_op(self, op, pic: Picture, *args, rect: pymupdf.Rect | None = None, done: str = "") -> bool:
        """Move/resize/replace/delete a picture as one undo step."""
        self._snapshot()
        try:
            ok = op(self.doc[self.page_no], pic, *args)
        except Exception as exc:
            self._restore(self.undo_stack.pop())
            QMessageBox.critical(self, APP_NAME, f"Could not change the picture:\n{exc}")
            return False
        if not ok:
            self.undo_stack.pop()
            QMessageBox.information(self, APP_NAME, "This picture is drawn in a way KhmerPDF can't change yet.")
            return False
        self._select_key = self._key(Picture(pic.xref, rect, "", 0, 0)) if rect is not None else None
        self._changed()
        if done:
            self.statusBar().showMessage(done, 4000)
        return True

    def move_item(self, item, dx: float, dy: float):
        if isinstance(item, Picture):
            r = item.rect
            new = pymupdf.Rect(r.x0 + dx, r.y0 + dy, r.x1 + dx, r.y1 + dy)
            self._picture_op(move_picture, item, new, rect=new)
        else:
            self._rewrite(item, item.text, dx=dx, dy=dy)

    def resize_item(self, item, r: pymupdf.Rect, corner: bool):
        if isinstance(item, Picture):
            self._picture_op(move_picture, item, r, rect=r, done=f"Picture: {r.width:.0f} × {r.height:.0f} pt")
        else:
            self.resize_paragraph(item, r, corner)

    def replace_image(self, pic: Picture | None = None):
        pic = pic if isinstance(pic, Picture) else self.canvas.selected
        if not isinstance(pic, Picture):
            return
        start = str(Path(self.path).parent) if self.path else ""
        path, _ = QFileDialog.getOpenFileName(self, "Replace picture", start, IMAGE_FILES)
        if not path:
            return
        try:
            data = Path(path).read_bytes()
            pymupdf.open(stream=data, filetype=Path(path).suffix[1:].lower())  # can it be read at all?
        except Exception as exc:
            QMessageBox.warning(self, APP_NAME, f"Could not read the picture:\n{exc}")
            return
        self._picture_op(replace_picture, pic, data, rect=pic.rect, done="Picture replaced (Ctrl+Z to undo)")

    def resize_paragraph(self, para: Paragraph, r: pymupdf.Rect, corner: bool):
        old = block_rect(para)
        if corner:
            k = r.width / old.width
            nx = r.x0 + (para.x - old.x0) * k
            nb = r.y0 + (para.first.baseline - old.y0) * k
            self._rewrite(para, para.text, dx=nx - para.x, dy=nb - para.first.baseline, scale=k)
        else:
            self._rewrite(para, para.text, dx=r.x0 - old.x0, width=max(para.width + r.width - old.width, 20))

    def delete_selection(self):
        para = self.canvas.selected
        if not para or self.canvas.mode != "select":
            return
        if isinstance(para, Picture):
            self.canvas.selected = None
            self._picture_op(delete_picture, para, done="Picture deleted (Ctrl+Z to undo)")
            return
        self._snapshot()
        try:
            delete_paragraph(self.doc[self.page_no], para)
        except Exception as exc:
            self._restore(self.undo_stack.pop())
            QMessageBox.critical(self, APP_NAME, f"Could not delete the text:\n{exc}")
            return
        self.canvas.selected = None
        self._changed()
        self.statusBar().showMessage("Text deleted (Ctrl+Z to undo)", 4000)

    def _edit_from_select(self, para):
        if isinstance(para, Picture):
            self.replace_image(para)
            return
        self.set_mode("edit")
        para = next((p for p in self.canvas.paras if self._key(p) == self._key(para)), para)
        self.edit_paragraph(para)

    # ---- in-place editing -------------------------------------------------
    def add_text_at(self, rect: pymupdf.Rect):
        if self.doc:
            self._open_inline("add", None, rect, "", self.cur_font, self.cur_size, self.cur_color, wrap=True)

    def _edit_message(self, item, font: str | None):
        if getattr(item, "garbled", False):
            msg = ("This line's text can't be read from the PDF (its text layer is broken and no matching font "
                   "was found). Type the replacement text.")
        elif any(ln.ocr for ln in getattr(item, "lines", [item])):
            msg = "⚠ This text was read by OCR and may contain mistakes. Please check it before saving."
        elif any(ln.approx for ln in getattr(item, "lines", [item])):
            msg = ("⚠ This PDF's font isn't available, so the text was rebuilt with Khmer spelling rules and may "
                   "contain mistakes. Please check it before saving.")
        elif any(ln.legacy for ln in getattr(item, "lines", [item])):
            msg = "Converted from the legacy Limon font to Unicode. Enter = save, Esc = cancel."
        elif any(ln.recovered for ln in getattr(item, "lines", [item])):
            msg = f"Text recovered from the glyphs ({font}). Enter = save, Shift+Enter = new line, Esc = cancel."
        else:
            msg = "Enter = save, Shift+Enter = new line, Esc = cancel."
        self.statusBar().showMessage(msg)

    def edit_paragraph(self, para: Paragraph):
        if len(para.lines) == 1:
            self.edit_line(para.first)
            return
        font = match_font(self.fonts.paths, para.first.font)
        self._edit_message(para, font)
        if not font:
            font = self.cur_font
            if not para.needs_check:
                self.statusBar().showMessage(f"The PDF's font “{para.first.font}” isn't available; using {font}. "
                                             "Pick another font in the toolbar if you like.")
        self._open_inline("para", para, block_rect(para), para.text, font, para.first.size,
                          QColor.fromRgbF(*para.first.color), wrap=True)

    def edit_line(self, line: TextLine):
        font = match_font(self.fonts.paths, line.font)
        self._edit_message(line, font or line.font)
        if not font:
            font = self.cur_font
            if line.font and not (line.needs_check or line.garbled or line.legacy):
                self.statusBar().showMessage(f"The PDF's font “{line.font}” isn't available; using {font}. "
                                             "Pick another font in the toolbar if you like.")
        self._open_inline("edit", line, line.rect, "" if line.garbled else line.text,
                          font, line.size, QColor.fromRgbF(*line.color), wrap=False)

    def _open_inline(self, kind, target, rect, text, font, size, color, wrap):
        self._close_inline(save=True)
        if kind == "edit":
            target = next((ln for ln in self.canvas.lines if ln.rect == target.rect), target)
        elif kind == "para":
            target = next((p for p in self.canvas.paras if self._key(p) == self._key(target)), target)
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
        ed.min_width = rect.width * s + 12  # always cover the original text completely
        garbled = kind == "edit" and target.garbled
        ed.setPlaceholderText("Type the replacement text…" if garbled else "Type here…")
        ed.setPlainText(text)
        ed.commit.connect(lambda: self._close_inline(save=True))
        ed.cancel.connect(lambda: self._close_inline(save=False))
        grip = EditorGrip(self.canvas, ed)
        self.inline = dict(editor=ed, grip=grip, kind=kind, line=target if kind == "edit" else None,
                           para=target if kind == "para" else None, rect=pymupdf.Rect(rect),
                           start=(text, font, size, QColor(color).name()))
        self._style_inline()
        top = (target.baseline - size * 1.0) * s if kind == "edit" else rect.y0 * s
        ed.move(int(rect.x0 * s) - 5, int(min(top, rect.y0 * s)) - 5)
        self.inline["origin"] = QPoint(ed.pos())
        ed.show()
        ed.fit()  # again, now that it is laid out on screen
        grip.show()
        grip.place()
        ed.setFocus()
        ed.moveCursor(ed.textCursor().MoveOperation.End)
        self.insert_zwsp_act.setEnabled(True)

    def _style_inline(self):
        if not self.inline:
            return
        ed = self.inline["editor"]
        ed.setFont(self.fonts.qfont(self.cur_font, self.cur_size * self.canvas.scale))
        target = self.inline.get("line") or self.inline.get("para")
        check = target is not None and (target.needs_check or target.garbled)
        border = self.theme.warning if check else self.theme.accent
        ed.setStyleSheet(f"QPlainTextEdit {{ color: {self.cur_color.name()}; background: white; "
                         f"border: 2px solid {border}; border-radius: 4px; selection-background-color: "
                         f"{self.theme.accent}; selection-color: white; }}")
        ed.fit()
        grip = self.inline["grip"]
        grip.setStyleSheet(f"QLabel {{ background: {border}; color: white; font-size: 11px; font-weight: 600; "
                           f"padding: 2px 8px; border-radius: 4px; }}")
        grip.place()

    def _close_inline(self, save: bool) -> None:
        """Save (or discard) the open in-place editor, if any."""
        if not getattr(self, "inline", None):
            return
        st, self.inline = self.inline, None
        ed = st["editor"]
        text = ed.toPlainText()
        moved = ed.pos() - st.get("origin", ed.pos())
        dx, dy = moved.x() / self.canvas.scale, moved.y() / self.canvas.scale  # dragged by its grip
        for w in (ed, st["grip"]):
            w.hide()
            w.deleteLater()
        self.insert_zwsp_act.setEnabled(False)
        self.canvas.setFocus()
        if not save or not self.doc:
            return
        unchanged = (text, self.cur_font, self.cur_size, self.cur_color.name()) == st["start"] and not (dx or dy)
        if st["kind"] == "add" and not text.strip():
            return
        if st["kind"] == "edit" and (unchanged or (st["line"].garbled and not text.strip())):
            return
        if st["kind"] == "para" and unchanged:
            return
        c = self.cur_color
        rgb = (c.redF(), c.greenF(), c.blueF())
        font = load_font(self.fonts.paths[self.cur_font])
        self._snapshot()
        warning, made_room = None, 0
        try:
            page = self.doc[self.page_no]
            if st["kind"] == "add":
                r = st["rect"]
                insert_khmer_text(page, pymupdf.Rect(r.x0 + dx, r.y0 + dy, r.x1 + dx, r.y1 + dy), text,
                                  font=font, size=self.cur_size, color=rgb)
            else:
                # longer text pushes what's below it down (unless it was dragged elsewhere)
                _, warning, made_room = edit_text(
                    page, st["line"] if st["kind"] == "edit" else st["para"], text, font=font,
                    size=self.cur_size, color=rgb, fit=st["start"][1:3] == (self.cur_font, self.cur_size),
                    dx=dx, dy=dy, font_for=lambda name: load_font(self.fonts.paths[self._font_for(name)]))
        except Exception as exc:
            self._restore(self.undo_stack.pop())
            QMessageBox.critical(self, APP_NAME, f"Could not save the text:\n{exc}")
            return
        self._changed()
        if warning:
            self.statusBar().showMessage("⚠ " + warning, 12000)
        elif made_room:
            self.statusBar().showMessage(f"Made room: moved {made_room} paragraph{'s' if made_room != 1 else ''} "
                                         "or picture(s) below it down. Ctrl+Z undoes it all.", 8000)

    def insert_zwsp(self):
        if self.inline:
            self.inline["editor"].insertPlainText(ZWSP)
            self.inline["editor"].setFocus()

    def _restore(self, state):
        data, page_no = state
        old_count = self.doc.page_count if self.doc else 0
        self.doc = pymupdf.open(stream=data, filetype="pdf")
        self.page_no = min(page_no, self.doc.page_count - 1)
        if self.doc.page_count != old_count:
            self._rebuild_thumbs()

    def undo(self):
        if self.undo_stack:
            self._close_inline(save=False)
            self.redo_stack.append((self.doc.tobytes(), self.page_no))
            self._restore(self.undo_stack.pop())
            self._changed(list(range(self.doc.page_count)))

    def redo(self):
        if self.redo_stack:
            self._close_inline(save=False)
            self.undo_stack.append((self.doc.tobytes(), self.page_no))
            self._restore(self.redo_stack.pop())
            self._changed(list(range(self.doc.page_count)))

    def _refresh_undo_actions(self):
        self.undo_act.setEnabled(bool(self.undo_stack))
        self.redo_act.setEnabled(bool(self.redo_stack))

    # ---- OCR and Limon ----------------------------------------------------
    def ocr_page(self):
        self.run_ocr([self.page_no])

    def ocr_all(self):
        self.run_ocr(list(range(self.doc.page_count)))

    def run_ocr(self, pages: list[int]):
        if not self.doc:
            return
        if not ocr.available():
            QMessageBox.information(self, APP_NAME, "Text recognition needs Tesseract OCR, which wasn't found.\n\n"
                                    "The Windows installer includes it. If you run KhmerPDF from source, install "
                                    "Tesseract (e.g. ‘sudo apt install tesseract-ocr’) or set KHMERPDF_TESSERACT "
                                    "to tesseract.exe.")
            return
        self._close_inline(save=True)
        progress = QProgressDialog("Recognizing text…", "Cancel", 0, len(pages), self)
        progress.setWindowTitle(APP_NAME)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        self._snapshot()
        total, done = 0, []
        pool = ThreadPoolExecutor(1)
        try:
            for k, n in enumerate(pages):
                progress.setLabelText(f"Recognizing page {n + 1}" + (f" ({k + 1} of {len(pages)})…"
                                                                     if len(pages) > 1 else "…"))
                progress.setValue(k)
                if progress.wasCanceled():
                    break
                page = self.doc[n]
                fut = pool.submit(ocr.run, ocr.render(page))  # Tesseract runs while the window stays alive
                while not fut.done():
                    QApplication.processEvents(QEventLoop.AllEvents, 50)
                total += add_ocr_layer(page, fut.result())
                done.append(n)
        except Exception as exc:
            self._restore(self.undo_stack.pop())
            self.render()
            QMessageBox.critical(self, APP_NAME, f"Text recognition failed:\n{exc}")
            return
        finally:
            progress.close()
            pool.shutdown(wait=False)
        if not done:
            self.undo_stack.pop()
            return
        self._changed(done)
        self.statusBar().showMessage(f"Recognized {total} line{'s' if total != 1 else ''} on {len(done)} "
                                     f"page{'s' if len(done) != 1 else ''}. OCR text has an orange outline in the "
                                     "Edit and Select tools: please check it.", 8000)

    def convert_limon(self):
        if not self.doc:
            return
        self._close_inline(save=True)
        r = QMessageBox.question(self, APP_NAME, "Rewrite all Limon-font text in this document as Unicode Khmer "
                                 f"in “{self.cur_font}”?\n\nThe text keeps its place and width, and becomes "
                                 "searchable and copyable. (Pick another font in the toolbar first if you like.)",
                                 QMessageBox.Ok | QMessageBox.Cancel)
        if r != QMessageBox.Ok:
            return
        font = load_font(self.fonts.paths[self.cur_font])
        self._snapshot()
        counts = {}
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            for n in range(self.doc.page_count):
                counts[n] = convert_legacy(self.doc[n], font)
        except Exception as exc:
            self._restore(self.undo_stack.pop())
            self.render()
            QMessageBox.critical(self, APP_NAME, f"Could not convert the text:\n{exc}")
            return
        finally:
            QApplication.restoreOverrideCursor()
        total = sum(counts.values())
        if not total:
            self.undo_stack.pop()
            self.statusBar().showMessage("No Limon-font text found in this document.", 5000)
            return
        self._changed([n for n, c in counts.items() if c])
        self.statusBar().showMessage(f"Converted {total} line{'s' if total != 1 else ''} to Unicode.", 6000)

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
        self._add_recent(path)
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
    QApplication.setOrganizationName("KhmerPDF")
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    if len(sys.argv) > 1 and Path(sys.argv[1]).is_file():
        win.open_file(sys.argv[1])
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
