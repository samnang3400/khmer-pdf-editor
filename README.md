# KhmerPDF Editor

A Windows PDF editor built for **Khmer (Unicode)** text. Most PDF editors break Khmer
when they write it. This one shapes Khmer correctly and keeps the text searchable and copyable.

## Why other editors fail on Khmer, and what this does instead

| Problem | Typical editors | KhmerPDF |
|---|---|---|
| **Shaping**: subscript consonants (`្ត`), pre-base vowels (`េ ែ ៃ`), the coeng sign | Place characters one by one: visible `្`, broken stacks | Shapes every line with **HarfBuzz** before writing |
| **Text layer**: shaped glyphs have no 1:1 Unicode value | Copy/search gives junk like `ĮĦអនÌរ` | Visible glyphs are drawn as **vector outlines** (not text). An **invisible Unicode layer** in a glyph-less font, the way OCR'd PDFs work, holds the real text |
| **Line wrapping**: Khmer has no spaces between words | Wraps only at spaces, or mid-character | Wraps at spaces and zero-width spaces (ZWSP), with orthographic-syllable fallback |
| **Broken Khmer PDFs** (Word, older tools): text stored in visual order, lost subscripts, junk like `េសចក្តីបញចប់`, `ǒថប័ន` | Copy, search and editing give that junk | **Recovers the real text from the glyphs**: the PDF still holds the font's glyph IDs, so a HarfBuzz-built reverse dictionary for the same font (Khmer OS family bundled) turns them back into Unicode, checked against the embedded font's outlines first |
| **Editing existing Khmer** | Often impossible | Click a line and **edit it in place** on the page, in the document's own font; the old line is removed and the new one drawn on the same baseline |

Copy, search and extraction are tested in the engines behind the viewers people actually use (`tests/`):
**MuPDF** (this app), **PDFium** (Chrome/Edge), **pdf.js** (Firefox) and **Poppler/xpdf**.
The pdf.js test runs when `PDFJS_DIR` points to a folder containing `node_modules/pdfjs-dist`.

Lines written by the app also store their own text, font, size and colour, so editing them is exact.

## Features (v0.3)

- Open, view, zoom, and page through PDFs (drag and drop works too)
- **Edit Text**: click any line and edit it right on the page (Enter = save, Shift+Enter = new line,
  Esc = cancel, clicking elsewhere saves). Text from broken Khmer PDFs is recovered first. An edited
  line keeps the original's width (justified paragraphs keep their right edge)
- **Recovery of broken Khmer PDFs**: the PDF's font is identified by its glyph *shapes* (works for
  renamed fonts and renumbered subsets) against the bundled Khmer OS fonts and fonts installed on the PC;
  differences in glyph order between Word and HarfBuzz are tolerated. When no font matches, the text is
  rebuilt with Khmer spelling rules and marked as *please check* (orange border)
- **Add Text**: drag a box (or click) and type in place; font, size and colour from the toolbar
- **Word break** button inserts an invisible Khmer word break (ZWSP) where a line may wrap
- **Find** Khmer or English across the document (Ctrl+F)
- Undo/redo, Save / Save As (saves atomically: never leaves a half-written file)
- Bundled fonts: the **Khmer OS** family (System, Battambang, Siemreap, Content, Muol…), Battambang
  and Moul. Khmer fonts installed on Windows
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
- `khmerpdf/recover.py`: recovering real text from broken Khmer PDFs
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

- **Next:** recovery for broken PDFs in fonts we don't have (OCR with Tesseract `khm`), scanned PDFs,
  Limon (legacy) → Unicode conversion; dictionary-based Khmer word segmentation, so wrapping works
  without ZWSP; move/resize/delete text boxes; reflow when editing a paragraph
- **Phase 3:** page operations (merge, split, rotate, reorder), form filling, signatures, annotations
- Rotated pages are supported for adding text, but less tested than upright pages
- Editing changes one line at a time; a justified paragraph doesn't reflow yet
- Exact recovery needs the same font the PDF used (bundled Khmer OS fonts, or one installed on the PC).
  Without it, the rule-based repair gets most syllables right but can't see letters hidden in ligatures
  (e.g. `ា` in Khmer OS's `កា` glyph). Spellings that look identical (`ក្ត`/`ក្ដ`) come back in their
  most common form
- Search highlights are spread evenly across a line, so they can be slightly offset within long lines
- Unsigned installer: Windows SmartScreen will warn until the app is code-signed

## Licensing note

PyMuPDF is **AGPL-3.0**. Internal use at the school is fine. Distributing the app means either
releasing this source under the AGPL or buying a commercial PyMuPDF license from Artifex.
Bundled fonts: Battambang and Moul are SIL OFL 1.1 (`khmerpdf/fonts/OFL.txt`); the Khmer OS fonts are
LGPL-2.1 (`khmerpdf/fonts/khmeros/COPYRIGHT-KhmerOS.txt`).
