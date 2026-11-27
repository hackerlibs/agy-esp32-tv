#include "port.h"

#include "driver/usb_serial_jtag.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

static SemaphoreHandle_t s_lock;

void z_port_init(void)
{
    s_lock = xSemaphoreCreateMutex();
    configASSERT(s_lock != NULL);
}

int64_t z_now_us(void)
{
    return esp_timer_get_time();
}

bool z_usb_connected(void)
{
    return usb_serial_jtag_is_connected();
}

void z_lock(void)
{
    xSemaphoreTake(s_lock, portMAX_DELAY);
}

void z_unlock(void)
{
    xSemaphoreGive(s_lock);
}

void z_reboot(void)
{
    esp_restart();
}
