/*
 * Bring-up experiment (CONFIG_ZTV_TEST_RAW): drive the panel the way the stock
 * firmware does, with bare register writes and none of the ESP-IDF SPI or GPIO
 * drivers in the path. Every value here was read off the stock firmware's
 * disassembly (see README, "Reverse engineering"). If the panel lights up this
 * way but not through the driver path, the difference is in bus/pin setup and
 * can be bisected between the two.
 *
 * Stock sequence that is reproduced:
 *   TFT_eSPI::init()            pinMode(DC=2,OUT) HIGH, pinMode(RST=6,OUT) HIGH
 *   SPIClass::begin(3,5,5,-1)   sck=GPIO3, miso=GPIO5, mosi=GPIO5, ss=none
 *   reset pulse                 RST high 5ms, low 20ms, high 150ms
 *   command list                11(500ms) 3A 55 36 00 2A 0000 00F0 2B 0000 00F0 21 13 29(500ms)
 *
 * The SPI transaction copies the stock spiTransaction()+writecommand(): set
 * clock, set mode 3, MSB first, UPDATE, then pulse DC and shift one byte.
 */
#include "esp_log.h"
#include "esp_rom_sys.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "sdkconfig.h"
#include "soc/gpio_reg.h"
#include "soc/io_mux_reg.h"
#include "soc/spi_reg.h"

static const char *TAG = "raw";

#define DC_PIN   2
#define RST_PIN  6
#define SCK_PIN  3
#define MOSI_PIN 5

/* Stock firmware values. The clock register decodes to n=64, pre=10, l=1, h=52,
 * which is not a textbook divider; it is reproduced verbatim on purpose. */
#define STOCK_SPI_CLOCK 0x19BFFCC0

static void ms(int t)
{
    vTaskDelay(pdMS_TO_TICKS(t));
}

static void gpio_high(int pin)
{
    REG_WRITE(GPIO_OUT_W1TS_REG, 1u << pin);
}

static void gpio_low(int pin)
{
    REG_WRITE(GPIO_OUT_W1TC_REG, 1u << pin);
}

/* esp_rom_gpio_connect_out_signal() == ROM gpio_matrix_out(), plus output enable. */
static void pin_out(int pin, int signal)
{
    REG_WRITE(GPIO_ENABLE_W1TS_REG, 1u << pin);
    gpio_matrix_out(pin, signal, false, false);
}

static void dump(const char *when)
{
    ESP_LOGI(TAG, "%s: IO_MUX g2=%08lx g3=%08lx g5=%08lx g6=%08lx", when,
             (unsigned long)REG_READ(IO_MUX_GPIO2_REG), (unsigned long)REG_READ(IO_MUX_GPIO3_REG),
             (unsigned long)REG_READ(IO_MUX_GPIO5_REG), (unsigned long)REG_READ(IO_MUX_GPIO6_REG));
    ESP_LOGI(TAG, "%s: GPIO out=%08lx enable=%08lx", when, (unsigned long)REG_READ(GPIO_OUT_REG),
             (unsigned long)REG_READ(GPIO_ENABLE_REG));
    ESP_LOGI(TAG, "%s: SPI2 cmd=%08lx ctrl=%08lx clock=%08lx user=%08lx misc=%08lx", when,
             (unsigned long)REG_READ(SPI_CMD_REG(1)), (unsigned long)REG_READ(SPI_CTRL_REG(1)),
             (unsigned long)REG_READ(SPI_CLOCK_REG(1)), (unsigned long)REG_READ(SPI_USER_REG(1)),
             (unsigned long)REG_READ(SPI_MISC_REG(1)));
}

static void spi_update(void)
{
    REG_SET_BIT(SPI_CMD_REG(1), SPI_UPDATE);
    while (REG_READ(SPI_CMD_REG(1)) & SPI_UPDATE) {
    }
}

static void spi_config(void)
{
    REG_WRITE(SPI_CLOCK_REG(1), STOCK_SPI_CLOCK);
    /* mode 3: MISC.CK_IDLE_EDGE=1 (CPOL), USER.CK_OUT_EDGE=1 (CPHA) */
    REG_SET_BIT(SPI_MISC_REG(1), SPI_CK_IDLE_EDGE);
    REG_SET_BIT(SPI_USER_REG(1), SPI_CK_OUT_EDGE);
    /* MSB first: clear the bit-order fields in CTRL */
    REG_WRITE(SPI_CTRL_REG(1), 7);
    REG_SET_BIT(SPI_USER_REG(1), SPI_USR_MOSI);
    REG_CLR_BIT(SPI_USER_REG(1), SPI_USR_MISO);
    REG_WRITE(SPI_MOSI_DLEN_REG(1), 7); /* 8 bits */
    spi_update();
}

static void spi_byte(uint8_t b)
{
    REG_WRITE(SPI_W0_REG(1), b);
    REG_SET_BIT(SPI_CMD_REG(1), SPI_USR);
    while (REG_READ(SPI_CMD_REG(1)) & SPI_USR) {
    }
}

static void cmd(uint8_t c)
{
    gpio_low(DC_PIN);
    spi_byte(c);
    gpio_high(DC_PIN);
}

static void data(const uint8_t *d, int n)
{
    for (int i = 0; i < n; i++) {
        spi_byte(d[i]);
    }
}

static void fill(uint16_t color)
{
    const uint8_t caset[4] = {0, 0, 0, 0xF0};
    const uint8_t raset[4] = {0, 0, 0, 0xF0};
    cmd(0x2A);
    data(caset, 4);
    cmd(0x2B);
    data(raset, 4);
    cmd(0x2C);
    gpio_high(DC_PIN);
    for (int p = 0; p < 240 * 240; p++) {
        spi_byte((uint8_t)(color >> 8));
        spi_byte((uint8_t)(color & 0xFF));
    }
}

void raw_test_run(void)
{
    ESP_LOGI(TAG, "=== raw stock-style panel bring-up ===");

    /* TFT_eSPI::init() */
    pin_out(DC_PIN, 128); /* SIG_GPIO_OUT_IDX */
    gpio_high(DC_PIN);
    pin_out(RST_PIN, 128);
    gpio_high(RST_PIN);

    /* SPIClass::begin(3, 5, 5, -1): sck, mosi, and miso on the same pin */
    pin_out(SCK_PIN, 63);  /* FSPICLK_OUT_IDX */
    pin_out(MOSI_PIN, 65); /* FSPID_OUT_IDX  */
    REG_SET_BIT(IO_MUX_GPIO5_REG, FUN_IE); /* miso input enable, as spiAttachMISO does */
    spi_config();
    dump("after setup");

    /* reset pulse */
    gpio_high(RST_PIN);
    ms(5);
    gpio_low(RST_PIN);
    ms(20);
    gpio_high(RST_PIN);
    ms(150);

    ESP_LOGI(TAG, "sending stock command list");
    cmd(0x11);
    ms(500);
    const uint8_t c55[1] = {0x55};
    cmd(0x3A);
    data(c55, 1);
    ms(10);
    const uint8_t c00[1] = {0x00};
    cmd(0x36);
    data(c00, 1);
    const uint8_t caset[4] = {0, 0, 0, 0xF0};
    cmd(0x2A);
    data(caset, 4);
    cmd(0x2B);
    data(caset, 4);
    cmd(0x21);
    ms(10);
    cmd(0x13);
    ms(10);
    cmd(0x29);
    ms(500);
    dump("after init");

    const uint16_t colors[] = {0xF800, 0x07E0, 0x001F, 0xFFE0, 0x07FF, 0xF81F};
    for (int i = 0;; i++) {
        const uint16_t c = colors[i % 6];
        ESP_LOGI(TAG, "fill 0x%04x", c);
        fill(c);
        ms(3000);
    }
}
