# AGY TV -- 把 Antigravity (AGY) 会话详情与进度显示在小电视上

把一块 ESP32-C3「小电视」（240x240 SPI 彩屏）改造成 **Antigravity (AGY)** 桌面状态显示器：屏幕实时显示当前 AGY 会话的状态（思考中 / 运行工具 / 等待输入 / 错误 / 完成 / 待机）、`update_steps` 步骤进度条与百分比、当前步骤说明、当前工具与命令/文件详情、模型名称、上下文与输出 Token 用量、子代理 / 后台任务数、用时和时钟。

主机侧通过 **AGY 生命周期 hooks**（`hooks.json`）抓取实时事件，并结合 `transcript.jsonl` 增量解析补齐真实模型名、Token 统计、思考摘要和最终回复；即使未重启 AGY 加载 hooks，桥接进程也可自动追踪最新活跃的 `transcript.jsonl`。

```
AGY hooks (6 种事件) ──► host/agy_tv.py hook ──► bridge 常驻进程 (+ transcript.jsonl 解析)
   (安全透传、永不阻塞)       (AF_UNIX DGRAM)         (状态机 + 心跳 + 串口自动重连)
                                                            │
                                                            ▼ USB Serial/JTAG (ZTV1 协议)
                                                      ESP32-C3 固件 ──► 240x240 彩屏
```

离屏渲染的全部 26 个界面见 `tools/uitest/run.sh` 生成的拼图（含中文界面），默认输出到 `/tmp/agy-tv-ui/sheet.png`。

## 显示内容一览

- **顶部状态栏**：`AGY TV` / `IDLE` / `THINKING` / `RUNNING` / `WAIT INPUT`（如 `ask_question`）/ `ERROR` / `DONE` / `NO HOST` / `NO USB` + 右上角链路状态 LED。
- **核心详情区**：
  - 思考中显示当前 Prompt 与调用轮次（`INV`）；
  - 运行工具显示工具名（`run_command` / `edit_file` / `subagent` / `grep` / `ask_question` 等）和命令/路径/参数摘要，超长文本自动水平往返滚动；
- **进度与活动区**：
  - 当 AGY 调用 `update_steps` 时，显示 `STEPS` 进度条、`完成/总数 百分比%` 与当前 `in_progress` 步骤标题；
  - 无步骤清单时自动切换为 `ACTIVITY`（当前目标/思考摘要）或 `PROGRESS` 动态活动条 + `STEP` 步数；
- **AGY 遥测条**：左侧显示解析出的实际模型名（如 `gemini-4-neon-high`），右侧显示上下文 Token（`CTX 89.4K`）；
- **底部统计与页脚**：左侧大字本轮用时（`ELAPSED`），右侧显示工具调用数（`TOOLS`）、子代理/后台任务数（`SUB` / `BG`）或输出 Token（`OUT`）、失败次数（`FAIL`）；页脚显示时钟与当前项目名（支持中文）。

## 硬件结论（来自对原厂固件的逆向）

原固件是 Arduino + TFT_eSPI 的「小电视」固件（开源项目 SmallDesktopDisplay 的定制版，源码见卖家提供的 `时钟/C3版本`）。屏幕引脚最初通过反汇编其 flash 备份得出，后与源码逐项核对一致：

| 项目 | 值 | 来源 |
|---|---|---|
| 主控 | ESP32-C3 (QFN32) rev 0.4，4MB flash | esptool flash-id |
| 屏幕 | 240x240、RGB565、IPS（需 INVON）、原生方向（MADCTL=0） | 原固件初始化命令表（rodata 0x3C28EE80） |
| SPI | mode 3，引脚 SCLK=3、MOSI=5（均为 FSPI 原生脚） | `SPIClass::begin(3, 5, 5, -1)` 反汇编 |
| DC / RST | GPIO2 / GPIO6（复位脉冲 20ms 低 + 150ms） | `pinMode`/`digitalWrite` 调用点反汇编 |
| CS | 未接（拉低） | `spi.begin(..., ss=-1)` |
| **背光** | **GPIO1，PWM 调光（LEDC）** | 源码 `User_Setup.h: #define TFT_BL 1`、`.ino: #define LCD_BL_PIN 1` + `analogWrite()` |

flash 分区（原厂）：nvs @0x9000、otadata、app0 @0x10000 (3MB)、spiffs @0x310000（相册）、coredump。
本固件只写 0x0..0x4B000（bootloader/分区表/app），**不碰 NVS 和相册**，随时可完整还原。

> **踩坑记录：背光**。排查黑屏先确认背光（`ZTV1 C bl`），再查显示数据。

## 仓库结构

```
main/            固件 (ESP-IDF v5.5, C)
  main.c           入口：面板初始化 → 链接 → 渲染任务
  panel.c          SPI 面板驱动（含 32KB DMA 分块）
  gfx.c            帧缓冲绘图层：脏行跟踪、混合文本（ASCII 5x8 + 中文 12px，无损 CJK 换行）
  zmodel.c         共享状态模型（含 AGY 模型/Token/步骤/子任务字段；演示模式样本）
  ui.c             AGY TV 九种状态界面；部件级局部重绘
  zlink.c          串口帧协议解析（向后兼容 ZTV1，容错忽略未知键与畸形行）
  port_esp.c       平台接缝（时钟/USB/互斥），使 ui/gfx/zmodel 可在桌面编译
  fonts/           生成的字库 + 三个开源字体的许可证
host/
  agy_tv.py        AGY bridge 常驻进程 + AGY hook 入口 + transcript.jsonl 追踪
  install_hooks.py 把 AGY hooks 写入 ~/.gemini/config/hooks.json（全局）或 .agents/hooks.json
  zcode_tv.py      兼容入口（转发到 agy_tv.py）
.agents/
  hooks.json       本仓库自带的便携工作区 AGY hooks 配置
tools/
  gen_fonts.py       字库生成（开发期，固件只 include 产物）
  uitest/            离屏渲染测试：无硬件验证全部界面 + 面板一致性校验（支持纯 Python 输出 PNG）
  build.sh           构建/烧录包装（支持 macOS /dev/cu.usbmodem* 与 Linux /dev/ttyACM*）
  monitor.sh         不会复位板子的串口监视器
  restore_original.sh 一键还原原厂固件（备份在 backup/，已校验）
backup/
  esp32c3_original_4MB.bin  原厂固件完整备份（含 WiFi 配置；勿提交，已在 .gitignore）
```

## 构建与烧录

依赖：ESP-IDF v5.5（如装在 `~/esp/esp-idf`，或通过 `IDF_PATH` 指定）。

```bash
tools/build.sh --flash        # 构建并烧录（自动找 /dev/cu.usbmodem* 或 /dev/ttyACM*）
tools/build.sh --monitor      # 烧录后打开串口监视（不会复位板子）
idf.py -p <port> menuconfig   # 引脚/时钟/旋转等全部可配 (AGY TV 菜单)
```

> **提示**：新版主机脚本 `host/agy_tv.py` 保持与旧版 v1 固件的 `ZTV1` 串口协议兼容；未重烧固件前也可直接显示状态、工具与步骤进度，重烧 v2.0.0 固件后即可解锁完整 AGY TV 界面（模型名、Token、步骤百分比、`SUB`/`BG` 计数等）。

无主机验证：烧录后屏幕自行显示 BOOT → NO HOST；也可以发 `ZTV1 C demo=1` 循环播放 AGY 演示数据（menuconfig 里 `ZTV_UI_DEMO_ON_BOOT` 可设为开机即演示）。

## 主机侧安装与启动

```bash
# 1. 安装 AGY 全局 hooks（~/.gemini/config/hooks.json，所有项目自动生效）
python3 host/install_hooks.py

# 或仅安装在某个工作区的 .agents/hooks.json：
# python3 host/install_hooks.py --workspace

# 2. 启动串口桥接（支持拔插自动重连；无需设备时可加 --dry-run 预览帧）
python3 host/agy_tv.py bridge --daemon

# 3. 查看桥接状态与日志
python3 host/agy_tv.py status
tail -f /tmp/agy-tv.log
```

- Hooks 严格遵循 AGY `hooks.json` 契约：`PreToolUse` 返回 `{"decision": "allow"}`（交给默认权限策略，**绝不**绕过授权确认），`Stop` 返回 `{"decision": "stop"}`，其余返回 `{}`，永远 exit 0，桥接未开或板子未插都不会阻塞 AGY。
- 卸载 hooks：`python3 host/install_hooks.py --remove`（或加 `--workspace`）。
- 即使尚未重启 AGY 会话，bridge 也会自动扫描 `~/.gemini/{antigravity-cli,antigravity,antigravity-ide}/brain/*/.system_generated/logs/transcript.jsonl` 追踪最近活跃会话（可用 `--no-watch` 关闭）。

恢复原厂固件（天气时钟 + 相册，含全部设置）：

```bash
tools/restore_original.sh
```

## 串口协议（115200，行式 ASCII，兼容 ZTV1）

主机 → 设备（每帧是完整快照，未知键自动忽略）：

```
ZTV1 S st=<idle|think|tool|perm|err|done> tool="run_command" n=7 f=1 el=42 todo=2/4
      cur="当前步骤" msg="命令或提示文本" proj="esp32_tv" sid="a3653af3"
      clk="14:02:11" date="2026-11-27 FRI"
      model="gemini-4-neon-high" inv=4 step=18 ctx=89400 out=3720 sub=1 bg=1
ZTV1 C ping | stats | get | bl | demo=<0|1> | rot=<0..3> | reboot
```

设备 → 主机：`ZTV1 R hello|pong|state ...|stats ...|bl ...`。`msg/cur/proj` 为 UTF-8，设备用 GB2312 字库渲染（7445 字，~190KB 在 flash，不占 RAM）；字库外的字符（emoji 等）跳过。

## 疑难排解

- **屏幕全黑但串口正常（`ZTV1 C stats` 帧数在涨）**：检查背光 `ZTV1 C bl`。
- **板子看起来「死」了 / 启动不起来**：ESP32-C3 的 USB-Serial/JTAG 用 DTR/RTS 控制 EN/IO0。不要用会拉低 IO0 的普通串口工具；重新插拔 USB 或使用 `tools/monitor.sh` 即可。
- **端口被占用**：桥接运行时独占端口。先 `kill $(cat /tmp/agy-tv.bridge.pid)`。
- **屏幕方向不对**：`python3 host/agy_tv.py send "ZTV1 C rot=0"`（支持 `0..3`）。

## 测试

- `tools/uitest/run.sh`：在桌面用 `clang` 或 `gcc`（开启 `-Wall -Wextra -Werror` + ASan/UBSan）编译真实的 `ui.c`/`gfx.c`/`zmodel.c`，渲染 26 个 AGY 屏幕场景，并逐帧校验模拟面板与帧缓冲逐字节一致（598 帧 0 差异）。无需 Pillow 也能通过纯 Python 生成 `sheet.png` 与单张 PNG。

## 字体许可

`main/fonts/` 内含三份许可证：Press Start 2P（OFL-1.1）、Spleen（BSD-2-Clause）、Cubic 11（OFL-1.1，基于 M+ BITMAP FONTS）。均允许固件捆绑；生成器 `tools/gen_fonts.py`。
