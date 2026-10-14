"""Build khmerpdf/fonts/glyphless/GlyphLess.ttf: a tiny TrueType font whose
only real glyph is empty. The invisible text layer maps every CID to it, so
the layer has no outlines (same idea as Tesseract's GlyphLessFont).
Run once at dev time: python tools/make_glyphless_font.py"""
from pathlib import Path

from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen

out = Path(__file__).resolve().parent.parent / "khmerpdf" / "fonts" / "glyphless" / "GlyphLess.ttf"
out.parent.mkdir(parents=True, exist_ok=True)
fb = FontBuilder(1000, isTTF=True)
fb.setupGlyphOrder([".notdef", "empty"])
fb.setupCharacterMap({})
empty = TTGlyphPen(None).glyph()
fb.setupGlyf({".notdef": empty, "empty": empty})
fb.setupHorizontalMetrics({".notdef": (500, 0), "empty": (500, 0)})
fb.setupHorizontalHeader(ascent=800, descent=-200)
fb.setupNameTable({"familyName": "GlyphLess", "styleName": "Regular"})
fb.setupOS2(sTypoAscender=800, sTypoDescender=-200, usWinAscent=800, usWinDescent=200)
fb.setupPost()
fb.save(str(out))
print(out, out.stat().st_size, "bytes")
