#!/usr/bin/env bash
# Copy the tesseract program and the libraries it needs into build-deps/tesseract/,
# so the app bundle carries its own OCR (like the Windows build). glibc and
# friends stay the user's system's. Needs patchelf.
# Usage: installer/linux/bundle-tesseract.sh [path/to/tesseract]
set -euo pipefail
exe=${1:-$(command -v tesseract)}
out=build-deps/tesseract
rm -rf "$out" && mkdir -p "$out"
cp "$exe" "$out/tesseract"
ldd "$exe" | awk '/=> \//{print $3}' | while read -r lib; do
  case "$(basename "$lib")" in
    libc.so*|libm.so*|libdl.so*|librt.so*|libpthread.so*|ld-linux*|libresolv.so*) ;;
    *) cp -L "$lib" "$out/" ;;
  esac
done
# each looks for the others next to itself
for f in "$out"/*; do patchelf --set-rpath '$ORIGIN' "$f"; done
"$out/tesseract" --version | head -1
