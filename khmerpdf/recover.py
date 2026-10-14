"""Recover the real Unicode text of Khmer PDFs whose text layer is broken.

PDFs made from Word (and many other tools) with Khmer fonts usually store
Khmer in *visual* order, lose COENG subscripts, and map ligature glyphs to
junk (``េសចក្តីបញចប់``, ``ǒថប័ន``). The glyph IDs in the PDF are still the
font's original glyph IDs, though. So if we have the same font (the Khmer OS
family is bundled), we can build a reverse dictionary with HarfBuzz:

    shape every Khmer syllable pattern  ->  {visual glyph sequence: Unicode}

and decode each line's glyph stream by longest match. Before trusting a
reference font, its outlines are compared with the PDF's embedded subset.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pymupdf
import uharfbuzz as hb

FONT_DIR = Path(__file__).parent / "fonts"

CONS = [chr(c) for c in range(0x1780, 0x17A3)]
INDEP = [chr(c) for c in range(0x17A5, 0x17B4)]
VOWELS = [""] + [chr(c) for c in range(0x17B6, 0x17C6)]
SIGNS = [chr(c) for c in (0x17C6, 0x17C7, 0x17C8, 0x17C9, 0x17CA, 0x17CB, 0x17CC,
                          0x17CD, 0x17CE, 0x17CF, 0x17D0, 0x17D1, 0x17DD)]
SHIFTERS = ["៉", "៊"]           # MUUSIKATOAN, TRIISAP
COMMON_FINAL = ["ំ", "ះ", "់"]  # NIKAHIT, REAHMUK, BANTOC
SUBS = ["្" + c for c in CONS]
SECOND_SUBS = ["្រ", "្វ", "្យ"]  # ្រ ្វ ្យ
OTHER = ([chr(c) for c in range(0x17D4, 0x17DB)] + [chr(c) for c in range(0x17E0, 0x17EA)]
         + [" ", "​"])
KHMER_MARK = re.compile(r"[ា-៓៝]")


def _norm(name: str) -> str:
    """'LDIPBJ+KhmerOSbattambang', 'Khmer OS Battambang Regular' -> 'khmerosbattambang'."""
    from .engine import norm_font_name
    return norm_font_name(name)


# --------------------------------------------------------------------------
# Reverse dictionary per reference font
# --------------------------------------------------------------------------
class GlyphDecoder:
    def __init__(self, path: str):
        self.path = path
        self.font = hb.Font(hb.Face(Path(path).read_bytes()))
        self.seq: dict[tuple[int, ...], str] = {}
        self.single: dict[int, str] = {}
        self._build()
        self.maxlen = max(len(k) for k in self.seq)

    def _shape(self, text: str) -> tuple[int, ...]:
        buf = hb.Buffer()
        buf.add_str(text)
        buf.guess_segment_properties()
        hb.shape(self.font, buf, {})
        return tuple(i.codepoint for i in buf.glyph_infos)

    def _add(self, text: str) -> None:
        g = self._shape(text)
        if 0 not in g and g not in self.seq:  # first (= canonical) spelling wins
            self.seq[g] = text

    def _build(self) -> None:
        add = self._add
        # Canonical order first: register shifter before the vowel (វ៉ា, ស៊ី);
        # legacy spellings (វា៉, សុី) shape to the same glyphs and lose.
        for c in CONS:
            for sh in SHIFTERS:
                for v in VOWELS:
                    add(c + sh + v)
                    for f in COMMON_FINAL:
                        add(c + sh + v + f)
        for c in CONS:
            for s1 in [""] + SUBS:
                for v in VOWELS:
                    add(c + s1 + v)
                if s1:
                    for s2 in SECOND_SUBS:
                        for v in VOWELS:
                            add(c + s1 + s2 + v)
                    for v in ("", "ា", "ុ", "ោ", "េ"):
                        for f in COMMON_FINAL:
                            add(c + s1 + v + f)
            for v in VOWELS:
                for s in SIGNS:
                    add(c + v + s)
        for c in INDEP + OTHER:
            add(c)
            for s in SIGNS:
                add(c + s)
        # Fallback for glyphs met outside a known syllable: what a single glyph
        # stands for when it follows a base consonant.
        for g, t in self.seq.items():
            if len(g) == 1:
                self.single.setdefault(g[0], t)
        base = self._shape("ក")[0]
        for x in [chr(c) for c in range(0x17B6, 0x17D2)] + SUBS + SIGNS:
            rest = [q for q in self._shape("ក" + x) if q != base]
            if len(rest) == 1:
                self.single.setdefault(rest[0], x)

    def decode(self, tokens: list[tuple[int, str]]) -> str:
        """tokens: (glyph id or -1 for 'not decodable', the PDF's own text)."""
        return self.decode_scored(tokens)[0]

    def decode_scored(self, tokens: list[tuple[int, str]]) -> tuple[str, int]:
        """Decoded text + number of glyphs that matched no known syllable."""
        out, i, misses = [], 0, 0
        gids = [t[0] for t in tokens]
        while i < len(tokens):
            if gids[i] >= 0:
                for n in range(min(self.maxlen, len(tokens) - i), 0, -1):
                    t = self.seq.get(tuple(gids[i:i + n]))
                    if t is not None:
                        out.append(t)
                        i += n
                        break
                else:
                    out.append(self.single.get(gids[i], tokens[i][1]))
                    misses += 1
                    i += 1
            else:
                out.append(tokens[i][1])
                i += 1
        return "".join(out), misses


@lru_cache(maxsize=None)
def decoder(path: str) -> GlyphDecoder:
    return GlyphDecoder(path)


@lru_cache(maxsize=1)
def reference_fonts() -> dict[str, str]:
    """normalised font name -> font file (bundled first, then installed)."""
    from .engine import bundled_fonts, system_khmer_fonts
    refs: dict[str, str] = {}
    for name, path in bundled_fonts().items():  # e.g. 'Khmer OS System' and 'KhmerOSsys'
        refs.setdefault(_norm(Path(path).stem), path)
        refs.setdefault(_norm(name), path)
    for name, path in system_khmer_fonts().items():
        refs.setdefault(_norm(Path(path).stem), path)
        refs.setdefault(_norm(name), path)
    return refs


class _Pen:
    def __init__(self):
        self.ops = []

    def moveTo(self, p): self.ops.append(("M", p))
    def lineTo(self, p): self.ops.append(("L", p))
    def qCurveTo(self, *a): self.ops.append(("Q", a))
    def curveTo(self, *a): self.ops.append(("C", a))
    def closePath(self): self.ops.append(("Z",))
    def endPath(self): self.ops.append(("E",))


def _outlines(font: hb.Font, gid: int):
    pen = _Pen()
    font.draw_glyph_with_pen(gid, pen)
    return pen.ops


def _matches(embedded: bytes, ref_path: str, gids: list[int]) -> bool:
    """Same glyph outlines at the same glyph IDs => same font (version)."""
    emb = hb.Font(hb.Face(embedded))
    ref = decoder(ref_path).font
    checked = 0
    for g in gids:
        a = _outlines(emb, g)
        if not a:
            continue
        if a != _outlines(ref, g):
            return False
        checked += 1
        if checked >= 12:
            break
    return checked > 0


def page_decoders(page: pymupdf.Page) -> dict[str, GlyphDecoder]:
    """Decoders for this page's embedded fonts, keyed by normalised name."""
    doc = page.parent
    cache = getattr(doc, "_khmerpdf_decoders", None)
    if cache is None:
        cache = {}
        try:
            doc._khmerpdf_decoders = cache
        except AttributeError:
            pass
    out: dict[str, GlyphDecoder] = {}
    refs = reference_fonts()
    # The glyph trace names fonts by their internal name ('KhmerOSsys'); the
    # PDF's font dictionary may use the display name ('Khmer OS System').
    # Both resolve to the same reference font file.
    gids_by_ref: dict[str, list[int]] = {}
    trace_keys_by_ref: dict[str, set[str]] = {}
    for sp in page.get_texttrace():
        key = _norm(sp["font"])
        ref = refs.get(key)
        if ref:
            gids_by_ref.setdefault(ref, []).extend(c[1] for c in sp["chars"] if c[1] > 0)
            trace_keys_by_ref.setdefault(ref, set()).add(key)
    for xref, _ext, ftype, basefont, *_ in page.get_fonts(full=True):
        ref = refs.get(_norm(basefont))
        if not ref or ref not in gids_by_ref:
            continue
        if (xref, ref) not in cache:
            dec = None
            try:
                buf = doc.extract_font(xref)[3]
                if buf and _matches(buf, ref, sorted(set(gids_by_ref[ref]))):
                    dec = decoder(ref)
            except Exception:
                dec = None
            cache[(xref, ref)] = dec
        if cache[(xref, ref)]:
            for key in trace_keys_by_ref[ref] | {_norm(basefont)}:
                out[key] = cache[(xref, ref)]
    return out


# --------------------------------------------------------------------------
# Building lines from the glyph stream
# --------------------------------------------------------------------------
@dataclass
class FoundLine:
    rect: pymupdf.Rect
    baseline: float
    size: float
    font: str
    color: tuple[float, float, float]
    original: str   # what the PDF's own ToUnicode says
    text: str       # recovered text (== original when nothing to recover)
    recovered: bool


def _decode_tokens(tokens: list[tuple[int, str, GlyphDecoder | None]]) -> tuple[str, int]:
    """Decode run by run, so a line mixing fonts (e.g. a bold Muol word in
    Battambang text, or Latin in another font) uses the right decoder.
    Returns the text and the number of glyphs that matched no syllable."""
    out, run, cur, misses = [], [], None, 0
    for g, t, d in tokens + [(-1, "", "END")]:
        if d is not cur and run:
            if cur:
                txt, m = cur.decode_scored([(rg, rt) for rg, rt, _ in run])
                out.append(txt)
                misses += m
            else:
                out.append("".join(rt for _, rt, _ in run))
            run = []
        cur = d
        run.append((g, t, d))
    return "".join(out).strip(), misses


def _visual_order(glyphs: list) -> list:
    """Turn a logically ordered glyph stream into visual order: a glyph drawn to
    the LEFT of the current syllable's first glyph is a pre-base form (េ, ្រ)
    and moves in front of it; zero-width marks stay with their syllable.
    A stream that is already visual comes back unchanged."""
    clusters: list[list] = []
    for g in glyphs:
        mark = _is_mark(ord(g[4][0]) if g[4] else 0, g[2], g[5])
        if clusters and g[0] < clusters[-1][0][0] - 0.01:  # drawn left of the syllable: pre-base
            clusters[-1].insert(0, g)
        elif clusters and mark:
            clusters[-1].append(g)
        else:
            clusters.append([g])
    return [g for c in clusters for g in c]


SPLIT_GAP_EM = 3.0  # a horizontal jump bigger than this starts a new line segment


def _is_mark(ucs: int, bbox, size: float) -> bool:
    return 0x17B6 <= ucs <= 0x17D3 or ucs == 0x17DD or (bbox[2] - bbox[0]) < 0.05 * size


def find_lines(page: pymupdf.Page, skip_fonts: tuple[str, ...] = ()) -> list[FoundLine]:
    """Group the page's glyphs into visual lines and recover their text.

    MuPDF's own line splitting can separate a Khmer consonant from its vowel
    mark in justified text (marks are drawn with their own offsets), so we
    group by baseline ourselves, keep the PDF's drawing order (= visual glyph
    order, which the decoder needs), and split only at large gaps.
    """
    decs = page_decoders(page)
    glyphs = []  # (x, y, bbox, gid, text, size, font, color, decoder)
    for sp in page.get_texttrace():
        if sp["font"] in skip_fonts:
            continue
        dec = decs.get(_norm(sp["font"]))
        col = tuple(sp["color"])[:3] if len(sp["color"]) >= 3 else (sp["color"][0],) * 3
        for c in sp["chars"]:
            uni = chr(c[0]) if c[0] > 0 else ""
            if c[1] < 0 and glyphs:  # continuation of the previous glyph's ToUnicode text
                g = glyphs[-1]
                glyphs[-1] = g[:4] + (g[4] + uni,) + g[5:]
                continue
            glyphs.append((c[2][0], c[2][1], c[3], c[1], uni, sp["size"], sp["font"], col, dec))
    if not glyphs:
        return []

    # 1. baselines from base glyphs (marks may sit at their own y offsets)
    ys = sorted(g[1] for g in glyphs if not _is_mark(ord(g[4][0]) if g[4] else 0, g[2], g[5]))
    rows: list[list[float]] = []
    for y in ys:
        if rows and y - rows[-1][-1] <= 1.5:
            rows[-1].append(y)
        else:
            rows.append([y])
    baselines = [max(set(r), key=r.count) for r in rows]

    # 2. assign glyphs to baselines in drawing order; split segments at big jumps
    segments: list[dict] = []
    open_seg: dict[int, dict] = {}
    for g in glyphs:
        x, y, bbox, gid, uni, size, font, col, dec = g
        bi = min(range(len(baselines)), key=lambda i: abs(baselines[i] - y))
        if abs(baselines[bi] - y) > 1.2 * size:
            continue
        seg = open_seg.get(bi)
        if seg is None or not (seg["x0"] - SPLIT_GAP_EM * size <= x <= seg["x1"] + SPLIT_GAP_EM * size):
            seg = {"baseline": baselines[bi], "x0": x, "x1": x, "glyphs": []}
            segments.append(seg)
            open_seg[bi] = seg
        seg["glyphs"].append(g)
        seg["x0"] = min(seg["x0"], bbox[0])
        seg["x1"] = max(seg["x1"], bbox[2])

    # 3. decode each segment
    out: list[FoundLine] = []
    for seg in segments:
        gl = seg["glyphs"]
        original = "".join(g[4] for g in gl).strip()
        if not re.sub(r"[\s\u17b6-\u17d3\u17dd\u200b]", "", original):
            continue
        text = original
        if any(g[8] for g in gl):
            # Word draws glyphs in visual order; other tools draw them in logical
            # order with explicit positions. Try the drawing order and the
            # left-to-right order; keep whichever matches more known syllables.
            candidates = []
            for order in (gl, _visual_order(gl)):
                candidates.append(_decode_tokens([(g[3] if g[8] else -1, g[4], g[8]) for g in order]))
            text = min(candidates, key=lambda c: c[1])[0]
        rect = pymupdf.Rect(seg["x0"], min(g[2][1] for g in gl), seg["x1"], max(g[2][3] for g in gl))
        bases = [g for g in gl if not _is_mark(ord(g[4][0]) if g[4] else 0, g[2], g[5])] or gl
        main = max(set((g[5], g[6], g[7]) for g in bases), key=lambda k: sum(1 for g in bases if (g[5], g[6], g[7]) == k))
        out.append(FoundLine(rect=rect, baseline=seg["baseline"], size=round(main[0] * 2) / 2,
                             font=main[1], color=main[2], original=original, text=text,
                             recovered=text != original))
    return out


# --------------------------------------------------------------------------
# Detecting broken text when we cannot decode it
# --------------------------------------------------------------------------
_VISUAL_ORDER = re.compile(r"(?:^|[\s​])[េ-ៃ][ក-អ]")  # េក at word start
_DOUBLE_MARK = re.compile(r"([ិ-ៅំ-៑])\1")              # ិិ
_LONE_MARK = re.compile(r"(?:^|\s)[ា-៑]")                           # mark after a space
_JUNK = re.compile("[\u0180-\u024f\ue000-\uf8ff\ufffd]")  # Latin Ext-B (Word junk: ǒ ƞ ǎ), not é ñ ü


def looks_broken(text: str) -> bool:
    """Heuristic for Khmer text that was extracted in visual order or with
    unmapped glyphs (what most Word-made Khmer PDFs give)."""
    if not re.search(r"[ក-៿]", text):
        return bool(_JUNK.search(text)) and len(_JUNK.findall(text)) >= max(1, len(text.strip()) // 5)
    return bool(_VISUAL_ORDER.search(text) or _DOUBLE_MARK.search(text)
                or _LONE_MARK.search(text) or _JUNK.search(text))
