/*
 * Small framebuffer-backed drawing layer.
 *
 * Pixels are kept in one static framebuffer in big-endian RGB565, which is the
 * byte order the panel expects, so regions can be handed to SPI DMA as is.
 * Every drawing call marks the touched rows dirty and gfx_flush() sends the
 * dirty row bands to the panel. With a single buffer nothing ever flickers:
 * a row is only pushed once its new content is complete.
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "sdkconfig.h"
#include "panel.h"

#define GFX_W CONFIG_ZTV_LCD_H_RES
#define GFX_H CONFIG_ZTV_LCD_V_RES

typedef uint16_t gfx_color_t; /* RGB565 */

#define GFX_RGB(r, g, b) \
    ((gfx_color_t)((((uint16_t)(r) & 0xF8) << 8) | (((uint16_t)(g) & 0xFC) << 3) | ((uint16_t)(b) >> 3)))

typedef struct {
    const uint8_t *data; /* 95 glyphs (ASCII 0x20..0x7E), 8 bytes each, bit 7 = left */
    uint8_t cell_w;
    uint8_t advance;
    uint8_t scale;
} gfx_font_t;

extern const gfx_font_t gfx_font_small; /* Spleen 5x8: dense body text */
extern const gfx_font_t gfx_font_big;   /* Press Start 2P: headings */

gfx_font_t gfx_font_scaled(const gfx_font_t *base, int scale);
static inline int gfx_font_height(const gfx_font_t *f)
{
    return 8 * f->scale;
}
static inline int gfx_char_advance(const gfx_font_t *f)
{
    return f->advance * f->scale;
}

void gfx_init(void);
void gfx_clear(gfx_color_t c);
void gfx_set_clip(int x, int y, int w, int h);
void gfx_reset_clip(void);

void gfx_fill_rect(int x, int y, int w, int h, gfx_color_t c);
void gfx_frame_rect(int x, int y, int w, int h, gfx_color_t c);
void gfx_fill_circle(int cx, int cy, int r, gfx_color_t c);
/* Line of the given thickness, drawn as a capsule between the two points. */
void gfx_line(int x0, int y0, int x1, int y1, int thickness, gfx_color_t c);

int gfx_text_width(const char *s, const gfx_font_t *f);
void gfx_text(int x, int y, const char *s, const gfx_font_t *f, gfx_color_t c);
void gfx_text_right(int right_x, int y, const char *s, const gfx_font_t *f, gfx_color_t c);
void gfx_text_center(int cx, int y, const char *s, const gfx_font_t *f, gfx_color_t c);

/*
 * Word-wraps `text` in place by inserting NULs, and stores pointers to the
 * start of each line. Returns the number of lines. If the text does not fit in
 * max_lines, the last entry points at the unwrapped remainder.
 */
int gfx_wrap(char *text, int max_width, const gfx_font_t *f, const char **lines, int max_lines);

/*
 * Mixed UTF-8 text: ASCII comes from the 5x8 font, CJK and other GB2312
 * characters from a 12 px bitmap font (Cubic 11). Anything without a glyph,
 * such as an emoji, is skipped. Use these for every string that comes from the
 * host; the gfx_font_t calls above stay for fixed ASCII labels.
 */
#define GFX_CJK_W    12 /* advance of a CJK glyph */
#define GFX_CJK_ROWS 13 /* cell height of a CJK glyph */

/* Line pitch for a block of text: tighter when it is pure ASCII. */
#define GFX_PITCH_ASCII 10
#define GFX_PITCH_CJK   12

/* Decodes the next code point; returns false at the end of the string. */
bool gfx_next_cp(const char **s, uint32_t *cp);
int gfx_cp_advance(uint32_t cp);
bool gfx_has_cjk(const char *s);

int gfx_text_width_u8(const char *s);
void gfx_text_u8(int x, int y, const char *s, gfx_color_t c);
void gfx_text_u8_right(int right_x, int y, const char *s, gfx_color_t c);
void gfx_text_u8_center(int cx, int y, const char *s, gfx_color_t c);
int gfx_pitch_u8(const char *s);

/* Same contract as gfx_wrap(), for mixed-width text. */
int gfx_wrap_u8(char *text, int max_width, const char **lines, int max_lines);

/* Sends dirty rows to the panel; returns the number of rows pushed. */
int gfx_flush(void);

typedef struct {
    uint32_t flushes;
    uint32_t rows;
    int32_t last_us; /* duration of the last flush */
    int32_t max_us;  /* duration of the slowest flush */
} gfx_stats_t;

void gfx_get_stats(gfx_stats_t *out);

const uint8_t *gfx_framebuffer(void); /* GFX_H * GFX_W * 2 bytes, big-endian RGB565 */
