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
import secrets
from dataclasses import dataclass, field
from dataclasses import replace as dataclass_replace
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
        """Shape a line. Khmer and non-Khmer runs are shaped separately with
        explicit scripts: HarfBuzz guesses ONE script per buffer from the
        first letter, so a line starting with an English word would otherwise
        get no Khmer shaping (broken subscripts). Clusters index into `text`."""
        if not text:
            return []
        cps = [ord(c) for c in text]
        out: list[Glyph] = []
        for start, end, khmer in _script_runs(text):
            buf = hb.Buffer()
            buf.add_codepoints(cps, start, end - start)  # whole line as context
            buf.direction = "ltr"
            buf.script = "Khmr" if khmer else "Latn"
            buf.language = "km" if khmer else "en"
            hb.shape(self.hb_font, buf, {"kern": True, "liga": True})
            out += [Glyph(i.codepoint, i.cluster, p.x_advance, p.x_offset, p.y_offset)
                    for i, p in zip(buf.glyph_infos, buf.glyph_positions)]
        return out

    def width(self, text: str, size: float) -> float:
        return sum(g.x_advance for g in self.shape(text)) * size / self.upem

    def line_height(self, size: float) -> float:
        return (self.ascender - self.descender + self.line_gap) * size / self.upem * LINE_SPACING

    def ascent(self, size: float) -> float:
        return self.ascender * size / self.upem


def _is_khmer(ch: str) -> bool:
    return "\u1780" <= ch <= "\u17ff" or "\u19e0" <= ch <= "\u19ff"


_NEUTRAL = set(" \t\xa0\u200b\u200c\u200d\u2060\ufeff.,;:!?()[]{}\"'«»-–—/\\%0123456789")


def _script_runs(text: str) -> list[tuple[int, int, bool]]:
    """(start, end, is_khmer) runs. Spaces, digits and punctuation join the
    run they follow (or the next one at the start of the line)."""
    runs: list[list] = []
    for i, ch in enumerate(text):
        k = None if ch in _NEUTRAL else _is_khmer(ch)
        if not runs:
            runs.append([i, i + 1, k])
        elif k is None or runs[-1][2] is None or k == runs[-1][2]:
            runs[-1][1] = i + 1
            if runs[-1][2] is None:
                runs[-1][2] = k
        else:
            runs.append([i, i + 1, k])
    return [(a, b, bool(k)) for a, b, k in runs]


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


FIT_RANGE = 0.15     # fit an edited line to the original width if within ±15%
MAX_SQUEEZE = 0.08   # ...using at most ±8% horizontal scaling (beyond word spacing)


def _fit_plan(glyphs: list[Glyph], text: str, scale: float, natural: float,
              target: float | None, justify: bool = False) -> tuple[float, dict[int, float]]:
    """How to make a line exactly `target` wide, like the justified line it
    replaces: (horizontal scale, extra advance per space glyph index).
    Word spacing changes first (spaces can shrink to 30%); a small horizontal
    scale takes up the rest. No fitting when the text changed length a lot.
    `justify`: a line of a justified paragraph - spaces, and invisible word
    breaks, grow as much as needed."""
    if not target or natural <= 0:
        return 1.0, {}
    gaps = (" ", "\xa0", ZERO_WIDTH[0]) if justify else (" ", "\xa0")
    spaces = [i for i, g in enumerate(glyphs) if text[g.cluster:g.cluster + 1] in gaps]
    if justify and spaces and target > natural:
        each = (target - natural) / len(spaces)
        return 1.0, {i: each for i in spaces}
    if abs(target - natural) / target > FIT_RANGE:
        return 1.0, {}
    extra = target - natural
    per_space: dict[int, float] = {}
    if spaces:
        each = extra / len(spaces)
        min_each = -0.7 * min(glyphs[i].x_advance for i in spaces) * scale
        each = max(each, min_each)
        per_space = {i: each for i in spaces}
        extra -= each * len(spaces)
    sx = target / (target - extra) if extra else 1.0
    sx = min(max(sx, 1 - MAX_SQUEEZE), 1 + MAX_SQUEEZE)
    return sx, per_space


def _write_line(page: pymupdf.Page, font: KhmerFont, text: str, x: float, baseline: float,
                size: float, color: tuple[float, float, float], to_pdf: pymupdf.Matrix,
                fit_width: float | None = None, visible: bool = True,
                meta: dict[str, str] | None = None, justify: bool = False) -> pymupdf.Rect:
    """Write one shaped line as its own Form XObject. (x, baseline) are MuPDF
    (top-left origin) coordinates on the unrotated page. With `fit_width`,
    the line is stretched/squeezed to that width (see _fit_plan).
    `visible=False` writes only the invisible text layer (OCR of a scan);
    `meta` adds keys to the line's dictionary (paragraph, OCR)."""
    doc = page.parent
    glyphs = font.shape(text)
    scale = size / font.upem
    glyph_res: dict[str, int] = {}
    ops = ["q", f"{color[0]:.3f} {color[1]:.3f} {color[2]:.3f} rg"]
    natural = sum(g.x_advance for g in glyphs) * scale
    sx, per_space = _fit_plan(glyphs, text, scale, natural, fit_width, justify)

    # 1. visible glyph outlines
    pen = 0.0  # in points, already including spacing adjustments
    for i, g in enumerate(glyphs):
        glyph = _glyph_xobject(doc, font, g.gid) if visible else None
        if glyph:
            name, gx = glyph
            glyph_res[name] = gx
            p = pymupdf.Point(x + (pen + g.x_offset * scale) * sx, baseline - g.y_offset * scale) * to_pdf
            ops.append(f"q {scale * sx:.6f} 0 0 {scale:.6f} {p.x:.3f} {p.y:.3f} cm /{name} Do Q")
        pen += g.x_advance * scale + per_space.get(i, 0.0)
    width = pen * sx
    if not visible and fit_width:
        width = fit_width  # nothing drawn: the text layer simply spans the measured width

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
        f"/KhmerPDFBaseline {baseline:.2f} "
        + "".join(f"/{k} {v} " for k, v in (meta or {}).items()) + ">>"), ("\n".join(ops) + "\n").encode())
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
                      baseline: float | None = None,
                      fit_width: float | None = None,
                      line_step: float | None = None, justify: bool = False,
                      visible: bool = True, meta: dict[str, str] | None = None) -> pymupdf.Rect:
    """Write shaped, searchable Khmer text into `rect` (page coordinates as
    displayed). Returns the rect covered by the text.
    Wrapped text is one paragraph: its lines remember the whole text, width,
    line spacing and justification, so it can be edited, moved and resized
    as a block. `justify` stretches every line but the last of each paragraph
    to the full width; `line_step` is the baseline-to-baseline distance."""
    font = font or default_font()
    rect = pymupdf.Rect(rect)
    rotation = page.rotation
    if rotation:
        rect = rect * page.derotation_matrix
        page.set_rotation(0)
    try:
        to_pdf = ~page.transformation_matrix
        step = line_step or font.line_height(size)
        lines: list[tuple[str, bool]] = []   # (line, ends a paragraph)
        for part in text.split("\n"):
            wrapped = layout(part, font, size, rect.width if wrap_text else None)
            lines += [(ln, i == len(wrapped) - 1) for i, ln in enumerate(wrapped)]
        if wrap_text:
            meta = dict(meta or {}, KhmerPDFBlock=f"({secrets.token_hex(6)})",
                        KhmerPDFBlockText=_hex16(text), KhmerPDFBlockWidth=f"{rect.width:.2f}",
                        KhmerPDFStep=f"{step:.2f}", KhmerPDFJustify="true" if justify else "false")
        y = baseline if baseline is not None else rect.y0 + font.ascent(size)
        used = pymupdf.Rect()
        for line, last in lines:
            stretch = justify and not last
            target = rect.width if stretch else (fit_width if len(lines) == 1 else None)
            w = font.width(line, size)
            if target and (stretch or abs(target - w) / target <= FIT_RANGE):
                w = target  # the line will be fitted to this width
            x = rect.x0
            if align == "center":
                x = rect.x0 + (rect.width - w) / 2
            elif align == "right":
                x = rect.x1 - w
            if line.strip(" " + ZERO_WIDTH):
                used |= _write_line(page, font, line, x, y, size, color, to_pdf, target, visible, meta, stretch)
            y += step
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
    approx: bool = False     # best-effort repair (font unknown): may contain mistakes
    ocr: bool = False        # recognised by OCR (scan): may contain mistakes
    legacy: bool = False     # converted from a legacy (Limon) font encoding
    trailing_space: bool = False  # the line ended with a space (joins paragraphs)
    block: str = ""          # paragraph id of lines written together by this app
    block_text: str = ""     # ...that paragraph's whole text, width, spacing, justification
    block_width: float = 0.0
    block_step: float = 0.0
    block_justify: bool = False

    @property
    def needs_check(self) -> bool:
        """Text that may contain mistakes (OCR or rule-based repair)."""
        return self.approx or self.ocr


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
        block = g("KhmerPDFBlock")
        ln = TextLine(rect=r, text=g("KhmerPDFText"), size=float(g("KhmerPDFSize")),
                      font=g("KhmerPDFFont"), color=c,
                      baseline=float(g("KhmerPDFBaseline")), garbled=False, xref=xref,
                      ocr=g("KhmerPDFOCR") == "true")
        if block and block != "null":
            ln.block, ln.block_text = block, g("KhmerPDFBlockText")
            ln.block_width, ln.block_step = float(g("KhmerPDFBlockWidth")), float(g("KhmerPDFStep"))
            ln.block_justify = g("KhmerPDFJustify") == "true"
        out.append(ln)
    return out


def text_lines(page: pymupdf.Page, rules=None) -> list[TextLine]:
    """Lines written by this app (exact text/font/size) + other text on the page.
    Other text is recovered from its glyphs when the PDF's own text layer is
    broken and a matching reference font is available (see recover.py).
    `rules`: the page's ruling lines (recover.page_rules), if already known."""
    from .recover import find_lines, looks_broken

    own = _own_lines(page)
    found = []
    for f in find_lines(page, skip_fonts=(TEXT_FONT,), rules=rules):
        centre = (f.rect.tl + f.rect.br) / 2
        if any(centre in o.rect for o in own):
            continue
        found.append(TextLine(rect=f.rect, text=f.text, size=f.size, font=f.font, color=f.color,
                              baseline=f.baseline,
                              garbled=not (f.recovered or f.approx) and looks_broken(f.text),
                              original=f.original, recovered=f.recovered, approx=f.approx,
                              legacy=f.legacy, trailing_space=f.trailing_space))
    return own + found


def _background(page: pymupdf.Page, rect: pymupdf.Rect) -> tuple[float, float, float]:
    """The paper colour around `rect` (median of a thin ring around it)."""
    try:
        ring = pymupdf.Rect(rect.x0 - 3, rect.y0 - 3, rect.x1 + 3, rect.y1 + 3) & page.rect
        pix = page.get_pixmap(clip=ring, dpi=72, alpha=False, colorspace=pymupdf.csRGB)
        inner = pymupdf.IRect(3, 3, max(pix.width - 3, 3), max(pix.height - 3, 3))
        px = [pix.pixel(x, y) for y in range(pix.height) for x in range(pix.width)
              if not (inner.x0 <= x < inner.x1 and inner.y0 <= y < inner.y1)]
        if not px:
            return (1.0, 1.0, 1.0)
        return tuple(sorted(p[i] for p in px)[len(px) // 2] / 255 for i in range(3))
    except Exception:
        return (1.0, 1.0, 1.0)


def delete_line(page: pymupdf.Page, line: TextLine) -> None:
    doc = page.parent
    if line.xref:
        # Empty the line's Form XObject: exact, and leaves other content alone.
        doc.update_stream(line.xref, b"")
        doc.xref_set_key(line.xref, "KhmerPDFLine", "false")
        if not line.ocr:
            return
        # OCR text: the visible text is in the scan (or in a broken text layer)
        # underneath. Paint it out with the paper colour.
        r = pymupdf.Rect(line.rect.x0 - 1, line.rect.y0 - 1, line.rect.x1 + 1, line.rect.y1 + 1)
        page.add_redact_annot(r, fill=_background(page, r))
        page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_PIXELS,
                              graphics=pymupdf.PDF_REDACT_LINE_ART_NONE)
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
                 color: tuple[float, float, float] | None = None, fit: bool = True,
                 dx: float = 0.0, dy: float = 0.0) -> pymupdf.Rect:
    """Remove a line of text and write `new_text` on the same baseline (or
    moved by dx, dy). `fit=False` when the user changed the font or size on
    purpose."""
    delete_line(page, line)
    return _draw_line(page, line, new_text, font, size, color, fit, dx, dy)


def _draw_line(page: pymupdf.Page, line: TextLine, new_text: str, font: KhmerFont | None, size: float | None,
               color: tuple[float, float, float] | None, fit: bool, dx: float, dy: float) -> pymupdf.Rect:
    if not new_text.strip():
        return pymupdf.Rect(line.rect)
    target = pymupdf.Rect(line.rect.x0 + dx, line.rect.y0 + dy, page.rect.x1 - 18, line.rect.y1 + dy)
    # Keep the paragraph's shape: an edited line ends where the original did
    # (justified text), unless the new text is much longer or shorter.
    return insert_khmer_text(page, target, new_text, font=font, size=size or line.size,
                             color=color or line.color, wrap_text=False, baseline=line.baseline + dy,
                             fit_width=line.rect.width if fit else None)


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


# --------------------------------------------------------------------------
# Paragraphs: edit, move, resize and delete several lines as one block
# --------------------------------------------------------------------------
@dataclass
class Paragraph:
    lines: list[TextLine]
    text: str
    x: float            # left edge of the text block
    width: float        # wrap width
    step: float         # baseline-to-baseline distance
    justify: bool
    rect: pymupdf.Rect = field(default_factory=pymupdf.Rect)

    def __post_init__(self):
        if self.rect.is_empty:
            self.rect = pymupdf.Rect(self.lines[0].rect)
            for ln in self.lines[1:]:
                self.rect |= ln.rect

    @property
    def first(self) -> TextLine:
        return self.lines[0]

    @property
    def garbled(self) -> bool:
        return any(ln.garbled for ln in self.lines)

    @property
    def needs_check(self) -> bool:
        return any(ln.needs_check for ln in self.lines)


def _bare(s: str) -> str:
    return re.sub(r"[\s" + ZERO_WIDTH + "]", "", s)


def _joiner(a: TextLine, b: TextLine) -> str:
    """What stood between two wrapped lines: Khmer wraps between words without
    a space, unless the line visibly ended with one."""
    if a.trailing_space or not (a.text and b.text and _is_khmer(a.text[-1]) and _is_khmer(b.text[0])):
        return " "
    return ZERO_WIDTH[0]  # invisible word break: the text wraps there again


def _usual_steps(lines: list[TextLine]) -> dict[tuple[str, float], float]:
    """The page's usual line spacing per font and size: the smaller common
    distance between a line and the one below it. A clearly bigger distance
    is space between paragraphs."""
    by_style: dict[tuple[str, float], list[float]] = {}
    ordered = sorted(lines, key=lambda ln: ln.baseline)
    for i, a in enumerate(ordered):
        key = (norm_font_name(a.font), round(a.size * 2) / 2)
        for b in ordered[i + 1:]:
            step = b.baseline - a.baseline
            if step > 2.6 * a.size:
                break
            if (step >= 0.9 * a.size and b.rect.x0 < a.rect.x1 and b.rect.x1 > a.rect.x0
                    and (norm_font_name(b.font), round(b.size * 2) / 2) == key):
                by_style.setdefault(key, []).append(step)
                break
    return {k: sorted(v)[len(v) // 4] for k, v in by_style.items()}


def _continues(p: list[TextLine], ln: TextLine, rules=None, cells: set[int] = frozenset(),
               usual: dict | None = None) -> float | None:
    """Baseline step if `ln` is the next line of the paragraph `p`, else None.
    Conservative: same style, regular spacing, aligned left edges, and the
    previous line ran (nearly) to the paragraph's right edge. Never across a
    ruling line, and never between two table cells."""
    last = p[-1]
    s = last.size
    if ln.garbled or last.garbled or ln.ocr != last.ocr:
        return None
    if id(last) in cells and id(ln) in cells:
        return None
    if rules is not None and rules.horizontal_between(last.baseline, ln.baseline - 0.5 * ln.size,
                                                      min(last.rect.x0, ln.rect.x0), max(last.rect.x1, ln.rect.x1)):
        return None
    if norm_font_name(ln.font) != norm_font_name(last.font) or abs(ln.size - s) > 0.6:
        return None
    if sum(abs(a - b) for a, b in zip(ln.color, last.color)) > 0.15:
        return None
    step = ln.baseline - last.baseline
    if not 0.9 * s <= step <= 2.6 * s:
        return None
    typical = (usual or {}).get((norm_font_name(last.font), round(s * 2) / 2))
    if typical and step > 1.25 * typical:                 # space between paragraphs
        return None
    if len(p) > 1 and abs(step - (last.baseline - p[-2].baseline)) > 0.15 * step:
        return None
    if len(p) == 1:
        if not -6 * s <= ln.rect.x0 - last.rect.x0 <= s:   # the first line may be indented
            return None
    elif abs(ln.rect.x0 - p[1].rect.x0) > s:
        return None
    # the line above ended early, though the next line's first word would
    # have fitted: the paragraph ended there (Khmer words are long, so a
    # ragged line can end well short of the edge)
    right = max(max(x.rect.x1 for x in p), ln.rect.x1)
    first = re.split(r"[ \u200b]", ln.text.strip(), maxsplit=1)[0]
    fits = min(ln.rect.width * len(first) / max(len(ln.text), 1), 6 * s)
    if right - last.rect.x1 > fits + s:
        return None
    return step


def _table_cells(lines: list[TextLine]) -> set[int]:
    """Lines that look like table cells: short, with other text beside them on
    the same baseline. (Long lines side by side are columns of prose.)"""
    out = set()
    for a in lines:
        if a.rect.width >= 20 * a.size:
            continue
        if any(b is not a and abs(b.baseline - a.baseline) < 1 and (b.rect.x0 >= a.rect.x1 or b.rect.x1 <= a.rect.x0)
               for b in lines):
            out.add(id(a))
    return out


def paragraphs(lines: list[TextLine], page: pymupdf.Page | None = None, rules=None) -> list[Paragraph]:
    """Group a page's lines into paragraphs. Lines this app wrote together keep
    their exact paragraph; other lines are joined when they clearly flow on.
    With the page (or its ruling lines, recover.page_rules), table borders
    keep cells apart."""
    if rules is None and page is not None:
        from .recover import page_rules
        rules = page_rules(page)
    cells = _table_cells(lines)
    usual = _usual_steps([ln for ln in lines if not ln.block])
    out: list[Paragraph] = []
    rest: list[TextLine] = []
    blocks: dict[str, list[TextLine]] = {}
    for ln in lines:
        (blocks.setdefault(ln.block, []) if ln.block else rest).append(ln)
    for group in blocks.values():
        group.sort(key=lambda ln: ln.baseline)
        b = group[0]
        if _bare(b.block_text) == _bare("".join(ln.text for ln in group)):
            out.append(Paragraph(group, b.block_text, min(ln.rect.x0 for ln in group),
                                 b.block_width, b.block_step, b.block_justify))
        else:  # some of its lines were edited one by one since
            rest += group
    open_: list[list[TextLine]] = []
    for ln in sorted(rest, key=lambda ln: (ln.baseline, ln.rect.x0)):
        best = None
        for p in open_:
            step = _continues(p, ln, rules, cells, usual)
            if step is not None and (best is None or step < best[0]):
                best = (step, p)
        if best:
            best[1].append(ln)
        else:
            open_.append([ln])
    for p in open_:
        text = p[0].text
        for a, b in zip(p, p[1:]):
            text += _joiner(a, b) + b.text
        x = min(ln.rect.x0 for ln in p[1:]) if len(p) > 1 else p[0].rect.x0
        right = max(ln.rect.x1 for ln in p)
        step = (p[-1].baseline - p[0].baseline) / (len(p) - 1) if len(p) > 1 else 0.0
        justify = len(p) >= 3 and all(right - ln.rect.x1 <= 0.5 * ln.size for ln in p[:-1])
        out.append(Paragraph(p, text, x, right - x, step, justify))
    out.sort(key=lambda para: (para.first.baseline, para.x))
    return out


def delete_paragraph(page: pymupdf.Page, para: Paragraph) -> None:
    for ln in para.lines:
        delete_line(page, ln)


def write_paragraph(page: pymupdf.Page, para: Paragraph, text: str, font: KhmerFont | None = None,
                    size: float | None = None, color: tuple[float, float, float] | None = None,
                    dx: float = 0.0, dy: float = 0.0, width: float | None = None,
                    scale: float = 1.0) -> pymupdf.Rect:
    """Replace a paragraph with `text`, re-wrapped inside its own width - or
    moved by (dx, dy), re-wrapped to a new `width`, or scaled (font size, width
    and spacing) by `scale`. A one-line paragraph that isn't resized stays one
    line and keeps its width, like replace_text."""
    delete_paragraph(page, para)
    return draw_paragraph(page, para, text, font, size, color, dx, dy, width, scale)


def draw_paragraph(page: pymupdf.Page, para: Paragraph, text: str, font: KhmerFont | None = None,
                   size: float | None = None, color: tuple[float, float, float] | None = None,
                   dx: float = 0.0, dy: float = 0.0, width: float | None = None,
                   scale: float = 1.0) -> pymupdf.Rect:
    """write_paragraph without removing the old text first (it already was)."""
    first = para.first
    size = (size or first.size) * scale
    font = font or default_font()
    if not text.strip():
        return pymupdf.Rect(para.rect)
    x, baseline = para.x + dx, first.baseline + dy
    if len(para.lines) == 1 and "\n" not in text and width is None and scale == 1.0:
        target = pymupdf.Rect(x, first.rect.y0 + dy, page.rect.x1 - 18, first.rect.y1 + dy)
        return insert_khmer_text(page, target, text, font=font, size=size, color=color or first.color,
                                 wrap_text=False, baseline=baseline,
                                 fit_width=first.rect.width if size == first.size else None)
    w = width if width is not None else para.width * scale
    step = para.step * size / first.size if para.step else None  # spacing follows the font size
    target = pymupdf.Rect(x, first.rect.y0 + dy, x + max(w, size), page.rect.y1)
    return insert_khmer_text(page, target, text, font=font, size=size, color=color or first.color,
                             baseline=baseline, line_step=step, justify=para.justify)


# --------------------------------------------------------------------------
# OCR: an invisible, searchable text layer over scanned text
# --------------------------------------------------------------------------
def _overlap(a: pymupdf.Rect, b: pymupdf.Rect) -> float:
    inter = pymupdf.Rect(a) & b
    small = min(abs(a), abs(b))
    return abs(inter) / small if small > 0 and not inter.is_empty else 0.0


def add_ocr_layer(page: pymupdf.Page, ocr_lines: list) -> int:
    """Add recognised lines (ocr.OcrLine) as invisible text, so a scan becomes
    searchable and its lines editable. Earlier OCR of this page is replaced.
    Only text that needs it is added: on a scan, and over text the PDF has
    broken - never over text it already has right, and never over pictures
    (photos, logos), which stay pictures. Returns the number of lines added."""
    from .ocr import looks_like_text, page_images
    for ln in _own_lines(page):
        if ln.ocr:
            delete_line(page, dataclass_replace(ln, ocr=False))  # just the invisible layer
    lines = text_lines(page)
    good = [ln for ln in lines if not (ln.garbled or ln.needs_check)]
    broken = [ln for ln in lines if ln.garbled or ln.approx]
    scans, pictures = page_images(page)
    font = default_font()
    n = 0
    heights = sorted(o.rect.height for o in ocr_lines) or [0]
    for o in ocr_lines:
        text = o.text.strip()
        if not looks_like_text(text) or o.rect.height < max(4.0, 0.4 * heights[len(heights) // 2]):
            continue  # junk: symbols, or specks too small to be a line of text
        if any(_overlap(o.rect, r) > 0.3 for r in pictures + [g.rect for g in good]):
            continue
        if not any(_overlap(o.rect, r) > 0.5 for r in scans) and not any(_overlap(o.rect, b.rect) > 0.3 for b in broken):
            continue
        # size: the line height Tesseract saw, refined by the width the text spans
        size = o.height / (font.line_height(1) / LINE_SPACING) if o.height else o.rect.height
        natural = font.width(text, 1)
        if natural > 0 and 0.6 * size <= o.rect.width / natural <= 1.4 * size:
            size = o.rect.width / natural
        size = round(size * 2) / 2 or 1
        insert_khmer_text(page, pymupdf.Rect(o.rect.x0, o.rect.y0, page.rect.x1, o.rect.y1), text,
                          font=font, size=size, wrap_text=False, baseline=o.baseline,
                          fit_width=o.rect.width, visible=False, meta={"KhmerPDFOCR": "true"})
        n += 1
    return n


# --------------------------------------------------------------------------
# Legacy fonts
# --------------------------------------------------------------------------
def convert_legacy(page: pymupdf.Page, font: KhmerFont | None = None) -> int:
    """Rewrite every Limon-font line of the page as Unicode Khmer (searchable,
    copyable), in place and at the same width. Returns the number of lines."""
    lines = [ln for ln in text_lines(page) if ln.legacy]
    for ln in lines:
        replace_text(page, ln, ln.text, font=font)
    return len(lines)


# --------------------------------------------------------------------------
# Pictures: move, resize, replace and delete
# --------------------------------------------------------------------------
@dataclass
class Picture:
    xref: int            # the image object
    rect: pymupdf.Rect
    name: str            # its resource name where it is drawn
    owner: int           # 0 = drawn by the page itself, else by this Form XObject
    nth: int             # which placement of the image on the page
    behind_text: bool = False  # text is drawn over it (e.g. a background)


def pictures(page: pymupdf.Page, lines: list[TextLine] | None = None) -> list[Picture]:
    """The page's pictures (photos, logos...). A scan covering the page is the
    page itself, not a picture."""
    from .ocr import SCAN_COVER
    area = abs(page.rect)
    names: dict[int, tuple[str, int]] = {}
    for im in page.get_images(full=True):
        names.setdefault(im[0], (im[7], im[9]))
    lines = text_lines(page) if lines is None else lines
    seen: dict[int, int] = {}
    out = []
    digests = _image_digests(page.parent, names)
    page._image_info = None  # PyMuPDF caches this per page object, also across our edits
    for info in page.get_image_info(hashes=True):
        xref = digests.get(info.get("digest"), 0)
        if not xref:
            continue  # inline image: nothing to point at
        k = seen[xref] = seen.get(xref, -1) + 1
        r = pymupdf.Rect(info["bbox"])
        vis = r & page.rect
        if vis.is_empty or abs(vis) >= SCAN_COVER * area:
            continue
        name, owner = names[xref]
        out.append(Picture(xref, r, name, owner, k, any(_overlap(vis, ln.rect) > 0.5 for ln in lines)))
    return out


def _point(doc: pymupdf.Document, owner: int, name: str, xref: int) -> None:
    """Make /Resources/XObject/<name> of `owner` (a page or form) point to
    `xref` - in its own copy of the resource dictionaries, which PDF writers
    often share between pages and forms."""
    for key in ("Resources", "Resources/XObject"):
        kind, val = doc.xref_get_key(owner, key)
        if kind == "xref":
            doc.xref_set_key(owner, key, doc.xref_object(int(val.split()[0]), compressed=True))
    doc.xref_set_key(owner, f"Resources/XObject/{name}", f"{xref} 0 R")


def _image_digests(doc: pymupdf.Document, xrefs) -> dict[bytes, int]:
    """Image fingerprint -> xref, to tell which image object each placement
    shows. Decoding an image to fingerprint it is slow: once per document."""
    cache = getattr(doc, "_khmerpdf_digests", None)
    if cache is None:
        cache = {}
        try:
            doc._khmerpdf_digests = cache
        except AttributeError:
            pass
    for x in xrefs:
        if x not in cache:
            try:
                cache[x] = pymupdf.Pixmap(doc, x).digest
            except Exception:
                cache[x] = None
    return {cache[x]: x for x in xrefs if cache[x]}


def _own_form(page: pymupdf.Page, form: int) -> int | None:
    """The Form XObject `form` drawn on this page (directly or inside other
    forms), safe to change: when other pages draw it too (a header logo, say),
    this page gets its own copy of it and of the forms around it."""
    doc = page.parent
    entries = [x for x in page.get_xobjects() if x[0] == form]
    if len(entries) != 1:
        return None  # not on this page, or drawn twice on it
    _, name, invoker, _ = entries[0]
    parent = page.xref if invoker == 0 else _own_form(page, invoker)
    if parent is None:
        return None
    shared = sum(1 for pg in doc for x in pg.get_xobjects() if x[0] == form) > 1
    if not shared and (invoker == 0 or parent == invoker):
        return form
    copy = doc.get_new_xref()
    doc.update_object(copy, doc.xref_object(form, compressed=False))
    doc.update_stream(copy, doc.xref_stream(form))
    _point(doc, parent, name, copy)
    return copy


def remove_picture(page: pymupdf.Page, pic: Picture) -> bool:
    """Take one placement of a picture off the page (the image itself stays
    in the file for other placements). False if it can't be found.
    Each place that draws the image is tried in turn, and kept only if the
    picture at pic.rect is then gone - no guessing which one it was."""
    doc = page.parent

    def count() -> int:
        page._image_info = None
        return sum(1 for i in page.get_image_info()
                   if all(abs(a - b) < 0.5 for a, b in zip(i["bbox"], pic.rect)))

    before = count()
    if not before:
        return False
    for xref, *_, name, _, owner in (im for im in page.get_images(full=True) if im[0] == pic.xref):
        pat = re.compile(rb"/" + re.escape(name.encode()) + rb"\s+Do\b")
        if owner:
            owner = _own_form(page, owner)
            if owner is None:
                continue
        for x in [owner] if owner else page.get_contents():
            data = doc.xref_stream(x) or b""
            for m in pat.finditer(data):
                doc.update_stream(x, data[:m.start()] + data[m.end():])
                if count() < before:
                    return True
                doc.update_stream(x, data)  # not this one
    return False


def move_picture(page: pymupdf.Page, pic: Picture, rect: pymupdf.Rect) -> bool:
    """Move/resize a picture to `rect` (stretched to fill it)."""
    if not remove_picture(page, pic):
        return False
    page.insert_image(rect, xref=pic.xref, keep_proportion=False, overlay=not pic.behind_text)
    return True


def replace_picture(page: pymupdf.Page, pic: Picture, data: bytes) -> bool:
    """Put another image (file contents) in the picture's place, fitted inside
    its box without distortion."""
    if not remove_picture(page, pic):
        return False
    page.insert_image(pic.rect, stream=data, keep_proportion=True, overlay=not pic.behind_text)
    return True


def delete_picture(page: pymupdf.Page, pic: Picture) -> bool:
    return remove_picture(page, pic)


# --------------------------------------------------------------------------
# Editing with room: when text grows, what is below it moves down
# --------------------------------------------------------------------------
def _below(page: pymupdf.Page, old: pymupdf.Rect, size: float,
           edited: list[TextLine]) -> tuple[list, float | None]:
    """What sits below `old` in its column: (paragraphs and pictures that can
    move down, the top of the first thing that can't - a table, a ruling
    line, unreadable text - or None). `edited`: the lines being replaced."""
    from .recover import page_rules
    rules = page_rules(page)
    lines = text_lines(page, rules)
    x0, x1 = old.x0, old.x1
    top = old.y1 - 0.35 * size  # Khmer stacks overlap the next line a little

    def column(r: pymupdf.Rect) -> bool:
        return r.x0 < x1 - 1 and r.x1 > x0 + 1 and r.y0 >= top

    limits = [y for a, b, y in rules.h if a < x1 - 1 and b > x0 + 1 and y >= top]
    limits += [a for a, b, x in rules.v if x0 - 1 <= x <= x1 + 1 and a >= top]
    cells = _table_cells(lines)
    movers: list = []
    gone = {(round(ln.rect.x0, 1), round(ln.baseline, 1)) for ln in edited}
    found = []
    for p in paragraphs(lines, rules=rules):
        rest = [ln for ln in p.lines if (round(ln.rect.x0, 1), round(ln.baseline, 1)) not in gone]
        if len(rest) == len(p.lines):
            found.append(p)
        else:  # a line of this paragraph is being edited: the lines after it move on their own
            found += [Paragraph([ln], ln.text, ln.rect.x0, ln.rect.width, 0.0, False) for ln in rest]
    for p in found:
        if not column(p.rect):
            continue
        if p.garbled or any(id(ln) in cells for ln in p.lines):
            limits.append(p.rect.y0)
        else:
            movers.append(p)
    movers += [pic for pic in pictures(page, lines) if column(pic.rect)]
    limit = min(limits, default=None)
    if limit is not None:
        movers = [m for m in movers if m.rect.y0 < limit]
    return movers, limit


def font_for_pdf(pdf_font: str) -> KhmerFont:
    """The bundled font that is the PDF's font, else the default font."""
    name = match_font(bundled_fonts(), pdf_font)
    return load_font(bundled_fonts()[name]) if name else default_font()


def edit_text(page: pymupdf.Page, target, text: str, font: KhmerFont | None = None,
              size: float | None = None, color: tuple[float, float, float] | None = None,
              fit: bool = True, dx: float = 0.0, dy: float = 0.0,
              font_for=None) -> tuple[pymupdf.Rect, str | None, int]:
    """Replace a line (TextLine) or paragraph with `text`. If it grows - more
    lines, a bigger size - the paragraphs and pictures below it in its column
    move down to make room, like in a word processor (not when the text is
    being moved somewhere else with dx/dy). `font_for(pdf_font_name)` gives
    the font to redraw moved text in. Returns (rect, warning, things moved)."""
    single = isinstance(target, TextLine)
    para = Paragraph([target], target.text, target.rect.x0, target.rect.width, 0.0, False) if single else target
    first = para.first
    font = font or default_font()
    size = size or first.size
    step = para.step * size / first.size if para.step else font.line_height(size)
    n = (len(text.split("\n")) if single
         else sum(len(layout(part, font, size, para.width)) for part in text.split("\n")))
    bottom = first.baseline + dy - font.ascent(size) + (n - 1) * step + font.line_height(size)
    grow = bottom - para.rect.y1
    movers, warning = [], None
    if text.strip() and grow > 0.5 and not (dx or dy):
        movers, limit = _below(page, para.rect, first.size, para.lines)
        lowest = max((m.rect.y1 for m in movers), default=para.rect.y1) + grow
        if limit is not None and lowest > limit + 1:
            warning = ("There isn't room below for the longer text: it now overlaps a table or line below it. "
                       "Make it shorter, or move what's below.")
        elif lowest > page.rect.y1 - 18:
            warning = "The text below was pushed past the bottom of the page."
    # 1. take everything off the page, then 2. draw it again: removing foreign
    #    text works by area, so it must not catch text drawn in the meantime
    delete_line(page, target) if single else delete_paragraph(page, para)
    moved = []
    for m in movers:
        if isinstance(m, Picture):
            if remove_picture(page, m):
                moved.append(m)
        else:
            delete_paragraph(page, m)
            moved.append(m)
    rect = (_draw_line(page, target, text, font, size, color, fit, dx, dy) if single
            else draw_paragraph(page, para, text, font, size, color, dx, dy))
    for m in moved:
        r = pymupdf.Rect(m.rect.x0, m.rect.y0 + grow, m.rect.x1, m.rect.y1 + grow)
        if isinstance(m, Picture):
            page.insert_image(r, xref=m.xref, keep_proportion=False, overlay=not m.behind_text)
        else:
            draw_paragraph(page, m, m.text, font=(font_for or font_for_pdf)(m.first.font), dy=grow)
    return rect, warning, len(moved)
