"""Khmer-aware line breaking.

Khmer does not separate words with spaces. Break opportunities, best first:
  1. explicit spaces and ZERO WIDTH SPACE (U+200B), which careful typists insert
  2. orthographic syllable boundaries (fallback when a run is wider than the line)

TODO (phase 2): dictionary-based word segmentation for natural breaks
in text that has no ZWSP.
"""
from __future__ import annotations

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


def words(text: str) -> list[str]:
    """Split on spaces/ZWSP, keeping the separator attached to the left word."""
    out: list[str] = []
    cur = ""
    for ch in text:
        cur += ch
        if ch in (" ", ZWSP):
            out.append(cur)
            cur = ""
    if cur:
        out.append(cur)
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
