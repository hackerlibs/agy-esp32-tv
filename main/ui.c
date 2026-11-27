/*
 * ZCode status screens.
 *
 * Each frame: copy the model, work out which screen we are on, and redraw only
 * what changed. Text blocks are "widgets" that keep their own copy of the text,
 * so a heartbeat that does not change anything costs nothing on the SPI bus,
 * while spinners and marquee lines repaint just their own few rows.
 *
 * Plain C on purpose: no ESP-IDF includes, so tools/uitest can render these
 * screens offscreen on a desktop.
 */
#include "ui.h"

#include <stdio.h>
#include <string.h>

#include "gfx.h"
#ifdef GFX_TRACE_PIXEL
/* Offscreen harness hook: called between the stages of ui_render() so the
 * test can pinpoint when a framebuffer byte changes. */
extern void ztv_phase(const char *phase);
#endif
#include "port.h"
#include "sdkconfig.h"
#include "version.h"
#include "zmodel.h"

#define SECOND INT64_C(1000000)

#define PAD      8
#define HEADER_H 30
#define FOOTER_H 22

#define CONTENT_Y0 (HEADER_H + 1)
#define CONTENT_Y1 (GFX_H - FOOTER_H)

#define ANIM_X (PAD)
#define ANIM_Y (CONTENT_Y0 + 6)
#define ANIM_S 60

#define RIGHT_X (ANIM_X + ANIM_S + 10)
#define RIGHT_W (GFX_W - RIGHT_X - PAD)

#define MSG_Y (CONTENT_Y0 + 20)
#define MSG_H 52  /* 5 ASCII lines, or 4 lines when the text contains CJK */
#define CUR_H 24
#define PLAN_Y 112
#define STATS_Y (CONTENT_Y1 - 38)
#define DONE_MSG_Y (CONTENT_Y0 + 68)
#define TEXTW_MAX_LINES 16

#define LINK_TIMEOUT_US   (12 * SECOND) /* no heartbeat for this long: bridge is gone */
#define BOOT_SPLASH_US    INT64_C(1500000)
#define MARQUEE_PAUSE_US  INT64_C(900000)
#define MARQUEE_SPEED_PPS 40

/* palette */
#define C_BG    GFX_RGB(8, 10, 18)
#define C_PANEL GFX_RGB(16, 20, 32)
#define C_FG    GFX_RGB(232, 236, 244)
#define C_DIM   GFX_RGB(128, 138, 162)
#define C_FAINT GFX_RGB(62, 70, 94)
#define C_GRID  GFX_RGB(36, 42, 60)
#define C_IDLE  GFX_RGB(96, 116, 250)
#define C_THINK GFX_RGB(40, 200, 255)
#define C_PERM  GFX_RGB(255, 180, 20)
#define C_ERR   GFX_RGB(255, 72, 72)
#define C_DONE  GFX_RGB(40, 220, 130)
#define C_NOHOST GFX_RGB(255, 120, 60)

/* What is on screen. BOOT..DONE mirror zstate_t, the last two are derived. */
typedef enum {
    DS_BOOT = 0,
    DS_IDLE,
    DS_THINK,
    DS_PERM,
    DS_TOOL,
    DS_ERR,
    DS_DONE,
    DS_NOUSB,
    DS_NOHOST,
} ds_t;

typedef struct {
    const char *label;
    gfx_color_t color;
} style_t;

static const style_t STYLES[] = {
    [DS_BOOT] = {"ZCODE TV", C_FG},     [DS_IDLE] = {"IDLE", C_IDLE},
    [DS_THINK] = {"THINKING", C_THINK}, [DS_PERM] = {"PERMISSION", C_PERM},
    [DS_TOOL] = {"RUNNING", C_IDLE},    [DS_ERR] = {"TOOL FAILED", C_ERR},
    [DS_DONE] = {"DONE", C_DONE},       [DS_NOUSB] = {"NO USB", C_ERR},
    [DS_NOHOST] = {"NO HOST", C_NOHOST},
};

/* sin(i * 90deg / 16) * 100, first quadrant only; isin() does the rest. */
static const uint8_t SIN16[17] = {0, 10, 20, 29, 38, 47, 56, 63, 71, 77, 83, 88, 92, 96, 98, 100, 100};

/* A wrapped, optionally scrolling block of body text. */
typedef struct {
    int x, y, w, h; /* region in pixels; the line count follows from the pitch */
    int pitch;
    int max_lines;
    char src[ZM_MSG_MAX + 1];     /* pristine copy, used to detect real changes */
    char text[ZM_MSG_MAX + 1];    /* wrapped in place by gfx_wrap_u8() */
    const char *lines[TEXTW_MAX_LINES];
    int nlines;
    bool scrolling; /* the last line overflows and travels sideways */
    int travel;
    int64_t anchor_us;
    bool valid;
} textw_t;

static const gfx_font_t *const SMALL = &gfx_font_small;
static gfx_font_t s_big2, s_big3;

static struct {
    bool inited;
    ds_t ds;
    uint32_t version;
    int rotation;
    int64_t second;
    bool force;
    char clock_drawn[ZM_CLK_MAX];
    textw_t msg, cur, done;
    gfx_color_t led; /* colour the LED was last drawn with */
    bool led_drawn;
    uint32_t frames;
    int32_t last_frame_us, max_frame_us;
} s;

static int isin(int q)
{
    q &= 63;
    if (q <= 16) {
        return SIN16[q];
    }
    if (q <= 32) {
        return SIN16[32 - q];
    }
    if (q <= 48) {
        return -SIN16[q - 32];
    }
    return -SIN16[64 - q];
}

static int icos(int q)
{
    return isin(q + 16);
}

static gfx_color_t mix(gfx_color_t a, gfx_color_t b, int t)
{
    const int r = (a >> 11) & 31, g = (a >> 5) & 63, bl = a & 31;
    const int r2 = (b >> 11) & 31, g2 = (b >> 5) & 63, b2 = b & 31;
    return (gfx_color_t)(((r + ((r2 - r) * t >> 8)) << 11) | ((g + ((g2 - g) * t >> 8)) << 5) |
                         (bl + ((b2 - bl) * t >> 8)));
}

static const style_t *style(ds_t ds)
{
    return &STYLES[ds];
}

/* ------------------------------------------------------------------ text widgets */

static void textw_layout(textw_t *t, int x, int y, int w, int h)
{
    if (t->x != x || t->y != y || t->w != w || t->h != h) {
        t->x = x;
        t->y = y;
        t->w = w;
        t->h = h;
        t->valid = false;
    }
}

static void textw_set(textw_t *t, const char *src, int64_t now)
{
    if (t->valid && strcmp(t->src, src) == 0) {
        return;
    }
    strlcpy(t->src, src, sizeof(t->src));
    memcpy(t->text, t->src, strlen(t->src) + 1);
    t->pitch = gfx_pitch_u8(t->src);
    t->max_lines = t->h / t->pitch;
    if (t->max_lines < 1) {
        t->max_lines = 1;
    } else if (t->max_lines > TEXTW_MAX_LINES) {
        t->max_lines = TEXTW_MAX_LINES;
    }
    t->nlines = gfx_wrap_u8(t->text, t->w, t->lines, t->max_lines);
    t->scrolling = false;
    t->travel = 0;
    if (t->nlines == t->max_lines && t->nlines > 0) {
        const int over = gfx_text_width_u8(t->lines[t->nlines - 1]) - t->w;
        if (over > 0) {
            t->scrolling = true;
            t->travel = over + 4;
        }
    }
    t->anchor_us = now;
    t->valid = true;
}

/* Ping-pong marquee offset for a line that is `travel` pixels too wide. */
static int marquee_pos(int travel, int64_t elapsed)
{
    const int64_t move = (int64_t)travel * SECOND / MARQUEE_SPEED_PPS;
    const int64_t cycle = 2 * (move + MARQUEE_PAUSE_US);
    int64_t t = elapsed % cycle;
    if (t < MARQUEE_PAUSE_US) {
        return 0;
    }
    t -= MARQUEE_PAUSE_US;
    if (t < move) {
        return (int)(t * MARQUEE_SPEED_PPS / SECOND);
    }
    t -= move;
    if (t < MARQUEE_PAUSE_US) {
        return travel;
    }
    t -= MARQUEE_PAUSE_US;
    return travel - (int)(t * MARQUEE_SPEED_PPS / SECOND);
}

static void textw_draw_line(const textw_t *t, int i, int64_t now, gfx_color_t color)
{
    const int y = t->y + i * t->pitch;
    if (!t->scrolling || i != t->nlines - 1) {
        gfx_text_u8(t->x, y, t->lines[i], color);
        return;
    }
    gfx_fill_rect(t->x, y, t->w, t->pitch, C_BG);
    gfx_set_clip(t->x, y, t->w, t->pitch);
    gfx_text_u8(t->x - marquee_pos(t->travel, now - t->anchor_us), y, t->lines[i], color);
    gfx_reset_clip();
}

static void textw_draw(const textw_t *t, int64_t now, gfx_color_t color)
{
    gfx_fill_rect(t->x, t->y, t->w, t->h, C_BG);
    for (int i = 0; i < t->nlines; i++) {
        textw_draw_line(t, i, now, color);
    }
}

/* ------------------------------------------------------------------ helpers */

static unsigned elapsed_now(const zmodel_t *m, int64_t now)
{
    int64_t el = (int64_t)m->elapsed_s + (now - m->elapsed_ref_us) / SECOND;
    return el > 0 ? (unsigned)el : 0;
}

static void format_elapsed(unsigned sec, char *out, size_t cap)
{
    if (sec >= 3600) {
        snprintf(out, cap, "%u:%02u:%02u", sec / 3600, (sec / 60) % 60, sec % 60);
    } else {
        snprintf(out, cap, "%02u:%02u", sec / 60, sec % 60);
    }
}

/* Centered label in the heading font, as long as it is plain ASCII. Anything
 * else (a Chinese project name, say) goes through the mixed text renderer. */
static void draw_label_center(int cx, int y, const char *s, gfx_color_t c)
{
    for (const char *p = s; *p; p++) {
        if ((unsigned char)*p < 0x20 || (unsigned char)*p > 0x7E) {
            gfx_text_u8_center(cx, y + 1, s, c);
            return;
        }
    }
    gfx_text_center(cx, y, s, &gfx_font_big, c);
}

static bool link_up(const zmodel_t *m, int64_t now)
{
    return z_usb_connected() && m->last_frame_us != 0 && now - m->last_frame_us < LINK_TIMEOUT_US;
}

static ds_t derive_state(const zmodel_t *m, int64_t now)
{
    if (m->demo) {
        return (ds_t)m->state; /* demo frames only use BOOT..DONE */
    }
    if (!z_usb_connected()) {
        return DS_NOUSB;
    }
    if (!link_up(m, now)) {
        return now < BOOT_SPLASH_US ? DS_BOOT : DS_NOHOST;
    }
    if (m->state == ZST_DONE && now - m->state_since_us > (int64_t)CONFIG_ZTV_UI_DONE_SECONDS * SECOND) {
        return DS_IDLE;
    }
    switch (m->state) {
    case ZST_BOOT:
        return DS_BOOT;
    case ZST_IDLE:
        return DS_IDLE;
    case ZST_THINK:
        return DS_THINK;
    case ZST_PERM:
        return DS_PERM;
    case ZST_TOOL:
        return DS_TOOL;
    case ZST_ERR:
        return DS_ERR;
    case ZST_DONE:
        return DS_DONE;
    }
    return DS_IDLE;
}

static void clear_content(void)
{
    gfx_fill_rect(0, CONTENT_Y0, GFX_W, CONTENT_Y1 - CONTENT_Y0, C_BG);
}

/* ------------------------------------------------------------------ chrome */

static void draw_header(ds_t ds)
{
    gfx_fill_rect(0, 0, GFX_W, HEADER_H, C_PANEL);
    gfx_fill_rect(0, 0, 4, HEADER_H, style(ds)->color);
    gfx_text(12, (HEADER_H - 16) / 2, style(ds)->label, &s_big2, style(ds)->color);
    s.led_drawn = false; /* the header background just covered the LED */
    gfx_fill_rect(0, CONTENT_Y0, GFX_W, 1, C_GRID);
    gfx_fill_rect(0, CONTENT_Y1, GFX_W, 1, C_GRID);
    gfx_fill_rect(0, CONTENT_Y1 + 1, GFX_W, GFX_H - CONTENT_Y1 - 1, C_BG);
}

static void draw_footer_text(const zmodel_t *m, bool live)
{
    const int y = CONTENT_Y1 + 7;
    const bool have_clock = live && m->clock[0] != '\0';
    gfx_fill_rect(PAD, y, GFX_W - 2 * PAD, 8, C_BG);
    gfx_text(PAD, y, have_clock ? m->clock : "--:--:--", SMALL, have_clock ? C_DIM : C_FAINT);
    gfx_text_u8_right(GFX_W - PAD, y, m->proj[0] ? m->proj : "zcode", C_FG);
}

/*
 * Footer clock, plus the big clock on the idle screens. Without a live link the
 * time on hand is stale, so dashes are shown instead of a frozen clock.
 */
static void draw_clock(const zmodel_t *m, ds_t ds)
{
    const bool live = ds != DS_NOUSB && ds != DS_NOHOST && ds != DS_BOOT;
    draw_footer_text(m, live);
    if (ds != DS_IDLE && ds != DS_NOHOST) {
        return;
    }
    enum { Y = 46 };
    gfx_fill_rect(0, Y - 6, GFX_W, 56, C_BG);
    if (!live || m->clock[0] == '\0' || strlen(m->clock) < 5) {
        gfx_text_center(GFX_W / 2, Y, "--:--", &s_big3, C_FAINT);
        return;
    }
    char hm[6];
    memcpy(hm, m->clock, 5);
    hm[5] = '\0';
    /* shifted left so the seconds fit beside the big digits */
    const int x = (GFX_W - 5 * 24) / 2 - 12;
    gfx_text(x, Y, hm, &s_big3, C_FG);
    if (strlen(m->clock) >= 8) {
        char ss[3] = {m->clock[6], m->clock[7], '\0'};
        gfx_text(x + 5 * 24 + 8, Y + 16, ss, SMALL, C_DIM);
    }
    if (m->date[0]) {
        gfx_text_center(GFX_W / 2, Y + 32, m->date, SMALL, C_DIM);
    }
}

/* ------------------------------------------------------------------ badges (left square of the active screens) */

static void badge_begin(void)
{
    gfx_fill_rect(ANIM_X, ANIM_Y, ANIM_S, ANIM_S, C_BG);
}

enum { BADGE_CHECK, BADGE_CROSS, BADGE_RING };

static void draw_badge(int kind, gfx_color_t color)
{
    const int cx = ANIM_X + ANIM_S / 2, cy = ANIM_Y + ANIM_S / 2;
    if (kind == BADGE_RING) {
        gfx_fill_circle(cx, cy, 26, color);
        gfx_fill_circle(cx, cy, 23, C_BG);
    } else {
        gfx_fill_circle(cx, cy, 26, color);
    }
    if (kind == BADGE_CHECK) {
        gfx_line(cx - 12, cy, cx - 4, cy + 8, 5, C_BG);
        gfx_line(cx - 4, cy + 8, cx + 13, cy - 9, 5, C_BG);
    } else if (kind == BADGE_CROSS) {
        gfx_line(cx - 10, cy - 10, cx + 10, cy + 10, 5, C_BG);
        gfx_line(cx - 10, cy + 10, cx + 10, cy - 10, 5, C_BG);
    }
}

static void draw_badge_bang(int64_t now)
{
    const int cx = ANIM_X + ANIM_S / 2, cy = ANIM_Y + ANIM_S / 2;
    const bool on = (now / 650000) % 2 == 0;
    badge_begin();
    if (on) {
        gfx_fill_circle(cx, cy, 26, C_PERM);
    } else {
        gfx_fill_circle(cx, cy, 26, C_PERM);
        gfx_fill_circle(cx, cy, 23, C_BG);
    }
    const gfx_color_t ink = on ? C_BG : C_PERM;
    gfx_fill_rect(cx - 3, cy - 15, 6, 19, ink);
    gfx_fill_rect(cx - 3, cy + 8, 6, 6, ink);
}

static void draw_badge_ticks(int64_t now, gfx_color_t accent)
{
    const int cx = ANIM_X + ANIM_S / 2, cy = ANIM_Y + ANIM_S / 2;
    const int phase = (int)(now / 70000);
    badge_begin();
    for (int k = 0; k < 16; k++) {
        const int d = (k - phase) & 15;
        /* the four ticks behind the head fade out, the rest sit dim */
        static const int LEVEL[4] = {255, 170, 100, 50};
        const int level = d < 4 ? LEVEL[d] : 0;
        gfx_line(cx + icos(k * 4) * 20 / 100, cy + isin(k * 4) * 20 / 100, cx + icos(k * 4) * 27 / 100,
                 cy + isin(k * 4) * 27 / 100, 3, mix(C_FAINT, accent, level));
    }
}

static void draw_badge_dots(int64_t now)
{
    const int cx = ANIM_X + ANIM_S / 2, cy = ANIM_Y + ANIM_S / 2 + 6;
    badge_begin();
    for (int i = 0; i < 3; i++) {
        const int lift = isin((int)(now / 15000) + i * 21) * 14 / 100;
        gfx_fill_circle(cx - 18 + i * 18, cy - lift, 5, C_THINK);
    }
}

static void draw_boot_spinner(int64_t now)
{
    const int cx = GFX_W / 2, cy = 164;
    gfx_fill_rect(cx - 16, cy - 16, 32, 32, C_BG);
    for (int k = 0; k < 8; k++) {
        const int d = (k - (int)(now / 100000)) & 7;
        gfx_line(cx + icos(k * 8) * 7 / 100, cy + isin(k * 8) * 7 / 100, cx + icos(k * 8) * 13 / 100,
                 cy + isin(k * 8) * 13 / 100, 3, mix(C_FAINT, C_FG, d == 0 ? 255 : d * 60));
    }
}

/* Link LED in the header corner. Repainted only when its colour changes, so a
 * steady screen pushes nothing over SPI from one frame to the next. */
static void draw_led(ds_t ds, bool usb, bool link, int64_t now)
{
    gfx_color_t c = link ? C_DONE : usb ? C_PERM : C_ERR;
    if (ds == DS_PERM && (now / 400000) % 2) {
        c = C_PANEL;
    }
    if (s.led_drawn && c == s.led) {
        return;
    }
    s.led = c;
    s.led_drawn = true;
    gfx_fill_circle(GFX_W - 13, HEADER_H / 2, 4, c);
}

/* ------------------------------------------------------------------ active screens: THINK / TOOL / PERM / ERR */

static void draw_plan_bar(const zmodel_t *m, ds_t ds, int64_t now)
{
    const int x = PAD + 2, y = PLAN_Y + 14, w = GFX_W - 2 * PAD - 4, h = 6;
    if (m->todo_total <= 0) {
        return;
    }
    gfx_fill_rect(x, y, w, h, C_BG);
    long fill = (long)w * m->todo_done / m->todo_total;
    if (fill > w) {
        fill = w;
    }
    if (fill > 0) {
        gfx_fill_rect(x, y, fill, h, style(ds)->color);
    }
    if (ds == DS_TOOL || ds == DS_THINK) {
        /* a bright block sweeping across the part of the plan still to do */
        const int rest = w - fill;
        if (rest > 4) {
            const int pos = (int)((now / 8000) % (rest + 20)) - 20;
            gfx_set_clip(x + fill, y, rest, h);
            gfx_fill_rect(x + fill + pos, y, 20, h, mix(C_FAINT, C_FG, 140));
            gfx_reset_clip();
        }
    }
}

static void draw_elapsed(const zmodel_t *m, int64_t now)
{
    char t[16];
    format_elapsed(elapsed_now(m, now), t, sizeof(t));
    gfx_fill_rect(PAD, STATS_Y + 11, 7 * 16, 16, C_BG);
    gfx_text(PAD, STATS_Y + 11, t, &s_big2, C_FG);
}

static void content_active(const zmodel_t *m, ds_t ds, int64_t now)
{
    clear_content();

    if (ds == DS_ERR) {
        draw_badge(BADGE_CROSS, C_ERR);
    } else if (ds == DS_PERM) {
        draw_badge_bang(now);
    } else if (ds == DS_TOOL) {
        draw_badge_ticks(now, style(ds)->color);
    } else {
        draw_badge_dots(now);
    }

    if (ds == DS_THINK) {
        gfx_text(RIGHT_X, CONTENT_Y0 + 2, "PROMPT", SMALL, C_DIM);
    } else if (m->tool[0]) {
        gfx_text(RIGHT_X, CONTENT_Y0 + 2, m->tool, &gfx_font_big, style(ds)->color);
    }
    textw_layout(&s.msg, RIGHT_X, MSG_Y, RIGHT_W, MSG_H);
    textw_set(&s.msg, m->msg, now);
    textw_draw(&s.msg, now, C_FG);

    if (m->todo_total > 0) {
        char label[24];
        snprintf(label, sizeof(label), "%d/%d", m->todo_done, m->todo_total);
        gfx_text(PAD, PLAN_Y, "PLAN", SMALL, C_DIM);
        gfx_text_right(GFX_W - PAD, PLAN_Y, label, SMALL, C_FG);
        gfx_frame_rect(PAD, PLAN_Y + 12, GFX_W - 2 * PAD, 10, C_GRID);
        draw_plan_bar(m, ds, now);
        textw_layout(&s.cur, PAD, PLAN_Y + 26, GFX_W - 2 * PAD, CUR_H);
        textw_set(&s.cur, m->cur, now);
        textw_draw(&s.cur, now, C_DIM);
    }

    gfx_fill_rect(0, STATS_Y - 6, GFX_W, CONTENT_Y1 - STATS_Y + 6, C_BG);
    gfx_text(PAD, STATS_Y, "ELAPSED", SMALL, C_DIM);
    draw_elapsed(m, now);
    char buf[24];
    snprintf(buf, sizeof(buf), "TOOLS %d", m->tool_count);
    gfx_text_right(GFX_W - PAD, STATS_Y, buf, SMALL, C_FG);
    snprintf(buf, sizeof(buf), "FAIL %d", m->fail_count);
    gfx_text_right(GFX_W - PAD, STATS_Y + 11, buf, SMALL, m->fail_count ? C_ERR : C_FAINT);
}

/* ------------------------------------------------------------------ DONE */

static void draw_done_countdown(const zmodel_t *m, int64_t now)
{
    const int64_t left = (int64_t)CONFIG_ZTV_UI_DONE_SECONDS * SECOND - (now - m->state_since_us);
    char buf[32];
    snprintf(buf, sizeof(buf), "BACK TO IDLE IN %lldS", (long long)(left > 0 ? left / SECOND : 0));
    gfx_fill_rect(RIGHT_X, CONTENT_Y0 + 24, RIGHT_W, 8, C_BG);
    gfx_text(RIGHT_X, CONTENT_Y0 + 24, buf, SMALL, C_FAINT);
}

static void content_done(const zmodel_t *m, int64_t now)
{
    clear_content();
    draw_badge(BADGE_CHECK, C_DONE);
    char buf[40];
    snprintf(buf, sizeof(buf), "TOOLS %d   FAIL %d", m->tool_count, m->fail_count);
    gfx_text(RIGHT_X, CONTENT_Y0 + 2, buf, SMALL, C_FG);
    char t[16];
    format_elapsed(m->elapsed_s, t, sizeof(t));
    snprintf(buf, sizeof(buf), "TIME %s", t);
    gfx_text(RIGHT_X, CONTENT_Y0 + 13, buf, SMALL, C_DIM);
    draw_done_countdown(m, now);
    textw_layout(&s.done, PAD, DONE_MSG_Y, GFX_W - 2 * PAD, CONTENT_Y1 - 4 - DONE_MSG_Y);
    textw_set(&s.done, m->msg, now);
    textw_draw(&s.done, now, C_FG);
}

/* ------------------------------------------------------------------ IDLE / NO HOST / NO USB / BOOT */

static void content_idle(const zmodel_t *m, ds_t ds)
{
    clear_content();
    draw_clock(m, ds);
    gfx_text_center(GFX_W / 2, 104, "PROJECT", SMALL, C_DIM);
    draw_label_center(GFX_W / 2, 114, m->proj[0] ? m->proj : "zcode", C_FG);
    if (ds == DS_NOHOST) {
        gfx_text_center(GFX_W / 2, 146, "BRIDGE NOT RUNNING", SMALL, C_NOHOST);
        gfx_text_center(GFX_W / 2, 158, "python3 host/zcode_tv.py bridge", SMALL, C_FG);
        gfx_text_center(GFX_W / 2, 170, "python3 host/install_hooks.py", SMALL, C_DIM);
        return;
    }
    gfx_text_center(GFX_W / 2, 146, "READY - WAITING FOR A PROMPT", SMALL, C_IDLE);
    if (m->tool_count > 0) {
        char buf[48], t[16];
        format_elapsed(m->elapsed_s, t, sizeof(t));
        snprintf(buf, sizeof(buf), "LAST TURN %s / %d TOOLS", t, m->tool_count);
        gfx_text_center(GFX_W / 2, 160, buf, SMALL, C_DIM);
    }
}

static void content_nousb(void)
{
    clear_content();
    gfx_text_center(GFX_W / 2, 84, "NO DATA LINK", &s_big2, C_ERR);
    gfx_text_center(GFX_W / 2, 118, "POWERED, BUT NO USB HOST", SMALL, C_FG);
    gfx_text_center(GFX_W / 2, 132, "RECONNECT THE USB CABLE", SMALL, C_DIM);
}

static void content_boot(void)
{
    clear_content();
    gfx_text_center(GFX_W / 2, 88, "ZCODE TV", &s_big2, C_FG);
    gfx_text_center(GFX_W / 2, 116, "ESP32 STATUS DISPLAY", SMALL, C_DIM);
    gfx_text_center(GFX_W / 2, 130, "v" ZTV_VERSION, SMALL, C_FAINT);
}

/* ------------------------------------------------------------------ frame */

static void draw_content(const zmodel_t *m, ds_t ds, int64_t now)
{
    switch (ds) {
    case DS_THINK:
    case DS_TOOL:
    case DS_PERM:
    case DS_ERR:
        content_active(m, ds, now);
        break;
    case DS_DONE:
        content_done(m, now);
        break;
    case DS_IDLE:
    case DS_NOHOST:
        content_idle(m, ds);
        break;
    case DS_NOUSB:
        content_nousb();
        break;
    case DS_BOOT:
    default:
        content_boot();
        break;
    }
}

static void draw_timers(const zmodel_t *m, ds_t ds, int64_t now)
{
    switch (ds) {
    case DS_THINK:
    case DS_TOOL:
    case DS_PERM:
    case DS_ERR:
        draw_elapsed(m, now);
        break;
    case DS_DONE:
        draw_done_countdown(m, now);
        break;
    default:
        break;
    }
}

static void draw_dynamic(const zmodel_t *m, ds_t ds, int64_t now)
{
    const bool usb = z_usb_connected();
    switch (ds) {
    case DS_THINK:
    case DS_TOOL:
    case DS_PERM:
    case DS_ERR:
        if (ds == DS_ERR) {
            draw_badge(BADGE_CROSS, C_ERR);
        } else if (ds == DS_PERM) {
            draw_badge_bang(now);
        } else if (ds == DS_TOOL) {
            draw_badge_ticks(now, style(ds)->color);
        } else {
            draw_badge_dots(now);
        }
        if (s.msg.scrolling) {
            textw_draw_line(&s.msg, s.msg.nlines - 1, now, C_FG);
        }
        if (m->todo_total > 0) {
            draw_plan_bar(m, ds, now);
            if (s.cur.scrolling) {
                textw_draw_line(&s.cur, s.cur.nlines - 1, now, C_DIM);
            }
        }
        break;
    case DS_DONE:
        if (s.done.scrolling) {
            textw_draw_line(&s.done, s.done.nlines - 1, now, C_FG);
        }
        break;
    case DS_BOOT:
        draw_boot_spinner(now);
        break;
    default:
        break;
    }
    draw_led(ds, usb, link_up(m, now), now);
}

void ui_render(void)
{
    if (!s.inited) {
        s_big2 = gfx_font_scaled(&gfx_font_big, 2);
        s_big3 = gfx_font_scaled(&gfx_font_big, 3);
        s.rotation = -1;
        s.inited = true;
    }

    const int64_t started = z_now_us();
    const int64_t now = started;
    zmodel_demo_tick(now);

    zmodel_t m;
    zmodel_get(&m);

    if (m.rotation != s.rotation) {
        s.rotation = panel_set_rotation((uint8_t)m.rotation) == ESP_OK ? m.rotation : -1;
        s.force = true; /* the panel remapped its axes: push the whole frame again */
    }

#ifdef GFX_TRACE_PIXEL
    ztv_phase("after-model");
#endif
    const ds_t ds = derive_state(&m, now);
#ifdef GFX_TRACE_PIXEL
    ztv_phase("after-derive");
#endif
    if (ds != s.ds || s.force) {
        s.ds = ds;
        s.force = false;
        draw_header(ds);
        draw_content(&m, ds, now);
        draw_clock(&m, ds);
        draw_timers(&m, ds, now);
        s.version = m.version;
        strlcpy(s.clock_drawn, m.clock, sizeof(s.clock_drawn));
        s.second = now / SECOND;
#ifdef GFX_TRACE_PIXEL
        ztv_phase("after-fullredraw");
#endif
    } else {
        if (m.version != s.version) {
            s.version = m.version;
            draw_content(&m, ds, now);
            draw_timers(&m, ds, now);
        }
        if (strcmp(s.clock_drawn, m.clock) != 0) {
            strlcpy(s.clock_drawn, m.clock, sizeof(s.clock_drawn));
            draw_clock(&m, ds);
        }
        if (now / SECOND != s.second) {
            s.second = now / SECOND;
            draw_timers(&m, ds, now);
        }
#ifdef GFX_TRACE_PIXEL
        ztv_phase("after-updates");
#endif
    }
    draw_dynamic(&m, ds, now);
#ifdef GFX_TRACE_PIXEL
    ztv_phase("after-dynamic");
#endif
    gfx_flush();

    s.frames++;
    s.last_frame_us = (int32_t)(z_now_us() - started);
    if (s.last_frame_us > s.max_frame_us) {
        s.max_frame_us = s.last_frame_us;
    }
}

void ui_get_stats(ui_stats_t *out)
{
    out->frames = s.frames;
    out->last_frame_us = s.last_frame_us;
    out->max_frame_us = s.max_frame_us;
}
