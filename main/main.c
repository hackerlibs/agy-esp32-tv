/*
 * AGY TV: shows what an Antigravity (AGY) session is doing on the little TV's
 * 240x240 panel. The host side (host/agy_tv.py) pushes status frames over the
 * USB Serial/JTAG link; see README.md for the protocol.
 */
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
#include "zlink.h"
#include "zmodel.h"


static const char *TAG = "main";

/* Unset Kconfig booleans are simply not defined in sdkconfig.h. */
#ifdef CONFIG_ZTV_LCD_INVERT
#define LCD_INVERT true
#else
#define LCD_INVERT false
#endif

#ifdef CONFIG_ZTV_LCD_BL_ACTIVE_LOW
#define LCD_BL_ACTIVE_LOW true
#else
#define LCD_BL_ACTIVE_LOW false
#endif

void app_main(void)
{
    ESP_LOGI(TAG, "AGY TV %s booting", ZTV_VERSION);

    z_port_init();

    const panel_config_t panel = {
        .sclk_gpio = CONFIG_ZTV_LCD_PIN_SCLK,
        .mosi_gpio = CONFIG_ZTV_LCD_PIN_MOSI,
        .dc_gpio = CONFIG_ZTV_LCD_PIN_DC,
        .rst_gpio = CONFIG_ZTV_LCD_PIN_RST,
        .cs_gpio = CONFIG_ZTV_LCD_PIN_CS,
        .h_res = CONFIG_ZTV_LCD_H_RES,
        .v_res = CONFIG_ZTV_LCD_V_RES,
        .pclk_hz = CONFIG_ZTV_LCD_SPI_CLOCK_KHZ * 1000,
        .spi_mode = CONFIG_ZTV_LCD_SPI_MODE,
        .rotation = CONFIG_ZTV_LCD_ROTATION,
        .invert = LCD_INVERT,
        .bl_gpio = CONFIG_ZTV_LCD_PIN_BL,
        .bl_brightness = CONFIG_ZTV_LCD_BL_BRIGHTNESS,
        .bl_active_low = LCD_BL_ACTIVE_LOW,
    };
    ESP_ERROR_CHECK(panel_init(&panel));

    gfx_init();
    zmodel_init(CONFIG_ZTV_LCD_ROTATION);
    zlink_start();
    ui_start();
}
