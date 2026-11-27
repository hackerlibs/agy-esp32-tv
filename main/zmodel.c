#include "zmodel.h"

#include <string.h>

#include "port.h"
#include "sdkconfig.h"

static zmodel_t s_model;
static int s_demo_step = -1;

static void copy_str(char *dst, size_t cap, const char *src)
{
    if (src == NULL) {
        dst[0] = '\0';
        return;
    }
    size_t n = strlen(src);
    if (n >= cap) {
        n = cap - 1;
    }
    memcpy(dst, src, n);
    dst[n] = '\0';
}

void zmodel_init(int rotation)
{
    memset(&s_model, 0, sizeof(s_model));
    s_model.state = ZST_IDLE;
    s_model.rotation = rotation & 3;
#if CONFIG_ZTV_UI_DEMO_ON_BOOT
    s_model.demo = true;
#endif
    s_model.version = 1;
}

void zmodel_get(zmodel_t *out)
{
    z_lock();
    *out = s_model;
    z_unlock();
}

void zmodel_apply(const zframe_t *frame, int64_t now_us)
{
    z_lock();
    if (frame->state != s_model.state) {
        s_model.state_since_us = now_us;
    }
    s_model.state = frame->state;
    copy_str(s_model.tool, sizeof(s_model.tool), frame->tool);
    copy_str(s_model.msg, sizeof(s_model.msg), frame->msg);
    copy_str(s_model.cur, sizeof(s_model.cur), frame->cur);
    copy_str(s_model.proj, sizeof(s_model.proj), frame->proj);
    copy_str(s_model.sid, sizeof(s_model.sid), frame->sid);
    copy_str(s_model.clock, sizeof(s_model.clock), frame->clock);
    copy_str(s_model.date, sizeof(s_model.date), frame->date);
    s_model.tool_count = (int)frame->tool_count;
    s_model.fail_count = (int)frame->fail_count;
    s_model.todo_done = (int)frame->todo_done;
    s_model.todo_total = (int)frame->todo_total;
    s_model.elapsed_s = frame->elapsed_s > 0 ? (unsigned)frame->elapsed_s : 0;
    s_model.elapsed_ref_us = now_us;
    s_model.last_frame_us = now_us;
    s_model.version++;
    z_unlock();
}

void zmodel_set_demo(bool on)
{
    z_lock();
    if (s_model.demo != on) {
        s_model.demo = on;
        s_demo_step = -1;
        s_model.version++;
    }
    z_unlock();
}

void zmodel_set_rotation(int rotation)
{
    z_lock();
    rotation &= 3;
    if (s_model.rotation != rotation) {
        s_model.rotation = rotation;
        s_model.version++;
    }
    z_unlock();
}

bool zmodel_demo_enabled(void)
{
    z_lock();
    const bool on = s_model.demo;
    z_unlock();
    return on;
}

int zmodel_rotation(void)
{
    z_lock();
    const int rotation = s_model.rotation;
    z_unlock();
    return rotation;
}

#define DEMO_STEP_US INT64_C(4000000)

static const zframe_t DEMO[] = {
    {
        .state = ZST_THINK,
        .tool = "",
        .msg = "Show what ZCode is doing on the little TV: the ESP32-C3 on the desk should display the "
               "current tool and how far along the plan is.",
        .cur = "Read the hook payload schema",
        .proj = "esp32_tv",
        .clock = "14:02:11",
        .date = "2026-11-27 FRI",
        .todo_done = 1,
        .todo_total = 5,
        .elapsed_s = 4,
    },
    {
        .state = ZST_TOOL,
        .tool = "Bash",
        .msg = "idf.py -p /dev/cu.usbmodem14201 build",
        .cur = "Write the SPI panel driver",
        .proj = "esp32_tv",
        .clock = "14:02:48",
        .date = "2026-11-27 FRI",
        .tool_count = 3,
        .todo_done = 1,
        .todo_total = 5,
        .elapsed_s = 41,
    },
    {
        .state = ZST_TOOL,
        .tool = "Edit",
        .msg = "main/ui.c: redraw only the spinner rows between status frames",
        .cur = "Render the status screens offscreen and check the layout",
        .proj = "esp32_tv",
        .clock = "14:03:44",
        .date = "2026-11-27 FRI",
        .tool_count = 11,
        .todo_done = 3,
        .todo_total = 5,
        .elapsed_s = 97,
    },
    {
        .state = ZST_PERM,
        .tool = "Bash",
        .msg = "esptool write_flash 0x0 build/zcode-tv.bin",
        .cur = "Flash it and verify on the hardware",
        .proj = "esp32_tv",
        .clock = "14:04:19",
        .date = "2026-11-27 FRI",
        .tool_count = 14,
        .todo_done = 4,
        .todo_total = 5,
        .elapsed_s = 132,
    },
    {
        .state = ZST_ERR,
        .tool = "Bash",
        .msg = "exit code 1: A fatal error occurred: Could not open /dev/cu.usbmodem14201, the port is busy",
        .cur = "Flash it and verify on the hardware",
        .proj = "esp32_tv",
        .clock = "14:04:27",
        .date = "2026-11-27 FRI",
        .tool_count = 15,
        .fail_count = 1,
        .todo_done = 4,
        .todo_total = 5,
        .elapsed_s = 140,
    },
    {
        .state = ZST_DONE,
        .msg = "Done. The firmware builds and the panel shows the live status; ZCode hooks push every "
               "event to the board over USB.",
        .cur = "",
        .proj = "esp32_tv",
        .clock = "14:05:30",
        .date = "2026-11-27 FRI",
        .tool_count = 18,
        .fail_count = 1,
        .todo_done = 5,
        .todo_total = 5,
        .elapsed_s = 203,
    },
    {
        .state = ZST_IDLE,
        .msg = "",
        .cur = "",
        .proj = "esp32_tv",
        .clock = "14:06:02",
        .date = "2026-11-27 FRI",
        .tool_count = 18,
        .fail_count = 1,
        .todo_total = 0,
        .elapsed_s = 0,
    },
};

void zmodel_demo_tick(int64_t now_us)
{
    const int steps = (int)(sizeof(DEMO) / sizeof(DEMO[0]));
    const int step = (int)((now_us / DEMO_STEP_US) % steps);
    z_lock();
    const bool advance = s_model.demo && step != s_demo_step;
    if (advance) {
        s_demo_step = step;
    }
    z_unlock();
    if (advance) {
        /* Fed through the normal path so the demo exercises the real pipeline. */
        zmodel_apply(&DEMO[step], now_us);
    }
}
