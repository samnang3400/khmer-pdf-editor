#!/usr/bin/env bash
# Package dist/KhmerPDF/ (from `pyinstaller khmerpdf.spec`) as
#   dist/khmerpdf_<version>_amd64.deb   (Ubuntu, Mint, Debian: double-click to install)
# The app goes in /opt/khmerpdf, with `KhmerPDF` on PATH, an app-menu entry and
# "Open With" for PDFs.
# Usage: installer/linux/build-deb.sh <version>
set -euo pipefail
ver=$1
here=$(cd "$(dirname "$0")" && pwd)
root=dist/deb
rm -rf "$root" && mkdir -p "$root/DEBIAN" "$root/opt" "$root/usr/bin" "$root/usr/share/applications" \
  "$root/usr/share/icons/hicolor/256x256/apps"
cp -a dist/KhmerPDF "$root/opt/khmerpdf"
ln -s /opt/khmerpdf/KhmerPDF "$root/usr/bin/KhmerPDF"
ln -s /opt/khmerpdf/KhmerPDF "$root/usr/bin/khmerpdf"
cp "$here/khmerpdf.desktop" "$root/usr/share/applications/"
python3 - "$root/usr/share/icons/hicolor/256x256/apps/khmerpdf.png" <<'PY'
import sys
from PySide6.QtGui import QImage
from PySide6.QtCore import Qt
QImage("assets/icon.png").scaled(256, 256, Qt.KeepAspectRatio, Qt.SmoothTransformation).save(sys.argv[1])
PY
cat > "$root/DEBIAN/control" <<CTL
Package: khmerpdf
Version: $ver
Architecture: amd64
Maintainer: Footprints School <ai@footprintsschool.edu.kh>
Section: graphics
Priority: optional
Installed-Size: $(du -sk "$root" | cut -f1)
Depends: libc6 (>= 2.35), libegl1, libgl1, libfontconfig1, libxkbcommon0, libxkbcommon-x11-0, libdbus-1-3
Recommends: libcups2
Homepage: https://github.com/samnang3400/khmer-pdf-editor
Description: PDF editor for Khmer (Unicode) text
 Edit, OCR and fill in PDFs with Khmer text that stays correctly shaped,
 searchable and copyable. Includes Tesseract OCR and Khmer fonts.
CTL
chmod -R u+rwX,go+rX,go-w "$root"
# xz, not zstd: older dpkg (Debian 11) can't read zstd packages
dpkg-deb -Zxz --root-owner-group --build "$root" "dist/khmerpdf_${ver}_amd64.deb"
ls -lh "dist/khmerpdf_${ver}_amd64.deb"
