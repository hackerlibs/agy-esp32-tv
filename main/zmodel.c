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

static bool str_changed(const char *incoming, const char *existing, size_t cap)
{
    char clipped[ZM_MSG_MAX];
    copy_str(clipped, cap < sizeof(clipped) ? cap : sizeof(clipped), incoming);
    return strcmp(clipped, existing) != 0;
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

/* Heartbeat frames re-send the same status; only real changes may trigger a
 * panel repaint, so compare everything except the clock (which has its own
 * cheap redraw path in the UI). Must be called BEFORE mutating s_model. */
static bool frame_changed(const zframe_t *f)
{
    const unsigned ctx = f->ctx_tokens > 0 ? (unsigned)f->ctx_tokens : 0;
    const unsigned out = f->out_tokens > 0 ? (unsigned)f->out_tokens : 0;
    return f->state != s_model.state || str_changed(f->tool, s_model.tool, sizeof(s_model.tool)) ||
           str_changed(f->msg, s_model.msg, sizeof(s_model.msg)) ||
           str_changed(f->cur, s_model.cur, sizeof(s_model.cur)) ||
           str_changed(f->proj, s_model.proj, sizeof(s_model.proj)) ||
           str_changed(f->sid, s_model.sid, sizeof(s_model.sid)) ||
           str_changed(f->date, s_model.date, sizeof(s_model.date)) ||
           str_changed(f->model, s_model.model, sizeof(s_model.model)) ||
           (int)f->tool_count != s_model.tool_count || (int)f->fail_count != s_model.fail_count ||
           (int)f->todo_done != s_model.todo_done || (int)f->todo_total != s_model.todo_total ||
           (int)f->inv_count != s_model.inv_count || (int)f->step_index != s_model.step_index ||
           ctx != s_model.ctx_tokens || out != s_model.out_tokens || (int)f->sub_count != s_model.sub_count ||
           (int)f->bg_count != s_model.bg_count;
}

void zmodel_apply(const zframe_t *frame, int64_t now_us)
{
    z_lock();
    const bool changed = frame_changed(frame);
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
    copy_str(s_model.model, sizeof(s_model.model), frame->model);
    s_model.tool_count = (int)frame->tool_count;
    s_model.fail_count = (int)frame->fail_count;
    s_model.todo_done = (int)frame->todo_done;
    s_model.todo_total = (int)frame->todo_total;
    s_model.inv_count = (int)frame->inv_count;
    s_model.step_index = (int)frame->step_index;
    s_model.ctx_tokens = frame->ctx_tokens > 0 ? (unsigned)frame->ctx_tokens : 0;
    s_model.out_tokens = frame->out_tokens > 0 ? (unsigned)frame->out_tokens : 0;
    s_model.sub_count = (int)frame->sub_count;
    s_model.bg_count = (int)frame->bg_count;
    s_model.elapsed_s = frame->elapsed_s > 0 ? (unsigned)frame->elapsed_s : 0;
    s_model.elapsed_ref_us = now_us;
    s_model.last_frame_us = now_us;
    if (changed) {
        s_model.version++;
    }
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
        .msg = "帮我把这个 esp32 设备改成显示 AGY 的详情进度、模型、Token 和步骤状态。",
        .cur = "读取 AGY hooks 规范和固件渲染代码",
        .proj = "esp32_tv",
        .model = "gemini-4-neon-high",
        .clock = "14:02:11",
        .date = "2026-11-27 FRI",
        .todo_done = 1,
        .todo_total = 5,
        .inv_count = 2,
        .step_index = 6,
        .ctx_tokens = 28400,
        .out_tokens = 940,
        .elapsed_s = 12,
    },
    {
        .state = ZST_TOOL,
        .tool = "run_command",
        .msg = "tools/uitest/run.sh /tmp/agy-tv-ui",
        .cur = "升级固件状态模型与协议",
        .proj = "esp32_tv",
        .model = "gemini-4-neon-high",
        .clock = "14:02:48",
        .date = "2026-11-27 FRI",
        .tool_count = 5,
        .todo_done = 2,
        .todo_total = 5,
        .inv_count = 4,
        .step_index = 14,
        .ctx_tokens = 48200,
        .out_tokens = 2150,
        .elapsed_s = 49,
    },
    {
        .state = ZST_TOOL,
        .tool = "invoke_subagent",
        .msg = "Codebase Researcher: inspect AGY transcript & hooks schema",
        .cur = "实现 AGY bridge 桥接与 transcript 解析",
        .proj = "esp32_tv",
        .model = "gemini-4-neon-high",
        .clock = "14:03:44",
        .date = "2026-11-27 FRI",
        .tool_count = 11,
        .todo_done = 3,
        .todo_total = 5,
        .inv_count = 6,
        .step_index = 24,
        .ctx_tokens = 76900,
        .out_tokens = 3810,
        .sub_count = 2,
        .bg_count = 1,
        .elapsed_s = 97,
    },
    {
        .state = ZST_PERM,
        .tool = "ask_question",
        .msg = "是否将 AGY hooks 安装到全局 ~/.gemini/config/hooks.json？",
        .cur = "安装 AGY 生命周期 hooks",
        .proj = "esp32_tv",
        .model = "gemini-4-neon-high",
        .clock = "14:04:19",
        .date = "2026-11-27 FRI",
        .tool_count = 14,
        .todo_done = 4,
        .todo_total = 5,
        .inv_count = 7,
        .step_index = 31,
        .ctx_tokens = 88300,
        .out_tokens = 4420,
        .elapsed_s = 132,
    },
    {
        .state = ZST_ERR,
        .tool = "run_command",
        .msg = "exit status 1: Could not open /dev/ttyACM0, port is busy",
        .cur = "烧录并验证硬件显示",
        .proj = "esp32_tv",
        .model = "gemini-4-neon-high",
        .clock = "14:04:27",
        .date = "2026-11-27 FRI",
        .tool_count = 15,
        .fail_count = 1,
        .todo_done = 4,
        .todo_total = 5,
        .inv_count = 8,
        .step_index = 34,
        .ctx_tokens = 94100,
        .out_tokens = 4890,
        .elapsed_s = 140,
    },
    {
        .state = ZST_DONE,
        .msg = "已完成 AGY TV 改造：支持 AGY hooks + transcript 实时同步步骤进度、模型与 Token 消耗。",
        .cur = "",
        .proj = "esp32_tv",
        .model = "gemini-4-neon-high",
        .clock = "14:05:30",
        .date = "2026-11-27 FRI",
        .tool_count = 18,
        .fail_count = 1,
        .todo_done = 5,
        .todo_total = 5,
        .inv_count = 9,
        .step_index = 42,
        .ctx_tokens = 104500,
        .out_tokens = 5620,
        .elapsed_s = 203,
    },
    {
        .state = ZST_IDLE,
        .msg = "",
        .cur = "",
        .proj = "esp32_tv",
        .model = "gemini-4-neon-high",
        .clock = "14:06:02",
        .date = "2026-11-27 FRI",
        .tool_count = 18,
        .fail_count = 1,
        .todo_total = 0,
        .ctx_tokens = 104500,
        .out_tokens = 5620,
        .elapsed_s = 203,
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
