#!/usr/bin/env python3
"""
Generate the bitmap font headers used by the firmware (main/fonts/*.h).

Development-time tool only: the firmware just includes the generated headers.

Sources (all free to bundle, see main/fonts/LICENSES.md):
  * Press Start 2P (TTF, OFL-1.1)  -> font_press_start_8.h  8x8 cell, headings
  * Spleen 5x8    (BDF, BSD-2)     -> font_spleen_5x8.h      5x8 cell, ASCII body text
  * Cubic 11      (TTF, OFL-1.1)   -> font_cubic11_12.h     12x13 cell, CJK body text

The two Latin fonts cover ASCII 0x20..0x7E: 8 bytes per glyph, one byte per
row, the leftmost pixel in bit 7. Cubic 11 covers every character of GB2312
plus a few extras: 24 bytes per glyph (12 rows, 2 bytes each, bit 15 = left).

Usage:

  gen_fonts.py --press-start-2p PressStart2P-Regular.ttf --spleen spleen-5x8.bdf \
               [--cjk Cubic_11.ttf] --out main/fonts

Press Start 2P and Cubic 11 are pixel fonts whose design grid maps to exactly
one screen pixel at a point size of 8 and 11 respectively, so rendering them
with anti-aliasing disabled reproduces the hand-drawn glyphs bit for bit.
"""
import argparse
import os
import sys

FIRST, LAST = 0x20, 0x7E
ROWS = 8

# Characters outside GB2312 that show up often enough in plans and commands.
CJK_EXTRA = "→←↑↓①②③④⑤⑥⑦⑧⑨⑩←→"

def gb2312_charset():
    """Every character the GB2312 codec can encode, plus CJK_EXTRA, sorted by code point."""
    chars = set(CJK_EXTRA)
    for cp in range(0x80, 0x10000):
        try:
            chr(cp).encode("gb2312")
        except UnicodeEncodeError:
            continue
        chars.add(chr(cp))
    return sorted(chars, key=ord)


def glyphs_cjk(ttf_path, chars):
    from PIL import Image, ImageDraw, ImageFont

    font = ImageFont.truetype(ttf_path, 11)
    out = []
    for ch in chars:
        # Cubic 11 at 11 px draws a 14-row line: ideographs put their ink on rows
        # 2..11, accents and quotes reach up to row 0, a few symbols reach down to
        # row 12, and nothing uses row 13. Rows 0..12 are kept whole, so no glyph
        # is clipped; the 13-row cell is used with a line pitch of 12.
        img = Image.new("L", (12, 14), 0)
        ImageDraw.Draw(img).text((0, 0), ch, font=font, fill=255)
        px = img.load()
        if any(px[x, 13] >= 128 for x in range(12)):
            sys.exit("glyph U+%04X has ink below the 13-row cell" % ord(ch))
        rows = []
        for y in range(0, 13):
            b = 0
            for x in range(12):
                if px[x, y] >= 128:
                    b |= 0x800 >> x
            rows.append(b)
        out.append(rows)
    return out


def glyphs_press_start_2p(ttf_path):
    from PIL import Image, ImageDraw, ImageFont

    font = ImageFont.truetype(ttf_path, 8)
    out = []
    for cp in range(FIRST, LAST + 1):
        img = Image.new("L", (8, 8), 0)
        ImageDraw.Draw(img).text((0, 0), chr(cp), font=font, fill=255)
        px = img.load()
        rows = []
        for y in range(ROWS):
            b = 0
            for x in range(8):
                if px[x, y] >= 128:
                    b |= 0x80 >> x
            rows.append(b)
        out.append(rows)
    return 8, out


def glyphs_spleen(bdf_path):
    """Parse the BDF and blit each glyph into a 5x8 cell with the baseline on row 7."""
    cell_w, baseline = 5, 7
    glyphs = {}
    with open(bdf_path, encoding="latin-1") as f:
        lines = f.read().splitlines()
    i = 0
    while i < len(lines):
        if lines[i].startswith("STARTCHAR"):
            enc = w = h = ox = oy = None
            bitmap = []
            i += 1
            while not lines[i].startswith("ENDCHAR"):
                t = lines[i].split()
                if t[0] == "ENCODING":
                    enc = int(t[1])
                elif t[0] == "BBX":
                    w, h, ox, oy = map(int, t[1:5])
                elif t[0] == "BITMAP":
                    bitmap = lines[i + 1:i + 1 + h]
                i += 1
            if enc is not None and FIRST <= enc <= LAST:
                rows = []
                for r in range(ROWS):
                    # distance of this cell row above the baseline
                    above = baseline - r
                    src = h - 1 - (above - oy)  # bitmap row index for this cell row
                    if 0 <= src < h and bitmap:
                        bits = int(bitmap[src], 16)
                        val = 0
                        for x in range(cell_w):
                            sx = x - ox
                            if 0 <= sx < w and (bits >> (7 - sx)) & 1:
                                val |= 0x80 >> x
                        rows.append(val)
                    else:
                        rows.append(0)
                glyphs[enc] = rows
        i += 1
    missing = [chr(c) for c in range(FIRST, LAST + 1) if c not in glyphs]
    if missing:
        sys.exit("BDF is missing ASCII glyphs: %r" % missing)
    return cell_w, [glyphs[c] for c in range(FIRST, LAST + 1)]


def emit_header(path, symbol, cell_w, glyphs, title, attribution, source, advance):
    lines = [
        "// Generated by tools/gen_fonts.py from %s -- do not edit by hand." % os.path.basename(source),
        "//",
        "// %s" % title,
        "// %s" % attribution,
        "//",
        "// ASCII 0x20..0x7E, %d bytes per glyph, one byte per row, bit 7 = leftmost pixel." % ROWS,
        "// Cell is %dx%d, recommended advance %d px." % (cell_w, ROWS, advance),
        "#pragma once",
        "#include <stdint.h>",
        "",
        "#define %s_CELL_W %d" % (symbol.upper(), cell_w),
        "#define %s_ADVANCE %d" % (symbol.upper(), advance),
        "",
        "static const uint8_t %s[%d][%d] = {" % (symbol, len(glyphs), ROWS),
    ]
    for idx, g in enumerate(glyphs):
        ch = FIRST + idx
        label = chr(ch) if 0x21 <= ch < 0x7F else {"0x20": "space", "0x7e": "~"}.get("%#x" % ch, "")
        lines.append("    {%s}, /* %02X %s */" % (", ".join("0x%02X" % b for b in g), ch, label))
    lines += ["};", ""]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("wrote %s (%d glyphs)" % (path, len(glyphs)))


def emit_cjk_header(path, chars, glyphs, source):
    lines = [
        "// Generated by tools/gen_fonts.py from %s -- do not edit by hand." % os.path.basename(source),
        "//",
        "// Cubic 11 (俐方體11號), SIL Open Font License 1.1.",
        "// Derived from M+ BITMAP FONTS (c) 2002-2004 COZ and JF Dot M+H 12 (c) 2005 M+ FONTS PROJECT.",
        "//",
        "// GB2312 plus a few extras, sorted by code point. 12x13 cell (rows 0..12 of",
        "// the font's 14-row line), 13 rows of 2 bytes, bit 15 = leftmost pixel.",
        "// Draw with a line pitch of 12. Lookup: binary search over the code points.",
        "#pragma once",
        "#include <stdint.h>",
        "",
        "#define FONT_CUBIC11_COUNT %d" % len(chars),
        "",
        "static const uint16_t font_cubic11_codepoints[FONT_CUBIC11_COUNT] = {",
    ]
    for i in range(0, len(chars), 16):
        lines.append("    " + ",".join("0x%04X" % ord(c) for c in chars[i:i + 16]) + ",")
    lines.append("};")
    lines.append("")
    lines.append("static const uint8_t font_cubic11_glyphs[FONT_CUBIC11_COUNT][26] = {")
    for ch, g in zip(chars, glyphs):
        flat = []
        for v in g:
            flat += [v >> 8, v & 0xFF]
        lines.append("    {%s}, /* U+%04X %s */" % (",".join("0x%02X" % b for b in flat), ord(ch), ch))
    lines += ["};", ""]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("wrote %s (%d glyphs, %d bytes of bitmap data)" % (path, len(chars), len(glyphs) * 13 * 2))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--press-start-2p", required=True, help="path to PressStart2P-Regular.ttf")
    ap.add_argument("--spleen", required=True, help="path to spleen-5x8.bdf")
    ap.add_argument("--cjk", help="path to Cubic_11.ttf (enables Chinese/Japanese text)")
    ap.add_argument("--out", required=True, help="output directory for the headers")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    w, g = glyphs_press_start_2p(a.press_start_2p)
    emit_header(os.path.join(a.out, "font_press_start_8.h"), "font_press_start_8", w, g,
                "Press Start 2P (8 px)", "Copyright 2012 The Press Start 2P Project Authors, SIL OFL 1.1.",
                a.press_start_2p, advance=8)
    w, g = glyphs_spleen(a.spleen)
    emit_header(os.path.join(a.out, "font_spleen_5x8.h"), "font_spleen_5x8", w, g,
                "Spleen 5x8 v2.3.0", "Copyright (c) 2018-2026 Frederic Cambus, BSD-2-Clause.",
                a.spleen, advance=5)
    if a.cjk:
        chars = gb2312_charset()
        emit_cjk_header(os.path.join(a.out, "font_cubic11_12.h"), chars, glyphs_cjk(a.cjk, chars), a.cjk)


if __name__ == "__main__":
    main()
