"""Khmer PDF text engine.

Why other editors break Khmer, and what we do instead:

* Shaping: Khmer needs OpenType shaping (subscript consonants via COENG,
  pre-base vowel reordering, stacked marks). We shape every line with HarfBuzz.

* Two layers per line (the technique OCR'd PDFs use):
    1. Visible: the shaped glyphs, drawn as vector outlines (one shared Form
       XObject per glyph). They are not text, so no viewer can extract junk
       from them. (An earlier version used real font text hidden behind an
       empty /ActualText; Chrome/Edge (PDFium), Firefox (pdf.js) and xpdf
       ignore that and extracted garbage.)
    2. Invisible (render mode 3): the original Unicode string in a glyph-less
       font whose character codes ARE Unicode code points, so copy, search
       and screen readers get the real text in every viewer.

* Each line is a self-contained Form XObject that also stores its own text,
  font, size and colour, so lines written by this app can be edited exactly.
"""
from __future__ import annotations

import hashlib
import re
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
        self.digest = digest  # stable per font file; names its glyph XObjects

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


_KHMEROS_NAMES = {
    "KhmerOS": "Khmer OS", "KhmerOSsys": "Khmer OS System", "KhmerOSbattambang": "Khmer OS Battambang",
    "KhmerOSsiemreap": "Khmer OS Siemreap", "KhmerOScontent": "Khmer OS Content",
    "KhmerOSbokor": "Khmer OS Bokor", "KhmerOSmuol": "Khmer OS Muol", "KhmerOSmuollight": "Khmer OS Muol Light",
    "KhmerOSmuolpali": "Khmer OS Muol Pali", "KhmerOSmetalchrieng": "Khmer OS Metal Chrieng",
    "KhmerOSfasthand": "Khmer OS Fasthand", "KhmerOSfreehand": "Khmer OS Freehand",
}


def bundled_fonts() -> dict[str, str]:
    """Display name -> path for fonts shipped with the app (Google Fonts Khmer
    fonts + the Khmer OS family, which most Khmer Word documents use)."""
    out = {p.stem.replace("-", " "): str(p) for p in sorted(FONT_DIR.glob("*.ttf"))}
    for p in sorted((FONT_DIR / "khmeros").glob("*.ttf")):
        out[_KHMEROS_NAMES.get(p.stem, p.stem)] = str(p)
    return out


def norm_font_name(name: str) -> str:
    """'LDIPBJ+KhmerOSbattambang', 'Khmer OS Battambang', 'KhmerOSbattambang.ttf'
    -> 'khmerosbattambang' (for matching PDF font names to font files)."""
    name = re.sub(r"^[A-Z]{6}\+", "", Path(name).stem if name.lower().endswith((".ttf", ".otf")) else name)
    name = re.sub(r"(?i)[-_ ]?(regular|normal)$", "", name)
    return re.sub(r"[^a-z0-9]", "", name.lower())


def match_font(fonts: dict[str, str], pdf_font: str) -> str | None:
    """Name of the available font that is the same as a PDF's font, if any."""
    key = norm_font_name(pdf_font)
    for name, path in fonts.items():
        if key in (norm_font_name(name), norm_font_name(Path(path).name)):
            return name
    return None


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
# Low-level PDF helpers
# --------------------------------------------------------------------------
ZERO_WIDTH = "​‌‍⁠﻿"   # layout hints, left out of the text layer
TEXT_FONT = "KhmerPDF-Text"
TEXT_DW = 500                                    # every text-layer char is 0.5 em wide (before Tz)


def _hex16(s: str) -> str:
    return "<FEFF" + s.encode("utf-16-be").hex().upper() + ">"


def _new_obj(doc: pymupdf.Document, src: str, stream: bytes | None = None) -> int:
    x = doc.get_new_xref()
    doc.update_object(x, src)
    if stream is not None:
        doc.update_stream(x, stream, compress=True)
    return x


def _catalog_ref(doc: pymupdf.Document, key: str) -> int | None:
    kind, val = doc.xref_get_key(doc.pdf_catalog(), key)
    return int(val.split()[0]) if kind == "xref" else None


def _set_resource(page_or_xref, doc: pymupdf.Document, category: str, name: str, xref: int) -> None:
    """Add /<category>/<name> -> xref to a resource dictionary, following
    indirect objects. Works for a page (creating /Resources if missing) or
    for a Form XObject given by xref."""
    if isinstance(page_or_xref, pymupdf.Page):
        owner = page_or_xref.xref
        if doc.xref_get_key(owner, "Resources")[0] == "null":
            # Resources may be inherited from a parent Pages node: copy them
            # onto the page before extending, so nothing is lost.
            value, node = "<<>>", owner
            while True:
                kind, parent = doc.xref_get_key(node, "Parent")
                if kind != "xref":
                    break
                node = int(parent.split()[0])
                k, v = doc.xref_get_key(node, "Resources")
                if k != "null":
                    value = v
                    break
            doc.xref_set_key(owner, "Resources", value)
    else:
        owner = page_or_xref
    path = "Resources"
    kind, val = doc.xref_get_key(owner, path)
    if kind == "xref":
        owner, path = int(val.split()[0]), ""
    sub = f"{path}/{category}" if path else category
    kind, val = doc.xref_get_key(owner, sub)
    if kind == "xref":
        owner, sub = int(val.split()[0]), ""
    elif kind == "null":
        doc.xref_set_key(owner, sub, "<<>>")
    doc.xref_set_key(owner, f"{sub}/{name}" if sub else name, f"{xref} 0 R")


def _text_font(doc: pymupdf.Document) -> int:
    """Glyph-less Type0 font for the invisible layer, created once per PDF.
    Character code = Unicode code point (BMP), ToUnicode maps it back 1:1,
    and every code draws the empty glyph 1 of a 604-byte font."""
    x = _catalog_ref(doc, "KhmerPDFTextFont")
    if x:
        return x
    ranges = [f"<{hi:02X}00> <{hi:02X}FF> <{hi:02X}00>" for hi in range(256) if not 0xD8 <= hi <= 0xDF]
    blocks = "".join(f"{len(ranges[i:i + 100])} beginbfrange\n" + "\n".join(ranges[i:i + 100]) + "\nendbfrange\n"
                     for i in range(0, len(ranges), 100))
    cmap = ("/CIDInit /ProcSet findresource begin\n12 dict begin\nbegincmap\n"
            "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n"
            "/CMapName /Adobe-Identity-UCS def\n/CMapType 2 def\n"
            "1 begincodespacerange\n<0000> <FFFF>\nendcodespacerange\n" + blocks +
            "endcmap\nCMapName currentdict /CMap defineresource pop\nend\nend\n").encode()
    ff = _new_obj(doc, "<<>>", GLYPHLESS_FONT.read_bytes())
    desc = _new_obj(doc, f"<< /Type /FontDescriptor /FontName /{TEXT_FONT} /Flags 4 "
                         f"/FontBBox [0 -200 1000 800] /ItalicAngle 0 /Ascent 800 /Descent -200 "
                         f"/CapHeight 700 /StemV 80 /FontFile2 {ff} 0 R >>")
    c2g = _new_obj(doc, "<<>>", b"\x00\x01" * 65536)
    cid = _new_obj(doc, f"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /{TEXT_FONT} "
                        f"/CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> "
                        f"/FontDescriptor {desc} 0 R /DW {TEXT_DW} /CIDToGIDMap {c2g} 0 R >>")
    tu = _new_obj(doc, "<<>>", cmap)
    t0 = _new_obj(doc, f"<< /Type /Font /Subtype /Type0 /BaseFont /{TEXT_FONT} /Encoding /Identity-H "
                       f"/DescendantFonts [{cid} 0 R] /ToUnicode {tu} 0 R >>")
    doc.xref_set_key(doc.pdf_catalog(), "KhmerPDFTextFont", f"{t0} 0 R")
    return t0


class _PathPen:
    """Collects a glyph outline (font units, y up) as PDF path operators."""

    def __init__(self):
        self.ops: list[str] = []
        self.cur = (0.0, 0.0)

    def moveTo(self, p):
        self.ops.append(f"{p[0]:g} {p[1]:g} m")
        self.cur = p

    def lineTo(self, p):
        self.ops.append(f"{p[0]:g} {p[1]:g} l")
        self.cur = p

    def qCurveTo(self, c, p):  # quadratic -> cubic
        (x0, y0), (cx, cy), (x, y) = self.cur, c, p
        self.ops.append(f"{x0 + 2 / 3 * (cx - x0):.1f} {y0 + 2 / 3 * (cy - y0):.1f} "
                        f"{x + 2 / 3 * (cx - x):.1f} {y + 2 / 3 * (cy - y):.1f} {x:g} {y:g} c")
        self.cur = p

    def curveTo(self, c1, c2, p):
        self.ops.append(f"{c1[0]:g} {c1[1]:g} {c2[0]:g} {c2[1]:g} {p[0]:g} {p[1]:g} c")
        self.cur = p

    def closePath(self):
        self.ops.append("h")

    def endPath(self):
        pass


def _glyph_xobject(doc: pymupdf.Document, font: KhmerFont, gid: int) -> tuple[str, int] | None:
    """Shared Form XObject drawing one glyph in font units (None if blank)."""
    name = f"G{font.digest}_{gid:04X}"
    reg = _catalog_ref(doc, "KhmerPDFGlyphs")
    if reg is None:
        reg = _new_obj(doc, "<<>>")
        doc.xref_set_key(doc.pdf_catalog(), "KhmerPDFGlyphs", f"{reg} 0 R")
    kind, val = doc.xref_get_key(reg, name)
    if kind == "xref":
        return name, int(val.split()[0])
    if kind == "null" and doc.xref_get_key(reg, "_blank_" + name)[0] != "null":
        return None
    pen = _PathPen()
    font.hb_font.draw_glyph_with_pen(gid, pen)
    if not pen.ops:
        doc.xref_set_key(reg, "_blank_" + name, "true")
        return None
    e = font.hb_font.get_glyph_extents(gid)
    bbox = f"[{e.x_bearing - 10} {e.y_bearing + e.height - 10} {e.x_bearing + e.width + 10} {e.y_bearing + 10}]"
    x = _new_obj(doc, f"<< /Type /XObject /Subtype /Form /BBox {bbox} >>",
                 ("\n".join(pen.ops) + "\nf\n").encode())
    doc.xref_set_key(reg, name, f"{x} 0 R")
    return name, x


# --------------------------------------------------------------------------
# Writing text
# --------------------------------------------------------------------------
def layout(text: str, font: KhmerFont, size: float, width: float | None) -> list[str]:
    if width is None:
        return text.split("\n")
    return wrap(text, width, lambda s: font.width(s, size))


def _write_line(page: pymupdf.Page, font: KhmerFont, text: str, x: float, baseline: float,
                size: float, color: tuple[float, float, float], to_pdf: pymupdf.Matrix) -> pymupdf.Rect:
    """Write one shaped line as its own Form XObject. (x, baseline) are MuPDF
    (top-left origin) coordinates on the unrotated page."""
    doc = page.parent
    glyphs = font.shape(text)
    scale = size / font.upem
    glyph_res: dict[str, int] = {}
    ops = ["q", f"{color[0]:.3f} {color[1]:.3f} {color[2]:.3f} rg"]

    # 1. visible glyph outlines
    pen = 0
    for g in glyphs:
        gx = glyph = _glyph_xobject(doc, font, g.gid)
        if glyph:
            name, gx = glyph
            glyph_res[name] = gx
            p = pymupdf.Point(x + (pen + g.x_offset) * scale, baseline - g.y_offset * scale) * to_pdf
            ops.append(f"q {scale:.6f} 0 0 {scale:.6f} {p.x:.3f} {p.y:.3f} cm /{name} Do Q")
        pen += g.x_advance
    width = pen * scale

    # 2. invisible text: ONE run for the whole line, horizontally scaled so it
    #    spans the shaped width. Per-cluster runs line up highlights more
    #    precisely, but PDFium (Chrome/Edge) drops some single-character runs;
    #    a single run per line is the structure every viewer handles.
    chars = [c for c in text if c not in ZERO_WIDTH and ord(c) <= 0xFFFF]
    if chars and width > 0:
        p = pymupdf.Point(x, baseline) * to_pdf
        tz = width / (len(chars) * TEXT_DW / 1000 * size) * 100
        ops += ["BT", "3 Tr", f"/KT {size:.2f} Tf", f"{tz:.3f} Tz",
                f"1 0 0 1 {p.x:.3f} {p.y:.3f} Tm",
                f"<{''.join(f'{ord(c):04X}' for c in chars)}> Tj", "ET"]
    ops += ["ET", "Q"]

    top = baseline - font.ascent(size)
    rect = pymupdf.Rect(x, top, x + max(width, 1), top + font.line_height(size))
    form = _new_obj(doc, (
        "<< /Type /XObject /Subtype /Form /BBox [-100000 -100000 100000 100000] "
        f"/KhmerPDFLine true /KhmerPDFText {_hex16(text)} /KhmerPDFFont {_hex16(font.name)} "
        f"/KhmerPDFSize {size:g} /KhmerPDFColor [{color[0]:.4f} {color[1]:.4f} {color[2]:.4f}] "
        f"/KhmerPDFRect [{rect.x0:.2f} {rect.y0:.2f} {rect.x1:.2f} {rect.y1:.2f}] "
        f"/KhmerPDFBaseline {baseline:.2f} >>"), ("\n".join(ops) + "\n").encode())
    _set_resource(form, doc, "Font", "KT", _text_font(doc))
    for name, gx in glyph_res.items():
        _set_resource(form, doc, "XObject", name, gx)

    # invoke it from the page in its own small content stream
    lname = f"KL{form}"
    _set_resource(page, doc, "XObject", lname, form)
    page.wrap_contents()  # isolate existing graphics state with q ... Q
    inv = _new_obj(doc, "<<>>", f"q /{lname} Do Q\n".encode())
    refs = page.get_contents() + [inv]
    doc.xref_set_key(page.xref, "Contents", "[" + " ".join(f"{r} 0 R" for r in refs) + "]")
    return rect


def insert_khmer_text(page: pymupdf.Page, rect: pymupdf.Rect, text: str,
                      font: KhmerFont | None = None, size: float = 12,
                      color: tuple[float, float, float] = (0, 0, 0),
                      align: str = "left", wrap_text: bool = True,
                      baseline: float | None = None) -> pymupdf.Rect:
    """Write shaped, searchable Khmer text into `rect` (page coordinates as
    displayed). Returns the rect covered by the text."""
    font = font or default_font()
    rect = pymupdf.Rect(rect)
    rotation = page.rotation
    if rotation:
        rect = rect * page.derotation_matrix
        page.set_rotation(0)
    try:
        to_pdf = ~page.transformation_matrix
        lines = layout(text, font, size, rect.width if wrap_text else None)
        y = baseline if baseline is not None else rect.y0 + font.ascent(size)
        used = pymupdf.Rect()
        for line in lines:
            w = font.width(line, size)
            x = rect.x0
            if align == "center":
                x = rect.x0 + (rect.width - w) / 2
            elif align == "right":
                x = rect.x1 - w
            if line.strip(" " + ZERO_WIDTH):
                used |= _write_line(page, font, line, x, y, size, color, to_pdf)
            y += font.line_height(size)
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
    garbled: bool        # the PDF's text for this line is broken and could not be recovered
    xref: int = 0        # Form XObject of a line written by this app (0 = foreign text)
    original: str = ""   # what the PDF's own text layer says (may be broken)
    recovered: bool = False  # text was rebuilt from the glyphs with a reference font


def _own_lines(page: pymupdf.Page) -> list[TextLine]:
    doc = page.parent
    out = []
    for xref, *_ in page.get_xobjects():
        if doc.xref_get_key(xref, "KhmerPDFLine")[1] != "true":
            continue
        def g(k, x=xref):  # PyMuPDF returns PDF strings already decoded
            return doc.xref_get_key(x, k)[1]
        r = pymupdf.Rect([float(v) for v in g("KhmerPDFRect").strip("[]").split()])
        c = tuple(float(v) for v in g("KhmerPDFColor").strip("[]").split())
        if page.rotation:
            r = r * page.rotation_matrix
        out.append(TextLine(rect=r, text=g("KhmerPDFText"), size=float(g("KhmerPDFSize")),
                            font=g("KhmerPDFFont"), color=c,
                            baseline=float(g("KhmerPDFBaseline")), garbled=False, xref=xref))
    return out


def text_lines(page: pymupdf.Page) -> list[TextLine]:
    """Lines written by this app (exact text/font/size) + other text on the page.
    Other text is recovered from its glyphs when the PDF's own text layer is
    broken and a matching reference font is available (see recover.py)."""
    from .recover import find_lines, looks_broken

    own = _own_lines(page)
    found = []
    for f in find_lines(page, skip_fonts=(TEXT_FONT,)):
        centre = (f.rect.tl + f.rect.br) / 2
        if any(centre in o.rect for o in own):
            continue
        found.append(TextLine(rect=f.rect, text=f.text, size=f.size, font=f.font, color=f.color,
                              baseline=f.baseline, garbled=not f.recovered and looks_broken(f.text),
                              original=f.original, recovered=f.recovered))
    return own + found


def delete_line(page: pymupdf.Page, line: TextLine) -> None:
    doc = page.parent
    if line.xref:
        # Empty the line's Form XObject: exact, and leaves other content alone.
        doc.update_stream(line.xref, b"")
        doc.xref_set_key(line.xref, "KhmerPDFLine", "false")
        return
    r = pymupdf.Rect(line.rect)
    # Khmer stacks are tall and overlap neighbouring lines; redact a slightly
    # narrower band so we don't eat glyphs from lines above/below.
    pad = r.height * 0.15
    page.add_redact_annot(pymupdf.Rect(r.x0, r.y0 + pad, r.x1, r.y1 - pad))
    page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE,
                          graphics=pymupdf.PDF_REDACT_LINE_ART_NONE)


def replace_text(page: pymupdf.Page, line: TextLine, new_text: str,
                 font: KhmerFont | None = None, size: float | None = None,
                 color: tuple[float, float, float] | None = None) -> pymupdf.Rect:
    """Remove a line of text and write `new_text` on the same baseline."""
    delete_line(page, line)
    if not new_text.strip():
        return pymupdf.Rect(line.rect)
    target = pymupdf.Rect(line.rect.x0, line.rect.y0, page.rect.x1 - 18, line.rect.y1)
    return insert_khmer_text(page, target, new_text, font=font, size=size or line.size,
                             color=color or line.color, wrap_text=False, baseline=line.baseline)


def search(page: pymupdf.Page, query: str) -> list[pymupdf.Rect]:
    """Find `query` on the page; one rect per match.
    MuPDF gives Khmer combining marks zero width, which splits a single match
    into several touching pieces. Merge pieces on the same line."""
    pieces = sorted(page.search_for(query.replace(ZERO_WIDTH[0], "")), key=lambda r: (round(r.y0), r.x0))
    merged: list[pymupdf.Rect] = []
    for r in pieces:
        last = merged[-1] if merged else None
        if last and abs(last.y0 - r.y0) < 1 and 0 <= r.x0 - last.x1 < r.height:
            merged[-1] = last | r  # Rect |= does not mutate the list item
        else:
            merged.append(pymupdf.Rect(r))
    return merged
