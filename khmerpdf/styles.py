"""Styled text: a line or paragraph as runs of (text, Style).

The plain text stays the main value everywhere; runs are an optional parallel
description of it ("these 5 characters are bold"). Concatenating a run list's
texts always gives the text it describes.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

ITALIC_SLANT = 0.21   # tan(12°): how far synthetic italic leans
BOLD_STROKE = 0.035   # synthetic bold: outline stroke width, in em


@dataclass(frozen=True)
class Style:
    font: str = ""                  # font name ("" = the paragraph's font)
    bold: bool = False
    italic: bool = False
    color: tuple[float, float, float] | None = None  # None = the paragraph's colour

    def resolved(self, font: str, color: tuple[float, float, float]) -> "Style":
        return Style(self.font or font, self.bold, self.italic, tuple(self.color or color))


Run = tuple[str, Style]
PLAIN = Style()


def runs_text(runs: list[Run]) -> str:
    return "".join(t for t, _ in runs)


def merge(runs: list[Run]) -> list[Run]:
    """Join neighbouring runs of the same style; drop empty runs."""
    out: list[Run] = []
    for t, s in runs:
        if not t:
            continue
        if out and out[-1][1] == s:
            out[-1] = (out[-1][0] + t, s)
        else:
            out.append((t, s))
    return out


def plain(runs: list[Run] | None) -> bool:
    """True when the runs carry no styling (all in the paragraph's own font and colour)."""
    return not runs or all(s == PLAIN for _, s in runs)


def slice_runs(runs: list[Run], start: int, end: int) -> list[Run]:
    out, pos = [], 0
    for t, s in runs:
        a, b = max(start, pos), min(end, pos + len(t))
        if a < b:
            out.append((t[a - pos:b - pos], s))
        pos += len(t)
    return merge(out)


def align_runs(runs: list[Run], text: str) -> list[Run]:
    """Runs re-cut to describe `text`, an edited version of their own text:
    unchanged characters keep their style; typed ones take the style of the
    character before them (at the very start, of the one after)."""
    from difflib import SequenceMatcher
    src = runs_text(runs)
    if src == text:
        return merge(runs)
    styles = [s for t, s in runs for _ in t]
    if not styles:
        return [(text, PLAIN)] if text else []
    out: list[Style | None] = [None] * len(text)
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, src, text, autojunk=False).get_opcodes():
        if tag == "equal":
            out[j1:j2] = styles[i1:i2]
    for j in range(len(text)):  # new characters: like the one before them
        if out[j] is None and j:
            out[j] = out[j - 1]
    first = next((s for s in out if s is not None), styles[0])
    out = [s if s is not None else first for s in out]
    return merge([(ch, s) for ch, s in zip(text, out)])


def line_slices(text: str, lines: list[str]) -> list[tuple[int, int]]:
    """Where each wrapped line came from in `text` (wrapping may drop the
    spaces and invisible breaks it wrapped at)."""
    out, pos = [], 0
    for ln in lines:
        at = text.find(ln, pos) if ln else pos
        if at < 0:
            at = pos
        out.append((at, at + len(ln)))
        pos = at + len(ln)
    return out


def to_json(runs: list[Run]) -> str:
    return json.dumps([[len(t), s.font, int(s.bold), int(s.italic), list(s.color) if s.color else None]
                       for t, s in runs], separators=(",", ":"))


def from_json(data: str, text: str) -> list[Run]:
    try:
        spec = json.loads(data)
        out, pos = [], 0
        for n, font, bold, italic, color in spec:
            out.append((text[pos:pos + n], Style(font, bool(bold), bool(italic), tuple(color) if color else None)))
            pos += n
        if pos != len(text):
            return []
        return merge(out)
    except (ValueError, TypeError):
        return []


# --------------------------------------------------------------------------
# Fonts by name (for runs in another font, and bold versions)
# --------------------------------------------------------------------------
_paths: dict[str, str] = {}


def register(name: str, path: str) -> None:
    """Make a font known by its display name (the app registers its fonts)."""
    _paths.setdefault(name, path)


def font_path(name: str) -> str | None:
    if name in _paths:
        return _paths[name]
    from .engine import bundled_fonts, norm_font_name, FONT_DIR
    for n, p in bundled_fonts().items():
        _paths.setdefault(n, p)
    if name in _paths:
        return _paths[name]
    key = norm_font_name(name)
    for n, p in list(_paths.items()):
        if key in (norm_font_name(n), norm_font_name(Path(p).name)):
            return p
    for p in sorted((FONT_DIR / "reference").glob("*.ttf")):
        if norm_font_name(p.name) == key:
            return str(p)
    return None


_BOLD = re.compile(r"(?i)(bold|black|heavy|semibold|extrabold)")


def is_bold_name(name: str) -> bool:
    return bool(_BOLD.search(re.sub(r"^[A-Z]{6}\+", "", name or "")))


def is_italic_name(name: str) -> bool:
    return bool(re.search(r"(?i)(italic|oblique)", name or ""))


def family(name: str) -> str:
    """'Battambang-Bold' / 'Battambang Bold' -> 'Battambang'."""
    name = re.sub(r"^[A-Z]{6}\+", "", name or "")
    return re.sub(r"(?i)[-_ ,]*(regular|normal|bold|italic|oblique|semibold|extrabold|black|heavy|"
                  r"bolditalic|boldoblique)\b", "", name).strip(" -_,") or name


def bold_path(name: str) -> str | None:
    """A real bold font file for this font, if there is one."""
    from .engine import norm_font_name
    fam = norm_font_name(family(name))
    for cand in (f"{family(name)} Bold", f"{family(name)}-Bold"):
        p = font_path(cand)
        if p and norm_font_name(family(Path(p).stem)) == fam and is_bold_name(Path(p).stem):
            return p
    return None


def norm(name: str) -> str:
    from .engine import norm_font_name
    return norm_font_name(name or "")
