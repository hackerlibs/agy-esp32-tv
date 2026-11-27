# 06. 完整 PCB 设计与嘉立创（JLCPCB）打板下单教程

按照你的需求，我们已经设计好一块可直接发给嘉立创打样的 **2 层全插件扩展底板（AGY TV Carrier Board v1.0）**，所有工程与生产文件均已生成在 [hardware/agy_tv_carrier/](../hardware/agy_tv_carrier/) 目录中：

| 文件 | 用途 |
|---|---|
| **[agy_tv_carrier_jlcpcb_gerber.zip](../hardware/agy_tv_carrier/agy_tv_carrier_jlcpcb_gerber.zip)** | **直接上传嘉立创下单的 Gerber + Excellon 钻孔压缩包**（无需再导出！） |
| **[agy_tv_carrier.kicad_pcb](../hardware/agy_tv_carrier/agy_tv_carrier.kicad_pcb)** & `.kicad_pro` | KiCad 8 标准工程（可导入 KiCad 或立创 EDA 专业版二次修改） |
| **[bom.csv](../hardware/agy_tv_carrier/bom.csv)** | 完整物料清单（含元件规格、封装、数量与立创商城参考编号 `Cxxxxx`） |
| **[schematic.png](../hardware/agy_tv_carrier/schematic.png)** | 完整参考原理图预览 |
| **[pcb_top.png](../hardware/agy_tv_carrier/pcb_top.png)** / **[pcb_bottom.png](../hardware/agy_tv_carrier/pcb_bottom.png)** | PCB 正面与背面渲染预览图 |
| **[generate_pcb.py](../hardware/agy_tv_carrier/generate_pcb.py)** | 源码级 PCB 生成器 + 独立几何 DRC 与连通性校验器（已通过 **0 短路、0 断路、最小间距 ≥ 0.20mm** 校验） |

---

## 1. 硬件规格与全功能一览

- **板框尺寸**：**`96.0 mm × 76.0 mm`**（严格在 **10cm × 10cm 以内**，完全符合嘉立创每月免费的 2 层板打样券条件！）
- **工艺要求**：常规 2 层 FR-4，板厚 `1.6 mm`，铜厚 `1 oz`，无铅喷锡（HASL），**全直插插件（Through-Hole）封装**，普通电烙铁即可轻松手焊。
- **集成功能**：
  1. **ESP32-C3 SuperMini 插槽**（`U1_L` / `U1_R`，双 1×8P 2.54mm 排母，行距 15.24mm，USB-C 朝向板上沿方便插线）；
  2. **1.54" 8 针 ST7789 IPS 屏幕排母**（`J_LCD`，按通用 `GND, VCC, SCL, SDA, RES, DC, CS, BLK` 排布，`CS` 接地，紧邻 `0.1uF` 退耦电容 `C3`）；
  3. **EC11 旋转编码器 + 按压开关**（`EC11`，完整官方 Alps EC11E 封装 + `10kΩ` 上拉 + `1kΩ/10nF` AB 相消抖 + `1kΩ/100nF` 按压消抖）；
  4. **5 键 ADC 电阻梯形键盘**（`SW1~SW5` 沿下沿一字排开：`BACK / UP / OK / DOWN / MUTE`，仅占 `GPIO0 / ADC1_CH0` 一根线，Wi-Fi 安全）；
  5. **无源电磁蜂鸣器驱动**（`BZ1` 12mm 无源蜂鸣器 + `Q1` S8050 NPN 三极管 + `D1` 1N4148 反并联续流二极管 + `1kΩ/10kΩ` 偏置电阻）；
  6. **DS3231 离线 RTC 时钟排母**（`J_RTC`）+ **并联 I2C 扩展排针**（`J_I2C`，可外挂 `PCF8574` 矩阵键盘等）+ 预留 `R_SDA/R_SCL` 4.7kΩ 上拉电阻位；
  7. **仪器调试专用接口**：`J_UART`（3.3V USB-TTL 3 针调试口）+ `JP_B`（EC11_B 与 GPIO20 隔离跳线帽）+ **`J_LA` 6 针示波器/逻辑分析仪排针**（`GND, CLK, DAT, DC, RST, BL`，夹探头不用飞线！）；
  8. 四角 **4× M3 安装孔**（3.2mm 孔径），方便装铜柱或 3D 打印外壳。

---

## 2. 嘉立创（JLCPCB）下单一步步教程（免费白嫖 5 片！）

1. 打开 **嘉立创下单助手** 或官网 [www.jlc.com](https://www.jlc.com/) → 进入 **PCB 计价/下单**。
2. 点击 **「上传 Gerber 文件」**，直接上传：
   `hardware/agy_tv_carrier/agy_tv_carrier_jlcpcb_gerber.zip`
3. 等待解析完成后，在页面右侧的 **Gerber 预览图** 中目视确认：
   - 外形尺寸显示 **`96.0 × 76.0 mm`**；
   - 层数自动识别为 **2 层**；
   - 正面丝印清晰可见 `AGY TV CARRIER V1.0`、各个排母引脚标注与元件位号。
4. 下单参数保持默认最省钱选项即可：
   - **板材**：FR-4
   - **层数**：2 层
   - **板厚**：`1.6 mm`
   - **数量**：`5` 片
   - **表面处理**：有铅喷锡 或 无铅喷锡（均可，推荐无铅）
   - **阻焊颜色**：绿色（出货最快且免费券默认支持，其他颜色按券规则选择）
   - **确认生产稿**：选「不需要」即可
5. 领取嘉立创每月免费打样券（10×10cm 以内 2 层板），抵扣后 **¥0（含/或仅付少量运费）** 下单，一般 2~3 天收到 5 片崭新 PCB！

> **想先在立创 EDA / KiCad 里预览或微调？**
> - **KiCad 8**：直接双击打开 `hardware/agy_tv_carrier/agy_tv_carrier.kicad_pcb`，按 `Alt+3` 可看 3D 效果，按「检查 → 设计规则检查 (DRC)」可再次验证。
> - **立创 EDA 专业版**：菜单 `文件 → 导入 → KiCad`，导入 `agy_tv_carrier.kicad_pcb` 即可查看或继续编辑。

---

## 3. 采购清单与焊接装配顺序（新手不翻车指南）

完整清单见 [bom.csv](../hardware/agy_tv_carrier/bom.csv)。除了 **ESP32-C3 SuperMini**、**1.54" 8 针 ST7789 模块** 和 **DS3231 模块** 淘宝/拼多多买现成的以外，其余直插电阻/电容/三极管/二极管/按键/排母在立创商城或淘宝电阻包都能配齐（总价约 ¥15）。

### 强烈建议的「从矮到高」焊接顺序
1. **先焊最矮的电阻与二极管**：
   - `R_AD0, R_B1, R_B2, R_EA, R_EB, R_ESW, R_PUA, R_PUB`（卧式 10.16mm 孔距）；
   - `R_AD1 ~ R_AD4`（立式折脚 5.08mm 孔距，注意对照阻值：`2.2k / 5.6k / 12k / 33k`）；
   - `R_SDA, R_SCL`（4.7kΩ）：**如果你买的 DS3231 模块自带上拉电阻，这两颗可以先不焊（空着）**；
   - **`D1 (1N4148)` 极性务必看准！** 二极管黑环（阴极 `K`）必须朝向 PCB 丝印的双竖线 / 方焊盘（`+3.3V` 端）。
2. **焊瓷片电容与三极管**：
   - `C2, C3, C_AD, C_ESW`（`0.1uF / 104`），`C_EA, C_EB`（`0.01uF / 103`），无极性；
   - **`Q1 (S8050)` 方向务必看准！** 把塑料平面对准丝印的平边（上方），从左到右正好对应丝印标出的 **`E - B - C`**（不确定时用万用表二极管/hFE 档核对后再焊）。
3. **焊按键、跳线排针与电解电容**：
   - `SW1 ~ SW5`（6×6mm 4 脚轻触开关）；
   - `J_UART (3P)`、`JP_B (2P)`、`J_LA (6P)`、`J_I2C (4P)` 公排针；
   - **`C1 (10uF 电解电容)` 极性看准！** 长脚（正极 `+`）插进丝印标 `+` 的方焊盘。
4. **最后焊最高的器件与排母**：
   - `U1_L (8P)`、`U1_R (8P)`、`J_LCD (8P)`、`J_RTC (4P)` **2.54mm 母排座**（建议先插上对应的排针或模块帮着对齐垂直，再点焊固定）；
   - **`BZ1`（12mm 无源蜂鸣器）**：标注 `+` 的长脚插进方焊盘；
   - **`EC11` 旋转编码器**：先把两侧大金属支撑脚卡平焊牢，再焊 5 根信号脚。

---

## 4. 上电前最后 4 项安全复核（必做！）

1. **断电测短路**：万用表电阻档测 `J_LCD` 的 `GND`（Pin 1）和 `VCC`（Pin 2），确认无短路。
2. **核对模块引脚顺序**：
   - **ESP32-C3 SuperMini**：确认 **USB-C 口朝上** 对准丝印 `USB-C TOP` 后再插入 `U1_L / U1_R`；
   - **1.54" ST7789 模块**：插进 `J_LCD` 前**肉眼逐针核对**模块背面丝印与底板 `GND, VCC, SCL, SDA, RES, DC, CS, BLK` 完全一致！
   - **DS3231 模块**：装普通不可充电 **CR2032** 电池前，记得先按第 5 篇提醒焊掉蓝色 ZS-042 模块板载的充电电阻/二极管！
3. **插上跳线帽 `JP_B`**：正常使用时插上 `JP_B` 让 `EC11_B` 连到 `GPIO20`；只有在接 `J_UART` 用 USB-TTL 往芯片发数据（需要 `RX`）时才拔下 `JP_B`。
4. **分步通电验证**：先只插 `ESP32-C3 SuperMini` 确认 `3V3` 正常且能 `ping` 通 → 再断电插上 `ST7789` 屏幕跑 `ZTV1 C demo=1` → 最后用示波器夹在 `J_LA` 排针和按键/蜂鸣器测试点验收全部扩展功能！
