"""Recovering real text from PDFs whose Khmer text layer is broken."""
import pymupdf
import pytest

from khmerpdf.engine import FONT_DIR, text_lines
from khmerpdf.recover import decoder, looks_broken

KHMER_OS = FONT_DIR / "khmeros"
LINES = ["សេចក្តីពន្យល់នៃក្រមនីតិវិធីព្រហ្មទណ្ឌកម្ពុជា",
         "ស្រាវជ្រាវឧក្រិដ្ឋកម្មសង្គ្រាម មហាវិទ្យាល័យច្បាប់",
         "ពិសេសចំពោះសាស្ត្រាចារ្យ និងបុគ្គលិក ត្រួតពិនិត្យដំណើរការ"]


def _broken_pdf(font_file="KhmerOSbattambang.ttf"):
    """A PDF shaped by another tool (MuPDF's HTML writer) with a Khmer OS font.
    Its text layer comes out in visual order / with junk, like Word's."""
    doc = pymupdf.open()
    page = doc.new_page()
    css = f"@font-face{{font-family:K;src:url({font_file});}} body{{font-family:K;font-size:13px}}"
    for i, t in enumerate(LINES):
        page.insert_htmlbox(pymupdf.Rect(40, 60 + i * 40, 560, 95 + i * 40), t, css=css,
                            archive=pymupdf.Archive(str(KHMER_OS)))
    return pymupdf.open(stream=doc.tobytes())


@pytest.mark.parametrize("font_file", ["KhmerOSbattambang.ttf", "KhmerOSsys.ttf", "KhmerOS.ttf"])
def test_recovers_text_from_glyphs(font_file):
    page = _broken_pdf(font_file)[0]
    lines = [ln for ln in text_lines(page) if ln.text.strip()]
    assert [ln.text for ln in lines] == LINES
    assert all(ln.recovered and not ln.garbled for ln in lines)
    assert any(ln.original != ln.text for ln in lines)  # the PDF's own text really is broken


def test_canonical_spelling_preferred():
    dec = decoder(str(KHMER_OS / "KhmerOSbattambang.ttf"))
    for word in ("វ៉ា", "ស៊ី", "ប៊ុន"):
        glyphs = list(dec._shape(word))
        assert dec.decode([(g, "") for g in glyphs]) == word


@pytest.mark.parametrize("text,broken", [
    ("េសចក្តីបញចប់", True),           # visual order (pre-base vowel first)
    ("កិចចករេដ�បល ិិ", True),          # replacement char, doubled mark
    ("ǒថប័ន", True),                   # Word junk glyph mapping
    ("សេចក្តីបញ្ចប់", False),
    ("Mélanie Vianney-Liaud និង Esther", False),
])
def test_looks_broken(text, broken):
    assert looks_broken(text) is broken
