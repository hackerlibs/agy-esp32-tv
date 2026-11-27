# 04. 固件架构与从零 DIY 点亮路线

有了硬件和仪器，这一篇把固件代码拆给你看——哪怕你之前没写过 ESP-IDF，也能看懂每个文件干了什么，并按 **6 个里程碑**稳扎稳打从零把整台设备调通。

---

## 1. 固件数据流与模块分层

整个固件只有 7 个核心 C 文件（都在 [main/](../main/) 下），职责边界非常干净：

```text
  电脑主机 (AGY hooks / transcript.jsonl)
       │  host/agy_tv.py (USB Serial/JTAG 或 RFC2217)
       ▼  行式文本协议 ZTV1 S st=... model=... ctx=...
  ┌──────────┐    写(加互斥锁)    ┌──────────┐    每帧只读拷贝    ┌──────────┐
  │ zlink.c  │ ────────────────► │ zmodel.c │ ────────────────► │   ui.c   │
  │ (串口任务) │                  │ (状态模型) │                   │ (状态界面) │
  └──────────┘                   └──────────┘                   └────┬─────┘
                                                                     │ 画矩形/文字/圆
                                                                     ▼
                                                                ┌──────────┐
                                                                │  gfx.c   │ ◄─ main/fonts/
                                                                │ (帧缓冲+  │    (Flash 中文字库)
                                                                │  脏行追踪) │
                                                                └────┬─────┘
                                                                     │ 只推送变脏的行带
                                                                     ▼
                                                                ┌──────────┐
                                                                │ panel.c  │ ──► SPI DMA (32KB分块)
                                                                │ (硬件驱动) │ ──► LEDC PWM (背光)
                                                                └──────────┘
```

### 几个值得学习的嵌入式设计技巧
1. **单帧缓冲 + 脏行跟踪（无闪烁 + 省流量）**：
   - [main/gfx.c](../main/gfx.c) 在 RAM 里开了一块静态显存 `s_fb[240][240]`（115KB，大端 RGB565，与 ST7789 字节序一致，DMA 直接发出零转换）。
   - 同时维护一个仅 **30 字节**的位图 `s_dirty[30]`（240 bit，对应 240 行）。任何绘图函数动了哪几行，就把对应 bit 置 1。
   - `gfx_flush()` 把连续的脏行合并成一个矩形推给 `panel_write_rect()`：静止画面 **0 行 SPI 流量**（帧耗时 `28 us`），跑动画时也只刷动画所在行带（~10ms），而且单缓冲先画完再推，永远不撕裂不闪烁。
2. **中文字库零 RAM 占用**：
   - 7445 个 GB2312 汉字（Cubic 11 12px 点阵）通过 `tools/gen_fonts.py` 编译成 `const` 数组存放在 Flash（约 190KB），ESP32-C3 通过 XIP（内存映射）直接二分查找读取，**不占用 1 字节宝贵的 SRAM**。
3. **硬件抽象接缝（电脑上直接调界面）**：
   - 锁、时钟、USB 连接检测抽在 [main/port.h](../main/port.h) 里，所以 `gfx.c` / `ui.c` / `zmodel.c` 不依赖任何 ESP-IDF 头文件！
   - 改完 UI 排版后，先跑 `tools/uitest/run.sh`，1 秒内就在电脑上编译渲染 26 个屏幕拼图并逐帧做**像素级脏行一致性校验**，完全调满意了再烧录真机。

---

## 2. 从零 DIY 的 6 步渐进点亮路线

如果你是自己买散件搭面包板/洞洞板，强烈建议严格按下面 6 步推进，**每一步验收通过再进下一步**，绝不一口气全接完瞎排错：

### 里程碑 1：最小系统跑通供电与串口
- **只接 ESP32-C3 开发板**（先不接屏幕！）。
- 用万用表确认 `3V3` 正常，插上 USB，运行：
  ```bash
  tools/build.sh --flash
  tools/monitor.sh
  ```
- **验收标准**：串口打印 `AGY TV 2.0.0 booting` 和 `ZTV1 R hello fw="2.0.0"`，敲 `ZTV1 C ping` 收到 `ZTV1 R pong`。

### 里程碑 2：独立验证背光与极性
- 断电，只接屏幕的 **`VCC / GND / BLK(GPIO1)`** 三根线。
- 上电后用万用表/示波器测 `GPIO1`，并用命令测试亮度变化：
  ```bash
  python3 host/agy_tv.py send "ZTV1 C bl=100"
  python3 host/agy_tv.py send "ZTV1 C bl=20"
  python3 host/agy_tv.py send "ZTV1 C bl=0"
  ```
- **验收标准**：`bl=100` 最亮、`bl=20` 明显变暗、`bl=0` 全灭。如果明暗刚好相反，发 `ZTV1 C bl_inv=0`（或 `1`）验证并记录进 `menuconfig` 的 `ZTV_LCD_BL_ACTIVE_LOW`。

### 里程碑 3：接通 SPI，跑内置演示模式（无需主机桥接）
- 断电，按 [02-hardware-and-screen.md](./02-hardware-and-screen.md) 接好 `SCLK(3) / MOSI(5) / DC(2) / RST(6)`（8 针屏 `CS` 接 `GND`）。
- 如果是面包板杜邦线，先在 `idf.py menuconfig` 把 `ZTV_LCD_SPI_CLOCK_KHZ` 设为 `10000`（10MHz）再刷机。
- 上电后应看到 `AGY TV` 开机动画 → `NO HOST` 提示页；发送演示命令循环看全部 7 种界面：
  ```bash
  python3 host/agy_tv.py send "ZTV1 C demo=1"
  ```
- **验收标准**：文字清晰无花屏、颜色正常（黑底彩字）、方向正确（方向不对发 `ZTV1 C rot=0..3` 找对后写入 `menuconfig`）。若全黑/白屏，直接按 [03-debugging-instruments.md](./03-debugging-instruments.md) 用示波器和逻辑分析仪排查。

### 里程碑 4：拉高 SPI 频率做稳定性压测
- 把 `ZTV_LCD_SPI_CLOCK_KHZ` 提回 `27000`（实测稳定甚至可试 `40000`），用示波器 `10X` 探头确认 `SCLK` 波形干净无严重过冲，跑 `ZTV1 C stats` 确认 `frame_max_us < 45000`（45ms 内）。

### 里程碑 5：跑通主机 Bridge 与在线调光
- 启动串口桥接（本地 USB 自动发现，远程 RFC2217 传 `--port`）：
  ```bash
  python3 host/agy_tv.py bridge --daemon
  python3 host/agy_tv.py status
  python3 host/agy_tv.py bl 80
  ```
- **验收标准**：小电视从 `NO HOST` 切到 `IDLE` 时钟页（显示当前时间与日期），`bl 80` 立刻调光并返回 `ZTV1 R bl pin=1 pct=80 ...`。

### 里程碑 6：接入 AGY Hooks 实现全自动状态同步
- 安装全局 AGY hooks 并重启 AGY 会话：
  ```bash
  python3 host/install_hooks.py
  ```
- 给 AGY 发一个多步骤任务，观察小电视自动流转：
  `IDLE` → `THINKING`（模型 + Prompt）→ `RUNNING`（工具名 + `STEPS` 进度条 + `CTX`/`OUT` Token）→ `WAIT INPUT`（遇到 `ask_question` 琥珀色闪烁提醒）→ `DONE`（总结与倒计时）→ `IDLE`。

---

## 3. 进阶二次开发玩法

掌握了上面的硬件与代码后，你可以很轻松地在这个底座上继续扩展：
- **自定义界面配色/布局**：改 [main/ui.c](../main/ui.c)，先用 `tools/uitest/run.sh` 在电脑上看效果图，满意再烧录；
- **加实体按键 / 旋钮调光**：ESP32-C3 还剩不少空闲 GPIO（如 `GPIO0 / GPIO4 / GPIO7 / GPIO10`），接一颗轻触按键（带内部上拉 + 软件消抖）或旋转编码器，在 `main/` 里起一个任务直接调用 `panel_set_brightness()` 就能物理旋钮调亮度；
- **无线 Wi-Fi 摆脱 USB 数据线**：ESP32-C3 本身带 Wi-Fi，未来可以把 [main/zlink.c](../main/zlink.c) 的串口读行替换成 TCP/UDP Socket 接收同样的 `ZTV1 S ...` 协议帧，只需一根 5V 供电线即可做成无线桌面小电视。
