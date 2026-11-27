/*
 * Offscreen renderer for the status screens.
 *
 * Compiles the real ui.c / gfx.c / zmodel.c on a desktop, feeds the model a
 * scripted session and writes one raw RGB565 frame per screen. It also keeps a
 * simulated panel that is only ever updated through panel_write_rect(): after
 * every frame it must be byte-identical to the framebuffer, which proves the
 * dirty-row tracking pushes every changed row (a miss would leave residue on
 * the real panel).
 *
 * Build and run with tools/uitest/run.sh.
 */
#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "gfx.h"
#include "panel.h"
#include "port.h"
#include "ui.h"
#include "zmodel.h"

static int64_t g_now;
static bool g_usb = true;
static uint8_t g_panel[GFX_H * GFX_W * 2];
static long g_frames, g_mismatches, g_bytes_pushed, g_worst_frame_bytes;

int64_t z_now_us(void)
{
    return g_now;
}

bool z_usb_connected(void)
{
    return g_usb;
}

void z_lock(void)
{
}
void z_unlock(void)
{
}
void z_reboot(void)
{
    abort();
}

esp_err_t panel_set_rotation(uint8_t rotation)
{
    (void)rotation;
    return ESP_OK;
}

esp_err_t panel_write_rect(uint16_t x, uint16_t y, uint16_t w, uint16_t h, const uint8_t *pixels)
{
    g_bytes_pushed += (long)w * h * 2;
    for (uint16_t r = 0; r < h; r++) {
        memcpy(&g_panel[((size_t)(y + r) * GFX_W + x) * 2], pixels + (size_t)r * w * 2, (size_t)w * 2);
    }
    return ESP_OK;
}

static void fail(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    vfprintf(stderr, fmt, ap);
    va_end(ap);
    exit(1);
}

static void render(const char *what)
{
    const long before = g_bytes_pushed;
    ui_render();
    g_frames++;
    const long pushed = g_bytes_pushed - before;
    if (pushed > g_worst_frame_bytes) {
        g_worst_frame_bytes = pushed;
    }
    if (memcmp(g_panel, gfx_framebuffer(), sizeof(g_panel)) != 0) {
        g_mismatches++;
        for (int y = 0; y < GFX_H; y++) {
            if (memcmp(&g_panel[(size_t)y * GFX_W * 2], gfx_framebuffer() + (size_t)y * GFX_W * 2, GFX_W * 2) != 0) {
                fail("after %s: row %d on the panel differs from the framebuffer (dirty row not pushed)\n", what, y);
            }
        }
        fail("after %s: panel differs from the framebuffer\n", what);
    }
    g_now += 50000; /* 20 fps */
}

/* Runs `seconds` worth of frames so spinners and marquees go through their paces. */
static void run(float seconds, const char *what)
{
    const int n = (int)(seconds * 20);
    for (int i = 0; i < n; i++) {
        render(what);
    }
}

static char g_outdir[1024];
static int g_index;

static void save(const char *name)
{
    char path[1200];
    snprintf(path, sizeof(path), "%s/%02d_%s.rgb565", g_outdir, g_index++, name);
    FILE *f = fopen(path, "wb");
    if (!f) {
        fail("cannot write %s\n", path);
    }
    fwrite(gfx_framebuffer(), GFX_H * GFX_W * 2, 1, f);
    fclose(f);
    printf("  %s\n", path);
}

static void clock_of(char out[ZM_CLK_MAX])
{
    const long sec = 14 * 3600 + 2 * 60 + (long)(g_now / 1000000);
    snprintf(out, ZM_CLK_MAX, "%02ld:%02ld:%02ld", sec / 3600 % 100, sec / 60 % 60, sec % 60);
}

static void push(zstate_t state, const char *tool, const char *msg, const char *cur, long tools, long fails,
                 long elapsed, long done, long total)
{
    char clk[ZM_CLK_MAX];
    clock_of(clk);
    const zframe_t f = {
        .state = state,
        .tool = tool,
        .msg = msg,
        .cur = cur,
        .proj = "esp32_tv",
        .clock = clk,
        .date = "2026-11-27 FRI",
        .tool_count = tools,
        .fail_count = fails,
        .elapsed_s = elapsed,
        .todo_done = done,
        .todo_total = total,
    };
    zmodel_apply(&f, g_now);
}

int main(int argc, char **argv)
{
    if (argc != 2) {
        fail("usage: uitest <output directory>\n");
    }
    snprintf(g_outdir, sizeof(g_outdir), "%s", argv[1]);

    panel_config_t cfg = {.rotation = 0};
    memset(g_panel, 0, sizeof(g_panel)); /* a fresh panel shows whatever it powers up with */
    zmodel_init(cfg.rotation);
    gfx_init();

    printf("scenario: boot with no bridge, then the cable unplugged\n");
    g_usb = true;
    run(0.3, "boot splash");
    save("boot");
    g_now = 3 * 1000000;
    run(1.0, "no host");
    save("nohost");
    g_usb = false;
    run(0.5, "no usb");
    save("nousb");

    printf("scenario: bridge connects, session idles, then a turn runs\n");
    g_usb = true;
    push(ZST_IDLE, "", "", "", 0, 0, 0, 0, 0);
    run(0.5, "idle");
    save("idle");

    g_now += 2 * 1000000;
    push(ZST_THINK, "", "Show what ZCode is doing on the little TV: the ESP32-C3 on the desk should display "
                        "the current tool and how far along the plan is.", "Read the hook payload schema",
         0, 0, 2, 1, 5);
    run(1.5, "think");
    save("think");

    push(ZST_TOOL, "Bash", "idf.py -p /dev/cu.usbmodem14201 build", "Write the SPI panel driver", 3, 0, 41, 1, 5);
    run(1.0, "tool");
    save("tool");

    push(ZST_TOOL, "Edit", "// more than one screenful of text: the tail of this line has to scroll "
                           "sideways so it can be read, ping-ponging with a pause at each end",
         "Render the status screens offscreen and check the layout for every state", 11, 0, 97, 3, 5);
    run(2.6, "tool with marquee");
    save("tool_marquee");

    push(ZST_PERM, "Bash", "esptool write_flash 0x0 build/zcode-tv.bin", "Flash it and verify on the hardware",
         14, 0, 132, 4, 5);
    run(0.5, "permission");
    save("perm");

    push(ZST_ERR, "Bash", "exit code 1: Could not open /dev/cu.usbmodem14201, the port is busy or does not exist",
         "Flash it and verify on the hardware", 15, 1, 140, 4, 5);
    run(0.5, "tool failed");
    save("err");

    push(ZST_DONE, "", "Done. The firmware builds and the panel shows the live status; ZCode hooks push every "
                       "event to the board over USB.\n\nWhat changed:\n- main/: panel driver, drawing layer, "
                       "status screens\n- host/: bridge and hook entry\n- tools/: build, flash and restore scripts",
         "", 18, 1, 203, 5, 5);
    run(0.5, "done");
    save("done");

    printf("scenario: the bridge stops sending while a tool runs (link lost)\n");
    push(ZST_TOOL, "Bash", "sleep 600", "Wait", 20, 1, 300, 4, 5);
    run(0.2, "tool before link loss");
    g_now += 13 * 1000000;
    run(0.5, "link lost");
    save("lost");

    printf("scenario: demo mode, one screen per step\n");
    g_now += 5 * 1000000;
    g_usb = false; /* demo must run without any host */
    zmodel_set_demo(true);
    for (int step = 0; step < 7; step++) {
        run(2.0, "demo step");
        char name[32];
        snprintf(name, sizeof(name), "demo%d", step);
        save(name);
    }

    printf("\n%ld frames, %ld panel/framebuffer mismatches, %ld bytes pushed, worst single frame %ld bytes (%.0f%% "
           "of a full screen)\n",
           g_frames, g_mismatches, g_bytes_pushed, g_worst_frame_bytes,
           100.0 * (double)g_worst_frame_bytes / (double)sizeof(g_panel));
    return g_mismatches ? 1 : 0;
}
