"""Legacy (pre-Unicode) Khmer fonts: Limon, ABC, Truth, Baidok, Khek, FK.

These fonts draw Khmer glyphs in place of ASCII/Latin-1 letters, so a PDF
made with them "contains" text like `km<uCa` that only looks like Khmer in
that font. We map the font's character codes back to Unicode Khmer and put
each syllable into logical order: legacy text is typed in *visual* order
(េ and ្រ before the consonant they belong to, vowels split into parts).

The code -> character tables (legacy_tables.py) come from khmerconverter's
fontdata.xml (Khmer OS project, LGPL-2.1).
"""
from __future__ import annotations

import re

from .legacy_tables import FONTS, TABLES

LIMON = TABLES["limon"]
_MAX_KEY = max(len(k) for t in TABLES.values() for k in t)
# families by the start of their font names, for variants the tables don't list
_PREFIXES = (("limon", "limon"), ("abctext", "abc"), ("truth", "truth"), ("baidok", "baidok"),
             ("khek", "khek"))

_PREBASE = {"\u17c1", "\u17c2", "\u17c3", "\u17d2រ"}   # េ ែ ៃ and ្រ, drawn before the consonant
# េ + the right-hand part of a split vowel = the whole vowel
_SPLIT = {"\u17b6": "\u17c4", "\u17b8": "\u17be", "\u17bf": "\u17bf", "\u17c0": "\u17c0", "\u17c5": "\u17c5"}


def legacy_family(name: str) -> str | None:
    """'ABCDEF+LimonS1' -> 'limon', 'ABC-TEXT-05' -> 'abc', 'Khmer OS' -> None."""
    key = re.sub(r"[^a-z0-9]", "", re.sub(r"^[A-Z]{6}\+", "", name).lower())
    key = re.sub(r"(regular|normal|bold|italic)$", "", key)
    if key in FONTS:
        return FONTS[key]
    if key.startswith("abctext") and key.endswith("a") and key[:-1] in FONTS:
        return "abc-zwsp"
    return next((family for prefix, family in _PREFIXES if key.startswith(prefix)), None)


def is_limon_font(name: str) -> bool:
    """A legacy-encoded Khmer font (Limon, ABC, ...)? (The name is historical.)"""
    return legacy_family(name) is not None


def _codes(text: str) -> bytes:
    """The font's character codes. PDF text extraction gives Windows-1252
    characters for them (0x91 comes back as ‘), so encode them back."""
    out = bytearray()
    for ch in text:
        try:
            out += ch.encode("cp1252")
        except UnicodeEncodeError:
            out += ch.encode("latin-1") if ord(ch) < 256 else b"?"
    return bytes(out)


def _tokens(codes: bytes, table: dict[bytes, str]) -> list[str]:
    toks, i = [], 0
    while i < len(codes):
        for n in range(min(_MAX_KEY, len(codes) - i), 0, -1):
            t = table.get(codes[i:i + n])
            if t is not None:
                toks.append(t)
                i += n
                break
        else:
            toks.append(chr(codes[i]))
            i += 1
    return toks


def _is_base(t: str) -> bool:
    return "ក" <= t[0] <= "ឳ"


def _is_mark(t: str) -> bool:
    return "\u17b6" <= t[0] <= "\u17d3" or t[0] == "\u17dd"


def _rank(t: str) -> int:
    """Logical order of the marks after a base consonant."""
    if t == "\u17d2រ":
        return 1                       # ្រ comes after other subscripts
    if t[0] == "\u17d2":
        return 0                       # subscript consonants
    if t[0] in "\u17c9\u17ca\u17cc":
        return 2                       # register shifters, robat
    if "\u17b6" <= t[0] <= "\u17c5":
        return 3                       # dependent vowels
    return 4                           # signs (ំ ះ ់ ...)


def _syllable(base: str, marks: list[str]) -> str:
    marks = sorted(marks, key=_rank)   # stable: keeps the typed order within a rank
    out = []
    for m in marks:
        if out and out[-1] == "\u17c1" and m in _SPLIT:
            out[-1] = _SPLIT[m]
        else:
            out.append(m)
    return base + "".join(out)


def limon_to_unicode(text: str, family: str = "limon") -> str:
    """Text extracted from a legacy-font PDF (Limon by default) -> Unicode Khmer."""
    return to_unicode(text, family)


def to_unicode(text: str, family: str) -> str:
    """Text in a legacy font of this family -> Unicode Khmer."""
    out: list[str] = []
    pending: list[str] = []            # pre-base forms waiting for their consonant
    base, marks = None, []

    def flush():
        nonlocal base, marks
        if base is not None:
            out.append(_syllable(base, marks))
        base, marks = None, []

    for t in _tokens(_codes(text), TABLES[family]):
        if t in _PREBASE:
            pending.append(t)
        elif _is_base(t):
            flush()
            base, marks, pending = t, pending, []
        elif _is_mark(t) and base is not None:
            marks.append(t)
        else:
            flush()
            out += pending + [t]
            pending = []
    flush()
    return "".join(out + pending)
