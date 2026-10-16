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
    ("assets/icon.png", "assets"),
]
# Tesseract OCR program, copied here by the build (see build-windows.yml, and
# installer/linux/bundle-tesseract.sh for Linux)
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
if sys.platform.startswith("linux"):
    # Leave the desktop stack (X11/Wayland, fonts, GTK, D-Bus, CUPS, libstdc++) to
    # the user's system, as the PySide6 wheels themselves do: copies from the
    # build machine clash with other distros' versions. Python's own needs stay.
    KEEP = ("libpython", "libffi", "libssl", "libcrypto", "libz.", "libbz2", "liblzma",
            "libexpat", "libmpdec", "libsqlite", "libuuid", "libreadline", "libncurses", "libtinfo")
    a.binaries = [b for b in a.binaries
                  if not (b[1] or "").startswith(("/lib/", "/usr/lib/", "/lib64/", "/usr/lib64/"))
                  or "/python3" in b[1] or Path(b[0]).name.startswith(KEEP)]
    # the GTK file-dialog theme would need GTK; the desktop-portal one gives native dialogs
    a.binaries = [b for b in a.binaries if "libqgtk3" not in b[0]]
    # Qt's X11 plugin needs xcb-cursor, which many distros don't install by default
    for d in ("/usr/lib/x86_64-linux-gnu", "/usr/lib64", "/usr/lib"):
        if Path(d, "libxcb-cursor.so.0").is_file():
            a.binaries.append(("libxcb-cursor.so.0", f"{d}/libxcb-cursor.so.0", "BINARY"))
            break
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
