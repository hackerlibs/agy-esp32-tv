#!/usr/bin/env python3
"""Convert the raw RGB565 frames written by the offscreen renderer into a PNG sheet.

    to_png.py <output directory>   -> writes <output directory>/sheet.png

Uses Pillow if available; otherwise falls back to a pure-Python PNG encoder
(stdlib zlib + struct) so contact sheets can be generated on any machine.
"""
import glob
import os
import struct
import sys
import zlib

SCALE = 2
COLS = 4
GAP = 10
LABEL_H = 14


def decode_rgb565(path, w, h):
    data = open(path, "rb").read()
    if len(data) != w * h * 2:
        sys.exit("%s: expected %d bytes, got %d" % (path, w * h * 2, len(data)))
    rgb = bytearray(w * h * 3)
    oi = 0
    for i in range(0, len(data), 2):
        c = (data[i] << 8) | data[i + 1]
        r = (c >> 11) & 31
        g = (c >> 5) & 63
        b = c & 31
        rgb[oi] = (r << 3) | (r >> 2)
        rgb[oi + 1] = (g << 2) | (g >> 4)
        rgb[oi + 2] = (b << 3) | (b >> 2)
        oi += 3
    return rgb


def write_png_rgb(path, w, h, rgb_bytes):
    def chunk(tag, data):
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    stride = w * 3
    raw = bytearray()
    for y in range(h):
        raw.append(0)  # filter type 0 (None)
        raw.extend(rgb_bytes[y * stride:(y + 1) * stride])

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(bytes(raw), level=6))
        + chunk(b"IEND", b"")
    )
    with open(path, "wb") as f:
        f.write(png)


def render_with_pillow(out, files, w, h):
    from PIL import Image, ImageDraw

    cell_w, cell_h = w * SCALE + GAP, h * SCALE + LABEL_H + GAP
    rows = (len(files) + COLS - 1) // COLS
    sheet = Image.new("RGB", (COLS * cell_w + GAP, rows * cell_h + GAP), (200, 200, 200))
    draw = ImageDraw.Draw(sheet)
    for i, path in enumerate(files):
        rgb = bytes(decode_rgb565(path, w, h))
        img = Image.frombytes("RGB", (w, h), rgb).resize((w * SCALE, h * SCALE), Image.NEAREST)
        x = GAP + (i % COLS) * cell_w
        y = GAP + (i // COLS) * cell_h
        draw.text((x, y), os.path.basename(path).replace(".rgb565", ""), fill=(0, 0, 0))
        sheet.paste(img, (x, y + LABEL_H))
    target = os.path.join(out, "sheet.png")
    sheet.save(target)
    print("wrote %s (%d screens, Pillow)" % (target, len(files)))


def render_pure_python(out, files, w, h):
    # Pure-Python 1x sheet (compact and fast) + individual 1x PNGs for close inspection
    scale = 1
    gap = 8
    cols = COLS
    rows = (len(files) + cols - 1) // cols
    sw = cols * (w * scale + gap) + gap
    sh = rows * (h * scale + gap) + gap
    sheet = bytearray([48, 52, 64] * (sw * sh))

    for i, path in enumerate(files):
        rgb = decode_rgb565(path, w, h)
        single = path.replace(".rgb565", ".png")
        write_png_rgb(single, w, h, rgb)
        ox = gap + (i % cols) * (w + gap)
        oy = gap + (i // cols) * (h + gap)
        for y in range(h):
            src_off = y * w * 3
            dst_off = ((oy + y) * sw + ox) * 3
            sheet[dst_off:dst_off + w * 3] = rgb[src_off:src_off + w * 3]

    target = os.path.join(out, "sheet.png")
    write_png_rgb(target, sw, sh, sheet)
    print("wrote %s (%d screens, pure-Python PNG)" % (target, len(files)))


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    out = sys.argv[1]
    files = sorted(glob.glob(os.path.join(out, "*.rgb565")))
    if not files:
        sys.exit("no .rgb565 frames in %s" % out)
    try:
        render_with_pillow(out, files, 240, 240)
    except ImportError:
        render_pure_python(out, files, 240, 240)


if __name__ == "__main__":
    main()
