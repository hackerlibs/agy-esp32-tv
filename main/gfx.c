#include "gfx.h"

#include <string.h>

#include "fonts/font_press_start_8.h"
#include "fonts/font_spleen_5x8.h"

static uint16_t s_fb[GFX_H][GFX_W] __attribute__((aligned(4)));
static uint8_t s_dirty[(GFX_H + 7) / 8];
static int s_clip_x, s_clip_y, s_clip_w, s_clip_h;

const gfx_font_t gfx_font_small = {
    .data = font_spleen_5x8[0],
    .cell_w = FONT_SPLEEN_5X8_CELL_W,
    .advance = FONT_SPLEEN_5X8_ADVANCE,
    .scale = 1,
};

const gfx_font_t gfx_font_big = {
    .data = font_press_start_8[0],
    .cell_w = FONT_PRESS_START_8_CELL_W,
    .advance = FONT_PRESS_START_8_ADVANCE,
    .scale = 1,
};

gfx_font_t gfx_font_scaled(const gfx_font_t *base, int scale)
{
    gfx_font_t f = *base;
    f.scale = (uint8_t)(scale < 1 ? 1 : scale);
    return f;
}

static void mark_rows(int y0, int y1)
{
    if (y0 < 0) {
        y0 = 0;
    }
    if (y1 > GFX_H) {
        y1 = GFX_H;
    }
    for (int y = y0; y < y1; y++) {
        s_dirty[y >> 3] |= (uint8_t)(1u << (y & 7));
    }
}

static inline void put_px(int x, int y, gfx_color_t c)
{
    x -= s_clip_x;
    y -= s_clip_y;
    if ((unsigned)x >= (unsigned)s_clip_w || (unsigned)y >= (unsigned)s_clip_h) {
        return;
    }
    /* Framebuffer is big-endian RGB565 so rows can go to the panel untouched. */
    s_fb[y][x] = __builtin_bswap16(c);
}

void gfx_init(void)
{
    s_clip_x = 0;
    s_clip_y = 0;
    s_clip_w = GFX_W;
    s_clip_h = GFX_H;
}

void gfx_clear(gfx_color_t c)
{
    gfx_fill_rect(0, 0, GFX_W, GFX_H, c);
}

void gfx_set_clip(int x, int y, int w, int h)
{
    if (w < 0) {
        w = 0;
    }
    if (h < 0) {
        h = 0;
    }
    s_clip_x = x;
    s_clip_y = y;
    s_clip_w = w;
    s_clip_h = h;
}

void gfx_reset_clip(void)
{
    gfx_init();
}

void gfx_fill_rect(int x, int y, int w, int h, gfx_color_t c)
{
    int xa = x, ya = y, xb = x + w, yb = y + h;
    if (xa < s_clip_x) {
        xa = s_clip_x;
    }
    if (ya < s_clip_y) {
        ya = s_clip_y;
    }
    if (xb > s_clip_x + s_clip_w) {
        xb = s_clip_x + s_clip_w;
    }
    if (yb > s_clip_y + s_clip_h) {
        yb = s_clip_y + s_clip_h;
    }
    if (xa >= xb || ya >= yb) {
        return;
    }
    const uint16_t v = __builtin_bswap16(c);
    for (int yy = ya; yy < yb; yy++) {
        uint16_t *row = s_fb[yy];
        for (int xx = xa; xx < xb; xx++) {
            row[xx] = v;
        }
    }
    mark_rows(ya, yb);
}

void gfx_frame_rect(int x, int y, int w, int h, gfx_color_t c)
{
    if (w <= 0 || h <= 0) {
        return;
    }
    gfx_fill_rect(x, y, w, 1, c);
    gfx_fill_rect(x, y + h - 1, w, 1, c);
    gfx_fill_rect(x, y, 1, h, c);
    gfx_fill_rect(x + w - 1, y, 1, h, c);
}

void gfx_fill_circle(int cx, int cy, int r, gfx_color_t c)
{
    for (int dy = -r; dy <= r; dy++) {
        for (int dx = -r; dx <= r; dx++) {
            /* nudge the threshold so small circles stay round */
            if (dx * dx + dy * dy <= r * r + r / 2) {
                put_px(cx + dx, cy + dy, c);
            }
        }
    }
    mark_rows(cy - r, cy + r + 1);
}

void gfx_line(int x0, int y0, int x1, int y1, int thickness, gfx_color_t c)
{
    if (thickness < 1) {
        thickness = 1;
    }
    const int dx = x1 - x0, dy = y1 - y0;
    const int64_t len2 = (int64_t)dx * dx + (int64_t)dy * dy;
    const int pad = thickness / 2 + 1;
    const int64_t r8 = (int64_t)thickness * 128; /* radius in 8.8 units */
    for (int py = (y0 < y1 ? y0 : y1) - pad; py <= (y0 < y1 ? y1 : y0) + pad; py++) {
        for (int px = (x0 < x1 ? x0 : x1) - pad; px <= (x0 < x1 ? x1 : x0) + pad; px++) {
            /* projection of the pixel onto the segment, in 8.8 fixed point */
            int64_t t8 = len2 ? ((int64_t)(px - x0) * dx + (int64_t)(py - y0) * dy) * 256 / len2 : 0;
            if (t8 < 0) {
                t8 = 0;
            } else if (t8 > 256) {
                t8 = 256;
            }
            int64_t cx8 = (int64_t)x0 * 256 + (int64_t)dx * t8;
            int64_t cy8 = (int64_t)y0 * 256 + (int64_t)dy * t8;
            int64_t ex = (int64_t)px * 256 - cx8;
            int64_t ey = (int64_t)py * 256 - cy8;
            if (ex * ex + ey * ey <= r8 * r8) {
                put_px(px, py, c);
            }
        }
    }
    mark_rows((y0 < y1 ? y0 : y1) - pad, (y0 < y1 ? y1 : y0) + pad + 1);
}

int gfx_text_width(const char *s, const gfx_font_t *f)
{
    int n = 0;
    while (*s++) {
        n++;
    }
    return n * gfx_char_advance(f);
}

void gfx_text(int x, int y, const char *s, const gfx_font_t *f, gfx_color_t c)
{
    const int sc = f->scale;
    const int adv = gfx_char_advance(f);
    for (; *s; s++) {
        unsigned ch = (unsigned char)*s;
        if (ch >= 0x20 && ch <= 0x7E) {
            const uint8_t *g = f->data + (ch - 0x20) * 8;
            for (int ry = 0; ry < 8; ry++) {
                const uint8_t bits = g[ry];
                for (int cx = 0; cx < f->cell_w; cx++) {
                    if (bits & (0x80u >> cx)) {
                        if (sc == 1) {
                            put_px(x + cx, y + ry, c);
                        } else {
                            gfx_fill_rect(x + cx * sc, y + ry * sc, sc, sc, c);
                        }
                    }
                }
            }
        }
        x += adv;
    }
    mark_rows(y, y + gfx_font_height(f));
}

void gfx_text_right(int right_x, int y, const char *s, const gfx_font_t *f, gfx_color_t c)
{
    gfx_text(right_x - gfx_text_width(s, f), y, s, f, c);
}

void gfx_text_center(int cx, int y, const char *s, const gfx_font_t *f, gfx_color_t c)
{
    gfx_text(cx - gfx_text_width(s, f) / 2, y, s, f, c);
}

int gfx_wrap(char *text, int max_width, const gfx_font_t *f, const char **lines, int max_lines)
{
    const int adv = gfx_char_advance(f);
    char *p = text;
    int n = 0;

    if (max_lines <= 0 || max_width < adv) {
        return 0;
    }
    while (*p) {
        while (*p == ' ') {
            p++;
        }
        if (!*p) {
            break;
        }
        int len = 0, width = 0;
        while (p[len] && p[len] != '\n' && width + adv <= max_width) {
            width += adv;
            len++;
        }
        if (p[len] == '\0' || n == max_lines - 1) {
            /* last line: whatever remains, unwrapped (the caller clips it) */
            lines[n++] = p;
            break;
        }
        int cut = len;
        if (p[len] != '\n') {
            /* prefer breaking at a space, fall back to a hard break for long words */
            while (cut > 0 && p[cut] != ' ') {
                cut--;
            }
            if (cut == 0) {
                cut = len;
            }
        }
        p[cut] = '\0';
        lines[n++] = p;
        p += cut + 1;
    }
    return n;
}

int gfx_flush(void)
{
    int sent = 0;
    for (int y = 0; y < GFX_H;) {
        if (!(s_dirty[y >> 3] & (1u << (y & 7)))) {
            y++;
            continue;
        }
        int y0 = y;
        while (y < GFX_H && (s_dirty[y >> 3] & (1u << (y & 7)))) {
            y++;
        }
        esp_err_t err = panel_write_rect(0, (uint16_t)y0, GFX_W, (uint16_t)(y - y0), (const uint8_t *)s_fb[y0]);
        if (err != ESP_OK) {
            /* leave the band dirty so it is retried on the next frame */
            return sent > 0 ? sent : -1;
        }
        for (int yy = y0; yy < y; yy++) {
            s_dirty[yy >> 3] &= (uint8_t)~(1u << (yy & 7));
        }
        sent += y - y0;
    }
    return sent;
}

const uint8_t *gfx_framebuffer(void)
{
    return (const uint8_t *)s_fb;
}
