/*
 * Shared status model: what the host bridge last told us about the AGY
 * (Antigravity) session. The link task writes it under a mutex, the UI task
 * copies it out once per frame.
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>

typedef enum {
    ZST_BOOT = 0,
    ZST_IDLE,
    ZST_THINK,
    ZST_PERM,
    ZST_TOOL,
    ZST_ERR,
    ZST_DONE,
} zstate_t;

#define ZM_TOOL_MAX  20
#define ZM_MSG_MAX   240 /* bytes of UTF-8: about 80 Chinese characters */
#define ZM_CUR_MAX   120
#define ZM_PROJ_MAX  32
#define ZM_SID_MAX   12
#define ZM_CLK_MAX   12 /* "HH:MM:SS" */
#define ZM_DATE_MAX  20 /* "YYYY-MM-DD DDD" */
#define ZM_MODEL_MAX 28 /* e.g. "gemini-4-neon-high" */

/* One full status frame as sent by the host bridge. Strings may be NULL. */
typedef struct {
    zstate_t state;
    const char *tool;
    const char *msg;
    const char *cur;
    const char *proj;
    const char *sid;
    const char *clock;
    const char *date;
    const char *model;
    long tool_count;
    long fail_count;
    long elapsed_s;
    long todo_done;
    long todo_total;
    long inv_count;
    long step_index;
    long ctx_tokens;
    long out_tokens;
    long sub_count;
    long bg_count;
} zframe_t;

typedef struct {
    zstate_t state;
    char tool[ZM_TOOL_MAX];
    char msg[ZM_MSG_MAX];
    char cur[ZM_CUR_MAX];
    char proj[ZM_PROJ_MAX];
    char sid[ZM_SID_MAX];
    char clock[ZM_CLK_MAX];
    char date[ZM_DATE_MAX];
    char model[ZM_MODEL_MAX];
    int tool_count;
    int fail_count;
    int todo_done;
    int todo_total;
    int inv_count;
    int step_index;
    unsigned ctx_tokens;
    unsigned out_tokens;
    int sub_count;
    int bg_count;
    unsigned elapsed_s;     /* turn elapsed time, seconds, as of elapsed_ref_us */
    int64_t elapsed_ref_us;
    int64_t state_since_us; /* when the current state was entered */
    int64_t last_frame_us;  /* when the last frame arrived, 0 if never */
    uint32_t version;       /* bumped on every change */
    bool demo;
    int rotation;           /* requested panel rotation, applied by the UI task */
} zmodel_t;

void zmodel_init(int rotation);
void zmodel_get(zmodel_t *out);
void zmodel_apply(const zframe_t *frame, int64_t now_us);
void zmodel_set_demo(bool on);
void zmodel_set_rotation(int rotation);
bool zmodel_demo_enabled(void);
int zmodel_rotation(void);

/* Demo mode: sample frames for each screen, advanced every few seconds. */
void zmodel_demo_tick(int64_t now_us);
