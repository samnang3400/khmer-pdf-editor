"""Khmer-aware line breaking.

Khmer does not separate words with spaces. Break opportunities, best first:
  1. explicit spaces and ZERO WIDTH SPACE (U+200B), which careful typists insert
  2. word boundaries found with a Khmer dictionary (ICU's word list, the one
     browsers use), so text without ZWSP still wraps between words
  3. orthographic syllable boundaries (fallback when a word is wider than the line)
"""
from __future__ import annotations

import gzip
from functools import lru_cache
from pathlib import Path

COENG = "្"
ZWSP = "​"


def is_khmer(ch: str) -> bool:
    return "ក" <= ch <= "៿" or "᧠" <= ch <= "᧿"


def _starts_syllable(ch: str) -> bool:
    # Consonants (U+1780-17A2) and independent vowels (U+17A3-17B3)
    return "ក" <= ch <= "ឳ"


def syllables(text: str) -> list[str]:
    """Split a run into orthographic syllables. A consonant after COENG is a
    subscript, so it stays in the same syllable."""
    out: list[str] = []
    cur = ""
    for i, ch in enumerate(text):
        prev = text[i - 1] if i else ""
        if cur and _starts_syllable(ch) and prev != COENG:
            out.append(cur)
            cur = ""
        elif cur and is_khmer(ch) != is_khmer(prev) and not ch.isspace():
            out.append(cur)
            cur = ""
        cur += ch
    if cur:
        out.append(cur)
    return out


DICTIONARY = Path(__file__).parent / "data" / "khmerdict.txt.gz"
MAX_WORD_SYLLABLES = 12


def _key(word: str) -> str:
    """Spellings that look the same match: subscript ដ and ត (្ដ / ្ត) are
    drawn alike and typed either way."""
    return word.replace("\u17d2\u178a", "\u17d2\u178f")


@lru_cache(maxsize=1)
def dictionary() -> frozenset[str]:
    """The Khmer word list (about 81,000 words), loaded on first use."""
    try:
        with gzip.open(DICTIONARY, "rt", encoding="utf-8") as f:
            return frozenset(_key(w) for w in (line.strip() for line in f) if w)
    except OSError:
        return frozenset()


def segment(run: str) -> list[str]:
    """Split text without spaces into words: the split with the fewest
    syllables the dictionary doesn't know, then the fewest words. Unknown
    syllables next to each other (a name, say) stay together as one piece."""
    syl = syllables(run)
    if len(syl) < 2 or not any(is_khmer(c) for c in run):
        return [run] if run else []
    known = dictionary()
    n = len(syl)
    best: list[tuple[int, int] | None] = [(0, 0)] + [None] * n
    back = [0] * (n + 1)
    for j in range(1, n + 1):
        for i in range(max(0, j - MAX_WORD_SYLLABLES), j):
            if best[i] is None:
                continue
            word = _key("".join(syl[i:j])) in known
            if not word and j - i > 1:
                continue
            cost = (best[i][0] + (0 if word else 1), best[i][1] + 1)
            if best[j] is None or cost < best[j]:
                best[j], back[j] = cost, i
    pieces: list[tuple[str, bool]] = []
    j = n
    while j:
        i = back[j]
        piece = "".join(syl[i:j])
        pieces.append((piece, j - i > 1 or _key(piece) in known))
        j = i
    out: list[str] = []
    prev_known = True
    for piece, ok in reversed(pieces):
        if out and not ok and not prev_known:
            out[-1] += piece  # keep unknown syllables together
        else:
            out.append(piece)
        prev_known = ok
    return out


def words(text: str) -> list[str]:
    """Split into pieces a line may break after: at spaces/ZWSP (kept with the
    piece before them) and, inside Khmer text, between dictionary words."""
    out: list[str] = []
    cur = ""
    for ch in text + "\0":
        if ch in (" ", ZWSP, "\0"):
            pieces = segment(cur) if cur else []
            if ch != "\0":
                if pieces:
                    pieces[-1] += ch
                else:
                    pieces = [ch]
            out += pieces
            cur = ""
        else:
            cur += ch
    return out


def wrap(text: str, max_width: float, measure) -> list[str]:
    """Greedy wrap. `measure(s) -> width` must use real shaping."""
    lines: list[str] = []
    for para in text.split("\n"):
        line = ""
        for word in words(para):
            if measure((line + word).rstrip(" " + ZWSP)) <= max_width:
                line += word
                continue
            if line:
                lines.append(line.rstrip(" " + ZWSP))
                line = ""
            if measure(word.rstrip(" " + ZWSP)) <= max_width:
                line = word
                continue
            # Word too long for a line: fall back to syllable breaks.
            for syl in syllables(word):
                if line and measure((line + syl).rstrip(" " + ZWSP)) > max_width:
                    lines.append(line.rstrip(" " + ZWSP))
                    line = ""
                line += syl
        lines.append(line.rstrip(" " + ZWSP))
    return lines
