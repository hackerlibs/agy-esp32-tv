# ESP32-C3 原厂固件备份 / 刷回记录

## 文件

| 文件 | 说明 |
| --- | --- |
| `esp32c3_original_4MB.bin` | 整片 4MB Flash 镜像（4194304 字节），包含 bootloader、分区表、应用和数据分区，从 `0x0` 开始 |

## 硬件信息

| 项目 | 值 |
| --- | --- |
| 芯片 | ESP32-C3（QFN32），revision v0.4 |
| 特性 | WiFi、BLE |
| 晶振 | 40 MHz |
| Flash | 4MB（Manufacturer ID `0xc4`，Device ID `0x6016`） |
| MAC | `20:d5:c2:6f:40:80` |
| USB 模式 | Espressif 原生 USB-Serial/JTAG（VID `303a` / PID `1001`） |
| 串口 | macOS：`/dev/cu.usbmodem14201`（编号可能随插拔变化，用 `ls /dev/cu.usbmodem*` 确认） |

## 工具

Mac 上没有全局 esptool，使用 ESP-IDF 5.5 环境里自带的 esptool（v4.14.0）：

```
~/.espressif/python_env/idf5.5_py3.13_env/bin/python -m esptool
```

也可以先 `source ~/esp/esp-idf/export.sh`（路径按实际安装位置调整）后直接用 `esptool.py`。

## 确认设备

```
~/.espressif/python_env/idf5.5_py3.13_env/bin/python -m esptool \
  --chip esp32c3 --port /dev/cu.usbmodem14201 flash_id
```

## 刷回原厂固件（已于 2026-11-27 执行成功）

在项目根目录 `~/CppPro/esp32_tv` 下运行：

```
~/.espressif/python_env/idf5.5_py3.13_env/bin/python -m esptool \
  --chip esp32c3 --port /dev/cu.usbmodem14201 --baud 460800 \
  write_flash --flash_mode keep --flash_freq keep --flash_size keep \
  0x0 backup/esp32c3_original_4MB.bin
```

说明：

- 从 `0x0` 整片写入，会擦除并覆盖 `0x00000000` 到 `0x003fffff`，设备上现有固件和数据（包括 NVS 里的配置）都会被替换成备份里的内容。
- `--flash_mode/--flash_freq/--flash_size keep` 保持镜像里原有的 flash 头参数，不让 esptool 改写。
- 写入结果：4194304 字节压缩为 1630220 字节，用时约 24.6 秒，esptool 输出 `Hash of data verified.`，随后通过 RTS 硬复位。

## 重新备份（以后需要时）

```
~/.espressif/python_env/idf5.5_py3.13_env/bin/python -m esptool \
  --chip esp32c3 --port /dev/cu.usbmodem14201 --baud 460800 \
  read_flash 0x0 0x400000 backup/esp32c3_original_4MB.bin
```

## 故障排查

- 连不上：拔掉 USB 重插；或按住 BOOT 键再插 USB（或按 RESET）进入下载模式后重试。
- 端口名变了：重新 `ls /dev/cu.usbmodem*`。
- 刷完设备行为异常：断电重启一次，用 `python -m esptool ... monitor` 或 `screen /dev/cu.usbmodem14201 115200` 看串口输出。
