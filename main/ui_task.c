/*
 * Render task: drives ui_render() at a fixed frame rate. Only this task touches
 * the framebuffer and the panel, so the drawing code needs no locking.
 */
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "port.h"
#include "sdkconfig.h"
#include "ui.h"

#define FRAME_PERIOD_US (1000000 / CONFIG_ZTV_UI_FPS)

static void ui_task(void *arg)
{
    int64_t next = 0;
    for (;;) {
        ui_render();
        next += FRAME_PERIOD_US;
        const int64_t now = z_now_us();
        if (now < next) {
            vTaskDelay(pdMS_TO_TICKS((next - now) / 1000));
        } else {
            next = now; /* fell behind: resync instead of accumulating a backlog */
        }
    }
}

void ui_start(void)
{
    /* ui_render() keeps a model copy, a text widget copy and printf buffers on the stack. */
    xTaskCreatePinnedToCore(ui_task, "ui", 6144, NULL, 4, NULL, 0);
}
