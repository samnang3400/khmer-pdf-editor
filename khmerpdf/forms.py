"""Filling PDF forms (AcroForm fields), with Khmer that displays correctly.

A text field's look (its appearance stream) is normally drawn by the viewer
or by MuPDF from the field's font, usually Helvetica, without Khmer shaping:
subscripts and vowels come out broken. We draw the value ourselves, shaped
with HarfBuzz like all other text in this app, and store the value itself
in /V so the form data stays real Unicode text.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pymupdf

from .engine import KhmerFont, _glyph_xobject, _hex16, _new_obj, default_font, layout

KINDS = {pymupdf.PDF_WIDGET_TYPE_TEXT: "text", pymupdf.PDF_WIDGET_TYPE_CHECKBOX: "checkbox",
         pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON: "radio", pymupdf.PDF_WIDGET_TYPE_COMBOBOX: "choice",
         pymupdf.PDF_WIDGET_TYPE_LISTBOX: "choice", pymupdf.PDF_WIDGET_TYPE_SIGNATURE: "signature",
         pymupdf.PDF_WIDGET_TYPE_BUTTON: "button"}
PAD = 2.0  # pt between a text field's border and its text


@dataclass
class Field:
    xref: int
    name: str
    kind: str              # text, checkbox, radio, choice, signature, button
    rect: pymupdf.Rect
    value: str
    options: list[str] = field(default_factory=list)
    multiline: bool = False
    readonly: bool = False
    size: float = 0.0      # font size from the field's settings (0 = fit the box)

    @property
    def checked(self) -> bool:
        return self.kind in ("checkbox", "radio") and self.value not in ("", "Off", "False")


def fields(page: pymupdf.Page) -> list[Field]:
    out = []
    for w in page.widgets():
        kind = KINDS.get(w.field_type, "other")
        out.append(Field(w.xref, w.field_name or "", kind, pymupdf.Rect(w.rect), str(w.field_value or ""),
                         list(w.choice_values or []), bool(w.field_flags & pymupdf.PDF_TX_FIELD_IS_MULTILINE)
                         if kind == "text" else False, bool(w.field_flags & pymupdf.PDF_FIELD_IS_READ_ONLY),
                         float(w.text_fontsize or 0.0)))
    return out


def _widget(page: pymupdf.Page, f: Field) -> pymupdf.Widget:
    return next(w for w in page.widgets() if w.xref == f.xref)


def _appearance(doc: pymupdf.Document, f: Field, text: str, font: KhmerFont, size: float | None,
                color: tuple[float, float, float]) -> int:
    """A Form XObject drawing `text` shaped, inside the field's box."""
    w, h = f.rect.width, f.rect.height
    inner = w - 2 * PAD
    if not size:
        size = f.size or 0.0
    if not size:  # auto: a line as tall as the box allows, within reason
        size = min(max((h - 2 * PAD) / font.line_height(1), 4.0), 14.0)
        if not f.multiline:
            natural = font.width(text, 1)
            if natural > 0:
                size = max(min(size, inner / natural), 4.0)
    lines = (layout(text, font, size, inner) if f.multiline else [text.replace("\n", " ")])
    step = font.line_height(size)
    ops = ["/Tx BMC", "q", f"{color[0]:.3f} {color[1]:.3f} {color[2]:.3f} rg",
           f"{PAD - 1:g} {PAD - 1:g} {w - 2 * PAD + 2:g} {h - 2 * PAD + 2:g} re W n"]
    res: dict[str, int] = {}
    scale = size / font.upem
    if f.multiline:
        baseline = h - PAD - font.ascent(size)  # from the top
    else:  # the line's box centred in the field
        baseline = (h + step) / 2 - font.ascent(size)
    for line in lines:
        pen = PAD
        for g in font.shape(line):
            glyph = _glyph_xobject(doc, font, g.gid)
            if glyph:
                name, gx = glyph
                res[name] = gx
                ops.append(f"q {scale:.6f} 0 0 {scale:.6f} {pen + g.x_offset * scale:.3f} "
                           f"{baseline + g.y_offset * scale:.3f} cm /{name} Do Q")
            pen += g.x_advance * scale
        baseline -= step
    ops += ["Q", "EMC"]
    xobjs = " ".join(f"/{n} {x} 0 R" for n, x in res.items())
    return _new_obj(doc, f"<< /Type /XObject /Subtype /Form /BBox [0 0 {w:.2f} {h:.2f}] "
                         f"/Resources << /XObject << {xobjs} >> >> >>", ("\n".join(ops) + "\n").encode())


def _set_value(page: pymupdf.Page, f: Field, value: str, ap: int) -> None:
    doc = page.parent
    target = f.xref
    # the value lives on the field, which may be the widget's parent
    kind, parent = doc.xref_get_key(f.xref, "Parent")
    if kind == "xref" and doc.xref_get_key(f.xref, "T")[0] == "null":
        target = int(parent.split()[0])
    doc.xref_set_key(target, "V", _hex16(value))
    doc.xref_set_key(f.xref, "AP", f"<< /N {ap} 0 R >>")
    # viewers would otherwise redraw the field from its (non-Khmer) font
    kind, acro = doc.xref_get_key(doc.pdf_catalog(), "AcroForm")
    if kind == "xref":
        doc.xref_set_key(int(acro.split()[0]), "NeedAppearances", "false")
    elif kind == "dict":
        doc.xref_set_key(doc.pdf_catalog(), "AcroForm/NeedAppearances", "false")


def fill_text(page: pymupdf.Page, f: Field, text: str, font: KhmerFont | None = None,
              size: float | None = None, color: tuple[float, float, float] = (0, 0, 0)) -> None:
    """Type `text` into a text field."""
    if f.kind != "text":
        raise ValueError(f"“{f.name}” is not a text field")
    if f.readonly:
        raise ValueError(f"“{f.name}” is read-only")
    _set_value(page, f, text, _appearance(page.parent, f, text, font or default_font(), size, color))


def choose(page: pymupdf.Page, f: Field, value: str, font: KhmerFont | None = None) -> None:
    """Pick one of a choice field's options (drawn shaped, like a text field)."""
    if f.kind != "choice":
        raise ValueError(f"“{f.name}” is not a choice field")
    if f.readonly:
        raise ValueError(f"“{f.name}” is read-only")
    _set_value(page, f, value, _appearance(page.parent, f, value, font or default_font(), None, (0, 0, 0)))


def toggle(page: pymupdf.Page, f: Field) -> bool:
    """Tick or untick a checkbox, or switch on a radio button. Returns the
    new state."""
    if f.kind not in ("checkbox", "radio"):
        raise ValueError(f"“{f.name}” is not a checkbox")
    if f.readonly:
        raise ValueError(f"“{f.name}” is read-only")
    w = _widget(page, f)
    on = True if f.kind == "radio" else not f.checked
    w.field_value = w.on_state() if on else "Off"
    w.update()
    return on
