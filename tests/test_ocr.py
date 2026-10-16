"""OCR of scanned pages (runs only when Tesseract is installed)."""
import pymupdf
import pytest

from khmerpdf import ocr
from khmerpdf.engine import add_ocr_layer, delete_line, replace_text, text_lines
from tests.test_recover import LINES, _broken_pdf

pytestmark = pytest.mark.skipif(not ocr.available(), reason="Tesseract not installed (set KHMERPDF_TESSERACT)")


def _scan():
    """The test page as a 200 dpi picture, like a scanner makes."""
    src = _broken_pdf()[0]
    doc = pymupdf.open()
    page = doc.new_page(width=src.rect.width, height=src.rect.height)
    page.insert_image(page.rect, pixmap=src.get_pixmap(dpi=200))
    return pymupdf.open(stream=doc.tobytes())


def _similar(a: str, b: str) -> float:
    import difflib
    return difflib.SequenceMatcher(None, a, b).ratio()


def test_scan_is_detected_and_recognised():
    page = _scan()[0]
    assert ocr.is_scanned(page) and not ocr.is_scanned(_broken_pdf()[0])
    lines = ocr.recognize(page)
    assert len(lines) == 3
    for got, want in zip(lines, LINES):
        assert _similar(got.text, want) > 0.9                      # keeps the subscripts
    assert abs(lines[0].baseline - 72.7) < 1.5                     # where the text really sits


def test_ocr_layer_makes_the_scan_searchable_and_editable():
    doc = _scan()
    page = doc[0]
    assert add_ocr_layer(page, ocr.recognize(page)) == 3
    lines = text_lines(page)
    assert len(lines) == 3 and all(ln.ocr and ln.needs_check for ln in lines)
    assert "ស្រាវជ្រាវ" in page.get_text()
    # running OCR again replaces the layer instead of doubling it
    assert add_ocr_layer(page, ocr.recognize(page)) == 3 and len(text_lines(page)) == 3
    # editing a line paints the scanned text out and writes the new text
    replace_text(page, lines[1], "សួស្តី")
    after = text_lines(pymupdf.open(stream=doc.tobytes())[0])
    assert "សួស្តី" in [ln.text for ln in after]


def test_deleting_ocr_text_blanks_the_scan_there():
    page = _scan()[0]
    add_ocr_layer(page, ocr.recognize(page))
    line = text_lines(page)[0]
    delete_line(page, line)
    pix = page.get_pixmap(clip=line.rect, dpi=72, colorspace=pymupdf.csGRAY)
    assert min(pix.samples) > 200                                   # only paper left


def test_pictures_stay_pictures():
    """OCR adds no text over photos or logos, nor on pages whose text is fine."""
    from tests.test_recover import KHMER_OS
    pic = pymupdf.open()
    pp = pic.new_page(width=200, height=120)
    pp.draw_rect(pp.rect, fill=(0.75, 0.85, 0.95))
    css = "@font-face{font-family:K;src:url(KhmerOSbattambang.ttf);} body{font-family:K;font-size:24px}"
    pp.insert_htmlbox(pymupdf.Rect(20, 30, 190, 100), "សាលារៀន", css=css, archive=pymupdf.Archive(str(KHMER_OS)))
    png = pp.get_pixmap(dpi=150).tobytes("png")

    page = _broken_pdf()[0]                                   # good (recovered) text + a picture with text
    page.insert_image(pymupdf.Rect(300, 300, 500, 420), stream=png)
    assert add_ocr_layer(page, ocr.recognize(page)) == 0

    scan = _scan()[0]                                         # a scan with a photo pasted on it
    scan.insert_image(pymupdf.Rect(300, 300, 500, 420), stream=png)
    assert add_ocr_layer(scan, ocr.recognize(scan)) == 3
    assert not any(ln.rect.intersects(pymupdf.Rect(300, 300, 500, 420)) for ln in text_lines(scan))
