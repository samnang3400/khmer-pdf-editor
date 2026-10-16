"""Limon (legacy Khmer font) text -> Unicode."""
import io

import pymupdf
import pytest

from khmerpdf.engine import FONT_DIR, convert_legacy, text_lines
from khmerpdf.legacy import is_limon_font, limon_to_unicode


@pytest.mark.parametrize("limon,unicode", [
    ("km<uCa", "កម្ពុជា"),
    ("salaeron", "សាលារៀន"),          # េ + ៀ-part = ៀ
    ("eRkam", "ក្រោម"),               # េ and ្រ typed before the consonant; េ + ា = ោ
    ("sYsþI", "សួស្តី"),              # 0xFE = subscript ta
    ("esovePA", "សៀវភៅ"),             # េ + ៅ-part = ៅ
    ("kumar", "កុមារ"),
    ("RbeTs", "ប្រទេស"),
    ("´", "ខ្ញុំ"),                    # one code for a whole word
    ("123", "១២៣"),
])
def test_limon_to_unicode(limon, unicode):
    assert limon_to_unicode(limon) == unicode


def test_windows_1252_quotes_map_back_to_their_codes():
    # 0x91 is ‘ in Windows-1252, which is what PDF text extraction gives
    assert limon_to_unicode("k‘") == "ក៊"


def test_font_names():
    assert is_limon_font("ABCDEF+LimonS1") and is_limon_font("Limon R1 Regular")
    assert not is_limon_font("Khmer OS Battambang")


def _limon_pdf():
    """A PDF whose text is in a font named Limon S1 (any font file will do:
    the codes are what matter)."""
    from fontTools.ttLib import TTFont
    f = TTFont(str(FONT_DIR / "Battambang-Regular.ttf"))
    for rec in f["name"].names:
        if rec.nameID in (1, 4, 6, 16):
            rec.string = "LimonS1" if rec.nameID == 6 else "Limon S1"
    buf = io.BytesIO()
    f.save(buf)
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_font(fontname="LS", fontbuffer=buf.getvalue())
    page.insert_text((50, 100), "km<uCa salaeron", fontname="LS", fontsize=14)
    page.insert_text((50, 140), "esovePA", fontname="LS", fontsize=14)
    return pymupdf.open(stream=doc.tobytes())


def test_limon_lines_read_as_unicode():
    lines = text_lines(_limon_pdf()[0])
    assert [ln.text for ln in lines] == ["កម្ពុជា សាលារៀន", "សៀវភៅ"]
    assert all(ln.legacy and not ln.garbled for ln in lines)


def test_convert_document_text_to_unicode():
    page = _limon_pdf()[0]
    assert convert_legacy(page) == 2
    lines = text_lines(page)
    assert sorted(ln.text for ln in lines) == ["កម្ពុជា សាលារៀន", "សៀវភៅ"]
    assert not any(ln.legacy for ln in lines)
    assert "សៀវភៅ" in page.get_text()


def _encode(word_tokens, family):
    """Type tokens (in visual order) with a legacy font's codes - the tables
    used backwards, so every family is tested without its font files."""
    from khmerpdf.legacy_tables import TABLES
    rev = {}
    for codes, text in sorted(TABLES[family].items(), key=lambda kv: len(kv[0])):
        rev.setdefault(text, codes)
    return b"".join(rev[t] for t in word_tokens).decode("cp1252", errors="replace")


@pytest.mark.parametrize("family", ["limon", "abc", "abc2", "abc-zwsp", "truth", "baidok", "khek", "fk"])
def test_every_legacy_family(family):
    from khmerpdf.legacy import to_unicode
    # កម្ពុជា and ក្រោម, typed visually: េ and ្រ before their consonant
    assert to_unicode(_encode(["ក", "ម", "្ព", "ុ", "ជ", "ា"], family), family) == "កម្ពុជា"
    assert to_unicode(_encode(["េ", "្រ", "ក", "ា", "ម"], family), family) == "ក្រោម"


@pytest.mark.parametrize("name,family", [("ABCDEF+LimonS1", "limon"), ("Limon R1 Regular", "limon"),
                                         ("ABC-TEXT-05", "abc"), ("ABC-TEXT-11", "abc2"), ("ABC-TEXT-05A", "abc-zwsp"),
                                         ("ABC-TEXT-99", "abc"), ("Truth11", "truth"), ("Khek Mool", "khek"),
                                         ("Baidok3C", "baidok"), ("FK1", "fk"), ("Khmer OS Battambang", None),
                                         ("Arial", None)])
def test_family_from_font_name(name, family):
    from khmerpdf.legacy import legacy_family
    assert legacy_family(name) == family
