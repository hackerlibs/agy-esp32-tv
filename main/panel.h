/*
 * SPI TFT panel driver (240x240, ST7789 class).
 *
 * The init sequence below is not the generic ST7789 one: it is the exact
 * command table the stock firmware of this board sends, recovered from its
 * flash image, so the panel comes up the way it is known to work here.
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    int sclk_gpio;
    int mosi_gpio;
    int dc_gpio;  /* data/command, -1 if not wired */
    int rst_gpio; /* -1 if not wired */
    int cs_gpio;  /* -1 if tied low */
    uint16_t h_res;
    uint16_t v_res;
    uint32_t pclk_hz;
    uint8_t spi_mode;
    uint8_t rotation; /* 0..3, same meaning as TFT_eSPI setRotation() */
    bool invert;      /* send INVON, needed by IPS panels */
} panel_config_t;

esp_err_t panel_init(const panel_config_t *cfg);

/* Writes a rectangle of big-endian RGB565 pixels (w * h * 2 bytes). */
esp_err_t panel_write_rect(uint16_t x, uint16_t y, uint16_t w, uint16_t h, const uint8_t *pixels);

esp_err_t panel_set_rotation(uint8_t rotation);
uint8_t panel_rotation(void);

#ifdef __cplusplus
}
#endif
