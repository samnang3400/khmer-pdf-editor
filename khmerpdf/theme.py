"""Look and feel: light/dark colour tokens (following the system setting),
the Qt stylesheet, and the app's line icons (drawn from inline SVG, so they
take the theme's colours and stay sharp at any scale)."""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtSvg import QSvgRenderer


@dataclass(frozen=True)
class Theme:
    dark: bool
    window: str      # app background
    surface: str     # toolbars, panels
    raised: str      # inputs, cards
    border: str
    text: str
    muted: str
    hover: str
    accent: str
    accent_text: str  # text on the accent colour
    accent_soft: str
    canvas: str      # behind the pages
    warning: str
    warning_soft: str
    info_soft: str
    highlight: str   # search hits (with alpha)


LIGHT = Theme(False, window="#f5f6f8", surface="#ffffff", raised="#ffffff", border="#e2e5e9",
              text="#1d2127", muted="#69717d", hover="#eef0f3", accent="#2563eb", accent_text="#ffffff",
              accent_soft="#e3ecfd", canvas="#e6e8ec", warning="#c2410c", warning_soft="#fff3e6",
              info_soft="#eaf1fe", highlight="#ffc80060")
DARK = Theme(True, window="#1b1d21", surface="#23262b", raised="#2b2f35", border="#363a41",
             text="#e7e9ec", muted="#9aa2ad", hover="#30343a", accent="#5b9bff", accent_text="#0d1117",
             accent_soft="#21365a", canvas="#141518", warning="#fb923c", warning_soft="#3a2a1c",
             info_soft="#1d2b42", highlight="#ffc80055")


def system_theme() -> Theme:
    try:
        dark = QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except AttributeError:  # Qt < 6.5
        dark = QGuiApplication.palette().color(QPalette.Window).lightness() < 128
    return DARK if dark else LIGHT


def palette(t: Theme) -> QPalette:
    p = QPalette()
    c = QColor
    for role, col in ((QPalette.Window, t.window), (QPalette.WindowText, t.text), (QPalette.Base, t.raised),
                      (QPalette.AlternateBase, t.surface), (QPalette.Text, t.text), (QPalette.Button, t.surface),
                      (QPalette.ButtonText, t.text), (QPalette.Highlight, t.accent),
                      (QPalette.HighlightedText, t.accent_text), (QPalette.ToolTipBase, t.raised),
                      (QPalette.ToolTipText, t.text), (QPalette.PlaceholderText, t.muted), (QPalette.Mid, t.border)):
        p.setColor(role, c(col))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, c(t.muted))
    return p


def stylesheet(t: Theme) -> str:
    return f"""
    QMainWindow, QWidget#central {{ background: {t.window}; }}
    QToolBar {{ background: {t.surface}; border: none; border-bottom: 1px solid {t.border};
               padding: 4px 8px; spacing: 2px; }}
    QToolBar#formatBar {{ padding: 3px 8px; }}
    QToolBar::separator {{ background: {t.border}; width: 1px; margin: 6px 6px; }}
    QToolButton {{ color: {t.text}; background: transparent; border: 1px solid transparent;
                  border-radius: 6px; padding: 5px 7px; }}
    QToolButton:hover {{ background: {t.hover}; }}
    QToolButton:pressed {{ background: {t.border}; }}
    QToolButton:checked {{ background: {t.accent_soft}; color: {t.accent}; }}
    QToolButton:disabled {{ color: {t.muted}; }}
    QToolButton::menu-button {{ border: none; width: 14px; }}
    QToolButton[popupMode="1"] {{ padding-right: 18px; }}
    QLabel {{ color: {t.text}; }}
    QLabel#muted, QLabel#hint {{ color: {t.muted}; }}
    QLabel#title {{ font-size: 22px; font-weight: 600; }}
    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
        background: {t.raised}; color: {t.text}; border: 1px solid {t.border}; border-radius: 6px;
        padding: 4px 8px; min-height: 20px; selection-background-color: {t.accent};
        selection-color: {t.accent_text}; }}
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {t.accent}; }}
    QComboBox::drop-down {{ border: none; width: 18px; }}
    QComboBox QAbstractItemView {{ background: {t.raised}; color: {t.text}; border: 1px solid {t.border};
        selection-background-color: {t.accent_soft}; selection-color: {t.text}; outline: none; }}
    QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
        width: 0; border: none; }}
    QPushButton {{ background: {t.raised}; color: {t.text}; border: 1px solid {t.border}; border-radius: 6px;
                  padding: 6px 14px; }}
    QPushButton:hover {{ background: {t.hover}; }}
    QPushButton#primary {{ background: {t.accent}; color: {t.accent_text}; border-color: {t.accent};
                          font-weight: 600; padding: 9px 22px; }}
    QPushButton#primary:hover {{ background: {t.accent}; border-color: {t.text}; }}
    QPushButton#link {{ background: transparent; border: none; color: {t.accent}; padding: 3px 4px;
                       text-align: left; }}
    QPushButton#link:hover {{ text-decoration: underline; }}
    QStatusBar {{ background: {t.surface}; color: {t.muted}; border-top: 1px solid {t.border}; }}
    QStatusBar QLabel {{ color: {t.muted}; }}
    QStatusBar::item {{ border: none; }}
    QScrollArea#pages {{ background: {t.canvas}; border: none; }}
    QScrollArea#pages > QWidget > QWidget {{ background: {t.canvas}; }}
    QListWidget#thumbs {{ background: {t.surface}; border: none; border-right: 1px solid {t.border};
                         outline: none; padding: 8px 4px; color: {t.muted}; }}
    QListWidget#thumbs::item {{ border-radius: 6px; padding: 6px 2px 4px 2px; margin: 2px 6px; }}
    QListWidget#thumbs::item:hover {{ background: {t.hover}; }}
    QListWidget#thumbs::item:selected {{ background: {t.accent_soft}; color: {t.accent}; }}
    QFrame#banner {{ background: {t.info_soft}; border: none; border-bottom: 1px solid {t.border}; }}
    QFrame#banner[kind="warning"] {{ background: {t.warning_soft}; }}
    QFrame#card {{ background: {t.surface}; border: 1px solid {t.border}; border-radius: 12px; }}
    QMenuBar {{ background: {t.surface}; color: {t.text}; border-bottom: 1px solid {t.border}; padding: 2px 4px; }}
    QMenuBar::item {{ padding: 4px 10px; border-radius: 4px; background: transparent; }}
    QMenuBar::item:selected {{ background: {t.hover}; }}
    QMenu {{ background: {t.raised}; color: {t.text}; border: 1px solid {t.border}; padding: 4px; }}
    QMenu::item {{ padding: 6px 22px 6px 12px; border-radius: 4px; }}
    QMenu::item:selected {{ background: {t.accent_soft}; color: {t.text}; }}
    QMenu::separator {{ height: 1px; background: {t.border}; margin: 4px 6px; }}
    QToolTip {{ background: {t.raised}; color: {t.text}; border: 1px solid {t.border}; padding: 4px 6px; }}
    QScrollBar:vertical {{ background: transparent; width: 11px; margin: 2px; }}
    QScrollBar:horizontal {{ background: transparent; height: 11px; margin: 2px; }}
    QScrollBar::handle {{ background: {t.border}; border-radius: 3px; min-height: 30px; min-width: 30px; }}
    QScrollBar::handle:hover {{ background: {t.muted}; }}
    QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{
        background: none; border: none; width: 0; height: 0; }}
    QProgressDialog QLabel {{ min-width: 280px; }}
    """


# 24x24 line icons, drawn for this app. {c} = stroke colour.
_ICONS = {
    "open": '<path d="M3 7.5A2.5 2.5 0 0 1 5.5 5H9l2 2h7.5A2.5 2.5 0 0 1 21 9.5v7a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 16.5z"/>',
    "save": '<path d="M5 3.5h10.5l4 4V18a2.5 2.5 0 0 1-2.5 2.5H7A2.5 2.5 0 0 1 4.5 18V6A2.5 2.5 0 0 1 7 3.5"/>'
            '<path d="M8 3.5v4.5h7V3.5"/><path d="M8 20.5v-6h8v6"/>',
    "undo": '<path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/>',
    "redo": '<path d="m15 14 5-5-5-5"/><path d="M20 9H9.5a5.5 5.5 0 0 0 0 11H13"/>',
    "view": '<path d="M2.5 12S6 5 12 5s9.5 7 9.5 7-3.5 7-9.5 7-9.5-7-9.5-7z"/><circle cx="12" cy="12" r="3"/>',
    "select": '<path d="M5 3.5 18.5 10l-6 2-2.5 6.5z"/><path d="m13 13 5 5"/>',
    "edit": '<path d="M8 4h2a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H8"/><path d="M16 4h-2a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h2"/>'
            '<path d="M10 12h4"/>',
    "add": '<path d="M4 7V4.5h11V7"/><path d="M9.5 4.5V19"/><path d="M7.5 19h4"/><path d="M18 13v7"/>'
           '<path d="M14.5 16.5h7"/>',
    "delete": '<path d="M4 7h16"/><path d="M10 11v6"/><path d="M14 11v6"/>'
              '<path d="m6 7 .9 11.2A2 2 0 0 0 8.9 20h6.2a2 2 0 0 0 2-1.8L18 7"/><path d="M9 7V4.5h6V7"/>',
    "ocr": '<path d="M4 8V5.5A1.5 1.5 0 0 1 5.5 4H8"/><path d="M16 4h2.5A1.5 1.5 0 0 1 20 5.5V8"/>'
           '<path d="M20 16v2.5a1.5 1.5 0 0 1-1.5 1.5H16"/><path d="M8 20H5.5A1.5 1.5 0 0 1 4 18.5V16"/>'
           '<path d="M8 9.5h8"/><path d="M8 12h8"/><path d="M8 14.5h5"/>',
    "convert": '<path d="M4.5 11a7.5 7.5 0 0 1 13.3-4.2L19.5 9"/><path d="M19.5 4.5V9H15"/>'
               '<path d="M19.5 13a7.5 7.5 0 0 1-13.3 4.2L4.5 15"/><path d="M4.5 19.5V15H9"/>',
    "wordbreak": '<path d="M4 9v5h16V9"/><path d="M12 5v4"/>',
    "search": '<circle cx="11" cy="11" r="6.5"/><path d="m20 20-4.2-4.2"/>',
    "prev": '<path d="m14.5 18-6-6 6-6"/>',
    "next": '<path d="m9.5 18 6-6-6-6"/>',
    "zoomin": '<circle cx="11" cy="11" r="6.5"/><path d="m20 20-4.2-4.2"/><path d="M8.5 11h5"/><path d="M11 8.5v5"/>',
    "zoomout": '<circle cx="11" cy="11" r="6.5"/><path d="m20 20-4.2-4.2"/><path d="M8.5 11h5"/>',
    "fit": '<path d="M3.5 12h17"/><path d="m7 8.5-3.5 3.5L7 15.5"/><path d="m17 8.5 3.5 3.5-3.5 3.5"/>',
    "sidebar": '<rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><path d="M9 4.5v15"/>',
    "document": '<path d="M14 3.5H7.5A2 2 0 0 0 5.5 5.5v13a2 2 0 0 0 2 2h9a2 2 0 0 0 2-2V8z"/>'
                '<path d="M14 3.5V8h4.5"/><path d="M9 13h6"/><path d="M9 16.5h4"/>',
    "image": '<rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><circle cx="9" cy="10" r="1.7"/>'
             '<path d="m20.5 15.5-4.5-4.5-8.5 8.5"/>',
    "print": '<path d="M7 9V4h10v5"/><rect x="3.5" y="9" width="17" height="8" rx="2"/><path d="M7 14h10v6.5H7z"/>',
    "pageadd": '<path d="M14 3.5H7.5a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h9a2 2 0 0 0 2-2V8z"/><path d="M14 3.5V8h4.5"/>'
               '<path d="M12 11v6"/><path d="M9 14h6"/>',
    "rotleft": '<path d="M4 4.5V9h4.5"/><path d="M4.8 9A8 8 0 1 1 6 16.5"/>',
    "rotright": '<path d="M20 4.5V9h-4.5"/><path d="M19.2 9A8 8 0 1 0 18 16.5"/>',
    "highlight": '<path d="m9.5 13.5-4 4V20h2.5l4-4"/><path d="m9.5 13.5 6.5-8.5 4 3.5-8.5 7.5z"/>'
                 '<path d="M14 20.5h6.5"/>',
    "note": '<path d="M20 14.5a2 2 0 0 1-2 2H9l-4.5 4v-14a2 2 0 0 1 2-2H18a2 2 0 0 1 2 2z"/>'
            '<path d="M8.5 9h7"/><path d="M8.5 12.5h4.5"/>',
    "sign": '<path d="M3.5 20.5h17"/><path d="m15 4 3.5 3.5L9 17H5.5v-3.5z"/>',
    "close": '<path d="M6 6l12 12"/><path d="M18 6 6 18"/>',
    "check": '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
}


def icon_pixmap(name: str, color: str, px: int = 20, dpr: float = 2.0) -> QPixmap:
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
           f'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">{_ICONS[name]}</svg>')
    size = round(px * dpr)
    pm = QPixmap(QSize(size, size))
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    QSvgRenderer(QByteArray(svg.encode())).render(p, QRectF(0, 0, size, size))
    p.end()
    pm.setDevicePixelRatio(dpr)
    return pm


def icon(name: str, t: Theme) -> QIcon:
    ic = QIcon()
    ic.addPixmap(icon_pixmap(name, t.text), QIcon.Normal, QIcon.Off)
    ic.addPixmap(icon_pixmap(name, t.accent), QIcon.Normal, QIcon.On)
    ic.addPixmap(icon_pixmap(name, t.muted), QIcon.Disabled, QIcon.Off)
    ic.addPixmap(icon_pixmap(name, t.muted), QIcon.Disabled, QIcon.On)
    return ic
