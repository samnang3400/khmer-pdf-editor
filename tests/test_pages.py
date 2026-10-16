"""Page operations: insert, merge, extract, duplicate, delete, rotate, reorder."""
import pymupdf
import pytest

from khmerpdf import pages as P
from khmerpdf.engine import insert_khmer_text, replace_text, text_lines


def _doc(n=4):
    """n pages, each with its number written on it by this app."""
    doc = pymupdf.open()
    for i in range(n):
        page = doc.new_page()
        insert_khmer_text(page, pymupdf.Rect(50, 50, 400, 100), f"ទំព័រ {i + 1}", wrap_text=False)
    return doc


def _labels(doc):
    return [text_lines(p)[0].text if text_lines(p) else "" for p in doc]


@pytest.mark.parametrize("text,want", [("1-3, 5", [0, 1, 2, 4]), ("5,1", [4, 0]), ("2-", [1, 2, 3, 4, 5]),
                                       ("-2", [0, 1]), ("3 3 1-2", [2, 0, 1])])
def test_parse_ranges(text, want):
    assert P.parse_ranges(text, 6) == want


@pytest.mark.parametrize("text", ["", "0", "7", "2-9", "a", "3-1"])
def test_parse_ranges_rejects(text):
    with pytest.raises(ValueError):
        P.parse_ranges(text, 6)


def test_describe():
    assert P.describe([0, 1, 2, 4, 6, 7]) == "1-3, 5, 7-8"


def test_insert_blank_merge_and_extract():
    doc = _doc(3)
    P.insert_blank(doc, 1)
    assert _labels(doc) == ["ទំព័រ 1", "", "ទំព័រ 2", "ទំព័រ 3"]
    P.insert_pdf(doc, _doc(2), 4)
    assert _labels(doc)[4:] == ["ទំព័រ 1", "ទំព័រ 2"]
    part = P.extract(doc, [3, 0])
    assert _labels(part) == ["ទំព័រ 3", "ទំព័រ 1"]
    assert "ទំព័រ 3" in pymupdf.open(stream=part.tobytes())[0].get_text()


def test_duplicate_is_independent():
    doc = _doc(2)
    (copy,) = P.duplicate(doc, [0])
    assert copy == 1 and _labels(doc) == ["ទំព័រ 1", "ទំព័រ 1", "ទំព័រ 2"]
    replace_text(doc[1], text_lines(doc[1])[0], "កែ")                # edit the copy only
    assert _labels(doc) == ["ទំព័រ 1", "កែ", "ទំព័រ 2"]


def test_delete_rotate_reorder_move():
    doc = _doc(4)
    P.delete(doc, [1, 2])
    assert _labels(doc) == ["ទំព័រ 1", "ទំព័រ 4"]
    with pytest.raises(ValueError):
        P.delete(doc, [0, 1])
    P.rotate(doc, [0], 90)
    P.rotate(doc, [0], 90)
    P.rotate(doc, [1], -90)
    assert [p.rotation for p in doc] == [180, 270]
    doc = _doc(4)
    P.reorder(doc, [3, 0, 1, 2])
    assert _labels(doc) == ["ទំព័រ 4", "ទំព័រ 1", "ទំព័រ 2", "ទំព័រ 3"]
    assert P.move(doc, [1, 2], -1) == [0, 1]
    assert _labels(doc) == ["ទំព័រ 1", "ទំព័រ 2", "ទំព័រ 4", "ទំព័រ 3"]
    assert P.move(doc, [2], 1) == [3]
    assert _labels(doc) == ["ទំព័រ 1", "ទំព័រ 2", "ទំព័រ 3", "ទំព័រ 4"]
    assert P.move(doc, [3], 1) == [3]                                # already last
