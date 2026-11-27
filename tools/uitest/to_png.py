#!/usr/bin/env python3
"""Convert the raw frames written by the offscreen renderer into a labelled PNG sheet.

    to_png.py <output directory>   -> writes <output directory>/sheet.png

Needs Pillow. The RGB565 frames are expanded to RGB888 without scaling (nearest
neighbour at 2x), so what you see is exactly what the panel gets.
"""
import glob
import os
import sys

from PIL import Image, ImageDraw

SCALE = 2
COLS = 4
GAP = 10
LABEL_H = 14


def rgb565_to_image(path, w, h):
    data = open(path, "rb").read()
    if len(data) != w * h * 2:
        sys.exit("%s: expected %d bytes, got %d" % (path, w * h * 2, len(data)))
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        row = data[y * w * 2:(y + 1) * w * 2]
        for x in range(w):
            c = (row[x * 2] << 8) | row[x * 2 + 1]
            r = (c >> 11) & 31
            g = (c >> 5) & 63
            b = c & 31
            # bit-replicated expansion keeps the levels faithful to the panel
            px[x, y] = ((r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2))
    return img


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    out = sys.argv[1]
    files = sorted(glob.glob(os.path.join(out, "*.rgb565")))
    if not files:
        sys.exit("no .rgb565 frames in %s" % out)
    first = rgb565_to_image(files[0], 240, 240)
    w, h = first.size
    cell_w, cell_h = w * SCALE + GAP, h * SCALE + LABEL_H + GAP
    rows = (len(files) + COLS - 1) // COLS
    sheet = Image.new("RGB", (COLS * cell_w + GAP, rows * cell_h + GAP), (200, 200, 200))
    draw = ImageDraw.Draw(sheet)
    for i, path in enumerate(files):
        img = rgb565_to_image(path, w, h).resize((w * SCALE, h * SCALE), Image.NEAREST)
        x = GAP + (i % COLS) * cell_w
        y = GAP + (i // COLS) * cell_h
        draw.text((x, y), os.path.basename(path).replace(".rgb565", ""), fill=(0, 0, 0))
        sheet.paste(img, (x, y + LABEL_H))
    target = os.path.join(out, "sheet.png")
    sheet.save(target)
    print("wrote %s (%d screens)" % (target, len(files)))


if __name__ == "__main__":
    main()
