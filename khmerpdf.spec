# PyInstaller spec: builds dist/KhmerPDF/ (one-folder app, wrapped by Inno Setup).
# Build:  pyinstaller --noconfirm khmerpdf.spec
import sys
from pathlib import Path

block_cipher = None

datas = [
    ("khmerpdf/fonts", "khmerpdf/fonts"),
    ("khmerpdf/tessdata", "khmerpdf/tessdata"),   # OCR language data (khm, eng)
    ("khmerpdf/data", "khmerpdf/data"),           # Khmer word list for line breaking
    ("assets/icon.ico", "assets"),
]
# Tesseract OCR program, copied here by the build (see build-windows.yml)
if Path("build-deps/tesseract").is_dir():
    datas.append(("build-deps/tesseract", "tesseract"))

a = Analysis(
    ["run.py"],
    pathex=[],
    datas=datas,
    hiddenimports=[],
    excludes=[
        # Keep the bundle small: Qt modules the app never uses.
        "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtQml",
        "PySide6.QtQuick", "PySide6.Qt3DCore", "PySide6.QtMultimedia", "PySide6.QtCharts",
        "PySide6.QtDataVisualization", "PySide6.QtPdf", "PySide6.QtBluetooth",
        "tkinter", "unittest", "pytest", "fontTools",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="KhmerPDF",
    console=False,
    icon="assets/icon.ico",
    version="installer/version_info.txt" if sys.platform == "win32" else None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="KhmerPDF")
