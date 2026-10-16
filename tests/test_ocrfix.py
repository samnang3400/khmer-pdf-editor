"""Putting right Tesseract's usual Khmer mistakes (no Tesseract needed)."""
import pymupdf
import pytest

from khmerpdf.ocr import OcrLine, clean
from khmerpdf.ocrfix import correct


@pytest.mark.parametrize("ocr,right", [
    ("ជាងនេ៖។ មិនតែប៉ុណ្ណោ៖ខ្ញុំ", "ជាងនេះ។ មិនតែប៉ុណ្ណោះខ្ញុំ"),     # ះ read as ៖
    ("សំខាន់១ និងមានឧទាហរណ៍ងាយ១", "សំខាន់ៗ និងមានឧទាហរណ៍ងាយៗ"),  # ៗ read as ១
    ("ពាក្យបរទេសខ្ល៖១", "ពាក្យបរទេសខ្លះៗ"),
    ("មូលដ្ឋានត្រឹះ", "មូលដ្ឋានគ្រឹះ"),                            # ត for គ
    ("ដែលអ្នកកំពុងកាន់ក្នង", "ដែលអ្នកកំពុងកាន់ក្នុង"),             # ុ dropped
    ("ងាយៗ ស្រលយល់", "ងាយៗ ស្រួលយល់"),
    ("បើទោះបឺជា", "បើទោះបីជា"),
    ("ទៅនីងជើង", "ទៅនឹងជើង"),
    ("មិត្តភភក្តិ", "មិត្តភក្តិ"),                                  # a letter read twice
])
def test_usual_mistakes(ocr, right):
    assert correct(ocr) == right


@pytest.mark.parametrize("text", [
    "សូមគូសបញ្ចាក់ថា៖",            # a real colon
    "ល្បះទី១ ទំព័រ១២",              # real digits
    "ក ខ គ ឃ ង",                    # letters on their own
    "ស្រៈ ◌ា «ដ» ។ល។",
    "សួស្តីមិត្តអ្នកសិក្សា",         # already right
    "Consonnes souscrites",
])
def test_right_text_is_left_alone(text):
    assert correct(text) == text


def _line(text, conf):
    return OcrLine(rect=pymupdf.Rect(0, 0, 100, 10), baseline=9, text=text, height=8, confidence=conf)


def test_noise_lines_are_dropped():
    lines = [_line("អារម្ភកថា", 93), _line("EEE", 0), _line("A", 85), _line("ជ", 21),
             _line(">ៗ2=0", 46), _line("១២", 40), _line("នេះ គឺជាសៀវភៅ", 80),
             _line("សេចក្តីពន្យល់នៃក្រមនីតិវិធី", 9)]  # a real line read badly stays
    assert [ln.text for ln in clean(lines)] == ["អារម្ភកថា", "១២", "នេះ គឺជាសៀវភៅ", "សេចក្តីពន្យល់នៃក្រមនីតិវិធី"]
