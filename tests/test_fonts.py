"""The bundled Khmer fonts and fonts installed on the computer."""
import shutil
from pathlib import Path

import pymupdf
import pytest

from khmerpdf import engine as E
from khmerpdf import styles as ST

EXTRA = sorted((E.FONT_DIR / "extra").glob("*.ttf"))


def test_extra_fonts_are_offered_by_their_names():
    fonts = E.bundled_fonts()
    for name in ("Khmer Mondulkiri", "Khmer Busra", "Khmer Busra Bold", "Tlok", "Khmer M1", "Khmer SBBIC Serif"):
        assert name in fonts, name
    assert len(EXTRA) == 36 and all(str(p) in fonts.values() for p in EXTRA)


def test_extra_fonts_have_their_licence_texts():
    folder = E.FONT_DIR / "extra"
    for f in ("OFL.txt", "LGPL-2.1.txt", "LICENSE-Apache-2.0.txt", "README.md"):
        assert (folder / f).is_file(), f


def test_bold_versions_are_used_for_bold():
    assert ST.bold_path("Khmer Busra").endswith("Khmer-Busra-Bold.ttf")
    assert ST.bold_path("Khmer Mondulkiri").endswith("Khmer-Mondulkiri-Bold.ttf")


@pytest.mark.parametrize("path", EXTRA, ids=[p.stem for p in EXTRA])
def test_each_extra_font_writes_khmer_that_reads_back(path):
    text = "ព្រះរាជាណាចក្រកម្ពុជា ស្ត្រី ១២៣"
    font = E.load_font(str(path))
    doc = pymupdf.open()
    page = doc.new_page()
    E.insert_khmer_text(page, pymupdf.Rect(50, 50, 550, 120), text, font=font, wrap_text=False)
    assert E.text_lines(page)[0].text == text


def test_fonts_in_the_users_own_folder_are_found_whatever_their_names(tmp_path, monkeypatch):
    import os
    if os.name == "nt":  # per-user fonts on Windows
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        own = tmp_path / "Microsoft" / "Windows" / "Fonts" / "Mine"
    else:
        own = tmp_path / ".local/share/fonts/Mine"
    own.mkdir(parents=True)
    shutil.copy(E.FONT_DIR / "extra" / "Tlok.ttf", own / "AKbalthom Kbach.ttf")   # no Khmer-like name
    shutil.copy(E.FONT_DIR / "extra" / "OFL.txt", own / "notes.ttf")              # not a font
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    found = E.system_khmer_fonts()
    assert found.get("AKbalthom Kbach") == str(own / "AKbalthom Kbach.ttf")
    assert "notes" not in found
