"""Legacy (pre-Unicode) Khmer fonts: Limon.

Limon fonts draw Khmer glyphs in place of ASCII/Latin-1 letters, so a PDF
made with them "contains" text like `km<uCa` that only looks like Khmer in
that font. We map the font's character codes back to Unicode Khmer and put
each syllable into logical order: Limon text is typed in *visual* order
(េ and ្រ before the consonant they belong to, vowels split into parts).

The code -> character table comes from khmerconverter's fontdata.xml
(Khmer OS project, LGPL-2.1), resolved for the Limon S1/R1/F1... family.
"""
from __future__ import annotations

import re

LIMON: dict[bytes, str] = {
    b'\x21': "1", b'\x23': "3", b'\x24': "4", b'\x25': "5", b'\x26': "7", b'\x28': "9",
    b'\x29': "ប", b'\x2a': "8", b'\x2b': "\u17ce", b'\x2c': "\u17d2ប", b'\x2d': "-", b'\x2e': "។",
    b'\x2f': ",", b'\x30': "០", b'\x31': "១", b'\x32': "២", b'\x33': "៣", b'\x34': "៤",
    b'\x35': "៥", b'\x36': "៦", b'\x37': "៧", b'\x38': "៨", b'\x39': "៩", b'\x3a': "\u17c9",
    b'\x3b': "\u17cb", b'\x3c': "\u17d2ព", b'\x3d': "=", b'\x3e': ".", b'\x40': "2", b'\x41': "\u17c5",
    b'\x42': "ព", b'\x43': "ជ", b'\x44': "ឌ", b'\x45': "\u17c2", b'\x46': "ធ", b'\x47': "អ",
    b'\x48': "\u17c7", b'\x49': "\u17b8", b'\x4a': "\u17d2ញ", b'\x4b': "គ", b'\x4c': "ឡ", b'\x4d': "\u17c6",
    b'\x4e': "ណ", b'\x4f': "\u17bf", b'\x50': "ភ", b'\x51': "ឈ", b'\x52': "\u17d2រ", b'\x53': "\u17d2ស",
    b'\x54': "ទ", b'\x55': "\u17bc", b'\x56': "\u17d2វ", b'\x57': "\u17ba", b'\x58': "ឃ", b'\x59': "\u17bd",
    b'\x5a': "ឍ", b'\x5b': "ឱ\u17d2យ", b'\x5c': "ឥ", b'\x5d': "ឧ", b'\x5e': "6", b'\x5f': "\u17cd",
    b'\x60': "៛", b'\x61': "\u17b6", b'\x62': "ប", b'\x63': "ច", b'\x64': "ដ", b'\x65': "\u17c1",
    b'\x66': "ថ", b'\x67': "ង", b'\x68': "ហ", b'\x69': "\u17b7", b'\x6a': "ញ", b'\x6b': "ក",
    b'\x6c': "ល", b'\x6d': "ម", b'\x6e': "ន", b'\x6f': "\u17c0", b'\x70': "ផ", b'\x71': "ឆ",
    b'\x72': "រ", b'\x73': "ស", b'\x74': "ត", b'\x75': "\u17bb", b'\x76': "វ", b'\x77': "\u17b9",
    b'\x78': "ខ", b'\x79': "យ", b'\x7a': "ឋ", b'\x7b': "\u201c", b'\x7c': "ឦ", b'\x7d': "\u201d",
    b'\x91': "\u17ca", b'\x92': "\u17cc", b'\xa1': "!", b'\xa2': "\u17d2ជ", b'\xa3': "\u17d2ញ",
    b'\xa4': "\u17d2ញ\u17bb", b'\xa5': "\u17d2អ", b'\xa6': ")", b'\xa7': "\u17d2ធ", b'\xa9': "\u17d2ច",
    b'\xab': "ឪ", b'\xac': "(", b'\xae': "\u17d2រ", b'\xb0': "%", b'\xb2': "ៗ", b'\xb3': "\u17c8",
    b'\xb4': "ខ\u17d2ញ\u17bb\u17c6", b'\xb5': "\u17d2ម", b'\xb6': "\u17d2ង", b'\xb9': "៛", b'\xbb': "ឱ",
    b'\xbc': "/", b'\xbd': "\u17d0", b'\xbe': "\u17cf", b'\xbf': "\u17c6", b'\xc1': "\u17d2គ", b'\xc4': "\u17d2ឈ",
    b'\xc5': "\u17ba", b'\xc6': "\u17d2ឍ", b'\xc7': "\u17d2ឃ", b'\xc9': "ឯ", b'\xcd': "\u17b8",
    b'\xd0': "\u17d2ឌ", b'\xd1': "\u17d2ណ", b'\xd3': "\u17bf", b'\xd6': "\u17d2ភ", b'\xd7': "\u17b7\u17cd",
    b'\xd8': "\u17d2ញ", b'\xda': "\u17bc", b'\xdc': "\u17bd", b'\xde': "\u17d2ទ", b'\xdf': "\u17d2ថ",
    b'\xe1': "\u17d2ក", b'\xe4': "\u17d2ឆ", b'\xe5': "\u17b9", b'\xe6': "\u17d2ឋ", b'\xe7': "\u17d2ខ",
    b'\xe9': "\u17c3", b'\xed': "\u17b7", b'\xf0': "\u17d2ហ", b'\xf1': "\u17d2ន", b'\xf3': "\u17c0",
    b'\xf6': "\u17d2ផ", b'\xf7': "+", b'\xf8': "\u17d2ល", b'\xfa': "\u17bb", b'\xfc': "\u17d2យ",
    b'\xfe': "\u17d2ត", b'\x20\xb3': "\u00a0៖", b'\x29\x41': "ប\u17c5", b'\x29\x61': "ប\u17b6",
    b'\x42\xa3': "ឭ", b'\x42\xa4': "ឮ", b'\x42\xa7': "ឰ", b'\x47\x61': "អ\u17b6", b'\x5d\x5f': "ឳ",
    b'\x5d\x75': "ឩ", b'\x5d\x91': "ឨ", b'\x62\xa3': "ឫ", b'\x62\xa4': "ឬ", b'\x2e\x6c\x2e': "៘",
}

_MAX_KEY = max(len(k) for k in LIMON)

_PREBASE = {"\u17c1", "\u17c2", "\u17c3", "\u17d2រ"}   # េ ែ ៃ and ្រ, drawn before the consonant
# េ + the right-hand part of a split vowel = the whole vowel
_SPLIT = {"\u17b6": "\u17c4", "\u17b8": "\u17be", "\u17bf": "\u17bf", "\u17c0": "\u17c0", "\u17c5": "\u17c5"}


def is_limon_font(name: str) -> bool:
    """'ABCDEF+LimonS1', 'Limon R1 Regular' ..."""
    return "limon" in re.sub(r"[^a-z]", "", name.lower())


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


def _tokens(codes: bytes) -> list[str]:
    toks, i = [], 0
    while i < len(codes):
        for n in range(min(_MAX_KEY, len(codes) - i), 0, -1):
            t = LIMON.get(codes[i:i + n])
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


def limon_to_unicode(text: str) -> str:
    """Text extracted from a Limon-font PDF -> Unicode Khmer."""
    out: list[str] = []
    pending: list[str] = []            # pre-base forms waiting for their consonant
    base, marks = None, []

    def flush():
        nonlocal base, marks
        if base is not None:
            out.append(_syllable(base, marks))
        base, marks = None, []

    for t in _tokens(_codes(text)):
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
