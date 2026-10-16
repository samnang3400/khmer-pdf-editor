#!/usr/bin/env bash
# Wrap dist/KhmerPDF/ (from `pyinstaller khmerpdf.spec`) as
#   dist/KhmerPDF-<version>-x86_64.AppImage   (one file: chmod +x and run)
#   dist/KhmerPDF-<version>-linux-x86_64.tar.gz
# Needs appimagetool on PATH (or APPIMAGETOOL=/path/to/it).
# Usage: installer/linux/build-appimage.sh <version>
set -euo pipefail
ver=$1
here=$(cd "$(dirname "$0")" && pwd)
app=dist/AppDir
rm -rf "$app" && mkdir -p "$app/usr/lib" "$app/usr/share/applications" "$app/usr/share/icons/hicolor/256x256/apps"
cp -a dist/KhmerPDF "$app/usr/lib/KhmerPDF"
cp "$here/khmerpdf.desktop" "$app/khmerpdf.desktop"
cp "$here/khmerpdf.desktop" "$app/usr/share/applications/"
python3 - "$app" <<'PY'
import sys
from PySide6.QtGui import QImage
from PySide6.QtCore import Qt
img = QImage("assets/icon.png").scaled(256, 256, Qt.KeepAspectRatio, Qt.SmoothTransformation)
for p in (f"{sys.argv[1]}/khmerpdf.png", f"{sys.argv[1]}/usr/share/icons/hicolor/256x256/apps/khmerpdf.png"):
    img.save(p)
PY
cat > "$app/AppRun" <<'SH'
#!/bin/sh
here=$(dirname "$(readlink -f "$0")")
exec "$here/usr/lib/KhmerPDF/KhmerPDF" "$@"
SH
chmod +x "$app/AppRun"
ARCH=x86_64 "${APPIMAGETOOL:-appimagetool}" --no-appstream "$app" "dist/KhmerPDF-$ver-x86_64.AppImage"
tar -C dist -czf "dist/KhmerPDF-$ver-linux-x86_64.tar.gz" KhmerPDF
ls -lh dist/KhmerPDF-"$ver"-*
