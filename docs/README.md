# AGY TV 硬件与调试文档中心

这套文档面向**具备基础模拟电路知识**（懂电阻/电容/三极管/MOS 管/PWM/电压电流），手头已有**万用表、示波器、USB-TTL 模块**，想真正理解 ESP32-C3 并从零 DIY、调试出这台「AGY 状态小电视」的读者。

## 阅读路线

| 顺序 | 文档 | 你会学到什么 |
|---|---|---|
| 1 | [01-esp32-basics.md](./01-esp32-basics.md) | **从模拟电路走进 ESP32-C3**：3.3V 供电与电平、Strapping 启动脚陷阱、内置 USB-Serial/JTAG 复位原理、LEDC PWM、SPI+DMA、Flash 分区与启动流程 |
| 2 | [02-hardware-and-screen.md](./02-hardware-and-screen.md) | **从零选型与电路搭建**：ST7789 等常见屏幕对比、无 CS 引脚为什么必须用 SPI Mode 3、完整 BOM、背光三极管/MOS 极性电路详解、完整接线表与面包板高速 SPI 布线要点 |
| 3 | [03-debugging-instruments.md](./03-debugging-instruments.md) | **仪器实战调试手册**：万用表测引脚与极性、USB-TTL 抓 ROM 启动日志、示波器看纹波/PWM/振铃与 SPI 时序、逻辑分析仪（PulseView）降频抓包解码与逆向原厂屏幕、故障排查树 |
| 4 | [04-firmware-and-bringup.md](./04-firmware-and-bringup.md) | **固件架构与 6 步从零点亮路线**：单帧缓冲 + 脏行跟踪 + DMA 分块原理、Flash 中文字库、电脑离屏 UI 测试、从空板到完整 AGY TV 的分阶段验收清单 |
| 5 | [05-buttons-keypad-and-alarm.md](./05-buttons-keypad-and-alarm.md) | **扩展实战：按键 / 键盘与闹钟电路设计**：ESP32-C3 剩余引脚预算、RC 消抖独立按键、EC11 旋转编码器、单脚 ADC 梯形键盘、I2C 扩展矩阵键盘、电磁蜂鸣器三极管+续流二极管驱动、DS3231 离线时钟避坑与仪器实测 |
| 6 | [06-pcb-design-and-jlcpcb.md](./06-pcb-design-and-jlcpcb.md) | **完整 PCB 设计与嘉立创打板下单教程**：直接可下单的 `96×76mm` 2 层全插件底板 Gerber ZIP、KiCad 工程、BOM 物料清单、嘉立创免费打样券下单流程与从矮到高焊接/上电安全核对清单 |

## 推荐搭配实操

- **先不买任何硬件也能体验固件 UI**：直接运行项目里的 `tools/uitest/run.sh`，在电脑上编译真实的 `ui.c`/`gfx.c`/`zmodel.c` 生成全部 26 张界面截图。
- **已有小电视成品或自己搭好面包板**：优先看第 2 篇核对接线与背光极性，遇到黑屏/暗屏/无法烧录直接翻第 3 篇的仪器测量方法。
