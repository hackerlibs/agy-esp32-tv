#pragma once

#include <stdint.h>

/*
 * AGY TV status screens. One call to ui_render() draws at most one frame: it
 * decides what changed since the previous call and pushes only the dirty rows
 * to the panel.
 */
void ui_start(void); /* ESP-IDF: spawns the render task */

/* Draws one frame. Needs panel_init() and gfx_init() first; also used by the
 * offscreen test harness (tools/uitest). */
void ui_render(void);

typedef struct {
    uint32_t frames;
    int32_t last_frame_us;
    int32_t max_frame_us;
} ui_stats_t;

void ui_get_stats(ui_stats_t *out);
