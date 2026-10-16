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

import itertools
import re
from dataclasses import dataclass
from functools import cached_property, lru_cache
from pathlib import Path

import pymupdf
import uharfbuzz as hb

from .legacy import legacy_family, to_unicode

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
CACHE_VERSION = 1  # bump when _build changes in a way the tables below don't show


def _cache_dir() -> Path:
    """Where built reverse dictionaries are kept between runs."""
    import os
    if os.environ.get("KHMERPDF_CACHE"):
        return Path(os.environ["KHMERPDF_CACHE"])
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "KhmerPDF" / "cache"
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "khmerpdf"


class GlyphDecoder:
    def __init__(self, path: str):
        self.path = path
        data = Path(path).read_bytes()
        self.font = hb.Font(hb.Face(data))
        self.seq: dict[tuple[int, ...], str] = {}
        self.single: dict[int, str] = {}
        # Building takes about a second per font; it is kept on disk, keyed by
        # the font file, the syllable tables and the HarfBuzz version.
        import hashlib
        tables = repr((CACHE_VERSION, hb.__version__, CONS, SHIFTERS, VOWELS, COMMON_FINAL, SUBS,
                       SECOND_SUBS, SIGNS, INDEP, OTHER)).encode()
        self._cache = _cache_dir() / f"decoder-{hashlib.sha1(data + tables).hexdigest()}.bin"
        if self._load():
            return
        self._build()
        self.maxlen = max(len(k) for k in self.seq)
        # Order-insensitive index: Word (Uniscribe) sometimes orders a
        # syllable's glyphs differently from HarfBuzz. Keyed by the sorted
        # glyphs; only keys that identify exactly one syllable are kept.
        bag: dict[tuple[int, ...], str | None] = {}
        for g, t in self.seq.items():
            if len(g) >= 2:
                k = tuple(sorted(g))
                bag[k] = t if k not in bag or bag[k] == t else None
        self.bag = {k: t for k, t in bag.items() if t is not None}
        # Pre-base glyphs (េ ែ ៃ and the pre-base ្រ) always START a syllable,
        # so an order-insensitive window may never end on one.
        self.prebase: set[int] = set()
        for c in CONS:
            base = self._shape(c)
            for x in ("\u17c1", "\u17c2", "\u17c3", "\u17d2\u179a"):
                g = self._shape(c + x)
                if len(g) >= 2 and g[0] not in base:
                    self.prebase.add(g[0])
        # Base glyphs (a consonant / independent vowel, alone or ligated with
        # its vowel): a syllable has exactly one, so a window with two spans
        # two syllables.
        self.bases: set[int] = {g[0] for g, t in self.seq.items()
                                if len(g) == 1 and t and ("\u1780" <= t[0] <= "\u17b3")}
        self._save()

    _TABLES = ("seq", "single", "bag", "prebase", "bases", "maxlen")

    def _load(self) -> bool:
        """The tables from the disk cache. marshal reads them ~10x faster than
        JSON; a checksum guards against a damaged file (marshal isn't meant
        for data it didn't write itself)."""
        import hashlib
        import marshal
        try:
            raw = self._cache.read_bytes()
            digest, payload = raw[:32], raw[32:]
            if hashlib.sha256(payload).digest() != digest:
                return False
            d = marshal.loads(payload)
            for k in self._TABLES:
                setattr(self, k, d[k])
            return bool(self.seq)
        except (OSError, ValueError, EOFError, KeyError, TypeError):
            return False

    def _save(self) -> None:
        import hashlib
        import marshal
        import os
        try:
            payload = marshal.dumps({k: getattr(self, k) for k in self._TABLES})
            self._cache.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._cache.with_suffix(f".{os.getpid()}.tmp")
            tmp.write_bytes(hashlib.sha256(payload).digest() + payload)
            os.replace(tmp, self._cache)  # whole file or nothing, even with two windows open
        except (OSError, ValueError):
            pass  # no cache, just slower

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
                for s1 in SUBS:  # e.g. ហ្ស៊ួ (subscript + register shifter + vowel)
                    for v in VOWELS:
                        add(c + s1 + sh + v)
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

    def decode_scored(self, tokens: list[tuple]) -> tuple[str, float]:
        """Decoded text + a miss score: 1 per glyph that matched no known
        syllable, 0.5 per syllable matched only with its glyphs reordered.
        A token's glyph may be an int or a tuple of candidate glyph IDs (when
        several glyphs of the font share one outline); the decoder picks the
        candidates that form a known syllable, in preference order."""
        out, i, misses = [], 0, 0.0
        cands = [t[0] if isinstance(t[0], tuple) else ((t[0],) if t[0] >= 0 else ()) for t in tokens]
        while i < len(tokens):
            if not cands[i]:
                out.append(tokens[i][1])
                i += 1
                continue
            # longest exact match vs longest order-insensitive match;
            # take the longer one (exact wins ties)
            exact = bag = (0, None)
            for n in range(min(self.maxlen, len(tokens) - i), 0, -1):
                t = self._lookup(cands[i:i + n], self.seq, ordered=True)
                if t is not None:
                    exact = (n, t)
                    break
            for n in range(min(self.maxlen, len(tokens) - i), max(exact[0], 1), -1):
                t = self._lookup(cands[i:i + n], self.bag, ordered=False)
                if t is not None:
                    bag = (n, t)
                    break
            n, t = bag if bag[0] > exact[0] else exact
            if t is not None:
                out.append(t)
                i += n
                if bag[0] > exact[0]:
                    misses += 0.5  # tolerated a glyph-order difference
            else:
                out.append(self.single.get(cands[i][0], tokens[i][1]))
                misses += 1
                i += 1
        return "".join(out), misses

    def _lookup(self, window: list[tuple[int, ...]], table: dict, ordered: bool) -> str | None:
        if any(not c for c in window):
            return None
        for combo in itertools.islice(itertools.product(*window), 32):
            if not ordered and (combo[-1] in self.prebase
                                or sum(1 for w in combo if w in self.bases) > 1):
                continue
            t = table.get(combo if ordered else tuple(sorted(combo)))
            if t is not None:
                return t
        return None


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


@lru_cache(maxsize=None)
def _ref_font(path: str) -> hb.Font:
    return hb.Font(hb.Face(Path(path).read_bytes()))


@lru_cache(maxsize=None)
def _shape_index(path: str) -> dict[tuple, tuple[int, ...]]:
    """Glyph outline -> every glyph ID with that outline (some glyphs share
    an outline, e.g. subscript ta/da in Khmer OS). Cheap: no dictionary."""
    font = _ref_font(path)
    index: dict[tuple, list[int]] = {}
    for gid in range(font.face.glyph_count):
        ops = tuple(_outlines(font, gid))
        if ops:
            index.setdefault(ops, []).append(gid)
    return {k: tuple(v) for k, v in index.items()}


# Among glyphs that look identical, prefer the more common character
PREFER = {"\u178f": 0, "\u178a": 1}  # ត before ដ (subscripts ្ត / ្ដ)


def _near(emb: hb.Font, ref: hb.Font, g: int, ops: tuple, ref_ops: tuple) -> bool:
    """Glyph g looks the same in both fonts, give or take the small redrawing
    between versions of a font (Khmer OS Muol 2007 vs today's): same advance,
    nearly the same size and number of drawing steps. Marks may sit a little
    to one side, so where a glyph starts doesn't count."""
    if not ref_ops or emb.get_glyph_h_advance(g) * ref.face.upem != ref.get_glyph_h_advance(g) * emb.face.upem:
        return False
    a, b = emb.get_glyph_extents(g), ref.get_glyph_extents(g)
    tol = 0.03 * emb.face.upem
    return (abs(a.width - b.width) <= tol and abs(a.height - b.height) <= tol
            and abs(len(ops) - len(ref_ops)) <= max(3, 0.15 * len(ref_ops)))


@lru_cache(maxsize=None)
def _variants(path: str) -> dict[int, tuple[int, ...]]:
    """Mark glyph -> the same mark drawn at other heights/offsets (no advance,
    same size and same number of drawing steps; only the position differs)."""
    font = _ref_font(path)
    groups: dict[tuple, list[int]] = {}
    for gid in range(font.face.glyph_count):
        if font.get_glyph_h_advance(gid):
            continue
        ops = _outlines(font, gid)
        if not ops:
            continue
        e = font.get_glyph_extents(gid)
        groups.setdefault((e.width, e.height, len(ops), tuple(o[0] for o in ops)), []).append(gid)
    return {g: tuple(x for x in grp if x != g) for grp in groups.values() if len(grp) > 1 for g in grp}


@lru_cache(maxsize=None)
def _mark_shapes(path: str) -> list[tuple[int, int, int, int, tuple]]:
    """(gid, y_bearing, width, height, drawing steps) of every mark glyph."""
    font = _ref_font(path)
    out = []
    for gid in range(font.face.glyph_count):
        if font.get_glyph_h_advance(gid):
            continue
        ops = _outlines(font, gid)
        if ops:
            e = font.get_glyph_extents(gid)
            out.append((gid, e.y_bearing, e.width, e.height, tuple(o[0] for o in ops)))
    return out


def _like_marks(path: str, emb: hb.Font, g: int, ops: tuple) -> tuple[int, ...]:
    """Reference marks drawn like the PDF's mark g (same steps, nearly the
    same size), closest first - for a mark whose glyph ID differs between
    font versions."""
    e = emb.get_glyph_extents(g)
    steps = tuple(o[0] for o in ops)
    tol = 0.03 * emb.face.upem
    hits = [(abs(w - e.width) + abs(h - e.height) + abs(y - e.y_bearing) / 10, gid)
            for gid, y, w, h, st in _mark_shapes(path)
            if st == steps and abs(w - e.width) <= tol and abs(h - e.height) <= tol]
    return tuple(gid for _, gid in sorted(hits)[:4])


SAME_FONT = 0.6  # this share of glyphs matching exactly by ID proves it is the same font


def _glyph_map(emb: hb.Font, ref_path: str, gids: list[int], known_drawn: int = 0,
               known_missing: int = 0, named: bool = False) -> tuple[dict[int, tuple[int, ...]], int, int] | None:
    """Map the embedded font's glyph IDs to the reference font's, by outline.
    Works for subsets that keep the original IDs and for renumbered subsets.
    Returns (mapping, drawn, missing), or None unless (nearly) every drawn
    glyph is found in the reference font. `known_*` count glyphs checked on
    earlier pages, so the bar applies to the whole font, not just new glyphs.
    Slightly redrawn glyphs (another version of the font) count when the
    glyph IDs prove it's the same font - or when the PDF names this font and
    nearly every glyph has its metrics (`named`)."""
    index = _shape_index(ref_path)
    ref_font = _ref_font(ref_path)
    mapping: dict[int, tuple[int, ...]] = {}
    near: dict[int, tuple[int, ...]] = {}  # same ID, slightly redrawn: kept if the IDs prove the font
    drawn = missing = same = 0
    # can't reach 95% any more, however the remaining glyphs turn out
    give_up = 0.05 * (known_drawn + len(gids)) - known_missing
    for g in gids:
        ops = tuple(_outlines(emb, g))
        if not ops:
            continue  # blank glyphs (space, ZWSP) prove nothing
        drawn += 1
        ref_ops = tuple(_outlines(ref_font, g)) if g < ref_font.face.glyph_count else ()
        # Same ID with the same outline wins: it tells look-alikes apart.
        if ref_ops == ops:
            mapping[g] = (g,)
            same += 1
            continue
        ref = index.get(ops)
        if ref is not None:
            mapping[g] = ref
        elif _near(emb, ref_font, g, ops, ref_ops):
            near[g] = (g,)
        elif emb.get_glyph_h_advance(g) == 0 and _like_marks(ref_path, emb, g, ops):
            near[g] = _like_marks(ref_path, emb, g, ops)  # a mark whose ID moved between versions
        else:
            missing += 1
            if missing > give_up:
                return None
    if near:
        if same >= SAME_FONT * drawn or named and same + len(near) >= 0.9 * drawn:
            mapping.update(near)
        else:
            missing += len(near)
    total = known_drawn + drawn
    if not total or known_missing + missing > 0.05 * total:
        return None
    return mapping, drawn, missing


class MappedDecoder:
    """A reference-font decoder plus the PDF-glyph -> reference-glyph mapping
    for one embedded font. The mapping grows as more pages are read."""

    def __init__(self, path: str, emb: hb.Font, named: bool = False):
        self.path = path
        self.emb = emb
        self.named = named  # the PDF names this font (it may be another version of it)
        self.mapping: dict[int, tuple[int, ...]] = {}
        self.same_ids = True
        self.drawn = self.missing = 0
        self.seen: set[int] = set()

    def extend(self, gids: list[int]) -> bool:
        new = [g for g in gids if g not in self.seen]
        if not new:
            return True
        if not any(_outlines(self.emb, g) for g in new):
            m = {}
        else:
            res = _glyph_map(self.emb, self.path, new, self.drawn, self.missing, self.named)
            if res is None:
                return False
            m, drawn, missing = res
            self.drawn += drawn
            self.missing += missing
        self.seen.update(new)
        self.same_ids &= all(b == (a,) for a, b in m.items())
        m = {g: tuple(sorted(c, key=self._rank)) for g, c in m.items()}
        self.mapping.update(m)
        # blank glyphs (space / ZWSP) can't be matched by outline; when the PDF
        # kept the font's own glyph IDs, keep theirs too (drawn glyphs that
        # didn't match stay unmapped: their outline says they're different)
        if self.same_ids:
            n = _ref_font(self.path).face.glyph_count
            self.mapping.update({g: (g,) for g in new
                                 if g not in self.mapping and g < n and not _outlines(self.emb, g)})
        return True

    def _with_variants(self, cands: tuple[int, ...]) -> tuple[int, ...]:
        """Add, as last resorts, glyphs that are the same mark at another
        height or offset: fonts differ in which positional variant a shaper
        picks (Word 2007's Muol uses ើ's 'base' form where HarfBuzz uses '.h')."""
        out = list(cands)
        for r in cands:
            out += [v for v in _variants(self.path).get(r, ()) if v not in out]
        return tuple(out)

    @cached_property
    def dec(self) -> "GlyphDecoder":
        # built on first use: it takes about a second, and most candidate
        # fonts are rejected by their outlines before it is needed
        return decoder(self.path)

    def _rank(self, g: int) -> tuple:
        """Among look-alike glyphs, try the more common character first."""
        t = self.dec.single.get(g, "")
        return (PREFER.get(t[-1:], 1) if t else 2, g)

    def decode_scored(self, tokens: list[tuple[int, str]]) -> tuple[str, float]:
        text, misses = self.dec.decode_scored([(self.mapping.get(g, ()) if g >= 0 else (), t) for g, t in tokens])
        if misses:  # last resort: marks drawn at another height (font versions differ)
            alt = self.dec.decode_scored([(self._with_variants(self.mapping.get(g, ())) if g >= 0 else (), t)
                                          for g, t in tokens])
            if alt[1] < misses:
                return alt
        return text, misses


def _identify(doc: pymupdf.Document, xref: int, preferred: str | None, gids: list[int]) -> MappedDecoder | None:
    """Find which reference font (bundled or installed) the PDF's embedded
    font is, by glyph outlines - not by name, which is often renamed."""
    try:
        buf = doc.extract_font(xref)[3]
    except Exception:
        return None
    if not buf:
        return None
    emb = hb.Font(hb.Face(buf))
    candidates = list(dict.fromkeys(([preferred] if preferred else []) + list(reference_fonts().values())))
    for path in candidates:
        md = MappedDecoder(path, emb, named=path == preferred)
        if md.extend(gids) and md.drawn:
            return md
    return None


def page_decoders(page: pymupdf.Page) -> dict[str, MappedDecoder]:
    """Decoders for this page's embedded fonts, keyed by normalised font name."""
    doc = page.parent
    cache = getattr(doc, "_khmerpdf_decoders", None)
    if cache is None:
        cache = {}
        try:
            doc._khmerpdf_decoders = cache
        except AttributeError:
            pass
    refs = reference_fonts()
    gids_by_key: dict[str, set[int]] = {}
    for sp in page.get_texttrace():
        if any(0x1780 <= c[0] <= 0x17FF for c in sp["chars"]) or any(
                0x0180 <= c[0] <= 0x024F or 0xE000 <= c[0] <= 0xF8FF for c in sp["chars"]):
            gids_by_key.setdefault(_norm(sp["font"]), set()).update(c[1] for c in sp["chars"] if c[1] > 0)
    out: dict[str, MappedDecoder] = {}
    fonts = page.get_fonts(full=True)
    page_keys = {_norm(f[3]) for f in fonts}
    for xref, _ext, _ftype, basefont, *_ in fonts:
        bkey = _norm(basefont)
        name_ref = refs.get(bkey)
        # the glyph trace may name the font differently ('KhmerOSsys' vs
        # 'Khmer OS System'); link them through the reference file
        # (MuPDF also truncates long names: 'Khmer OS Siemreap Regula') - but
        # 'khmeros' is no truncation of 'khmerosbattambang' when both are fonts
        keys = {k for k in gids_by_key
                if k == bkey or (name_ref and refs.get(k) == name_ref)
                or (min(len(k), len(bkey)) >= 6 and (k.startswith(bkey) or bkey.startswith(k))
                    and k not in page_keys and refs.get(k, name_ref) == name_ref)}
        if not keys:
            continue
        gids = sorted(set().union(*(gids_by_key[k] for k in keys)))
        try:
            if xref not in cache:
                cache[xref] = _identify(doc, xref, name_ref, gids)
            elif cache[xref] and not cache[xref].extend(gids):
                cache[xref] = None  # this page shows it isn't the same font after all
        except Exception:
            cache[xref] = None  # a font HarfBuzz can't read: show the PDF's own text
        if cache[xref]:
            for k in keys | {bkey}:
                out[k] = cache[xref]
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
    recovered: bool  # rebuilt exactly from the glyphs with a matching font
    approx: bool = False  # best-effort repair (no matching font): please check
    legacy: bool = False  # converted from a Limon (pre-Unicode) font
    trailing_space: bool = False  # the PDF's text ended with a space


def _decode_tokens(tokens: list[tuple[int, str, "MappedDecoder | None"]]) -> tuple[str, float]:
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
    return "".join(out).replace("\xa0", " ").strip(), misses


def _visual_order(glyphs: list) -> list:
    """Turn a logically ordered glyph stream into visual order: a glyph drawn to
    the LEFT of the current syllable's first glyph is a pre-base form (េ, ្រ)
    and moves in front of it; zero-width marks stay with their syllable.
    A stream that is already visual comes back unchanged."""
    clusters: list[list] = []
    for g in glyphs:
        mark = _is_mark(ord(g[4][0]) if g[4] else 0, g[2], g[5])
        if clusters and mark and g[2][2] - g[2][0] < 0.05 * g[5]:
            # A vowel sign or subscript (drawn with no width of its own)
            # belongs to the letter it is anchored at
            # the end of. Usually that's the syllable just drawn, but Word draws
            # a word's letters first and its marks afterwards, or a subscript
            # after the next letter - so go by position, not by order.
            letters = [c for c in clusters if not _blank(c[0]) and min(q[2][0] for q in c) < g[0] + 0.01]
            home = min(reversed(letters), key=lambda c: abs(max(q[2][2] for q in c) - g[0]),
                       default=clusters[-1])
            if _subscript(g):  # subscripts come right after their letter, before its vowels
                at = next((i for i, q in enumerate(home) if i and q[2][2] - q[2][0] < 0.05 * q[5]
                           and not _subscript(q)), len(home))
                home.insert(at, g)
            else:
                home.append(g)
        elif (clusters and not _blank(clusters[-1][0])
              and clusters[-1][0][0] - 1.5 * g[5] < g[0] < clusters[-1][0][0] - 0.01):
            clusters[-1].insert(0, g)  # drawn just left of the syllable: pre-base (េ, ្រ)
        elif clusters and mark and not _blank(clusters[-1][0]):
            clusters[-1].append(g)
        else:
            clusters.append([g])
    # a letter drawn out of turn (Word sometimes draws a line's first letter
    # last) goes back to its place; everything else keeps its order
    placed: list[list] = []
    for c in clusters:
        x = min(q[0] for q in c)
        if placed and x < min(q[0] for q in placed[-1]) - 1.5 * c[0][5]:
            at = next((i for i, d in enumerate(placed) if min(q[0] for q in d) > x), len(placed))
            placed.insert(at, c)
        else:
            placed.append(c)
    return [g for c in placed for g in c]


def _blank(g) -> bool:
    """A space or word break: it starts no syllable and takes no marks."""
    return not g[4].strip(" \u200b\xa0")


def _subscript(g) -> bool:
    """A subscript consonant glyph (or the COENG sign), drawn with no width."""
    t = g[4][:1]
    return t == "\u17d2" or ("\u1780" <= t <= "\u17a2" and g[2][2] - g[2][0] < 0.05 * g[5])


SPLIT_GAP_EM = 3.0  # a horizontal jump bigger than this starts a new line segment
RULE_MAX = 3.0      # a filled rectangle thinner than this (pt) is a ruling line


@dataclass
class Rules:
    """Ruling lines on a page (table borders, underlines, separators)."""
    h: list[tuple[float, float, float]]   # horizontal: (x0, x1, y)
    v: list[tuple[float, float, float]]   # vertical: (y0, y1, x)

    def vertical_between(self, x0: float, x1: float, y: float) -> bool:
        """A vertical rule crossing height y somewhere between x0 and x1."""
        return any(x0 - 0.5 <= x <= x1 + 0.5 and a - 1 <= y <= b + 1 for a, b, x in self.v)

    def horizontal_between(self, y0: float, y1: float, x0: float, x1: float) -> bool:
        """A horizontal rule between heights y0 and y1, overlapping x0..x1."""
        return any(y0 < y < y1 and a < x1 and b > x0 for a, b, y in self.h)


_OWN_STREAM = re.compile(rb"^\s*q /KL\d+ Do Q\s*$")  # a line this app wrote (engine._write_line)


def page_rules(page: pymupdf.Page) -> Rules:
    """Table borders and other ruling lines: stroked straight lines, thin
    filled rectangles, and the edges of stroked rectangles. (Glyph outlines,
    including the ones this app draws, are filled curves and don't count.)
    Cached per page: they only change when the PDF's own content does, not
    when this app adds or edits text."""
    doc = page.parent
    cache = getattr(doc, "_khmerpdf_rules", None)
    if cache is None:
        cache = {}
        try:
            doc._khmerpdf_rules = cache
        except AttributeError:
            pass
    key = tuple(x for x in page.get_contents() if not _OWN_STREAM.match(doc.xref_stream(x) or b""))
    hit = cache.get(page.xref)
    if hit and hit[0] == key:
        return hit[1]
    rules = _find_rules(page)
    cache[page.xref] = (key, rules)
    return rules


def _find_rules(page: pymupdf.Page) -> Rules:
    h: list = []
    v: list = []

    def edge(x0, y0, x1, y1):
        if abs(y1 - y0) < 0.5 and abs(x1 - x0) > 2:
            h.append((min(x0, x1), max(x0, x1), (y0 + y1) / 2))
        elif abs(x1 - x0) < 0.5 and abs(y1 - y0) > 2:
            v.append((min(y0, y1), max(y0, y1), (x0 + x1) / 2))

    try:
        paths = page.get_cdrawings()  # the fast variant: plain tuples
    except Exception:
        return Rules(h, v)
    for path in paths:
        stroked = "s" in (path.get("type") or "")
        for it in path["items"]:
            if it[0] == "l" and stroked:
                edge(it[1][0], it[1][1], it[2][0], it[2][1])
            elif it[0] in ("re", "qu"):
                r = pymupdf.Rect(it[1]) if it[0] == "re" else pymupdf.Quad(it[1]).rect
                if r.height <= RULE_MAX < r.width:
                    edge(r.x0, (r.y0 + r.y1) / 2, r.x1, (r.y0 + r.y1) / 2)
                elif r.width <= RULE_MAX < r.height:
                    edge((r.x0 + r.x1) / 2, r.y0, (r.x0 + r.x1) / 2, r.y1)
                elif stroked:
                    edge(r.x0, r.y0, r.x1, r.y0)
                    edge(r.x0, r.y1, r.x1, r.y1)
                    edge(r.x0, r.y0, r.x0, r.y1)
                    edge(r.x1, r.y0, r.x1, r.y1)
    return Rules(h, v)


ZWSP_GAP_EM = 0.15  # a "space" that leaves less of a gap than this is an invisible word break


def _invisible_spaces(gl: list) -> list:
    """Word (and Khmer OS's own documents) separate Khmer words with ZERO WIDTH
    SPACE; Distiller writes it as the font's space glyph and pulls the next
    letters back over it, and the text layer says " ". Judge by the page:
    a space that leaves no visible gap becomes U+200B again."""
    boxes = sorted((g[2][0], g[2][2]) for g in gl if g[4].strip())
    if not boxes:
        return gl
    out, spaces = [], []
    for g in gl:
        if g[4] == " ":
            x, eps = g[0], 0.05 * g[5]
            left = max((b for a, b in boxes if b <= x + eps), default=None)   # where the text before ends
            right = min((a for a, b in boxes if a >= x - eps), default=None)  # where the text after starts
            gap = right - left if left is not None and right is not None else ZWSP_GAP_EM * g[5]
            # Distiller also writes "space, then word break" at one spot: one space is enough
            if gap < ZWSP_GAP_EM * g[5] or any(abs(x - s) < eps for s in spaces):
                g = g[:3] + (-1, "\u200b") + g[5:]  # not decodable: keep the word break as it is
            else:
                spaces.append(x)
        out.append(g)
    return out


COLUMN_GAP_EM = 1.0  # gaps at least this wide may be a table's column gutter
COLUMN_ROWS = 3      # ...when this many nearby lines have one in the same place


def _gaps(seg: dict) -> list[tuple[float, float]]:
    """Wide horizontal gaps inside a line segment, left to right."""
    gl = sorted(seg["glyphs"], key=lambda g: g[2][0])
    out, right = [], gl[0][2][2]
    for g in gl[1:]:
        if g[2][0] - right > COLUMN_GAP_EM * g[5]:
            out.append((right, g[2][0]))
        right = max(right, g[2][2])
    return out


def _split_columns(segments: list[dict]) -> list[dict]:
    """Split segments at the column gutters of a table without borders: wide
    gaps in the same place on several nearby lines, with the text after them
    starting (or the text before them ending) at the same x. The wide spaces
    of justified text never line up like that."""
    gaps = [_gaps(s) for s in segments]
    if sum(map(len, gaps)) < COLUMN_ROWS:
        return segments
    out = []
    for i, s in enumerate(segments):
        size = max(g[5] for g in s["glyphs"])
        cuts = []
        for a, b in gaps[i]:
            starts, ends = [b], [a]
            for j, t in enumerate(segments):
                if j != i and abs(t["baseline"] - s["baseline"]) <= 8 * size:
                    for c, d in gaps[j]:
                        if c < b and d > a:
                            starts.append(d)
                            ends.append(c)
            if len(starts) >= COLUMN_ROWS and (max(starts) - min(starts) <= 2 or max(ends) - min(ends) <= 2):
                cuts.append((a + b) / 2)
        if not cuts:
            out.append(s)
            continue
        edges = [-1e9] + sorted(cuts) + [1e9]
        for lo, hi in zip(edges, edges[1:]):
            gl = [g for g in s["glyphs"] if lo <= (g[2][0] + g[2][2]) / 2 < hi]  # keeps the drawing order
            if gl:
                out.append({"baseline": s["baseline"], "glyphs": gl,
                            "x0": min(g[2][0] for g in gl), "x1": max(g[2][2] for g in gl)})
    return out


def _is_mark(ucs: int, bbox, size: float) -> bool:
    return 0x17B6 <= ucs <= 0x17D3 or ucs == 0x17DD or (bbox[2] - bbox[0]) < 0.05 * size


def find_lines(page: pymupdf.Page, skip_fonts: tuple[str, ...] = (),
               rules: Rules | None = None) -> list[FoundLine]:
    """Group the page's glyphs into visual lines and recover their text.

    MuPDF's own line splitting can separate a Khmer consonant from its vowel
    mark in justified text (marks are drawn with their own offsets), so we
    group by baseline ourselves, keep the PDF's drawing order (= visual glyph
    order, which the decoder needs), and split only at large gaps - or where
    a vertical rule (a table border) runs between two glyphs.
    """
    rules = rules if rules is not None else page_rules(page)
    decs = page_decoders(page)
    glyphs = []  # (x, y, bbox, gid, text, size, font, color, decoder)
    seen: set[tuple] = set()  # Word draws bold text twice (fill, then outline) in the same place
    for sp in page.get_texttrace():
        if sp["font"] in skip_fonts or sp.get("type") == 3:
            continue
        dec = decs.get(_norm(sp["font"]))
        col = tuple(sp["color"])[:3] if len(sp["color"]) >= 3 else (sp["color"][0],) * 3
        for c in sp["chars"]:
            uni = chr(c[0]) if c[0] > 0 else ""
            if c[1] < 0 and glyphs:  # continuation of the previous glyph's ToUnicode text
                g = glyphs[-1]
                glyphs[-1] = g[:4] + (g[4] + uni,) + g[5:]
                continue
            key = (c[1], c[0], round(c[2][0] * 4), round(c[2][1] * 4), sp["font"])
            if key in seen:
                continue
            seen.add(key)
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
        if seg is not None and rules.v and (
                rules.vertical_between(seg["x1"], bbox[0], baselines[bi] - 0.3 * size)
                or rules.vertical_between(bbox[2], seg["x0"], baselines[bi] - 0.3 * size)):
            seg = None  # the next table cell
        if seg is None or not (seg["x0"] - SPLIT_GAP_EM * size <= x <= seg["x1"] + SPLIT_GAP_EM * size):
            seg = {"baseline": baselines[bi], "x0": x, "x1": x, "glyphs": []}
            segments.append(seg)
            open_seg[bi] = seg
        seg["glyphs"].append(g)
        seg["x0"] = min(seg["x0"], bbox[0])
        seg["x1"] = max(seg["x1"], bbox[2])

    segments = _split_columns(segments)
    for seg in segments:
        seg["glyphs"] = _invisible_spaces(seg["glyphs"])

    # 3. decode each segment
    out: list[FoundLine] = []
    for seg in segments:
        gl = seg["glyphs"]
        raw = "".join(g[4] for g in gl).replace("\xa0", " ")
        original = raw.strip()
        if not re.sub(r"[\s\u17b6-\u17d3\u17dd\u200b]", "", original):
            continue
        text = original
        legacy = any(legacy_family(g[6]) for g in gl)
        if legacy:
            # Limon, ABC...: convert the font's codes, run by run (Latin words may be in another font)
            runs = [(k, "".join(g[4] for g in grp)) for k, grp in itertools.groupby(gl, key=lambda g: legacy_family(g[6]))]
            text = "".join(to_unicode(t, k) if k else t for k, t in runs).replace("\xa0", " ").strip()
        elif any(g[8] for g in gl):
            # Word draws glyphs in visual order; other tools draw them in logical
            # order with explicit positions. Try the drawing order and the
            # left-to-right order; keep whichever matches more known syllables.
            # On a tie, left-to-right wins: it is the order the reference
            # dictionary is built in (drawn ហ េ ង also decodes, wrongly, as ហ + ងេ).
            candidates = []
            for order in (_visual_order(gl), gl):
                candidates.append(_decode_tokens([(g[3] if g[8] else -1, g[4], g[8]) for g in order]))
            text = min(candidates, key=lambda c: c[1])[0]
        approx = False
        if not legacy and text == original and looks_broken(original):
            repaired = repair_visual_order(_visual_order(gl))  # normalise logical-order streams first
            if repaired and repaired != original:
                text, approx = repaired, True
        rect = pymupdf.Rect(seg["x0"], min(g[2][1] for g in gl), seg["x1"], max(g[2][3] for g in gl))
        bases = [g for g in gl if not _is_mark(ord(g[4][0]) if g[4] else 0, g[2], g[5])] or gl
        main = max(set((g[5], g[6], g[7]) for g in bases), key=lambda k: sum(1 for g in bases if (g[5], g[6], g[7]) == k))
        out.append(FoundLine(rect=rect, baseline=seg["baseline"], size=round(main[0] * 2) / 2,
                             font=main[1], color=main[2], original=original, text=text,
                             recovered=text != original and not approx, approx=approx,
                             legacy=legacy, trailing_space=raw != raw.rstrip()))
    return out


# --------------------------------------------------------------------------
# Best-effort repair when no matching font is available
# --------------------------------------------------------------------------
_PREBASE_VOWELS = ("\u17c1", "\u17c2", "\u17c3")       # េ ែ ៃ
_SPLIT_VOWELS = {("\u17c1", "\u17b6"): "\u17c4",        # េ + ា = ោ
                 ("\u17c1", "\u17b8"): "\u17be",        # េ + ី = ើ
                 ("\u17c1", "\u17c5"): "\u17c5"}        # េ + ៅ-tail = ៅ


def _is_base(t: str) -> bool:
    return bool(t) and "\u1780" <= t[0] <= "\u17b3"


def repair_visual_order(glyphs: list) -> str:
    """Rebuild logical Khmer from a glyph run whose PDF text mapping is broken,
    using only the PDF's own mapping plus Khmer rules (no font needed):
      * a consonant glyph with (almost) no width is a subscript -> add COENG
      * pre-base vowels (េ ែ ៃ) and the pre-base ្រ move after their consonant
        (and its subscripts); េ + ា -> ោ, េ + ី -> ើ
      * junk characters (Latin Ext-B, private use, U+FFFD) are dropped
    Good for most syllables; ligature glyphs mapped to junk are lost."""
    toks: list[str] = []
    for g in glyphs:
        t = _JUNK.sub("", g[4])
        if not t:
            continue
        w = g[2][2] - g[2][0]
        em = w / g[5] if g[5] else 0
        if len(t) == 1 and "\u1780" <= t <= "\u17a2":
            if w < 0.05 * g[5] and toks:
                t = "\u17d2" + t                      # zero-width consonant = subscript
        elif t == "\u17d2" and em > 0.1:
            t = "\u17d2\u179a"                        # a COENG with width = the pre-base ្រ
        toks.append(t)
    out: list[str] = []
    i = 0
    while i < len(toks):
        pre_vowel, pre_ro = None, False
        while i < len(toks) and (toks[i] in _PREBASE_VOWELS or toks[i] == "\u17d2\u179a"):
            nxt = next((t for t in toks[i + 1:] if t not in _PREBASE_VOWELS and t != "\u17d2\u179a"), "")
            if not _is_base(nxt):
                break
            if toks[i] == "\u17d2\u179a":
                pre_ro = True
            else:
                pre_vowel = toks[i]
            i += 1
        if i >= len(toks):
            out += [pre_vowel or ""] + (["\u17d2\u179a"] if pre_ro else [])
            break
        if pre_vowel is None and not pre_ro:
            out.append(toks[i])
            i += 1
            continue
        out.append(toks[i])  # the base consonant
        i += 1
        while i < len(toks) and toks[i].startswith("\u17d2") and toks[i] != "\u17d2\u179a":
            out.append(toks[i])  # its subscripts
            i += 1
        if pre_ro:
            out.append("\u17d2\u179a")
        while i < len(toks) and toks[i] in ("\u17c9", "\u17ca"):
            out.append(toks[i])  # register shifter (៉ ៊) comes before the vowel
            i += 1
        if pre_vowel:
            if i < len(toks) and (pre_vowel, toks[i]) in _SPLIT_VOWELS:
                out.append(_SPLIT_VOWELS[(pre_vowel, toks[i])])
                i += 1
            else:
                out.append(pre_vowel)
    return "".join(out).replace("\xa0", " ").strip()


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
