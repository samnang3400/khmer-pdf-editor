import subprocess
import shutil

import pymupdf
import pytest

from khmerpdf.engine import insert_khmer_text, replace_text, search, text_lines, default_font
from khmerpdf.linebreak import syllables, wrap

SAMPLES = ["ស្ត្រី", "រៀន", "ទាំង", "ព្រះរាជាណាចក្រកម្ពុជា", "ត្រូវ", "សិស្សានុសិស្ស", "ក្ស្ម"]


def _doc_with(text, **kw):
    doc = pymupdf.open()
    page = doc.new_page()
    insert_khmer_text(page, pymupdf.Rect(40, 40, 560, 400), text, **kw)
    return pymupdf.open(stream=doc.tobytes())


@pytest.mark.parametrize("word", SAMPLES)
def test_roundtrip_mupdf(word):
    page = _doc_with(word, size=16)[0]
    assert page.get_text().strip() == word
    assert page.search_for(word), "search must find shaped Khmer"


VIEWER_TEXT = "ស្ត្រី រៀន ទាំង ព្រះរាជាណាចក្រកម្ពុជា សិស្សានុសិស្ស ឆ្នាំ២០២៦ Footprints School"


def _extract_pdftotext(fn):
    if not shutil.which("pdftotext"):
        pytest.skip("pdftotext not installed")
    out = subprocess.run(["pdftotext", "-enc", "UTF-8", str(fn), "-"], capture_output=True).stdout
    return out.decode("utf-8")


def _extract_pdfium(fn):
    pdfium = pytest.importorskip("pypdfium2")
    doc = pdfium.PdfDocument(str(fn))
    try:
        return doc[0].get_textpage().get_text_range()
    finally:
        doc.close()


def _extract_pdfjs(fn):
    """Firefox's PDF engine. Needs node + `npm i pdfjs-dist` (PDFJS_DIR)."""
    import os
    d = os.environ.get("PDFJS_DIR")
    if not (d and shutil.which("node")):
        pytest.skip("pdf.js not available (set PDFJS_DIR to a folder with node_modules/pdfjs-dist)")
    js = ("import * as p from 'pdfjs-dist/legacy/build/pdf.mjs';"
          "const d=await p.getDocument(process.argv[1]).promise;"
          "const t=await (await d.getPage(1)).getTextContent();"
          "process.stdout.write(t.items.map(i=>i.str).join(''))")
    out = subprocess.run(["node", "--input-type=module", "-e", js, str(fn)], cwd=d, capture_output=True)
    return out.stdout.decode("utf-8")


@pytest.mark.parametrize("extract", [_extract_pdftotext, _extract_pdfium, _extract_pdfjs],
                         ids=["poppler/xpdf", "pdfium (Chrome/Edge)", "pdf.js (Firefox)"])
def test_text_layer_in_other_viewers(tmp_path, extract):
    fn = tmp_path / "v.pdf"
    _doc_with(VIEWER_TEXT, size=14).save(fn)
    assert "".join(extract(fn).split()) == "".join(VIEWER_TEXT.split())


def test_mixed_latin_and_wrap():
    text = "សិស្សទាំងអស់ត្រូវមកសាលារៀន Footprints School " * 4
    page = _doc_with(text, size=12)[0]
    got = "".join(page.get_text().split())
    assert got == "".join(text.split())


def test_syllables_keep_subscripts():
    assert syllables("ស្ត្រី") == ["ស្ត្រី"]
    assert syllables("សាលារៀន") == ["សា", "លា", "រៀ", "ន"]


def test_wrap_never_exceeds_width():
    f = default_font()
    text = "ព្រះរាជាណាចក្រកម្ពុជាជាតិសាសនាព្រះមហាក្សត្រ" * 3
    lines = wrap(text, 150, lambda s: f.width(s, 12))
    assert "".join(lines) == text
    assert all(f.width(line, 12) <= 150 for line in lines)


def test_replace_existing_line():
    doc = _doc_with("សួស្តី Hello", size=14)
    page = doc[0]
    line = text_lines(page)[0]
    replace_text(page, line, "ជម្រាបសួរ")
    page = pymupdf.open(stream=doc.tobytes())[0]
    assert page.get_text().strip() == "ជម្រាបសួរ"


def test_multiple_pages_share_one_font_file():
    doc = pymupdf.open()
    for i in range(3):
        page = doc.new_page()
        insert_khmer_text(page, pymupdf.Rect(40, 40, 500, 200), f"ទំព័រទី {i + 1} ស្ត្រី")
        insert_khmer_text(page, pymupdf.Rect(40, 300, 500, 400), "សិស្សានុសិស្ស")
    doc = pymupdf.open(stream=doc.tobytes(garbage=3))
    objs = [doc.xref_object(x) for x in range(1, doc.xref_length())]
    assert sum("/FontFile2" in o for o in objs) == 1  # only the tiny glyph-less text font
    glyph_forms = sum("/Subtype /Form" in o and "KhmerPDFLine" not in o for o in objs)
    distinct = {g.gid for t in ("ទំព័រទី 1 ស្ត្រី", "ទំព័រទី 2", "ទំព័រទី 3", "សិស្សានុសិស្ស")
                for g in default_font().shape(t)}
    assert glyph_forms <= len(distinct)  # glyph drawings are shared, not repeated per page
    for i, page in enumerate(doc):
        assert page.search_for(f"ទំព័រទី {i + 1}") and page.search_for("សិស្សានុសិស្ស")


def test_rotated_page():
    doc = pymupdf.open()
    page = doc.new_page()
    page.set_rotation(90)
    insert_khmer_text(page, pymupdf.Rect(40, 40, 500, 200), "ព្រះរាជាណាចក្រកម្ពុជា")
    page = pymupdf.open(stream=doc.tobytes())[0]
    assert page.rotation == 90
    assert page.get_text().strip() == "ព្រះរាជាណាចក្រកម្ពុជា"


def test_edit_pdf_with_broken_khmer_text_layer():
    """A PDF from another tool (MuPDF's HTML writer) whose Khmer extracts as junk."""
    from khmerpdf.engine import FONT_DIR
    doc = pymupdf.open()
    page = doc.new_page()
    css = "@font-face{font-family:K;src:url(Battambang-Regular.ttf);} body{font-family:K;font-size:14px}"
    page.insert_htmlbox(pymupdf.Rect(40, 40, 500, 100), "សាលាអន្តរជាតិ", css=css,
                        archive=pymupdf.Archive(str(FONT_DIR)))
    page = pymupdf.open(stream=doc.tobytes())
    line = text_lines(page[0])[0]
    assert line.original != "សាលាអន្តរជាតិ"  # the PDF's own text layer is broken...
    assert line.recovered and line.text == "សាលាអន្តរជាតិ"  # ...but the glyphs decode
    replace_text(page[0], line, "សាលាអន្តរជាតិ ហ្វតព្រីនស៍")
    assert pymupdf.open(stream=page.tobytes())[0].get_text().strip() == "សាលាអន្តរជាតិ ហ្វតព្រីនស៍"


@pytest.mark.parametrize("size", [9, 12, 18, 30])
def test_detected_size_matches(size):
    doc = pymupdf.open()
    page = doc.new_page()
    insert_khmer_text(page, pymupdf.Rect(40, 40, 560, 200), "សួស្តី ពិភពលោក Hello", size=size)
    page.insert_text((40, 400), "Plain Latin text", fontsize=size)
    page = pymupdf.open(stream=doc.tobytes())[0]
    sizes = [ln.size for ln in text_lines(page)]
    assert len(sizes) == 2 and all(abs(s - size) <= 0.5 for s in sizes), sizes


def test_search_returns_one_rect_per_match():
    page = _doc_with("សាលាអន្តរជាតិ ហ្វតព្រីនស៍ និង ហ្វតព្រីនស៍", size=16)[0]
    assert len(search(page, "ហ្វតព្រីនស៍")) == 2
    assert len(search(page, "ស្ត្រី")) == 0


def test_line_starting_with_english_still_shapes_khmer():
    """HarfBuzz guesses one script per buffer from the first letter; a line
    starting with an English word used to lose its Khmer subscripts."""
    f = default_font()
    khmer = "វិទ្យាស្ថានសិក្សាអំពីមនុស្សធម៌អន្តរជាតិ"
    mixed = f.shape("Justice " + khmer)
    alone = f.shape(khmer)
    assert [g.gid for g in mixed][-len(alone):] == [g.gid for g in alone]
    assert f.shape("") == []
    page = _doc_with("Justice " + khmer + " Aix-Marseille", size=12)[0]
    assert "".join(page.get_text().split()) == "Justice" + khmer + "Aix-Marseille"


def test_edited_line_keeps_original_width():
    """A justified line that is edited ends where the original did."""
    doc = _doc_with("សាលាអន្តរជាតិ ហ្វតព្រីនស៍ ភ្នំពេញ", size=14)
    page = doc[0]
    line = text_lines(page)[0]
    target = line.rect.width * 1.06  # pretend the original was justified a bit wider
    line.rect.x1 = line.rect.x0 + target
    r = replace_text(page, line, "សាលាអន្តរជាតិ ហ្វតព្រីនស៍ ខេត្ត")
    assert abs(r.width - target) < 0.5
