/*
 * Software seam between the portable UI code and the platform it runs on.
 *
 * ui.c / gfx.c / zmodel.c are plain C so they can be compiled on a desktop and
 * rendered offscreen by tools/uitest. Everything platform specific (clock,
 * USB state, the model mutex) goes through these functions, which are
 * implemented by port_esp.c in the firmware and by the test harness on the host.
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>

/* Creates the mutex. Call once before anything else in this file is used. */
void z_port_init(void);

/* Monotonic time in microseconds. */
int64_t z_now_us(void);

/* True when the USB host is enumerated (so a bridge could be talking to us). */
bool z_usb_connected(void);

/* Protects the shared status model. Lock calls do not nest. */
void z_lock(void);
void z_unlock(void);

/* Software reset. */
void z_reboot(void);
