# ZCode TV -- 把 ZCode 会话状态显示在小电视上

把一块 ESP32-C3「小电视」（240x240 SPI 彩屏）改造成 ZCode 状态显示器：屏幕实时显示当前会话的
状态（思考中 / 运行工具 / 等待授权 / 失败 / 完成）、待办进度条、当前工具命令、用时和时钟。
主机侧通过 ZCode hooks 抓取事件，经 USB 串口推给固件。

```
ZCode hooks ──► host/zcode_tv.py hook ──► bridge 常驻进程 ──► USB Serial/JTAG ──► 固件 ──► 屏幕
  (7 种事件)      (快速、静默、永不阻塞)      (状态机+心跳)         (文本帧协议)      (本地渲染)
```

离屏渲染的全部界面见 `tools/uitest/run.sh` 生成的拼图（含中文界面），如 `/tmp/zcode-tv-ui/sheet.png`。

## 硬件结论（来自对原厂固件的逆向）

原固件是 Arduino + TFT_eSPI 的「小电视」固件（开源项目 SmallDesktopDisplay 的定制版，源码见
卖家提供的 `时钟/C3版本`）。屏幕引脚最初通过反汇编其 flash 备份得出，后与源码逐项核对一致：

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

> **踩坑记录：背光**。反汇编时只搜索了 `pinMode`/`digitalWrite`/LEDC 调用点，没找到背光控制，就
> 错误地得出「背光硬件常亮」的结论；实际上原厂用 TFT_eSPI 的 `TFT_BL` 宏 + Arduino `analogWrite`
> 点灯，绕过了那些搜索。结果固件能正常刷屏、串口一切正常，但屏幕漆黑——而且换 8 种 SPI 配置
> 都「无效」，因为面板本来就在显示，只是背光没亮。**教训：排查黑屏先确认背光，再查显示数据。**

## 仓库结构

```
main/            固件 (ESP-IDF v5.5, C)
  main.c           入口：面板初始化 → 链接 → 渲染任务
  panel.c          SPI 面板驱动（含 32KB DMA 分块）
  gfx.c            帧缓冲绘图层：脏行跟踪、混合文本（ASCII 5x8 + 中文 12px）
  zmodel.c         共享状态模型（互斥保护；演示模式样本）
  ui.c             九种状态界面；部件级局部重绘
  zlink.c          串口帧协议解析（容错：畸形行直接忽略）
  port_esp.c       平台接缝（时钟/USB/互斥），使 ui/gfx/zmodel 可在桌面编译
  fonts/           生成的字库 + 三个开源字体的许可证
host/
  zcode_tv.py      bridge 常驻进程 + hook 快速入口（单文件两子命令）
  install_hooks.py 把 7 种 hook 事件写入工作区 .zcode/config.json
tools/
  gen_fonts.py       字库生成（开发期，固件只 include 产物）
  uitest/            离屏渲染测试：无硬件验证全部界面 + 面板一致性校验
  build.sh           构建包装（自动 source export.sh）
  monitor.sh         不会复位板子的串口监视器
  restore_original.sh 一键还原原厂固件（备份在 backup/，已校验）
backup/
  esp32c3_original_4MB.bin  原厂固件完整备份（含 WiFi 配置；勿提交，已在 .gitignore）
```

## 构建与烧录

依赖：ESP-IDF v5.5（本机已装在 `~/esp/esp-idf`，工具链在 `~/.espressif`）。

```bash
tools/build.sh --flash        # 构建并烧录（自动找 /dev/cu.usbmodem*）
tools/build.sh --monitor      # 烧录后打开串口监视（不会复位板子）
idf.py -p /dev/cu.usbmodem14201 menuconfig   # 引脚/时钟/旋转等全部可配 (ZCode TV 菜单)
```

无主机验证：烧录后屏幕自行显示 BOOT → NO HOST；也可以发 `ZTV1 C demo=1` 让它循环播放
全部界面的演示数据（menuconfig 里 `ZTV_UI_DEMO_ON_BOOT` 可设为开机即演示）。

## 主机侧安装

```bash
python3 host/install_hooks.py                       # 写入 .zcode/config.json（7 事件）
python3 host/zcode_tv.py bridge --daemon            # 启动桥接（重启会话后 hooks 即生效）
python3 host/zcode_tv.py status                     # 查看桥接状态
tail -f /tmp/zcode-tv.log                           # 桥接日志
```

hooks 已配置为静默且永远 exit 0：桥接没开、板子没插，都不会影响 ZCode 本身。
桥接要开机自启的话，加一条 LaunchAgent 或登录项即可（`zcode_tv.py bridge --daemon`）。

恢复原厂固件（天气时钟 + 相册，含全部设置）：

```bash
tools/restore_original.sh
```

## 串口协议（115200，行式 ASCII）

主机 → 设备（每帧是完整快照，未知键忽略）：

```
ZTV1 S st=<idle|think|tool|perm|err|done> tool="Bash" n=7 f=1 el=42 todo=2/4
      cur="当前待办" msg="命令或提示文本" proj="esp32_tv" sid="a1b2c3"
      clk="14:02:11" date="2026-11-27 FRI"
ZTV1 C ping | stats | get | demo=<0|1> | rot=<0..3> | reboot
```

设备 → 主机：`ZTV1 R hello|pong|state ...|stats ...`。`msg/cur` 为 UTF-8，设备用
GB2312 字库渲染（7445 字，~190KB 在 flash，不占 RAM）；字库外的字符（emoji 等）跳过。

## 性能（真机实测）

| 场景 | SPI 流量 |
|---|---|
| 静止界面（45 帧基准） | 0 行/帧（无流量） |
| 运行中动画界面 | ~67 行/帧（仅动画区域） |
| 整屏重绘 | 34.7ms（27MHz SPI，理论值 34.2ms） |
| 最慢单帧 | 40.6ms（20fps 预算 50ms） |
| 空闲堆 | 188KB / 305KB |

## 疑难排解

**屏幕全黑但串口正常（`ZTV1 C stats` 帧数在涨）**：显示在跑，是背光没亮。用
`ZTV1 C bl` 读回占空比；为 -1 说明背光初始化失败（看启动日志），为 0 说明亮度被设成 0。
引脚和亮度在 menuconfig 的 `ZCode TV → LCD panel → Backlight GPIO / brightness`。

**板子看起来「死」了 / 启动不起来**：ESP32-C3 的 USB-Serial/JTAG 外设用 DTR/RTS 控制
EN（复位）和 IO0（启动模式）。某些串口工具打开端口时给出 `DTR=1, RTS=0` 的组合
= IO0 低 + EN 高 = ROM 下载模式，屏幕熄灭，看起来像砖。**恢复方法：重新插拔 USB，
或跑一次 esptool（任何命令都会做正确复位），或用 `tools/monitor.sh` 打开端口**。
本项目的脚本都把 DTR/RTS 固定在安全组合（双高），不会触发。

**端口被占用**：桥接运行时独占端口。先 `kill $(cat /tmp/zcode-tv.bridge.pid)`。

**屏幕方向不对**：`ZTV1 C rot=0..3` 即时切换（与 TFT_eSPI setRotation 同义）。

**想看固件渲染循环是否健康**：`ZTV1 C stats` 返回帧数/单帧耗时/SPI 行数/堆。

## 测试

- `tools/uitest/run.sh`：在桌面编译真实的 ui/gfx/zmodel（clang + ASan/UBSan），
  渲染 22 个屏幕场景；内置「面板一致性校验」——模拟面板只经 `panel_write_rect` 更新，
  每帧后必须与帧缓冲逐字节一致，专抓漏推脏行导致的残影。532 帧 0 差异。
- 真机验证脚本历史输出见会话记录；关键项：启动 0 错误、冷启动 12s 无主机存活、
  下载模式陷阱可复现可恢复、中文帧往返、hooks 全事件联动。

## 字体许可

`main/fonts/` 内含三份许可证：Press Start 2P（OFL-1.1）、Spleen（BSD-2-Clause）、
Cubic 11（OFL-1.1，基于 M+ BITMAP FONTS）。均允许固件捆绑；生成器 `tools/gen_fonts.py`。
