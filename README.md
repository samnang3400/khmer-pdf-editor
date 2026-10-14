# KhmerPDF Editor

A Windows PDF editor built for **Khmer (Unicode)** text. Most PDF editors break Khmer
when they write it. This one shapes Khmer correctly and keeps the text searchable and copyable.

## Why other editors fail on Khmer, and what this does instead

| Problem | Typical editors | KhmerPDF |
|---|---|---|
| **Shaping**: subscript consonants (`្ត`), pre-base vowels (`េ ែ ៃ`), the coeng sign | Place characters one by one: visible `្`, broken stacks | Shapes every line with **HarfBuzz** before writing |
| **Text layer**: shaped glyphs have no 1:1 Unicode value | Copy/search gives junk like `ĮĦអនÌរ` | Visible glyphs are drawn as **vector outlines** (not text). An **invisible Unicode layer** in a glyph-less font, the way OCR'd PDFs work, holds the real text |
| **Line wrapping**: Khmer has no spaces between words | Wraps only at spaces, or mid-character | Wraps at spaces and zero-width spaces (ZWSP), with orthographic-syllable fallback |
| **Editing existing Khmer** | Often impossible | Click a line → it is redacted and rewritten on the same baseline. Detects PDFs whose Khmer text layer is broken |

Copy, search and extraction are tested in the engines behind the viewers people actually use (`tests/`):
**MuPDF** (this app), **PDFium** (Chrome/Edge), **pdf.js** (Firefox) and **Poppler/xpdf**.
The pdf.js test runs when `PDFJS_DIR` points to a folder containing `node_modules/pdfjs-dist`.

Lines written by the app also store their own text, font, size and colour, so editing them is exact.

## Features (v0.1)

- Open, view, zoom, and page through PDFs (drag and drop works too)
- **Add Text**: drag a box, type Khmer/English, choose font, size, color and alignment
- **Edit Text**: click any line to replace it
- **Find** Khmer or English across the document (Ctrl+F)
- Undo/redo, Save / Save As (saves atomically: never leaves a half-written file)
- Bundled fonts: Battambang (Regular/Bold) and Moul. Khmer fonts installed on Windows
  (Khmer UI, DaunPenh, Khmer OS…) are detected automatically, and any .ttf can be added

## Development

```bash
python -m venv .venv && .venv\Scripts\activate      # Windows
pip install -r requirements-dev.txt
python run.py [file.pdf]
python -m pytest -q tests
```

Code map:

- `khmerpdf/engine.py`: shaping, PDF writing (visible + invisible layers), reading/editing lines
- `khmerpdf/linebreak.py`: Khmer-aware wrapping
- `khmerpdf/app.py`: PySide6 GUI
- `tools/make_glyphless_font.py`: regenerates `fonts/glyphless/GlyphLess.ttf` (dev only)

## Building the Windows installer

**Automatic (recommended):** push to GitHub. `.github/workflows/build-windows.yml` runs the tests,
builds the app with PyInstaller, smoke-tests it, and compiles `KhmerPDF-Setup-<version>.exe`
with Inno Setup. Download it from the run's *Artifacts*. Pushing a tag like `v0.1.0` also attaches
it to a GitHub Release.

**Manual (on a Windows PC):**

```bat
pip install -r requirements-dev.txt
pyinstaller --noconfirm khmerpdf.spec
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" /DAppVersion=0.1.0 installer\khmerpdf.iss
```

The installer installs per user (no admin needed), adds Start-menu and optional desktop shortcuts,
and optionally adds KhmerPDF to *Open with* for PDFs without changing the default PDF app.

## Known limitations / roadmap

- **Phase 2:** dictionary-based Khmer word segmentation, so wrapping works without ZWSP;
  move/resize/delete added text boxes; OCR (Tesseract `khm`) for scanned PDFs and broken text layers;
  Limon (legacy) → Unicode conversion
- **Phase 3:** page operations (merge, split, rotate, reorder), form filling, signatures, annotations
- Rotated pages are supported for adding text, but less tested than upright pages
- Editing a line the app wrote edits that line only (a wrapped paragraph doesn't reflow yet)
- Search highlights are spread evenly across a line, so they can be slightly offset within long lines
- Unsigned installer: Windows SmartScreen will warn until the app is code-signed

## Licensing note

PyMuPDF is **AGPL-3.0**. Internal use at the school is fine. Distributing the app means either
releasing this source under the AGPL or buying a commercial PyMuPDF license from Artifex.
Bundled fonts are SIL OFL 1.1 (`khmerpdf/fonts/OFL.txt`).
