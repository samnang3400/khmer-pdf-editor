# PyInstaller spec: builds dist/KhmerPDF/ (one-folder app, wrapped by Inno Setup).
# Build:  pyinstaller --noconfirm khmerpdf.spec
import sys

block_cipher = None

a = Analysis(
    ["run.py"],
    pathex=[],
    datas=[
        ("khmerpdf/fonts", "khmerpdf/fonts"),
        ("assets/icon.ico", "assets"),
    ],
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
