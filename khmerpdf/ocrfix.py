"""Correct the usual mistakes Tesseract makes in Khmer.

Tesseract reads some Khmer signs as look-alikes: ះ as ៖, ៗ as the digit ១,
ត as គ, one of ិ ី ឹ ឺ as another, and it often drops a ុ or ួ. Two rules
fix ះ and ៗ where the context leaves no doubt; the rest is checked against
the Khmer word list: a word the list doesn't know is changed only when one
such edit turns it into words the list does know.
"""
from __future__ import annotations

import re

from .linebreak import _key, dictionary, is_khmer, segment, syllables

# what Tesseract confuses a character with (tried in this order)
CONFUSE = {
    "ត": "គ", "គ": "ត",
    "ិ": "ីឹ", "ី": "ឹិ", "ឹ": "ឺី", "ឺ": "ឹី",
    "ុ": "ូ", "ូ": "ុួ", "ួ": "ូ",
    "៉": "៊", "៊": "៉",
    "ដ": "ជ", "ជ": "ដ",
}
DROPPED = "ុួូំ"          # vowel signs Tesseract leaves out
MAX_EDITS = 2             # per unknown word
CONTEXT = 4               # syllables either side considered when re-segmenting
NUMBERING = ("ទី", "លេខ", "ទំព័រ", "ជំពូក", "ភាគ", "មាត្រា", "ថ្នាក់")  # ...១ here is a real digit

_CONS = re.compile(r"[ក-ឳ]")
_DIGIT = re.compile(r"[០-៩0-9]")


def _rules(text: str) -> str:
    out = list(text)
    for i, ch in enumerate(text):  # ៗ first: ខ្ល៖១ is ខ្លះៗ
        prev = text[i - 1] if i else ""
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if (ch == "១" and prev and is_khmer(prev) and not _DIGIT.match(prev) and not _DIGIT.match(nxt)
                and not text[:i].endswith(NUMBERING)):
            out[i] = "ៗ"  # repetition sign right after a word
    text = "".join(out)
    for i, ch in enumerate(text):
        prev = text[i - 1] if i else ""
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if ch == "៖" and prev and is_khmer(prev) and prev != "៖" and nxt and (_CONS.match(nxt) or nxt in "។ៗ"):
            out[i] = "ះ"  # a colon is never followed straight by a letter
    return "".join(out)


def _cost(syl: list[str]) -> tuple[int, int]:
    """(syllables no dictionary word covers, number of words) for one run:
    lower is better. The word list has single syllables too, so a real
    word beats the same letters read as fragments by having fewer pieces."""
    known = dictionary()
    pieces = segment("".join(syl))
    return sum(len(syllables(p)) for p in pieces if _key(p) not in known), len(pieces)


def _edits(s: str):
    """Every single edit of one syllable."""
    for i, ch in enumerate(s):
        for alt in CONFUSE.get(ch, ""):
            yield s[:i] + alt + s[i + 1:]
    for i, ch in enumerate(s):
        if _CONS.match(ch) and (i + 1 == len(s) or s[i + 1] != "្") and (i == 0 or s[i - 1] != "្") or (
                i and s[i - 1] == "្"):
            for v in DROPPED:
                if v not in s:
                    yield s[:i + 1] + v + s[i + 1:]


def _fix_run(run: str) -> str:
    syl = syllables(run)
    if not _CONS.search(run):
        return run  # a lone sign between digits or symbols: nothing to look up
    known = dictionary()
    for _ in range(MAX_EDITS * 4):
        if not _cost(syl)[0]:
            break
        best = None
        # only syllables in pieces the dictionary doesn't know are candidates
        pos = 0
        unknown_at: list[int] = []
        for p in segment("".join(syl)):
            n = len(syllables(p))
            if _key(p) not in known:
                unknown_at += range(pos, pos + n)
            pos += n
        # the mistake may be in the syllable next to the one left unknown (ក្ន|ង)
        near = sorted({j for k in unknown_at for j in (k - 1, k, k + 1) if 0 <= j < len(syl)})
        for k in near:
            if len(syl[k]) == 1 and not (k and syl[k] == syl[k - 1]):
                continue  # a letter on its own (a list of letters, ។ល។, «ដ») is left alone
            lo, hi = max(0, k - CONTEXT), min(len(syl), k + CONTEXT + 1)
            before = _cost(syl[lo:hi])
            twice = [""] if k and syl[k] == syl[k - 1] and len(syl[k]) == 1 else []  # ភភក្តិ: read twice
            for cand in twice + list(_edits(syl[k])):
                after = _cost(syl[lo:k] + (syllables(cand) if cand else []) + syl[k + 1:hi])
                gain = (before[0] - after[0], before[1] - after[1])
                if gain[0] > 0 and gain[1] >= 0 and (best is None or gain > best[0]):
                    best = (gain, k, cand)
        if best is None:
            break
        syl = syl[:best[1]] + (syllables(best[2]) if best[2] else []) + syl[best[1] + 1:]
    return "".join(syl)


def correct(text: str) -> str:
    """The OCR'd line with Tesseract's usual Khmer mistakes put right."""
    if not dictionary() or not any(is_khmer(c) for c in text):
        return text
    text = _rules(text)
    # Khmer runs between spaces / punctuation are corrected one at a time
    parts = re.split(r"([^ក-៓៝]+)", text)
    return "".join(p if i % 2 else _fix_run(p) for i, p in enumerate(parts))
