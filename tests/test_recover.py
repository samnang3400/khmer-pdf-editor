"""Recovering real text from PDFs whose Khmer text layer is broken."""
from pathlib import Path

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


def _renumber_glyphs(doc):
    """Re-embed each Khmer font as a subset with NEW glyph IDs (as LibreOffice,
    browsers and others do) and add a CIDToGIDMap so the page still renders."""
    import io
    from fontTools import subset
    from fontTools.ttLib import TTFont
    for xref in range(1, doc.xref_length()):
        if "/CIDFontType2" not in doc.xref_object(xref):
            continue
        desc = int(doc.xref_get_key(xref, "FontDescriptor")[1].split()[0])
        ff = int(doc.xref_get_key(desc, "FontFile2")[1].split()[0])
        font = TTFont(io.BytesIO(doc.xref_stream(ff)))
        order = font.getGlyphOrder()
        used = sorted({c[1] for page in doc for sp in page.get_texttrace() for c in sp["chars"] if c[1] > 0})
        opts = subset.Options()
        opts.retain_gids = False
        opts.notdef_outline = True
        opts.layout_features = []
        sub = subset.Subsetter(opts)
        sub.populate(glyphs=[order[g] for g in used])
        sub.subset(font)
        new_ids = {name: i for i, name in enumerate(font.getGlyphOrder())}
        out = io.BytesIO()
        font.save(out)
        doc.update_stream(ff, out.getvalue())
        c2g = bytearray(2 * (max(used) + 1))
        for g in used:
            n = new_ids[order[g]]
            c2g[2 * g:2 * g + 2] = n.to_bytes(2, "big")
        m = doc.get_new_xref()
        doc.update_object(m, "<<>>")
        doc.update_stream(m, bytes(c2g))
        doc.xref_set_key(xref, "CIDToGIDMap", f"{m} 0 R")
    return pymupdf.open(stream=doc.tobytes())


@pytest.mark.parametrize("font_file", ["KhmerOSsiemreap.ttf", "KhmerOScontent.ttf"])
def test_recovers_renamed_and_renumbered_fonts(font_file):
    """Fonts are found by glyph outlines, not names or glyph IDs."""
    doc = _broken_pdf(font_file)
    for xref in range(1, doc.xref_length()):  # rename the font, like some PDF writers do
        if doc.xref_get_key(xref, "BaseFont")[0] == "name":
            doc.xref_set_key(xref, "BaseFont", "/ABCDEF+F1")
    doc = _renumber_glyphs(pymupdf.open(stream=doc.tobytes()))
    page = doc[0]
    gids = {c[1] for sp in page.get_texttrace() for c in sp["chars"]}
    assert max(gids) < 120  # the glyph IDs really were renumbered
    assert [ln.text for ln in text_lines(page)] == LINES


def test_only_the_matching_font_gets_a_reverse_dictionary(monkeypatch):
    """Building a font's reverse dictionary takes about a second: candidate
    fonts rejected by their outlines must not get one (opening was slow)."""
    from khmerpdf import recover
    built = []
    real = recover.decoder
    monkeypatch.setattr(recover, "decoder", lambda path: built.append(path) or real(path))
    doc = _broken_pdf("KhmerOScontent.ttf")
    for xref in range(1, doc.xref_length()):  # renamed: every candidate font is tried
        if doc.xref_get_key(xref, "BaseFont")[0] == "name":
            doc.xref_set_key(xref, "BaseFont", "/ABCDEF+F1")
    page = pymupdf.open(stream=doc.tobytes())[0]
    assert [ln.text for ln in text_lines(page)] == LINES
    assert {Path(p).name for p in built} == {"KhmerOScontent.ttf"}


def test_tolerates_glyph_order_differences():
    """Word (Uniscribe) can order a syllable's marks differently from HarfBuzz;
    that used to leave a stray ្ in the text."""
    dec = decoder(str(KHMER_OS / "KhmerOSbattambang.ttf"))
    for word in ("ង្ចា", "ស្ត្រី", "ព្រះ", "ក្រុង", "ឡើង"):
        g = list(dec._shape(word))
        marks = [i for i in range(len(g) - 1) if g[i] not in dec.bases and g[i + 1] not in dec.bases]
        for i in marks:
            perm = g[:]
            perm[i], perm[i + 1] = perm[i + 1], perm[i]
            ctx = list(dec._shape("ការ")) + perm + list(dec._shape("នេះ"))
            assert dec.decode([(x, "") for x in ctx]) == "ការ" + word + "នេះ", (word, i)


def test_unknown_font_gets_best_effort_text_not_an_empty_box(monkeypatch):
    """Without a matching font, the line is rebuilt with Khmer rules (marked
    approximate) instead of being offered as 'type the replacement'."""
    import khmerpdf.recover as R
    monkeypatch.setattr(R, "page_decoders", lambda page: {})
    page = _broken_pdf()[0]
    lines = text_lines(page)
    assert lines and all(ln.approx and not ln.garbled for ln in lines)
    # this PDF maps its subscripts to U+FFFD, so those are lost, but the
    # syllable order is right (no "សចេ" / stray ្)
    assert lines[0].text.startswith("សេចក") and "\u17d2" not in lines[0].text[:2]


def test_repair_visual_order_rules():
    from khmerpdf.recover import repair_visual_order
    # (x, y, bbox, gid, text, size): base glyphs 6 wide, subscripts 0 wide
    def g(t, w=6.0):
        return (0, 0, (0, 0, w, 10), 1, t, 12)
    assert repair_visual_order([g("េ"), g("ស"), g("ច"), g("ក"), g("ត", 0), g("ី", 0)]) == "សេចក្តី"
    assert repair_visual_order([g("្", 4), g("ក"), g("ម")]) == "ក្រម"     # pre-base ro
    assert repair_visual_order([g("េ"), g("ល"), g("ា")]) == "លោ"           # split vowel
    assert repair_visual_order([g("ǒ"), g("ថ"), g("ប"), g("័", 0), g("ន")]) == "ថប័ន"  # junk dropped


def test_pre_base_vowel_drawn_after_its_consonant():
    """Tools that draw ហ then េ (placed to its left) must not decode as ហ + ងេ."""
    words = ["ម៉េង", "ម៉ោង", "លី ម៉េង", "ហេង", "សេង ហេង", "ការប្រកាសព័ត៌មាន ហេង"]
    doc = pymupdf.open()
    page = doc.new_page()
    css = "@font-face{font-family:K;src:url(KhmerOSbattambang.ttf);} body{font-family:K;font-size:15px}"
    page.insert_htmlbox(pymupdf.Rect(50, 50, 500, 700), "<br>".join(words), css=css,
                        archive=pymupdf.Archive(str(KHMER_OS)))
    page = pymupdf.open(stream=doc.tobytes())[0]
    assert [ln.text for ln in text_lines(page)] == words


def test_reverse_dictionary_is_cached_on_disk(tmp_path, monkeypatch):
    from khmerpdf import recover
    monkeypatch.setenv("KHMERPDF_CACHE", str(tmp_path))
    path = str(KHMER_OS / "KhmerOSsys.ttf")
    built = recover.GlyphDecoder(path)
    (cache,) = tmp_path.glob("decoder-*.bin")
    monkeypatch.setattr(recover.GlyphDecoder, "_build", lambda self: pytest.fail("rebuilt"))
    loaded = recover.GlyphDecoder(path)                         # from the file, not rebuilt
    for k in recover.GlyphDecoder._TABLES:
        assert getattr(loaded, k) == getattr(built, k)
    word = [(g, "") for g in built._shape("ស្រាវជ្រាវ")]
    assert loaded.decode(word) == "ស្រាវជ្រាវ"
    cache.write_bytes(cache.read_bytes()[:-10] + b"0123456789")  # damaged: built again, not trusted
    monkeypatch.undo()
    monkeypatch.setenv("KHMERPDF_CACHE", str(tmp_path))
    again = recover.GlyphDecoder(path)
    assert again.seq == built.seq
