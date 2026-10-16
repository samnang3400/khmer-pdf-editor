"""Khmer line breaking with a word dictionary (no ZWSP needed)."""
import pytest

from khmerpdf.engine import default_font
from khmerpdf.linebreak import segment, words, wrap

SENTENCE = "ការប្រកាសព័ត៌មានស្តីពីការចុះឈ្មោះសិស្សថ្មី"


@pytest.mark.parametrize("text,want", [
    (SENTENCE, ["ការប្រកាស", "ព័ត៌មាន", "ស្តីពី", "ការ", "ចុះឈ្មោះ", "សិស្ស", "ថ្មី"]),
    ("ខ្ញុំស្រលាញ់ប្រទេសកម្ពុជា", ["ខ្ញុំ", "ស្រលាញ់", "ប្រទេស", "កម្ពុជា"]),
    ("ស្ដីពី", ["ស្ដីពី"]),                     # ្ដ and ្ត spellings both found
    ("Hello", ["Hello"]),
])
def test_segment(text, want):
    assert segment(text) == want
    assert "".join(segment(text)) == text       # nothing added or lost


def test_words_keep_spaces_and_zwsp():
    assert words("សួស្តីពិភពលោក អ្នក​ទាំងអស់") == ["សួស្តី", "ពិភពលោក ", "អ្នក​", "ទាំងអស់"]


def test_text_without_spaces_wraps_between_words():
    font = default_font()
    text = SENTENCE * 3
    lines = wrap(text, 160, lambda s: font.width(s, 12))
    assert len(lines) > 2 and "".join(lines) == text
    pieces = segment(text)
    ends, pos = set(), 0
    for p in pieces:
        pos += len(p)
        ends.add(pos)
    pos = 0
    for line in lines[:-1]:
        pos += len(line)
        assert pos in ends                         # every break is between two words
        assert font.width(line, 12) <= 160
