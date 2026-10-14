"""Khmer PDF text engine.

Why other editors break Khmer and what we do instead:

* Rendering: Khmer needs OpenType shaping (subscript consonants via COENG,
  pre-base vowel reordering, ligatures). We shape with HarfBuzz and write
  the resulting glyph IDs directly, positioned glyph by glyph.
* Text layer: a shaped glyph (e.g. a subscript ta) has no 1:1 Unicode value,
  so normal PDFs give garbage on copy/search. We wrap every HarfBuzz cluster
  in a /Span << /ActualText (...) >> marked-content sequence holding the
  original Unicode, so copy, search and screen readers get the real text.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pymupdf
import uharfbuzz as hb

from .linebreak import wrap

FONT_DIR = Path(__file__).parent / "fonts"
GLYPHLESS_FONT = FONT_DIR / "glyphless" / "GlyphLess.ttf"
LINE_SPACING = 1.0  # multiplier on the font's own ascender-descender height


# --------------------------------------------------------------------------
# Fonts and shaping
# --------------------------------------------------------------------------
@dataclass
class Glyph:
    gid: int
    cluster: int
    x_advance: int
    x_offset: int
    y_offset: int


class KhmerFont:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.data = self.path.read_bytes()
        self.face = hb.Face(self.data)
        self.hb_font = hb.Font(self.face)
        self.upem = self.face.upem
        ext = self.hb_font.get_font_extents("ltr")
        self.ascender = ext.ascender
        self.descender = ext.descender  # negative
        self.line_gap = ext.line_gap
        digest = hashlib.sha1(self.data).hexdigest()[:8]
        self.resource_name = f"KhF{digest}"  # PDF resource name, stable per font

    @property
    def name(self) -> str:
        return self.path.stem.replace("-", " ")

    def shape(self, text: str) -> list[Glyph]:
        buf = hb.Buffer()
        buf.add_str(text)
        buf.guess_segment_properties()
        hb.shape(self.hb_font, buf, {"kern": True, "liga": True})
        return [
            Glyph(i.codepoint, i.cluster, p.x_advance, p.x_offset, p.y_offset)
            for i, p in zip(buf.glyph_infos, buf.glyph_positions)
        ]

    def width(self, text: str, size: float) -> float:
        return sum(g.x_advance for g in self.shape(text)) * size / self.upem

    def line_height(self, size: float) -> float:
        return (self.ascender - self.descender + self.line_gap) * size / self.upem * LINE_SPACING

    def ascent(self, size: float) -> float:
        return self.ascender * size / self.upem


@lru_cache(maxsize=None)
def load_font(path: str) -> KhmerFont:
    return KhmerFont(path)


def bundled_fonts() -> dict[str, str]:
    """Display name -> path for fonts shipped with the app."""
    return {p.stem.replace("-", " "): str(p) for p in sorted(FONT_DIR.glob("*.ttf"))}


_KHMER_FONT_HINTS = ("khmer", "daunpenh", "moolboran", "battambang", "siemreap", "kantumruy",
                     "hanuman", "moul", "bokor", "dangrek", "koulen", "nokora", "suwannaphum",
                     "taprom", "preahvihear", "angkor", "bayon", "chenla", "odormean", "kdam",
                     "fasthand", "freehand", "metal", "content", "limon")


def system_khmer_fonts() -> dict[str, str]:
    """Khmer-capable .ttf/.otf fonts installed on this computer (e.g. Khmer UI,
    DaunPenh and Khmer OS on Windows). Filenames are pre-filtered by name,
    then confirmed by checking for a Khmer glyph."""
    import os
    dirs = []
    if os.name == "nt":
        dirs.append(Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts")
        dirs.append(Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Windows" / "Fonts")
    else:
        dirs += [Path("/usr/share/fonts"), Path.home() / ".local/share/fonts", Path.home() / ".fonts"]
    found: dict[str, str] = {}
    for d in dirs:
        if not d.is_dir():
            continue
        for p in d.rglob("*"):
            if p.suffix.lower() not in (".ttf", ".otf") or not any(h in p.name.lower() for h in _KHMER_FONT_HINTS):
                continue
            try:
                f = hb.Font(hb.Face(p.read_bytes()))
                if f.get_nominal_glyph(0x1780):
                    found[p.stem.replace("-", " ").replace("_", " ")] = str(p)
            except Exception:
                continue
    return dict(sorted(found.items()))


def default_font() -> KhmerFont:
    return load_font(str(FONT_DIR / "Battambang-Regular.ttf"))


# --------------------------------------------------------------------------
# PDF writing
# --------------------------------------------------------------------------
# Uniform advance (1/1000 em) for the invisible text font. Kept small so the
# per-character horizontal scaling (Tz) is almost always >= 100%: MuPDF reports
# span size as font size x min(1, Tz), so this keeps one consistent size per line.
INVISIBLE_WIDTH = 50
ZERO_WIDTH = "\u200b\u200c\u200d\u2060\ufeff"


def _ensure_font(page: pymupdf.Page, font: KhmerFont) -> tuple[str, str]:
    """Register the font on the page. Returns (visible, invisible) resource
    names. Both share one embedded font file."""
    doc = page.parent
    vis, inv = font.resource_name, font.resource_name + "I"
    xref = page.insert_font(fontname=vis, fontbuffer=font.data)  # reused per doc
    kind, val = doc.xref_get_key(xref, "KhmerPDFInv")
    if kind == "xref":
        inv_xref = int(val.split()[0])
    else:
        _write_full_widths(doc, xref, font)
        inv_xref = _make_invisible_font(doc, xref, font)
        doc.xref_set_key(xref, "KhmerPDFInv", f"{inv_xref} 0 R")
    _set_font_resource(page, inv, inv_xref)
    return vis, inv


def _set_font_resource(page: pymupdf.Page, name: str, xref: int) -> None:
    """Add /Font/<name> to the page's resources, following indirect objects.
    insert_font() above has already guaranteed a Resources dict exists."""
    doc = page.parent
    owner, path = page.xref, "Resources"
    kind, val = doc.xref_get_key(owner, path)
    if kind == "xref":
        owner, path = int(val.split()[0]), ""
    kind, val = doc.xref_get_key(owner, f"{path}/Font" if path else "Font")
    if kind == "xref":
        owner, path = int(val.split()[0]), ""
    else:
        path = f"{path}/Font" if path else "Font"
    doc.xref_set_key(owner, f"{path}/{name}" if path else name, f"{xref} 0 R")


def _descendant(doc: pymupdf.Document, type0_xref: int) -> int:
    kind, val = doc.xref_get_key(type0_xref, "DescendantFonts")
    return int(val.strip("[]").split()[0])


def _write_full_widths(doc: pymupdf.Document, type0_xref: int, font: KhmerFont) -> None:
    """MuPDF only writes widths for glyphs reachable from the cmap. Khmer
    subscripts and pre-base forms are unmapped glyphs, so they'd get the
    default width and text extractors see bogus gaps. Write every glyph."""
    widths = [round(font.hb_font.get_glyph_h_advance(g) * 1000 / font.upem)
              for g in range(font.face.glyph_count)]
    doc.xref_set_key(_descendant(doc, type0_xref), "W", "[0 [" + " ".join(map(str, widths)) + "]]")


def _make_invisible_font(doc: pymupdf.Document, type0_xref: int, font: KhmerFont) -> int:
    """Build the font for the invisible text layer.

    It reuses the real font's ToUnicode (so CID = real glyph ID still maps to
    the right character), but:
      * every CID is drawn with an EMPTY glyph from a tiny glyph-less font
        (CIDToGIDMap -> 1), like Tesseract's GlyphLessFont. No outlines means
        no inflated glyph boxes when the layer is horizontally scaled, so
        redaction and hit-testing use clean character boxes;
      * every glyph has the same non-zero width. Combining marks such as
        U+17BD have zero advance in the real font, and extractors drop or
        reorder zero-width characters; here each character gets its own slot.
    """
    def new_obj(src: str) -> int:
        x = doc.get_new_xref()
        doc.update_object(x, src)
        return x

    cid = _descendant(doc, type0_xref)
    kind, val = doc.xref_get_key(cid, "FontDescriptor")
    desc_inv = new_obj(doc.xref_object(int(val.split()[0])))
    ff = new_obj("<<>>")
    doc.update_stream(ff, GLYPHLESS_FONT.read_bytes())
    doc.xref_set_key(desc_inv, "FontFile2", f"{ff} 0 R")
    doc.xref_set_key(desc_inv, "FontName", "/KhmerPDF-GlyphLess")
    cmap = new_obj("<<>>")
    doc.update_stream(cmap, b"\x00\x01" * font.face.glyph_count)
    cid_inv = new_obj(doc.xref_object(cid))
    doc.xref_set_key(cid_inv, "W", "null")
    doc.xref_set_key(cid_inv, "DW", str(INVISIBLE_WIDTH))
    doc.xref_set_key(cid_inv, "FontDescriptor", f"{desc_inv} 0 R")
    doc.xref_set_key(cid_inv, "CIDToGIDMap", f"{cmap} 0 R")
    doc.xref_set_key(cid_inv, "BaseFont", "/KhmerPDF-GlyphLess")
    t0_inv = new_obj(doc.xref_object(type0_xref))
    doc.xref_set_key(t0_inv, "DescendantFonts", f"[{cid_inv} 0 R]")
    doc.xref_set_key(t0_inv, "BaseFont", "/KhmerPDF-GlyphLess")
    doc.xref_set_key(t0_inv, "KhmerPDFInv", "null")
    return t0_inv


def _append_stream(page: pymupdf.Page, data: bytes) -> None:
    doc = page.parent
    page.wrap_contents()  # isolate existing graphics state with q ... Q
    xref = doc.get_new_xref()
    doc.update_object(xref, "<<>>")
    doc.update_stream(xref, data)
    refs = page.get_contents() + [xref]
    doc.xref_set_key(page.xref, "Contents", "[" + " ".join(f"{r} 0 R" for r in refs) + "]")


def _line_ops(font: KhmerFont, text: str, x: float, baseline: float,
              size: float, to_pdf: pymupdf.Matrix, res: tuple[str, str]) -> list[str]:
    """Content-stream operators for one line. (x, baseline) are in MuPDF page
    coordinates (top-left origin).

    Two layers, the same technique OCR'd PDFs use:
      1. Visible: HarfBuzz-shaped glyphs, wrapped in an EMPTY ActualText so
         extractors ignore them (shaped glyphs have no 1:1 Unicode).
      2. Invisible (render mode 3): the original characters as nominal glyphs,
         one run per cluster, horizontally scaled (Tz) to the cluster's shaped
         width, so copy/search return real Unicode and highlights line up.
    Verified with both MuPDF and Poppler; a per-cluster ActualText on the
    visible glyphs makes MuPDF split lines and duplicate characters.
    """
    glyphs = font.shape(text)
    scale = size / font.upem
    ops: list[str] = [f"/{res[0]} {size:.2f} Tf", "/Span <</ActualText <FEFF>>> BDC"]
    cluster_adv: dict[int, int] = {}
    pen = 0
    for g in glyphs:
        gx = x + (pen + g.x_offset) * scale
        gy = baseline - g.y_offset * scale  # HarfBuzz y is up, MuPDF y is down
        p = pymupdf.Point(gx, gy) * to_pdf
        ops.append(f"1 0 0 1 {p.x:.3f} {p.y:.3f} Tm <{g.gid:04X}> Tj")
        pen += g.x_advance
        cluster_adv[g.cluster] = cluster_adv.get(g.cluster, 0) + g.x_advance
    ops += ["EMC", "3 Tr", f"/{res[1]} {size:.2f} Tf"]

    starts = sorted(cluster_adv)
    ends = starts[1:] + [len(text)]
    pen = 0
    for s, e in zip(starts, ends):
        # Zero-width format characters (ZWSP word-break hints, ZWJ, ZWNJ) are
        # layout hints only; leaving them out of the text layer keeps search
        # working whether or not the query contains them.
        chunk = [c for c in text[s:e] if c not in ZERO_WIDTH]
        if not chunk:
            continue
        slot = cluster_adv[s] / len(chunk)  # each char gets its own x slot so
        for i, ch in enumerate(chunk):      # extractors keep logical order
            gid = font.hb_font.get_nominal_glyph(ord(ch)) or 0
            tz = slot / (INVISIBLE_WIDTH * font.upem / 1000) * 100
            p = pymupdf.Point(x + (pen + i * slot) * scale, baseline) * to_pdf
            ops.append(f"{tz:.2f} Tz 1 0 0 1 {p.x:.3f} {p.y:.3f} Tm <{gid:04X}> Tj")
        pen += cluster_adv[s]
    ops += ["100 Tz", "0 Tr"]
    return ops


def layout(text: str, font: KhmerFont, size: float, width: float | None) -> list[str]:
    if width is None:
        return text.split("\n")
    return wrap(text, width, lambda s: font.width(s, size))


def insert_khmer_text(page: pymupdf.Page, rect: pymupdf.Rect, text: str,
                      font: KhmerFont | None = None, size: float = 12,
                      color: tuple[float, float, float] = (0, 0, 0),
                      align: str = "left", wrap_text: bool = True,
                      baseline: float | None = None) -> pymupdf.Rect:
    """Write shaped, searchable Khmer text into `rect` (MuPDF coordinates).
    Returns the rect actually covered by the text."""
    font = font or default_font()
    rect = pymupdf.Rect(rect)
    rotation = page.rotation
    if rotation:
        rect = rect * page.derotation_matrix
        page.set_rotation(0)
    try:
        to_pdf = ~page.transformation_matrix
        res = _ensure_font(page, font)
        lines = layout(text, font, size, rect.width if wrap_text else None)
        lh = font.line_height(size)
        y = baseline if baseline is not None else rect.y0 + font.ascent(size)
        ops = ["q", f"{color[0]:.3f} {color[1]:.3f} {color[2]:.3f} rg", "BT"]
        max_w = 0.0
        for line in lines:
            w = font.width(line, size)
            max_w = max(max_w, w)
            x = rect.x0
            if align == "center":
                x = rect.x0 + (rect.width - w) / 2
            elif align == "right":
                x = rect.x1 - w
            ops += _line_ops(font, line, x, y, size, to_pdf, res)
            y += lh
        ops += ["ET", "Q"]
        _append_stream(page, ("\n".join(ops) + "\n").encode("ascii"))
        top = (baseline - font.ascent(size)) if baseline is not None else rect.y0
        used = pymupdf.Rect(rect.x0, top, rect.x0 + max(max_w, 1), top + lh * len(lines))
    finally:
        if rotation:
            page.set_rotation(rotation)
    return used * page.rotation_matrix if rotation else used


# --------------------------------------------------------------------------
# Reading and editing existing text
# --------------------------------------------------------------------------
@dataclass
class TextLine:
    rect: pymupdf.Rect
    text: str
    size: float
    font: str
    color: tuple[float, float, float]
    baseline: float
    garbled: bool  # True when the PDF's own Unicode mapping looks broken


def _looks_garbled(s: str) -> bool:
    """Khmer PDFs with broken ToUnicode usually extract as Latin-1 / PUA junk."""
    bad = sum(1 for c in s if ("À" <= c <= "ɏ") or ("" <= c <= "") or c == "�")
    return bad > 0 and bad >= max(1, len(s.strip()) // 5)


def text_lines(page: pymupdf.Page) -> list[TextLine]:
    out: list[TextLine] = []
    d = page.get_text("dict", flags=pymupdf.TEXT_PRESERVE_WHITESPACE)
    for block in d["blocks"]:
        for line in block.get("lines", []):
            spans = [s for s in line["spans"] if s["text"].strip()]
            if not spans:
                continue
            text = "".join(s["text"] for s in line["spans"]).strip()
            s0 = spans[0]
            # Our invisible layer uses horizontal scaling, which skews the
            # per-span size; the span with the most text has the true size.
            main = max(spans, key=lambda sp: len(sp["text"].strip()))
            # Span "size" is unreliable for scaled text (incl. our own invisible
            # layer), so derive it from geometry: height / (ascender - descender).
            em = main["ascender"] - main["descender"]
            size = (main["bbox"][3] - main["bbox"][1]) / em if em > 0 else main["size"]
            c = main["color"]
            out.append(TextLine(
                rect=pymupdf.Rect(line["bbox"]),
                text=text,
                size=round(size * 2) / 2,
                font=main["font"],
                color=((c >> 16 & 255) / 255, (c >> 8 & 255) / 255, (c & 255) / 255),
                baseline=s0["origin"][1],
                garbled=_looks_garbled(text),
            ))
    return out


def replace_text(page: pymupdf.Page, line: TextLine, new_text: str,
                 font: KhmerFont | None = None, size: float | None = None,
                 color: tuple[float, float, float] | None = None) -> pymupdf.Rect:
    """Remove an existing line of text and write `new_text` in its place,
    on the same baseline."""
    r = pymupdf.Rect(line.rect)
    # Khmer stacks are tall and overlap neighbouring lines; redact a narrower
    # band around the baseline so we don't eat glyphs from lines above/below.
    pad = r.height * 0.15
    band = pymupdf.Rect(r.x0, r.y0 + pad, r.x1, r.y1 - pad)
    page.add_redact_annot(band)
    page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE,
                          graphics=pymupdf.PDF_REDACT_LINE_ART_NONE)
    if not new_text.strip():
        return r
    target = pymupdf.Rect(r.x0, r.y0, page.rect.x1 - 18, r.y1)
    return insert_khmer_text(page, target, new_text, font=font, size=size or line.size,
                             color=color or line.color, wrap_text=False, baseline=line.baseline)
