/*
 * Bring-up test (CONFIG_ZTV_TEST_BARS): cycle panel configurations, each shown
 * with its own color, so one glance at the panel identifies a working setup.
 * The console logs every phase switch. Sits in place of the status UI and never
 * returns.
 */
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "panel.h"
#include "port.h"
#include "sdkconfig.h"

#include <string.h>

static const char *TAG = "bars";

typedef struct {
    const char *name;
    uint8_t spi_mode;
    uint32_t pclk_hz;
    bool skip_reset;
    bool swap_dc_rst;
    bool invert;
    uint16_t color; /* RGB565; 0 = vertical rainbow bars */
} phase_t;

static const phase_t PHASES[] = {
    {"mode0 27MHz RAINBOW BARS", 0, 27000000, false, false, true, 0},
    {"mode3 27MHz SOLID GREEN", 3, 27000000, false, false, true, 0x07E0},
    {"mode0 10MHz SOLID CYAN", 0, 10000000, false, false, true, 0x07FF},
    {"mode3 10MHz SOLID ORANGE", 3, 10000000, false, false, true, 0xFD20},
    {"mode0 40MHz SOLID PINK", 0, 40000000, false, false, true, 0xFB56},
    {"mode3 40MHz SOLID YELLOW", 3, 40000000, false, false, true, 0xFFE0},
    {"mode0 27MHz NO RESET PULSE, RED", 0, 27000000, true, false, true, 0xF800},
    {"mode0 27MHz NO INVERT, RED", 0, 27000000, false, false, false, 0xF800},
};

#define PHASE_SECONDS 6

static void fill_solid(uint16_t color)
{
    static uint16_t row[240];
    for (int x = 0; x < 240; x++) {
        row[x] = __builtin_bswap16(color);
    }
    for (int y = 0; y < 240; y++) {
        panel_write_rect(0, (uint16_t)y, 240, 1, (const uint8_t *)row);
    }
}

static void fill_rainbow(void)
{
    static uint16_t row[240];
    for (int x = 0; x < 240; x++) {
        const uint8_t band = (uint8_t)(x / 20); /* 12 color bands */
        uint16_t c;
        switch (band) {
        case 0: c = 0xF800; break;   /* red */
        case 1: c = 0xFD20; break;   /* orange */
        case 2: c = 0xFFE0; break;   /* yellow */
        case 3: c = 0x07E0; break;   /* green */
        case 4: c = 0x07FF; break;   /* cyan */
        case 5: c = 0x001F; break;   /* blue */
        case 6: c = 0xF81F; break;   /* magenta */
        default: c = (uint16_t)(band * 811); break; /* shades */
        }
        row[x] = __builtin_bswap16(c);
    }
    for (int y = 0; y < 240; y++) {
        panel_write_rect(0, (uint16_t)y, 240, 1, (const uint8_t *)row);
    }
}

void bars_test_run(void)
{
    int index = 0;
    for (;;) {
        const phase_t *p = &PHASES[index % (sizeof(PHASES) / sizeof(PHASES[0]))];
        panel_free();
        const panel_config_t cfg = {
            .sclk_gpio = CONFIG_ZTV_LCD_PIN_SCLK,
            .mosi_gpio = CONFIG_ZTV_LCD_PIN_MOSI,
            .dc_gpio = CONFIG_ZTV_LCD_PIN_DC,
            .rst_gpio = CONFIG_ZTV_LCD_PIN_RST,
            .cs_gpio = CONFIG_ZTV_LCD_PIN_CS,
            .h_res = CONFIG_ZTV_LCD_H_RES,
            .v_res = CONFIG_ZTV_LCD_V_RES,
            .pclk_hz = p->pclk_hz,
            .spi_mode = p->spi_mode,
            .rotation = CONFIG_ZTV_LCD_ROTATION,
            .invert = p->invert,
            .skip_reset = p->skip_reset,
            .swap_dc_rst = p->swap_dc_rst,
        };
        ESP_LOGI(TAG, "phase %d: %s", index, p->name);
        esp_err_t err = panel_init(&cfg);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "phase %d init failed: %s", index, esp_err_to_name(err));
            vTaskDelay(pdMS_TO_TICKS(PHASE_SECONDS * 1000));
        } else {
            if (p->color == 0) {
                fill_rainbow();
            } else {
                fill_solid(p->color);
            }
            vTaskDelay(pdMS_TO_TICKS(PHASE_SECONDS * 1000));
        }
        index++;
    }
}
