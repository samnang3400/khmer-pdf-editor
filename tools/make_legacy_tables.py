"""Build khmerpdf/legacy_tables.py from khmerconverter's fontdata.xml (Khmer OS
project, LGPL-2.1; in its source package as modules/fontdata.xml).
Run at dev time: python tools/make_legacy_tables.py path/to/fontdata.xml"""
import re
import sys
from pathlib import Path

ENTITIES = {"mark": "៪", "coeng": "្", "zwsp": "​", "zwj": "‌", "zwnj": "‍", "nbsp": "\xa0"}


def read(xml: str) -> dict:
    """family -> (parent, {codes: text}, hidden, aliases)"""
    fonts = {}
    for m in re.finditer(r'<font type="([^"]+)"([^>]*)>(.*?)</font>', xml, re.S):
        typ, attrs, body = m.groups()
        body = re.sub(r"<!--.*?-->", "", body, flags=re.S)
        maps = {}
        for section in ("global", "tounicode"):  # (fromunicode is the other direction)
            for sec in re.findall(rf"<{section}>(.*?)</{section}>", body, re.S):
                for codes, text in re.findall(r'<map legacy="([^"]*)" unicode="([^"]*)"\s*/>', sec):
                    if codes:
                        text = re.sub(r"&(\w+);", lambda e: ENTITIES[e.group(1)], text)
                        text = re.sub(r"&#x([0-9a-fA-F]+);", lambda e: chr(int(e.group(1), 16)), text)
                        maps[bytes(int(h, 16) for h in codes.split(";"))] = text
        parent = re.search(r'inherit="([^"]+)"', attrs)
        default = re.search(r"default=['\"]([^'\"]+)", attrs)
        aliases = re.findall(r'<alias name="([^"]+)"', body) + ([default.group(1)] if default else [])
        fonts[typ] = (parent.group(1) if parent else None, maps, "hidden" in attrs, aliases)
    return fonts


def resolve(fonts: dict, family: str) -> dict:
    parent, maps, _, _ = fonts[family]
    out = resolve(fonts, parent) if parent else {}
    out.update(maps)
    return out


def literal(text: str) -> str:
    """Khmer letters as they are; marks, zero-width and other characters escaped."""
    out = ""
    for c in text:
        o = ord(c)
        if c in '"\\' or o < 0x20 or 0x7F <= o < 0x1780 or 0x17B4 <= o <= 0x17D3 or o == 0x17DD or o > 0x17FF:
            out += f"\\u{o:04x}"
        else:
            out += c
    return f'"{out}"'


def wrapped(entries: list[str], indent: str) -> list[str]:
    lines, row = [], indent
    for e in entries:
        if len(row) + len(e) > 100:
            lines.append(row)
            row = indent
        row += e
    return lines + [row]


def main(src: str) -> None:
    fonts = read(Path(src).read_text(encoding="utf-8"))
    out = ['"""Character tables of legacy (pre-Unicode) Khmer fonts: font code(s) ->',
           "Unicode. GENERATED from khmerconverter's fontdata.xml (Khmer OS project,",
           "LGPL-2.1) by tools/make_legacy_tables.py - edit the generator, not this file.",
           'Families inherit as in that file; aliases are the font names each covers."""', "",
           "TABLES: dict[str, dict[bytes, str]] = {"]
    aliases = {}
    for family, (_, _, hidden, names) in fonts.items():
        if hidden:
            continue
        table = resolve(fonts, family)
        out.append(f'    "{family}": {{')
        out += wrapped([" b'" + "".join(f"\\x{b:02x}" for b in k) + "': " + literal(v) + ","
                        for k, v in sorted(table.items(), key=lambda kv: (len(kv[0]), kv[0]))], "       ")
        out.append("    },")
        for name in names:
            aliases[re.sub(r"[^a-z0-9]", "", name.lower())] = family
    out += ["}", "", "# normalised font name -> family", "FONTS: dict[str, str] = {"]
    out += wrapped([f' "{a}": "{f}",' for a, f in sorted(aliases.items())], "   ")
    out.append("}")
    dest = Path(__file__).resolve().parent.parent / "khmerpdf" / "legacy_tables.py"
    dest.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"wrote {dest}: {len([f for f in fonts.values() if not f[2]])} families, {len(aliases)} font names")


if __name__ == "__main__":
    main(sys.argv[1])
