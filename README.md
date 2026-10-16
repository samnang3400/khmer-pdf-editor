# KhmerPDF Editor

A Windows PDF editor built for **Khmer (Unicode)** text. Most PDF editors break Khmer
when they write it. This one shapes Khmer correctly and keeps the text searchable and copyable.

## Why other editors fail on Khmer, and what this does instead

| Problem | Typical editors | KhmerPDF |
|---|---|---|
| **Shaping**: subscript consonants (`្ត`), pre-base vowels (`េ ែ ៃ`), the coeng sign | Place characters one by one: visible `្`, broken stacks | Shapes every line with **HarfBuzz** before writing |
| **Text layer**: shaped glyphs have no 1:1 Unicode value | Copy/search gives junk like `ĮĦអនÌរ` | Visible glyphs are drawn as **vector outlines** (not text). An **invisible Unicode layer** in a glyph-less font, the way OCR'd PDFs work, holds the real text |
| **Line wrapping**: Khmer has no spaces between words | Wraps only at spaces, or mid-character | Wraps between words found with a Khmer dictionary (ICU's, as browsers use), at spaces and zero-width spaces (ZWSP), with a syllable fallback |
| **Broken Khmer PDFs** (Word, older tools): text stored in visual order, lost subscripts, junk like `េសចក្តីបញចប់`, `ǒថប័ន` | Copy, search and editing give that junk | **Recovers the real text from the glyphs**: the PDF still holds the font's glyph IDs, so a HarfBuzz-built reverse dictionary for the same font (Khmer OS family bundled) turns them back into Unicode, checked against the embedded font's outlines first |
| **Editing existing Khmer** | Often impossible | Click a line or paragraph and **edit it in place** on the page, in the document's own font; the old text is removed and the new text drawn on the same baseline |
| **Legacy fonts** (Limon, ABC, Truth, Baidok, Khek, FK) | Show ASCII junk like `km<uCa` when copied | **Converts their text to Unicode** (`កម្ពុជា`) for editing, and can rewrite the whole document in a Unicode font |
| **Scanned pages** | Not editable; MuPDF's built-in OCR drops Khmer subscripts (`ក្តី` → `ក្ី`) | **OCR with the Tesseract program itself** (keeps `ក្តី` intact), added as an invisible text layer so scans become searchable and editable |

Copy, search and extraction are tested in the engines behind the viewers people actually use (`tests/`):
**MuPDF** (this app), **PDFium** (Chrome/Edge), **pdf.js** (Firefox) and **Poppler/xpdf**.
The pdf.js test runs when `PDFJS_DIR` points to a folder containing `node_modules/pdfjs-dist`.

Lines written by the app also store their own text, font, size and colour, so editing them is exact.

## Features

- Open, view, zoom, and page through PDFs (drag and drop works too), with a page-thumbnail sidebar,
  recent files and a light/dark interface that follows the Windows setting
- **Edit Text**: click any line or paragraph and edit it right on the page (Enter = save, Shift+Enter =
  new line, Esc = cancel, clicking elsewhere saves). Text from broken Khmer PDFs is recovered first.
  An edited line keeps the original's width (justified paragraphs keep their right edge). Drag the
  editor's **Move** tab to put the text somewhere else. When the text grows (or you add lines), the
  paragraphs and pictures **below it move down to make room**, like in a word processor; tables and
  ruling lines stay put, and you're warned if there isn't room
- **Paragraphs**: lines that flow on (same font and size, regular spacing, aligned edges) are edited
  as one paragraph and re-wrapped inside its width; justified paragraphs stay justified. Text added
  with the app keeps its exact paragraph
- **Select** (V): click text to select it, drag it to **move** it, drag a side handle to change its
  **width** (the text re-wraps), a corner to make it **bigger or smaller**, and press Delete to
  **delete** it. Double-click to edit
- **Pictures** (Select tool): move, resize (corners keep the shape, sides stretch), **Replace Image**
  (or double-click) and delete. A picture shared with other pages (a header logo) only changes on this page
- **Pages**: insert a blank page, insert the pages of another PDF (merge), extract pages to a new PDF
  (split), duplicate, delete, rotate and move pages; drag thumbnails to reorder them, Ctrl/Shift-click
  to work on several pages at once (Pages menu, or right-click the page list)
- **Fill in forms**: click a field to type (Khmer is shaped correctly in the saved PDF, which most
  PDF tools get wrong), tick checkboxes, pick from lists
- **Highlight** (H) text, add comment **notes** (N); right-click anything on the page for what you
  can do with it (edit or remove notes and highlights, replace pictures, page tools…)
- **Sign**: draw your signature once (it's remembered) or use a picture of it, then click to place it;
  it's a picture you can move and resize. (Not a certificate-based digital signature)
- **Print** (Ctrl+P) and **Print Preview**: all pages, the current page, a range or the selected pages;
  landscape pages are turned to fit the paper
- **Legacy fonts → Unicode**: text in Limon (S1, R1, F1…), ABC-TEXT, Truth, Baidok, Khek and FK fonts is
  converted to Unicode Khmer when you edit it; **Convert to Unicode** rewrites all of it in a Unicode
  font, in place, so it can be searched and copied
- **Recognize Text (OCR)** for scanned pages (this page or all pages): the recognised text becomes an
  invisible, searchable layer, and each line can be edited (the scanned text is painted out with the
  paper colour). OCR text is outlined in orange as *please check*
- **Recovery of broken Khmer PDFs**: the PDF's font is identified by its glyph *shapes* (works for
  renamed fonts and renumbered subsets) against the bundled Khmer OS fonts and fonts installed on the PC;
  differences in glyph order between Word and HarfBuzz are tolerated. When no font matches, the text is
  rebuilt with Khmer spelling rules and marked as *please check* (orange border)
- **Add Text**: drag a box (or click) and type in place; font, size and colour from the toolbar
- **Word break** button inserts an invisible Khmer word break (ZWSP) where a line may wrap; usually not
  needed, since text wraps between dictionary words anyway
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

OCR needs the Tesseract program (the Windows installer bundles it). From source, install it
(`sudo apt install tesseract-ocr`, or the UB Mannheim build on Windows) or point
`KHMERPDF_TESSERACT` at `tesseract.exe`; the OCR tests are skipped without it. The Khmer and English
language data ship in `khmerpdf/tessdata`.

Code map:

- `khmerpdf/engine.py`: shaping, PDF writing (visible + invisible layers), reading/editing lines
- `khmerpdf/recover.py`: recovering real text from broken Khmer PDFs
- `khmerpdf/linebreak.py`: Khmer-aware wrapping (dictionary word breaks)
- `khmerpdf/pages.py`: page operations
- `khmerpdf/legacy.py`, `legacy_tables.py`: legacy fonts → Unicode (tables generated by
  `tools/make_legacy_tables.py`)
- `khmerpdf/forms.py`, `annots.py`: form filling; highlights, notes, signatures
- `khmerpdf/ocr.py`: Tesseract OCR (hOCR) for scanned pages
- `khmerpdf/app.py`, `khmerpdf/theme.py`: PySide6 GUI, colours and icons
- `tools/make_glyphless_font.py`: regenerates `fonts/glyphless/GlyphLess.ttf` (dev only)

## Building the Windows installer

**Automatic (recommended):** push to GitHub. `.github/workflows/build-windows.yml` installs Tesseract,
runs the tests, builds the app with PyInstaller (bundling `tesseract.exe` and its DLLs), smoke-tests it, and compiles `KhmerPDF-Setup-<version>.exe`
with Inno Setup. Download it from the run's *Artifacts*. Pushing a tag like `v0.1.0` also attaches
it to a GitHub Release.

**Manual (on a Windows PC):**

```bat
pip install -r requirements-dev.txt
:: optional, for OCR: copy tesseract.exe and its *.dll files from a Tesseract install
:: (e.g. C:\Program Files\Tesseract-OCR) into build-deps\tesseract\
pyinstaller --noconfirm khmerpdf.spec
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" /DAppVersion=0.1.0 installer\khmerpdf.iss
```

The installer installs per user (no admin needed), adds Start-menu and optional desktop shortcuts,
and optionally adds KhmerPDF to *Open with* for PDFs without changing the default PDF app.

## Known limitations / roadmap

- **Next:** using OCR to check text rebuilt without its font
- **Later:** certificate-based digital signatures; creating form fields
- Rotated pages are supported for adding text, but less tested than upright pages
- Text that moves to make room is redrawn; if its font isn't available it is drawn in the toolbar's font
- Lines in tables and borderless tables are found from borders and aligned columns; unusual layouts
  may still join or split wrongly
- Paragraph detection is deliberately cautious: lines in tables or columns stay separate lines
- Moving or resizing text from another PDF redraws it; if its font isn't available it is drawn in
  the toolbar's font. First-line indents are not kept when a paragraph re-wraps
- OCR quality depends on the scan (300 dpi works best); the bundled "fast" models favour speed
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
The legacy font tables in `khmerpdf/legacy_tables.py` are derived from khmerconverter's `fontdata.xml`
(Khmer OS project, LGPL-2.1). Tesseract OCR and its language data (`khmerpdf/tessdata`) are
Apache-2.0. The Khmer word list (`khmerpdf/data`) is ICU's, under the Unicode License v3.
