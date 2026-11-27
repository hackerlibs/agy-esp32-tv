idf_component_register(
    SRCS
        "main.c"
        "bars_test.c"
        "raw_test.c"
        "panel.c"        # SPI panel driver (ESP-IDF)
        "gfx.c"          # framebuffer drawing, plain C
        "zmodel.c"       # shared status model, plain C
        "ui.c"           # status screens, plain C
        "port_esp.c"     # platform seam for the plain C files
        "ui_task.c"      # render task (FreeRTOS)
        "zlink.c"        # USB Serial/JTAG link + frame parser
    INCLUDE_DIRS "."
        REQUIRES esp_driver_gpio esp_driver_spi esp_driver_usb_serial_jtag esp_timer
)
