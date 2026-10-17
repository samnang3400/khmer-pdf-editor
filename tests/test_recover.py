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


# ---- Word/Distiller PDFs: word breaks, bold, late marks, older font versions ----
def _g(x, text, gid, w, size=8.0, font="KhmerOSmc"):
    """A glyph as find_lines collects it: (x, y, bbox, gid, text, size, font, color, decoder)."""
    return (x, 40.0, (x, 32.0, x + w, 42.0), gid, text, size, font, (0, 0, 0), None)


def test_space_that_leaves_no_gap_is_a_word_break():
    from khmerpdf.recover import _invisible_spaces
    gl = [_g(70.0, "ក", 562, 6.0), _g(76.0, " ", 1, 3.2), _g(76.0, "ខ", 563, 6.0),      # no gap: ZWSP
          _g(82.0, " ", 1, 3.2), _g(86.0, "គ", 564, 6.0),                               # a real space
          _g(92.0, " ", 1, 3.2), _g(92.0, " ", 1, 3.2), _g(96.0, "ឃ", 565, 6.0)]        # space + ZWSP
    texts = [g[4] for g in _invisible_spaces(gl)]
    assert texts == ["ក", "​", "ខ", " ", "គ", " ", "​", "ឃ"]


def test_marks_drawn_after_the_word_go_back_to_their_letters():
    """Word draws a word's letters, then its vowels and subscripts (the header
    of Khmer OS's own typing guide: យូនីកូដខ្មែរ)."""
    from khmerpdf.recover import _visual_order
    drawn = [_g(108.3, "យ", 587, 9.4), _g(117.7, "ន", 581, 6.2), _g(123.9, "ក", 562, 6.2), _g(130.1, "ដ", 572, 6.3),
             _g(136.4, "​", -1, 3.1), _g(136.4, "ែ", 628, 3.1), _g(139.5, "ខ", 563, 6.0), _g(145.5, "រ", 588, 6.1),
             _g(116.9, "ូ", 622, 0), _g(129.7, "ូ", 622, 0), _g(145.9, "ម", 407, 0), _g(123.9, "ី", 618, 0)]
    order = [g[4] for g in _visual_order(drawn)]
    assert order == ["យ", "ូ", "ន", "ី", "ក", "ូ", "ដ", "​", "ែ", "ខ", "ម", "រ"]


def test_bold_drawn_twice_is_read_once():
    doc = pymupdf.open()
    page = doc.new_page()
    css = "@font-face{font-family:K;src:url(KhmerOSbattambang.ttf);} body{font-family:K;font-size:13px}"
    for _ in range(2):  # Word's bold: the same text filled, then outlined, in the same place
        page.insert_htmlbox(pymupdf.Rect(40, 60, 560, 95), LINES[1], css=css, archive=pymupdf.Archive(str(KHMER_OS)))
    page = pymupdf.open(stream=doc.tobytes())[0]
    assert [ln.text for ln in text_lines(page)] == [LINES[1]]


def test_slightly_redrawn_font_version_still_decodes(tmp_path):
    """A PDF made with another version of a font (some marks redrawn a little)
    is still recognised by glyph IDs and metrics."""
    from fontTools.ttLib import TTFont
    f = TTFont(str(KHMER_OS / "KhmerOSmuol.ttf"))
    glyf = f["glyf"]
    for name in ("uni17b8", "uni17b7", "uni17c6"):  # ី ិ ំ: nudge a point, like a redraw
        g = glyf[name]
        coords = g.getCoordinates(glyf)[0]
        coords[0] = (coords[0][0] + 12, coords[0][1] + 9)
    f.save(str(tmp_path / "KhmerOSmuol.ttf"))
    words = ["ជំពូកទី២", "សេចក្ដីផ្ដើម", "ពិសេស"]
    doc = pymupdf.open()
    page = doc.new_page()
    css = "@font-face{font-family:K;src:url(KhmerOSmuol.ttf);} body{font-family:K;font-size:16px}"
    page.insert_htmlbox(pymupdf.Rect(40, 60, 560, 400), "<br>".join(words), css=css,
                        archive=pymupdf.Archive(str(tmp_path)))
    page = pymupdf.open(stream=doc.tobytes())[0]
    assert [ln.text for ln in text_lines(page)] == words


@pytest.mark.parametrize("word", ["សង្ស័យ", "ឱ្យខ្ញុំ", "សួគ៌ាល័យ", "ព្រឹត្តិការណ៍", "ចំណុច"])
def test_syllables_that_need_more_than_consonant_vowel_sign(word):
    # a subscript with ័/៍, an independent vowel with ្យ, robat before the vowel
    dec = decoder(str(KHMER_OS / "KhmerOSmetalchrieng.ttf"))
    assert dec.decode([(g, "?") for g in dec._shape(word)]) == word


def test_a_letter_mapped_to_space_is_still_a_letter():
    # Word maps ណ to " " in the text layer; its outline says otherwise
    import uharfbuzz as hb
    from khmerpdf.recover import _inked
    font = hb.Font(hb.Face((KHMER_OS / "KhmerOSmetalchrieng.ttf").read_bytes()))
    dec = decoder(str(KHMER_OS / "KhmerOSmetalchrieng.ttf"))
    assert _inked(font, dec._shape("ណ")[0]) and not _inked(font, dec._shape(" ")[0])


def test_a_space_drawn_back_into_a_word_is_not_a_pre_base_vowel():
    from khmerpdf.recover import _visual_order
    gl = [_g(70.0, "ដ", 572, 9.4), _g(79.6, "រ", 588, 5.0), _g(74.0, " ", 1, 3.5)]
    assert [g[4] for g in _visual_order(gl)] == ["ដ", "រ", " "]


def test_a_justified_line_split_at_a_wide_gap_is_joined_again():
    from khmerpdf.recover import Rules, _rejoin_justified

    def seg(x0, x1, y):
        return {"baseline": y, "x0": x0, "x1": x1, "glyphs": [_g(x0, "ក", 562, x1 - x0)]}
    lines = [seg(72, 540, 100), seg(72, 130, 113), seg(170, 500, 113), seg(72, 540, 126)]
    out = _rejoin_justified(lines, Rules(h=[], v=[]))
    assert [(s["x0"], s["x1"]) for s in out] == [(72, 540), (72, 500), (72, 540)]
    # table cells: no line spans the gap, so they stay apart
    cells = [seg(72, 130, 100), seg(170, 300, 100), seg(72, 130, 113), seg(170, 300, 113)]
    assert len(_rejoin_justified(cells, Rules(h=[], v=[]))) == 4


def test_word_khmer_os_6_decoded_with_its_own_embedded_font():
    """Word 2021 with Khmer OS 6.00 (2010): the embedded fonts carry only
    Apple morx tables and match none of the bundled 1.10 fonts. They are
    whole fonts, though, so the PDF's own copy is the reference. A page of
    the MoEYS grade-2 health teacher's book."""
    from khmerpdf.recover import find_lines
    page = pymupdf.open(Path(__file__).parent / "fixtures" / "word_khmeros6_page.pdf")[0]
    text = "\n".join(ln.text for ln in find_lines(page))
    for want in ("សមាសភាគទី១", "សុខភាពបឋម", "មេរៀនទី",            # ស read as រ, េ as ល
                 "ក្រោយពេលរៀនចប់សិស្សនឹងមាន",                   # pre-base េ and ្រ
                 "ត្រឹមត្រូវ", "ដែលទាក់ទងនឹងមេរៀននេះ", "នៃ",     # ្រ ែ ៃ
                 "សៀវភៅ", "គោរពគ្នាទៅវិញទៅមក",                  # ៅ (consonant + ៅ glyph)
                 "ឥរិយាបថល្អក្នុងការបរិភោគអាហារ", "គ្រូពិនិត្យវត្តមាន"):
        assert want in text, want
    for junk in ("លម្លរៀន", "ស្គូ", "ខដល", "សៀវលៅ", "អាហារ្រ"):
        assert junk not in text, junk


def test_khmer_os_6_decoded_with_the_bundled_google_fonts(monkeypatch):
    """The same page when Word embeds only a subset (no whole font to fall
    back on): the Google Fonts builds of Khmer OS 6.00 in fonts/reference
    match it, and are shaped with GSUB as Word does (HarfBuzz would prefer
    their morx, which fuses pre-base vowels into the consonant)."""
    from khmerpdf import recover
    monkeypatch.setattr(recover, "embedded_references", lambda doc: {})
    page = pymupdf.open(Path(__file__).parent / "fixtures" / "word_khmeros6_page.pdf")[0]
    text = "\n".join(ln.text for ln in recover.find_lines(page))
    for want in ("សុខភាពបឋម", "ក្រោយពេលរៀនចប់សិស្សនឹងមាន", "ត្រឹមត្រូវ", "សៀវភៅ",
                 "គោរពគ្នាទៅវិញទៅមក", "ដែលទាក់ទងនឹងមេរៀននេះ"):
        assert want in text, want


def test_recovery_needs_no_library_the_app_does_not_ship(tmp_path):
    """The packaged app excludes fontTools (khmerpdf.spec): 0.5.4 removed morx
    with it, the import failed silently in the installed app, and Khmer OS 6.00
    PDFs came out broken there while every test passed here. Decode the page
    in a process where fontTools can't be imported."""
    import os
    import subprocess
    import sys
    code = (
        "import sys; sys.modules['fontTools'] = None\n"
        "import pymupdf\n"
        "from khmerpdf.recover import find_lines\n"
        f"page = pymupdf.open({str(Path(__file__).parent / 'fixtures' / 'word_khmeros6_page.pdf')!r})[0]\n"
        "print('\\n'.join(ln.text for ln in find_lines(page)))\n")
    env = {**os.environ, "KHMERPDF_CACHE": str(tmp_path),  # tables built here, not reused
           "PYTHONPATH": str(Path(__file__).parent.parent),
           "PYTHONIOENCODING": "utf-8"}  # Windows consoles default to cp1252: no Khmer
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, encoding="utf-8",
                         env=env, timeout=600)
    assert out.returncode == 0, out.stderr
    for want in ("ក្រោយពេលរៀនចប់សិស្សនឹងមាន", "ត្រឹមត្រូវ", "សៀវភៅ", "ដែលទាក់ទងនឹងមេរៀននេះ"):
        assert want in out.stdout, want
