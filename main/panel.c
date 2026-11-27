#include "panel.h"

#include "driver/gpio.h"
#include "driver/spi_master.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#define CMD_NORON  0x13
#define CMD_SLPOUT 0x11
#define CMD_DISPON 0x29
#define CMD_CASET  0x2A
#define CMD_RASET  0x2B
#define CMD_RAMWR  0x2C
#define CMD_MADCTL 0x36
#define CMD_COLMOD 0x3A
#define CMD_INVON  0x21

/* The GPSPI DMA length register holds 18 bits, so one transaction carries at
 * most 32 KiB: a full 240x240 frame is 115200 bytes and has to be split. The
 * panel keeps streaming pixels into the open window across transactions. */
#define PANEL_MAX_TX_BYTES 32640

/* MADCTL values used by the stock firmware's setRotation(). */
static const uint8_t MADCTL[4] = {0x00, 0x60, 0xC0, 0xA0};

static const char *TAG = "panel";

static spi_device_handle_t s_spi;
static panel_config_t s_cfg;
static uint8_t s_rotation;

static void delay_ms(uint32_t ms)
{
    vTaskDelay(pdMS_TO_TICKS(ms));
}

/* The panel's chip select is driven by hand so that a whole window write stays
 * inside one uninterrupted stream. Boards that tie CS low leave it at -1. */
static void cs_select(void)
{
    if (s_cfg.cs_gpio >= 0) {
        gpio_set_level(s_cfg.cs_gpio, 0);
    }
}

static void cs_release(void)
{
    if (s_cfg.cs_gpio >= 0) {
        gpio_set_level(s_cfg.cs_gpio, 1);
    }
}

static esp_err_t spi_tx(const void *buf, size_t len)
{
    spi_transaction_t t = {
        .length = len * 8,
        .tx_buffer = buf,
    };
    return spi_device_polling_transmit(s_spi, &t);
}

/* Every function below expects the caller to hold the chip select. */

static esp_err_t write_cmd(uint8_t cmd)
{
    if (s_cfg.dc_gpio >= 0) {
        gpio_set_level(s_cfg.dc_gpio, 0);
    }
    esp_err_t err = spi_tx(&cmd, 1);
    if (s_cfg.dc_gpio >= 0) {
        gpio_set_level(s_cfg.dc_gpio, 1);
    }
    return err;
}

static esp_err_t write_data(const void *data, size_t len)
{
    return spi_tx(data, len);
}

static esp_err_t write_cmd_data(uint8_t cmd, const void *data, size_t len)
{
    esp_err_t err = write_cmd(cmd);
    if (err == ESP_OK) {
        err = write_data(data, len);
    }
    return err;
}

static esp_err_t set_window(uint16_t x0, uint16_t y0, uint16_t x1, uint16_t y1)
{
    uint8_t caset[4] = {(uint8_t)(x0 >> 8), (uint8_t)x0, (uint8_t)(x1 >> 8), (uint8_t)x1};
    uint8_t raset[4] = {(uint8_t)(y0 >> 8), (uint8_t)y0, (uint8_t)(y1 >> 8), (uint8_t)y1};
    esp_err_t err = write_cmd_data(CMD_CASET, caset, sizeof(caset));
    if (err == ESP_OK) {
        err = write_cmd_data(CMD_RASET, raset, sizeof(raset));
    }
    if (err == ESP_OK) {
        err = write_cmd(CMD_RAMWR);
    }
    return err;
}

esp_err_t panel_init(const panel_config_t *cfg)
{
    s_cfg = *cfg;
    s_rotation = cfg->rotation & 3;

    uint64_t mask = 0;
    if (cfg->dc_gpio >= 0) {
        mask |= 1ULL << cfg->dc_gpio;
    }
    if (cfg->rst_gpio >= 0) {
        mask |= 1ULL << cfg->rst_gpio;
    }
    if (cfg->cs_gpio >= 0) {
        mask |= 1ULL << cfg->cs_gpio;
    }
    if (mask) {
        const gpio_config_t io = {
            .pin_bit_mask = mask,
            .mode = GPIO_MODE_OUTPUT,
            .pull_up_en = GPIO_PULLUP_DISABLE,
            .pull_down_en = GPIO_PULLDOWN_DISABLE,
            .intr_type = GPIO_INTR_DISABLE,
        };
        gpio_config(&io);
    }
    if (cfg->dc_gpio >= 0) {
        gpio_set_level(cfg->dc_gpio, 1);
    }
    cs_release();

    spi_bus_config_t bus = {
        .sclk_io_num = cfg->sclk_gpio,
        .mosi_io_num = cfg->mosi_gpio,
        .miso_io_num = -1,
        .quadwp_io_num = -1,
        .quadhd_io_num = -1,
        .max_transfer_sz = PANEL_MAX_TX_BYTES,
    };
    esp_err_t err = spi_bus_initialize(SPI2_HOST, &bus, SPI_DMA_CH_AUTO);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "spi_bus_initialize failed: %s", esp_err_to_name(err));
        return err;
    }

    const spi_device_interface_config_t dev = {
        .clock_speed_hz = (int)cfg->pclk_hz,
        .mode = cfg->spi_mode & 3,
        .spics_io_num = -1,
        .queue_size = 4,
    };
    err = spi_bus_add_device(SPI2_HOST, &dev, &s_spi);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "spi_bus_add_device failed: %s", esp_err_to_name(err));
        return err;
    }

    /* Same reset pulse as the stock firmware: release, 20 ms low, 150 ms settle. */
    if (cfg->rst_gpio >= 0) {
        gpio_set_level(cfg->rst_gpio, 1);
        delay_ms(5);
        gpio_set_level(cfg->rst_gpio, 0);
        delay_ms(20);
        gpio_set_level(cfg->rst_gpio, 1);
        delay_ms(150);
    }

    /* Command table of the stock firmware (see README, "Reverse engineering"). */
    const uint8_t colmod[1] = {0x55};
    const uint8_t caset[4] = {0x00, 0x00, (uint8_t)(((cfg->h_res - 1) >> 8) & 0xFF), (uint8_t)((cfg->h_res - 1) & 0xFF)};
    const uint8_t raset[4] = {0x00, 0x00, (uint8_t)(((cfg->v_res - 1) >> 8) & 0xFF), (uint8_t)((cfg->v_res - 1) & 0xFF)};

    cs_select();
    err = write_cmd(CMD_SLPOUT);
    delay_ms(500);
    if (err == ESP_OK) {
        err = write_cmd_data(CMD_COLMOD, colmod, sizeof(colmod));
    }
    delay_ms(10);
    if (err == ESP_OK) {
        err = write_cmd_data(CMD_MADCTL, &MADCTL[s_rotation], 1);
    }
    if (err == ESP_OK) {
        err = write_cmd_data(CMD_CASET, caset, sizeof(caset));
    }
    if (err == ESP_OK) {
        err = write_cmd_data(CMD_RASET, raset, sizeof(raset));
    }
    if (cfg->invert) {
        if (err == ESP_OK) {
            err = write_cmd(CMD_INVON);
        }
        delay_ms(10);
    }
    if (err == ESP_OK) {
        err = write_cmd(CMD_NORON);
    }
    delay_ms(10);
    if (err == ESP_OK) {
        err = write_cmd(CMD_DISPON);
    }
    delay_ms(500);
    cs_release();

    if (err != ESP_OK) {
        ESP_LOGE(TAG, "panel init failed: %s", esp_err_to_name(err));
        return err;
    }
    ESP_LOGI(TAG, "panel %ux%u ready (sclk=%d mosi=%d dc=%d rst=%d cs=%d, %lu Hz, rot %u)", cfg->h_res,
             cfg->v_res, cfg->sclk_gpio, cfg->mosi_gpio, cfg->dc_gpio, cfg->rst_gpio, cfg->cs_gpio,
             (unsigned long)cfg->pclk_hz, s_rotation);
    return ESP_OK;
}

esp_err_t panel_write_rect(uint16_t x, uint16_t y, uint16_t w, uint16_t h, const uint8_t *pixels)
{
    if (w == 0 || h == 0) {
        return ESP_OK;
    }
    if (x >= s_cfg.h_res || y >= s_cfg.v_res || w > s_cfg.h_res - x || h > s_cfg.v_res - y) {
        return ESP_ERR_INVALID_ARG;
    }
    cs_select();
    esp_err_t err = set_window(x, y, x + w - 1, y + h - 1);
    const uint8_t *p = pixels;
    for (size_t left = (size_t)w * h * 2; err == ESP_OK && left > 0;) {
        const size_t n = left > PANEL_MAX_TX_BYTES ? PANEL_MAX_TX_BYTES : left;
        err = write_data(p, n);
        p += n;
        left -= n;
    }
    cs_release();
    return err;
}

esp_err_t panel_set_rotation(uint8_t rotation)
{
    rotation &= 3;
    cs_select();
    esp_err_t err = write_cmd_data(CMD_MADCTL, &MADCTL[rotation], 1);
    cs_release();
    if (err == ESP_OK) {
        s_rotation = rotation;
    }
    return err;
}

uint8_t panel_rotation(void)
{
    return s_rotation;
}
