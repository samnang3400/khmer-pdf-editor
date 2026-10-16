"""Optical character recognition (Tesseract) for scanned pages and for text
whose font we can't decode.

We run the Tesseract program and read its hOCR output. (MuPDF has Tesseract
built in, but it keeps only the first character of each recognised symbol,
which drops every Khmer subscript: ក្តី comes back as ក្ី.)

Where Tesseract is looked for, first found wins:
  1. $KHMERPDF_TESSERACT
  2. tesseract/ next to the app (bundled by the Windows installer)
  3. `tesseract` on PATH, then the usual Windows install folders
Language data: $KHMERPDF_TESSDATA, else khmerpdf/tessdata (khm + eng, shipped).
"""
from __future__ import annotations

import html
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pymupdf

TESSDATA = Path(__file__).parent / "tessdata"
OCR_DPI = 300
LANGS = "khm+eng"


def _app_dir() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))


def find_tesseract() -> str | None:
    env = os.environ.get("KHMERPDF_TESSERACT")
    if env and Path(env).is_file():
        return env
    exe = "tesseract.exe" if os.name == "nt" else "tesseract"
    bundled = _app_dir() / "tesseract" / exe
    if bundled.is_file():
        return str(bundled)
    found = shutil.which("tesseract")
    if found:
        return found
    if os.name == "nt":
        for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                     os.environ.get("LOCALAPPDATA", "")):
            p = Path(base) / "Tesseract-OCR" / exe
            if p.is_file():
                return str(p)
    return None


def tessdata_dir() -> Path:
    return Path(os.environ.get("KHMERPDF_TESSDATA", TESSDATA))


def available() -> bool:
    return find_tesseract() is not None and (tessdata_dir() / "khm.traineddata").is_file()


@dataclass
class OcrLine:
    rect: pymupdf.Rect     # page coordinates (as displayed)
    baseline: float
    text: str
    height: float          # Tesseract's line height (x_size), in points
    confidence: float      # 0..100, mean of the words


def _bbox(title: str) -> tuple[float, ...]:
    m = re.search(r"bbox (\d+) (\d+) (\d+) (\d+)", title)
    return tuple(float(v) for v in m.groups()) if m else (0, 0, 0, 0)


def parse_hocr(hocr: str, scale: float, origin: tuple[float, float] = (0, 0)) -> list[OcrLine]:
    """hOCR -> lines in page coordinates. `scale` = points per image pixel."""
    lines: list[OcrLine] = []
    ox, oy = origin
    parts = re.split(r"<span class='ocr_line'", hocr)[1:]
    for part in parts:
        title = re.match(r"[^>]*title=\"([^\"]*)\"", part)
        if not title:
            continue
        t = title.group(1)
        x0, y0, x1, y1 = _bbox(t)
        m = re.search(r"baseline (-?[\d.]+) (-?[\d.]+)", t)
        base = y1 + (float(m.group(2)) if m else 0)
        m = re.search(r"x_size ([\d.]+)", t)
        xsize = float(m.group(1)) if m else y1 - y0
        words = re.findall(r"<span class='ocrx_word'[^>]*title='([^']*)'[^>]*>(.*?)</span>", part, re.S)
        out, confs, prev_x1 = "", [], None
        for wt, wtext in words:
            wtext = html.unescape(re.sub(r"<[^>]+>", "", wtext)).strip()
            if not wtext:
                continue
            wx0, _, wx1, _ = _bbox(wt)
            if prev_x1 is not None:
                # a real gap is a space; Tesseract also splits words at tight joins
                out += " " if wx0 - prev_x1 > 0.2 * xsize else ""
            out += wtext
            prev_x1 = wx1
            c = re.search(r"x_wconf (\d+)", wt)
            confs.append(float(c.group(1)) if c else 0.0)
        if not out.strip():
            continue
        lines.append(OcrLine(rect=pymupdf.Rect(ox + x0 * scale, oy + y0 * scale, ox + x1 * scale, oy + y1 * scale),
                             baseline=oy + base * scale, text=out, height=xsize * scale,
                             confidence=sum(confs) / len(confs) if confs else 0.0))
    return lines


def recognize(page: pymupdf.Page, clip: pymupdf.Rect | None = None, dpi: int = OCR_DPI) -> list[OcrLine]:
    """OCR a page (or part of it). Raises RuntimeError if Tesseract is missing."""
    png = render(page, clip, dpi)
    return run(png, dpi, clip.tl if clip else (0, 0))


def render(page: pymupdf.Page, clip: pymupdf.Rect | None = None, dpi: int = OCR_DPI) -> bytes:
    """The page image Tesseract reads (separate, so callers can run `run` in a thread)."""
    return page.get_pixmap(dpi=dpi, clip=clip, alpha=False, colorspace=pymupdf.csGRAY).tobytes("png")


def run(png: bytes, dpi: int = OCR_DPI, origin=(0, 0)) -> list[OcrLine]:
    exe = find_tesseract()
    if not exe:
        raise RuntimeError("Tesseract OCR was not found.")
    langs = LANGS if (tessdata_dir() / "eng.traineddata").is_file() else "khm"
    cmd = [exe, "stdin", "stdout", "-l", langs, "--tessdata-dir", str(tessdata_dir()), "--dpi", str(dpi),
           "--psm", "3", "-c", "tessedit_create_hocr=1", "-c", "tessedit_create_txt=0"]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # no console flashing up on Windows
    res = subprocess.run(cmd, input=png, capture_output=True, timeout=300, creationflags=flags)
    if res.returncode != 0:
        raise RuntimeError(res.stderr.decode("utf-8", "replace").strip() or "Tesseract failed")
    return parse_hocr(res.stdout.decode("utf-8", "replace"), 72 / dpi, tuple(origin))


def is_scanned(page: pymupdf.Page) -> bool:
    """No real text, but an image covering most of the page."""
    if page.get_text("text").strip():
        return False
    area = abs(page.rect)
    for img in page.get_image_info():
        if abs(pymupdf.Rect(img["bbox"]) & page.rect) > 0.5 * area:
            return True
    return False
