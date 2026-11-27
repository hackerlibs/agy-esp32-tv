/*
 * Offscreen renderer for the AGY TV status screens.
 *
 * Compiles the real ui.c / gfx.c / zmodel.c on a desktop, feeds the model a
 * scripted AGY session and writes one raw RGB565 frame per screen. It also
 * keeps a simulated panel that is only ever updated through panel_write_rect():
 * after every frame it must be byte-identical to the framebuffer, which proves
 * the dirty-row tracking pushes every changed row (a miss would leave residue
 * on the real panel).
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
    const long pushed_before = g_bytes_pushed;
    ui_render();
    g_frames++;
    const long pushed = g_bytes_pushed - pushed_before;
    if (pushed > g_worst_frame_bytes) {
        g_worst_frame_bytes = pushed;
    }
    if (memcmp(g_panel, gfx_framebuffer(), sizeof(g_panel)) != 0) {
        g_mismatches++;
        for (int y = 0; y < GFX_H; y++) {
            if (memcmp(&g_panel[(size_t)y * GFX_W * 2], gfx_framebuffer() + (size_t)y * GFX_W * 2, GFX_W * 2) != 0) {
                const uint8_t *want = gfx_framebuffer() + (size_t)y * GFX_W * 2;
                int first = -1, last = -1;
                for (int x = 0; x < GFX_W; x++) {
                    if (memcmp(&g_panel[(size_t)y * GFX_W * 2 + x * 2], &want[x * 2], 2) != 0) {
                        if (first < 0) {
                            first = x;
                        }
                        last = x;
                    }
                }
                fail("after %s: row %d on the panel differs from the framebuffer (dirty row not pushed); "
                     "differing pixels x=%d..%d\n",
                     what, y, first, last);
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

static const char *PROJ = "esp32_tv";
static const char *MODEL = "gemini-4-neon-high";

static void push_agy(zstate_t state, const char *tool, const char *msg, const char *cur, long tools, long fails,
                     long elapsed, long done, long total, long inv, long step, long ctx, long out, long sub, long bg)
{
    char clk[ZM_CLK_MAX];
    clock_of(clk);
    const zframe_t f = {
        .state = state,
        .tool = tool,
        .msg = msg,
        .cur = cur,
        .proj = PROJ,
        .model = MODEL,
        .clock = clk,
        .date = "2026-11-27 FRI",
        .tool_count = tools,
        .fail_count = fails,
        .elapsed_s = elapsed,
        .todo_done = done,
        .todo_total = total,
        .inv_count = inv,
        .step_index = step,
        .ctx_tokens = ctx,
        .out_tokens = out,
        .sub_count = sub,
        .bg_count = bg,
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
    memset(g_panel, 0, sizeof(g_panel));
    zmodel_init(cfg.rotation);
    gfx_init();

    printf("scenario: boot with no bridge, then cable unplugged\n");
    g_usb = true;
    run(0.3f, "boot splash");
    save("boot");
    g_now = 3 * 1000000;
    run(1.0f, "no host");
    save("nohost");
    g_usb = false;
    run(0.5f, "no usb");
    save("nousb");

    printf("scenario: bridge connects, AGY idles, then runs a multi-step turn\n");
    g_usb = true;
    push_agy(ZST_IDLE, "", "", "", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0);
    run(0.5f, "idle");
    save("idle");

    g_now += 2 * 1000000;
    push_agy(ZST_THINK, "", "Inspect the ESP32 firmware and redesign the display to show AGY progress, model, and tokens.",
             "Read hooks.md and inspect main/ui.c", 0, 0, 4, 1, 5, 2, 6, 28400, 940, 0, 0);
    run(1.5f, "think");
    save("think");

    push_agy(ZST_TOOL, "run_command", "tools/uitest/run.sh /tmp/agy-tv-ui", "Upgrade firmware model and UI",
             5, 0, 41, 2, 5, 4, 14, 48200, 2150, 0, 0);
    run(1.0f, "tool");
    save("tool");

    /* Verify live same-state update (bug fix in zmodel_apply): stays ZST_TOOL while tool/msg/sub change */
    push_agy(ZST_TOOL, "subagent", "Codebase Researcher: scan host/ and main/ for AGY integration points",
             "Build AGY host bridge and transcript tracker", 8, 0, 68, 3, 5, 5, 22, 76900, 3810, 2, 1);
    run(1.0f, "tool with subagents and bg");
    save("tool_subagent");

    /* No checklist yet -> ACTIVITY fallback with cur */
    push_agy(ZST_TOOL, "read_file", "main/zlink.c:1-285",
             "Checking ZTV1 line protocol parser for backward compatibility", 2, 0, 15, 0, 0, 1, 5, 16200, 420, 0, 0);
    run(0.8f, "tool activity no steps");
    save("tool_activity");

    /* No checklist and no cur -> indeterminate PROGRESS bar */
    push_agy(ZST_THINK, "", "Quick question about ESP32-C3 backlight GPIO1 PWM duty cycle", "",
             1, 0, 7, 0, 0, 1, 3, 9800, 180, 0, 0);
    run(1.0f, "think indeterminate bar");
    save("think_nocur");

    /* Marquee overflow */
    push_agy(ZST_TOOL, "edit_file",
             "Long single line that cannot wrap because it has no spaces at all, "
             "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa so the tail has to scroll sideways "
             "to be readable at all, ping-ponging with a pause at each end",
             "Render the AGY status screens offscreen and check the layout for every single state",
             11, 0, 97, 3, 5, 6, 28, 84100, 4120, 0, 0);
    run(2.6f, "tool with marquee");
    save("tool_marquee");

    /* ask_question -> WAIT INPUT */
    push_agy(ZST_PERM, "ask_question", "Install AGY hooks globally (~/.gemini/config/hooks.json) or workspace only?",
             "Install AGY lifecycle hooks", 14, 0, 132, 4, 5, 7, 31, 88300, 4420, 0, 0);
    run(0.5f, "wait input");
    save("wait_input");

    /* Error */
    push_agy(ZST_ERR, "run_command", "exit status 1: Could not open /dev/ttyACM0, the port is busy",
             "Flash and verify on hardware", 15, 1, 140, 4, 5, 8, 34, 94100, 4890, 0, 0);
    run(0.5f, "error");
    save("err");

    /* Done */
    push_agy(ZST_DONE, "", "Done! ESP32 TV now shows AGY progress, active steps, model name, token usage, "
                           "subagents, and tool summaries via AGY hooks + transcript.jsonl.",
             "", 18, 1, 203, 5, 5, 9, 42, 104500, 5620, 0, 0);
    run(0.5f, "done");
    save("done");

    /* Idle after a turn (shows model + last turn + tokens) */
    push_agy(ZST_IDLE, "", "", "", 18, 1, 203, 0, 0, 9, 42, 104500, 5620, 0, 0);
    run(0.5f, "idle after turn");
    save("idle_stats");

    printf("scenario: link lost while running\n");
    push_agy(ZST_TOOL, "run_command", "sleep 600", "Wait", 20, 1, 300, 4, 5, 10, 45, 110000, 6000, 0, 1);
    run(0.2f, "tool before link loss");
    g_now += 13 * 1000000;
    run(0.5f, "link lost");
    save("lost");

    printf("scenario: Chinese text + mixed scripts + CJK project name\n");
    g_usb = true;
    push_agy(ZST_THINK, "", "帮我把这个 esp32 设备改成显示 AGY 的详情进度：包含步骤条、模型、上下文 Token 和工具详情。",
             "解析 AGY hooks 与 transcript.jsonl", 4, 0, 26, 2, 6, 3, 12, 62300, 2410, 0, 0);
    run(1.0f, "cjk think");
    save("cjk_think");

    push_agy(ZST_TOOL, "edit_file", "main/ui.c：新增 AGY 模型与 Token 状态条、步骤百分比与子任务计数",
             "\xe2\x9c\x85 实时同步 AGY 进度 → update_steps + transcript", 9, 0, 77, 3, 6, 5, 21, 89400, 3720, 1, 1);
    run(1.0f, "cjk tool");
    save("cjk_tool");

    push_agy(ZST_DONE, "", "已完成改造：\n- 固件升级为 AGY TV v2.0.0\n- 支持 AGY hooks 与 transcript 自动追踪\n"
                           "- 兼容旧版 ZTV1 串口协议",
             "", 21, 0, 150, 6, 6, 10, 48, 118200, 6250, 0, 0);
    run(0.5f, "cjk done");
    save("cjk_done");

    PROJ = "\xe5\xb0\x8f\xe7\x94\xb5\xe8\xa7\x86";
    push_agy(ZST_IDLE, "", "", "", 21, 0, 150, 0, 0, 10, 48, 118200, 6250, 0, 0);
    run(0.5f, "cjk idle");
    save("cjk_idle");
    PROJ = "esp32_tv";

    printf("scenario: demo mode, one screen per step\n");
    g_now += 5 * 1000000;
    g_usb = false; /* demo must run without any host */
    zmodel_set_demo(true);
    for (int step = 0; step < 7; step++) {
        run(2.0f, "demo step");
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
