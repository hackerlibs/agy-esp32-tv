#include "zlink.h"

#include <stdarg.h>
#include <stdlib.h>
#include <string.h>

#include "driver/usb_serial_jtag.h"
#include "esp_log.h"
#include "esp_system.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "gfx.h"
#include "panel.h"
#include "port.h"
#include "sdkconfig.h"
#include "ui.h"
#include "version.h"
#include "zmodel.h"

#define RX_LINE_MAX 640
#define KEY_MAX  16

static const char *TAG = "zlink";

static void reply(const char *fmt, ...)
{
    char buf[192];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(buf, sizeof(buf) - 1, fmt, ap);
    va_end(ap);
    if (n < 0) {
        return;
    }
    if (n > (int)sizeof(buf) - 2) {
        n = (int)sizeof(buf) - 2;
    }
    buf[n++] = '\n';
    /* Never wait: a bridge that is not reading must not stall the link task. */
    usb_serial_jtag_write_bytes(buf, (size_t)n, 0);
}

/* Control characters would only render as blanks. Bytes of 0x80 and above are
 * kept: they are UTF-8, which the firmware renders with its CJK font. */
static char printable(char c)
{
    return (c >= 0x20 && c <= 0x7E) || (unsigned char)c >= 0x80 ? c : ' ';
}

/*
 * Reads one "key=value" pair from *p. Quoted values may contain spaces.
 * Returns false at the end of the line.
 */
static bool next_token(char **p, char *key, char *value, size_t value_cap)
{
    char *s = *p;
    while (*s == ' ') {
        s++;
    }
    if (*s == '\0') {
        return false;
    }
    size_t kl = 0;
    while (*s && *s != ' ' && *s != '=') {
        if (kl < KEY_MAX - 1) {
            key[kl++] = *s;
        }
        s++;
    }
    key[kl] = '\0';
    value[0] = '\0';
    if (*s == '=') {
        s++;
        size_t vl = 0;
        const bool quoted = (*s == '"');
        if (quoted) {
            s++;
        }
        while (*s && (quoted ? *s != '"' : *s != ' ')) {
            if (vl < value_cap - 1) {
                value[vl++] = printable(*s);
            }
            s++;
        }
        if (quoted && *s == '"') {
            s++;
        }
        value[vl] = '\0';
    }
    *p = s;
    return true;
}

static bool parse_state(const char *st, zstate_t *out)
{
    static const struct {
        const char *name;
        zstate_t state;
    } TABLE[] = {
        {"idle", ZST_IDLE}, {"think", ZST_THINK}, {"tool", ZST_TOOL},
        {"perm", ZST_PERM}, {"err", ZST_ERR},     {"done", ZST_DONE},
    };
    for (size_t i = 0; i < sizeof(TABLE) / sizeof(TABLE[0]); i++) {
        if (strcmp(st, TABLE[i].name) == 0) {
            *out = TABLE[i].state;
            return true;
        }
    }
    return false;
}

static void handle_status(char *p)
{
    zframe_t f = {0};
    char st[KEY_MAX] = "";
    char tool[ZM_TOOL_MAX] = "";
    char msg[ZM_MSG_MAX] = "";
    char cur[ZM_CUR_MAX] = "";
    char proj[ZM_PROJ_MAX] = "";
    char sid[ZM_SID_MAX] = "";
    char clk[ZM_CLK_MAX] = "";
    char date[ZM_DATE_MAX] = "";
    char key[KEY_MAX];
    char value[ZM_MSG_MAX];

    while (next_token(&p, key, value, sizeof(value))) {
        if (strcmp(key, "st") == 0) {
            strlcpy(st, value, sizeof(st));
        } else if (strcmp(key, "tool") == 0) {
            strlcpy(tool, value, sizeof(tool));
            f.tool = tool;
        } else if (strcmp(key, "msg") == 0) {
            strlcpy(msg, value, sizeof(msg));
            f.msg = msg;
        } else if (strcmp(key, "cur") == 0) {
            strlcpy(cur, value, sizeof(cur));
            f.cur = cur;
        } else if (strcmp(key, "proj") == 0) {
            strlcpy(proj, value, sizeof(proj));
            f.proj = proj;
        } else if (strcmp(key, "sid") == 0) {
            strlcpy(sid, value, sizeof(sid));
            f.sid = sid;
        } else if (strcmp(key, "clk") == 0) {
            strlcpy(clk, value, sizeof(clk));
            f.clock = clk;
        } else if (strcmp(key, "date") == 0) {
            strlcpy(date, value, sizeof(date));
            f.date = date;
        } else if (strcmp(key, "n") == 0) {
            f.tool_count = atol(value);
        } else if (strcmp(key, "f") == 0) {
            f.fail_count = atol(value);
        } else if (strcmp(key, "el") == 0) {
            f.elapsed_s = atol(value);
        } else if (strcmp(key, "todo") == 0) {
            char *slash = strchr(value, '/');
            if (slash) {
                *slash = '\0';
                f.todo_total = atol(slash + 1);
            }
            f.todo_done = atol(value);
        }
    }
    if (!parse_state(st, &f.state)) {
        return; /* a frame without a state we know is not a snapshot we can show */
    }
    /* Leaving demo mode happens implicitly: real frames always win. */
    zmodel_set_demo(false);
    zmodel_apply(&f, z_now_us());
}

static void handle_command(char *p)
{
    char key[KEY_MAX];
    char value[32];
    if (!next_token(&p, key, value, sizeof(value))) {
        return;
    }
    if (strcmp(key, "ping") == 0) {
        reply("ZTV1 R pong fw=\"%s\" lcd=%dx%d rot=%d usb=%d demo=%d", ZTV_VERSION, CONFIG_ZTV_LCD_H_RES,
              CONFIG_ZTV_LCD_V_RES, zmodel_rotation(), z_usb_connected() ? 1 : 0,
              zmodel_demo_enabled() ? 1 : 0);
    } else if (strcmp(key, "demo") == 0) {
        const bool on = atoi(value) != 0;
        zmodel_set_demo(on);
        reply("ZTV1 R demo=%d", on ? 1 : 0);
    } else if (strcmp(key, "rot") == 0) {
        const int rot = atoi(value);
        if (rot >= 0 && rot <= 3) {
            zmodel_set_rotation(rot);
            reply("ZTV1 R rot=%d", rot);
        } else {
            reply("ZTV1 R error rot must be 0..3");
        }
    } else if (strcmp(key, "get") == 0) {
        /* What the device is currently showing: lets the host check a frame landed. */
        static const char *const NAMES[] = {"boot", "idle", "think", "perm", "tool", "err", "done"};
        zmodel_t m;
        zmodel_get(&m);
        reply("ZTV1 R state st=%s tool=\"%s\" n=%d f=%d el=%u todo=%d/%d demo=%d rot=%d msg=\"%s\"",
              NAMES[m.state], m.tool, m.tool_count, m.fail_count, m.elapsed_s, m.todo_done, m.todo_total,
              m.demo ? 1 : 0, m.rotation, m.msg);
    } else if (strcmp(key, "stats") == 0) {
        /* Runtime numbers, so the render loop can be checked without seeing the panel. */
        gfx_stats_t g;
        ui_stats_t u;
        gfx_get_stats(&g);
        ui_get_stats(&u);
        reply("ZTV1 R stats frames=%lu frame_last_us=%ld frame_max_us=%ld flushes=%lu rows=%lu "
              "flush_last_us=%ld flush_max_us=%ld heap=%u",
              (unsigned long)u.frames, (long)u.last_frame_us, (long)u.max_frame_us, (unsigned long)g.flushes,
              (unsigned long)g.rows, (long)g.last_us, (long)g.max_us,
              (unsigned)esp_get_free_heap_size());
    } else if (strcmp(key, "bl") == 0) {
        /* Backlight diagnostics, so the host can confirm the PWM is really applied. */
        reply("ZTV1 R bl pin=%d duty=%d", CONFIG_ZTV_LCD_PIN_BL, panel_backlight_duty());
    } else if (strcmp(key, "reboot") == 0) {
        reply("ZTV1 R rebooting");
        vTaskDelay(pdMS_TO_TICKS(50));
        z_reboot();
    } else {
        reply("ZTV1 R error unknown command: %s", key);
    }
}

static void handle_line(char *line)
{
    if (strncmp(line, "ZTV1 ", 5) != 0) {
        return; /* boot logs and anything else that shares the console */
    }
    char *p = line + 5;
    if (*p == 'S' && (p[1] == ' ' || p[1] == '\0')) {
        handle_status(p + 1);
    } else if (*p == 'C' && (p[1] == ' ' || p[1] == '\0')) {
        handle_command(p + 1);
    }
}

static void reader_task(void *arg)
{
    static char line[RX_LINE_MAX];
    size_t len = 0;
    bool overlong = false;
    uint8_t buf[64];

    for (;;) {
        const int n = (int)usb_serial_jtag_read_bytes(buf, sizeof(buf), pdMS_TO_TICKS(50));
        for (int i = 0; i < n; i++) {
            const char c = (char)buf[i];
            if (c == '\n' || c == '\r') {
                if (len > 0 && !overlong) {
                    line[len] = '\0';
                    handle_line(line);
                }
                len = 0;
                overlong = false;
            } else if (len < RX_LINE_MAX - 1) {
                line[len++] = c;
            } else {
                /* overlong line: drop the rest of it rather than act on a truncated frame */
                overlong = true;
            }
        }
    }
}

void zlink_start(void)
{
    usb_serial_jtag_driver_config_t cfg = {
        .rx_buffer_size = 1024,
        .tx_buffer_size = 1024,
    };
    esp_err_t err = usb_serial_jtag_driver_install(&cfg);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "usb_serial_jtag_driver_install failed: %s", esp_err_to_name(err));
        return;
    }
    /* Installing the driver also switches the console (stdout) over to it. */

    xTaskCreate(reader_task, "zlink", 4096, NULL, 6, NULL);
    reply("ZTV1 R hello fw=\"%s\" lcd=%dx%d rot=%d", ZTV_VERSION, CONFIG_ZTV_LCD_H_RES, CONFIG_ZTV_LCD_V_RES,
          zmodel_rotation());
    ESP_LOGI(TAG, "link ready, firmware %s", ZTV_VERSION);
}
