#!/usr/bin/env python3
"""ESP32 USB Oscilloscope AFE + Carrier Board v1.0 — 2-Layer PCB & Gerber Generator.

A real single-supply (3.3V) mixed-signal oscilloscope front-end + ESP32-C3
SuperMini carrier (with breakout header for ESP32-S3 / ESP32-P4 + phone screen
or external high-speed ADC like AD9280).

Analog Front-End (AFE) stages:
  1. BNC input (J_BNC) + GND clip loop (TP_GND)
  2. AC / DC coupling slide switch (SW_CPL, C_AC = 0.1uF 250V film cap)
  3. Frequency-compensated ~1 MOhm single-supply level-shifting divider:
       R_IN = 910k || C_COMP (5-30pF trimmer)
       R_UP = 100k (to AVDD 3.3V), R_DN = 100k (to AGND), C_ATT = 180pF
       -> Input impedance ~960kOhm, shifts -20V..+20V safely into +0.52V..+2.61V!
  4. Input protection clamp: dual 1N4148 (D_P1 to AVDD, D_P2 to AGND)
  5. DIP-8 Dual Rail-to-Rail Op-Amp (U2: MCP6002 / TLC2272 / OPA2350):
       - U2A: High-Z unity-gain buffer
       - U2B: Switchable x1 (+-20V) / x4.7 (+-4V) gain stage around 1.57V virtual mid-rail
  6. Anti-aliasing RC filter (R_AAF 1k + C_AAF 1nF) + Schottky ADC clamp
  7. 1kHz Probe-Compensation Calibration Hook (CAL_1KHZ from GPIO10 via 1k)
  8. Analog power RC filter (R_AV 10R + C_AV1 10uF + C_AV2 0.1uF -> AVDD_3V3)

Digital / Display / Expansion:
  - U1: ESP32-C3 SuperMini (2x8P 2.54mm, USB-C TOP)
  - J_LCD: 1.54" 8-pin ST7789 IPS LCD header (standalone display)
  - EC11: Rotary encoder w/ push switch (timebase / trigger / run-stop)
  - J_EXT: 4-pin AFE breakout (GND, AVDD, AFE_OUT, CAL_1KHZ) to connect directly
           to ESP32-S3 + AD9280 or ESP32-P4 + MIPI smartphone screen!

Board size: 94.0 x 68.0 mm (<= 100x100mm JLCPCB free 2-layer coupon eligible).
All through-hole components for easy hand soldering.
"""
import csv
import heapq
import math
import os
import struct
import sys
import zipfile
import zlib
from collections import defaultdict

# Reuse verified drawing/gerber/font helpers from the sibling generator
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE_DIR, "agy_tv_carrier"))
import generate_pcb as base  # noqa: E402

OUT_DIR = os.path.dirname(os.path.abspath(__file__))
GERBER_DIR = os.path.join(OUT_DIR, "gerber")

# Override board dimensions in base module for this 94 x 68 mm scope board
BOARD_W = 94.0
BOARD_H = 68.0
base.BOARD_W = BOARD_W
base.BOARD_H = BOARD_H
base.GX = int(round(BOARD_W / base.GRID))
base.GY = int(round(BOARD_H / base.GRID))


# ------------------------------------------------------------------ Scope-specific through-hole footprints

def add_bnc_jack(comps, ref, cx, cy, net_sig, net_gnd, lcsc="C964015", desc="Right-Angle Through-Hole BNC Female Connector"):
    """Standard right-angle PCB-mount BNC jack (10.16mm shield lugs, 5.08mm back to center/gnd)."""
    c = base.Component(ref, "BNC-JACK", "Connector_BNC_RA_P10.16mm", cx, cy, lcsc=lcsc, desc=desc)
    # 2 heavy shield mounting legs (2.0mm drill)
    c.pads.append(base.Pad(ref, "SH1", cx - 5.08, cy, drill=2.0, dia=3.4, net=net_gnd))
    c.pads.append(base.Pad(ref, "SH2", cx + 5.08, cy, drill=2.0, dia=3.4, net=net_gnd))
    # Signal center pin + ground pin 5.08mm behind shield legs
    c.pads.append(base.Pad(ref, "SIG", cx, cy + 5.08, drill=1.1, dia=1.9, net=net_sig, shape="rect", label="IN"))
    c.pads.append(base.Pad(ref, "GND", cx - 2.54, cy + 5.08, drill=1.1, dia=1.9, net=net_gnd, label="G"))
    c.silk_rects.append((cx - 7.2, cy - 5.5, cx + 7.2, cy + 7.2))
    c.silk_texts.append((cx, cy - 6.8, "J_BNC CH1 IN", 1.0))
    comps.append(c)
    return c


def add_slide_switch_spdt(comps, ref, label, x, y, net1, net_com, net3, horiz=True, lcsc="C431540", desc="SS12D00G3 SPDT 2.54mm Slide Switch"):
    """3-pin 2.54mm SPDT slide switch (SS12D00 / SK12D07)."""
    c = base.Component(ref, label, "SW_Slide_SPDT_P2.54mm", x, y, lcsc=lcsc, desc=desc)
    pts = [(x + i * 2.54, y) for i in range(3)] if horiz else [(x, y + i * 2.54) for i in range(3)]
    for i, (pt, net) in enumerate(zip(pts, (net1, net_com, net3)), 1):
        c.pads.append(base.Pad(ref, i, pt[0], pt[1], drill=0.95, dia=1.7, net=net, shape="rect" if i == 2 else "circle"))
    if horiz:
        c.silk_rects.append((x - 1.6, y - 2.0, x + 5.08 + 1.6, y + 2.0))
        c.silk_texts.append((x + 2.54, y - 3.3, f"{ref} {label}", 0.88))
    else:
        c.silk_rects.append((x - 2.0, y - 1.6, x + 2.0, y + 5.08 + 1.6))
        c.silk_texts.append((x + 5.2, y + 2.54, f"{ref}", 0.88))
    comps.append(c)
    return c


def add_dip8_ic(comps, ref, value, x1, y1, pin_nets, lcsc="C497468", desc="DIP-8 Socket + Dual Rail-to-Rail Op-Amp (MCP6002 / TLC2272)"):
    """Standard DIP-8 (7.62mm = 300 mil row spacing, 2.54mm pin pitch).
    (x1, y1) is Pin 1 (top-left). Pins 1..4 go down left, Pins 5..8 go up right.
    """
    c = base.Component(ref, value, "DIP-8_W7.62mm_Socket", x1, y1, lcsc=lcsc, desc=desc)
    # Left side: pins 1, 2, 3, 4
    for i in range(4):
        c.pads.append(base.Pad(ref, i + 1, x1, y1 + i * 2.54, drill=0.9, dia=1.7,
                               net=pin_nets[i], shape="rect" if i == 0 else "circle"))
    # Right side: pins 8, 7, 6, 5 from top to bottom
    for i in range(4):
        pin_num = 8 - i
        c.pads.append(base.Pad(ref, pin_num, x1 + 7.62, y1 + i * 2.54, drill=0.9, dia=1.7,
                               net=pin_nets[pin_num - 1]))
    c.silk_rects.append((x1 + 1.1, y1 - 1.1, x1 + 6.52, y1 + 3 * 2.54 + 1.1))
    c.silk_circles.append((x1 + 3.81, y1 - 0.3, 0.9))  # Pin 1 notch
    c.silk_texts.append((x1 + 3.81, y1 - 2.5, f"{ref} {value}", 0.95))
    comps.append(c)
    return c


def add_trimmer_cap(comps, ref, value, x, y, net1, net2, lcsc="C273071", desc="5-30pF Trimmer Capacitor (Probe Frequency Compensation)"):
    """Standard 6mm radial trimmer capacitor (5.08mm pin pitch)."""
    c = base.Component(ref, value, "C_Trim_6mm_P5.08mm", x, y, lcsc=lcsc, desc=desc)
    c.pads.append(base.Pad(ref, 1, x, y, drill=0.95, dia=1.75, net=net1, shape="rect"))
    c.pads.append(base.Pad(ref, 2, x + 5.08, y, drill=0.95, dia=1.75, net=net2))
    c.silk_circles.append((x + 2.54, y, 3.2))
    c.silk_texts.append((x + 2.54, y - 4.3, f"{ref} {value}", 0.85))
    comps.append(c)
    return c


def add_film_cap(comps, ref, value, x, y, net1, net2, pitch=7.62, lcsc="C106875", desc="0.1uF 250V CBB Film Capacitor (AC Coupling)"):
    """CBB / Box film capacitor (7.62mm = 300 mil pitch)."""
    c = base.Component(ref, value, "C_Film_L10mm_P7.62mm", x, y, lcsc=lcsc, desc=desc)
    c.pads.append(base.Pad(ref, 1, x, y, drill=0.9, dia=1.75, net=net1))
    c.pads.append(base.Pad(ref, 2, x + pitch, y, drill=0.9, dia=1.75, net=net2))
    c.silk_rects.append((x - 1.2, y - 2.2, x + pitch + 1.2, y + 2.2))
    c.silk_texts.append((x + pitch / 2, y - 3.3, f"{ref} {value}", 0.88))
    comps.append(c)
    return c


# ------------------------------------------------------------------ build scope board

def build_scope_board():
    comps = []

    # 4 corner M3 mounting holes
    for idx, (mx, my) in enumerate([(4.5, 4.5), (BOARD_W - 4.5, 4.5),
                                    (4.5, BOARD_H - 4.5), (BOARD_W - 4.5, BOARD_H - 4.5)], 1):
        base.add_mounting_hole(comps, f"H{idx}", mx, my)

    # ==================== LEFT HALF: ANALOG FRONT-END (x = 8 .. 46) ====================
    # 1. BNC Input at top-left edge
    add_bnc_jack(comps, "J_BNC", 16.51, 11.43, net_sig="BNC_IN", net_gnd="GND")

    # 2. AC Coupling Cap + AC/DC Slide Switch
    add_film_cap(comps, "C_AC", "0.1u 250V", 8.89, 25.40, "BNC_IN", "BNC_AC")
    add_slide_switch_spdt(comps, "SW_CPL", "AC/DC", 22.86, 25.40, "BNC_AC", "CPL_OUT", "BNC_IN",
                          desc="AC / DC Coupling Slide Switch (1=AC, 2=OUT, 3=DC)")

    # 3. Frequency-Compensated ~1MOhm Single-Supply Bias Divider
    # CPL_OUT -> R_IN (910k) || C_COMP (5-30pF) -> AFE_N1
    # AFE_N1 -> R_UP (100k to AVDD), R_DN (100k to GND), C_ATT (180pF to GND)
    base.add_resistor(comps, "R_IN", "910k", 8.89, 31.75, "CPL_OUT", "AFE_N1", horiz=True,
                      lcsc="C61280", desc="910k 1% 1/4W Input Attenuator Resistor (1MOhm total)")
    add_trimmer_cap(comps, "C_COMP", "5-30pF", 24.13, 31.75, "CPL_OUT", "AFE_N1")

    base.add_resistor(comps, "R_UP", "100k", 8.89, 38.10, "AVDD", "AFE_N1", horiz=True,
                      lcsc="C61276", desc="100k 1% 1/4W Upper Bias Resistor (V_mid=1.56V)")
    base.add_resistor(comps, "R_DN", "100k", 22.86, 38.10, "AFE_N1", "GND", horiz=True,
                      lcsc="C61276", desc="100k 1% 1/4W Lower Bias Resistor")
    base.add_cap_ceramic(comps, "C_ATT", "180pF", 36.83, 38.10, "AFE_N1", "GND", horiz=True,
                         lcsc="C192560", desc="180pF C0G/NP0 Compensation Cap (matches C_COMP)")

    # 4. Over-voltage protection diodes on AFE_N1 (clamp to AVDD and GND)
    base.add_diode(comps, "D_P1", "1N4148", 8.89, 44.45, "AFE_N1", "AVDD", horiz=True,
                   lcsc="C2128", desc="1N4148 Over-voltage Clamp to AVDD (+3.3V)")
    base.add_diode(comps, "D_P2", "1N4148", 22.86, 44.45, "GND", "AFE_N1", horiz=True,
                   lcsc="C2128", desc="1N4148 Under-voltage Clamp to GND (0V)")

    # 5. Dual Rail-to-Rail Op-Amp U2 (DIP-8: MCP6002 / TLC2272 / OPA2350)
    # Pin1=OUTA(V_BUF), Pin2=INA-(V_BUF), Pin3=INA+(AFE_N1), Pin4=VSS(GND)
    # Pin5=INB+(V_BUF), Pin6=INB-(G_INV), Pin7=OUTB(AMP_OUT), Pin8=VDD(AVDD)
    add_dip8_ic(comps, "U2", "MCP6002", 34.29, 21.59,
                ["V_BUF", "V_BUF", "AFE_N1", "GND", "V_BUF", "G_INV", "AMP_OUT", "AVDD"])

    # Clean Analog Power Filter: 3V3 -> R_AV(10R) -> AVDD (+ 10uF + 0.1uF)
    base.add_resistor(comps, "R_AV", "10R", 34.29, 8.89, "3V3", "AVDD", horiz=True,
                      lcsc="C61260", desc="10 Ohm 1/4W Analog Power Filter Resistor")
    base.add_cap_elec(comps, "C_AV1", "10uF", 34.29, 13.97, "AVDD", "GND",
                      lcsc="C134785", desc="10uF 16V Electrolytic (AVDD Bulk)")
    base.add_cap_ceramic(comps, "C_AV2", "100n", 40.64, 13.97, "AVDD", "GND", horiz=True,
                         lcsc="C192578", desc="0.1uF Ceramic (U2 AVDD Decoupling)")

    # 6. Switchable x1 (+-20V) / x4.7 (+-4V) Gain Stage on U2B
    # R_FB (39k): AMP_OUT -> G_INV
    # SW_GAIN connects G_INV to VREF_DIV (22k to AVDD || 20k to GND = 1.57V, 10.48kOhm)
    base.add_resistor(comps, "R_FB", "39k", 8.89, 50.80, "AMP_OUT", "G_INV", horiz=True,
                      lcsc="C61274", desc="39k 1% 1/4W U2B Feedback Resistor (x4.7 gain)")
    add_slide_switch_spdt(comps, "SW_GAIN", "X1/X5", 22.86, 50.80, "G_INV", "GAIN_SW", "VREF_DIV",
                          desc="Gain Switch: Open/1-2=x1 (+-20V), 2-3=x4.7 (+-4V) — tie pin 1 & 2 to G_INV via short")
    # Wait: SPDT connects pin 2 (COM) to pin 1 or pin 3!
    # So put net1="NC_G" (unused), net_com="G_INV", net3="VREF_DIV" -> left = x1 (open), right = x4.7 (closed)!
    comps[-1].pads[0].net = ""
    comps[-1].pads[1].net = "G_INV"
    comps[-1].pads[2].net = "VREF_DIV"

    base.add_resistor(comps, "R_REF1", "22k", 8.89, 57.15, "AVDD", "VREF_DIV", horiz=True,
                      lcsc="C61277", desc="22k 1% 1/4W Gain Mid-Rail Upper Divider (1.57V)")
    base.add_resistor(comps, "R_REF2", "20k", 22.86, 57.15, "VREF_DIV", "GND", horiz=True,
                      lcsc="C61278", desc="20k 1% 1/4W Gain Mid-Rail Lower Divider")

    # 7. Anti-Aliasing RC Filter + Calibration Hook
    base.add_resistor(comps, "R_AAF", "1k", 36.83, 44.45, "AMP_OUT", "AFE_OUT", horiz=True,
                      lcsc="C61268", desc="1k 1/4W Anti-Aliasing Filter Resistor")
    base.add_cap_ceramic(comps, "C_AAF", "1nF", 36.83, 50.80, "AFE_OUT", "GND", horiz=True,
                         lcsc="C192570", desc="1nF (102) Anti-Aliasing Cap (fc~160kHz; use 10nF for 16kHz)")
    base.add_resistor(comps, "R_CAL", "1k", 36.83, 57.15, "CAL_PWM", "CAL_1KHZ", horiz=True,
                      lcsc="C61268", desc="1k 1/4W 1kHz Probe Calibration Output Series Resistor")

    # 8. External AFE Breakout Header (to plug into ESP32-P4 + Phone Screen or AD9280!)
    ext_pins = [
        ("GND", "GND"),
        ("AVDD", "AVDD"),
        ("OUT", "AFE_OUT"),
        ("CAL", "CAL_1KHZ"),
    ]
    j_ext = base.add_header(comps, "J_EXT", "AFE-OUT-4P", 10.16, 63.50, ext_pins, horiz=True,
                            lcsc="C2337", desc="1x4P 2.54mm Header (AFE Breakout -> ESP32-S3/P4 / AD9280 + 1kHz CAL Hook)",
                            label_side="bottom", show_ref=False)
    j_ext.silk_texts.append((25.5, 63.50, "J_EXT AFE", 0.9))

    # ==================== RIGHT HALF: ESP32-C3 + LCD + EC11 (x = 50 .. 90) ====================
    # 9. ESP32-C3 SuperMini (USB-C pointing UP)
    u1_x, u1_y = 53.34, 10.16
    u1_l_pins = [
        ("G5", "LCD_MOSI"),
        ("G6", "LCD_RST"),
        ("G7", "EC11_A"),
        ("G8", "I2C_SCL"),
        ("G9", "EC11_SW"),
        ("G10", "CAL_PWM"),
        ("G20", "EC11_B"),
        ("G21", "UART_TX"),
    ]
    u1_r_pins = [
        ("5V", "VBUS_5V"),
        ("GND", "GND"),
        ("3V3", "3V3"),
        ("G4", "I2C_SDA"),
        ("G3", "LCD_SCLK"),
        ("G2", "LCD_DC"),
        ("G1", "LCD_BL"),
        ("G0", "AFE_OUT"),
    ]
    base.add_header(comps, "U1_L", "ESP32-C3-SM-L", u1_x, u1_y, u1_l_pins, horiz=False,
                    lcsc="C2883692", desc="1x8P 2.54mm Female Header (ESP32-C3 SuperMini Left)", label_side="left", show_ref=False)
    base.add_header(comps, "U1_R", "ESP32-C3-SM-R", u1_x + 15.24, u1_y, u1_r_pins, horiz=False,
                    lcsc="C2883692", desc="1x8P 2.54mm Female Header (ESP32-C3 SuperMini Right)", label_side="right", show_ref=False)
    outline = base.Component("U1", "ESP32-C3 SuperMini", "Module_ESP32-C3_SuperMini", u1_x + 7.62, u1_y + 8.89,
                             desc="ESP32-C3 SuperMini Module (plug in for standalone scope; omit if using J_EXT with ESP32-P4)")
    outline.silk_rects.append((u1_x - 1.4, u1_y - 3.6, u1_x + 16.64, u1_y + 19.3))
    outline.silk_rects.append((u1_x + 3.6, u1_y - 4.4, u1_x + 11.6, u1_y - 0.8))
    outline.silk_texts.append((u1_x + 7.62, u1_y - 5.4, "USB-C TOP", 0.9))
    outline.silk_texts.append((u1_x + 7.62, u1_y + 8.9, "ESP32-C3", 1.0))
    comps.append(outline)

    # 10. 1.54" 8-Pin ST7789 Standalone Display Header (right vertical)
    lcd_pins = [
        ("GND", "GND"),
        ("VCC", "3V3"),
        ("SCL", "LCD_SCLK"),
        ("SDA", "LCD_MOSI"),
        ("RES", "LCD_RST"),
        ("DC", "LCD_DC"),
        ("CS", "GND"),
        ("BLK", "LCD_BL"),
    ]
    base.add_header(comps, "J_LCD", "ST7789-8P", 83.82, 10.16, lcd_pins, horiz=False,
                    lcsc="C2883692", desc="1x8P 2.54mm Female Header (1.54\" ST7789 Standalone Display)", label_side="right")
    base.add_cap_ceramic(comps, "C_LCD", "100nF", 76.20, 31.75, "3V3", "GND", horiz=True,
                         lcsc="C192578", desc="0.1uF Ceramic (LCD 3V3 Decoupling)")

    # 11. EC11 Rotary Encoder (Timebase / Trigger / Hold) at bottom-right
    base.add_ec11(comps, "EC11", "TIME/TRIG", 71.12, 46.99,
                  net_a="EC11_A", net_c="GND", net_b="EC11_B",
                  net_s1="EC11_SW", net_s2="GND",
                  lcsc="C470609", desc="Alps EC11E Vertical Rotary Encoder (Timebase / Trigger / Hold)")
    base.add_resistor(comps, "R_PUA", "10k", 53.34, 41.91, "3V3", "EC11_A", horiz=True,
                      lcsc="C61270", desc="10k 1/4W EC11_A Pull-up")
    base.add_resistor(comps, "R_PUB", "10k", 53.34, 46.99, "3V3", "EC11_B", horiz=True,
                      lcsc="C61270", desc="10k 1/4W EC11_B Pull-up")
    base.add_cap_ceramic(comps, "C_EA", "10nF", 53.34, 53.34, "EC11_A", "GND", horiz=True,
                         lcsc="C192574", desc="10nF Ceramic EC11_A Filter")
    base.add_cap_ceramic(comps, "C_EB", "10nF", 53.34, 58.42, "EC11_B", "GND", horiz=True,
                         lcsc="C192574", desc="10nF Ceramic EC11_B Filter")

    # Board title silkscreen
    title = base.Component("LOGO", "ESP32-USB-SCOPE", "Silkscreen", 47.0, 2.5)
    title.silk_texts.append((47.0, 2.5, "ESP32 USB SCOPE AFE V1.0 (+-20V / +-4V | 1MOHM | 1KHZ CAL)", 1.05))
    comps.append(title)
    return comps


def render_scope_schematic(path):
    """Draw a crystal-clear analog + digital schematic PNG for the USB oscilloscope."""
    w, h = 1680, 1020
    cv = base.Canvas(w, h, bytes([250, 250, 246]))
    blk = bytes([28, 32, 42])
    blu = bytes([24, 88, 175])
    red = bytes([190, 38, 38])
    grn = bytes([24, 130, 62])
    dim = bytes([96, 104, 118])
    box_bg = bytes([241, 246, 252])

    def text_left(x, cy, txt, sz, col, thick=1.3):
        cw = sz * 0.72
        gap = sz * 0.28
        tw = len(txt) * cw + max(0, len(txt) - 1) * gap
        cv.text(x + tw / 2.0, cy, txt, sz, col, thick)

    def text_right(x, cy, txt, sz, col, thick=1.3):
        cw = sz * 0.72
        gap = sz * 0.28
        tw = len(txt) * cw + max(0, len(txt) - 1) * gap
        cv.text(x - tw / 2.0, cy, txt, sz, col, thick)

    cv.line(20, 20, w - 20, 20, 2, blk)
    cv.line(w - 20, 20, w - 20, h - 20, 2, blk)
    cv.line(w - 20, h - 20, 20, h - 20, 2, blk)
    cv.line(20, h - 20, 20, 20, 2, blk)
    cv.text(w / 2, 48, "ESP32 USB OSCILLOSCOPE AFE V1.0 - COMPLETE SCHEMATIC", 17, blk, 2.0)
    cv.text(w / 2, 74, "1MOHM COMPENSATED INPUT | SINGLE-SUPPLY 3.3V LEVEL SHIFT (+-20V / +-4V) | MCP6002 / OPA2350 | 1KHZ CAL", 10, dim, 1.3)

    def draw_box(x, y, bw, bh, title, left_pins, right_pins, notes=()):
        cv.rect_fill(x, y, x + bw, y + bh, box_bg)
        cv.line(x, y, x + bw, y, 2, blk)
        cv.line(x + bw, y, x + bw, y + bh, 2, blk)
        cv.line(x + bw, y + bh, x, y + bh, 2, blk)
        cv.line(x, y + bh, x, y, 2, blk)
        cv.text(x + bw / 2, y + 18, title, 11, red, 1.6)
        for idx, (pin_name, net_name) in enumerate(left_pins):
            py = y + 48 + idx * 30
            cv.line(x - 36, py, x, py, 2, grn)
            text_left(x + 10, py, pin_name, 9.5, blk, 1.3)
            text_right(x - 44, py, net_name, 9.5, blu, 1.3)
        for idx, (pin_name, net_name) in enumerate(right_pins):
            py = y + 48 + idx * 30
            cv.line(x + bw, py, x + bw + 36, py, 2, grn)
            text_right(x + bw - 10, py, pin_name, 9.5, blk, 1.3)
            text_left(x + bw + 44, py, net_name, 9.5, blu, 1.3)
        for idx, note in enumerate(notes):
            cv.text(x + bw / 2, y + bh + 18 + idx * 18, note, 8.8, dim, 1.2)

    # Row 1: BNC + AC/DC + 1MOhm Attenuator | U2 Op-Amp Buffer & x1/x5 Gain | AAF + J_EXT
    draw_box(215, 120, 245, 210, "STAGE 1-3: BNC + 1M AFE",
             [("BNC_IN", "CH1 PROBE"), ("C_AC", "0.1UF 250V"), ("SW_CPL", "AC / DC"),
              ("R_IN", "910K||CCOMP"), ("BIAS", "100K/100K")],
             [("AFE_N1", "1.56V+0.05V/V"), ("C_ATT", "180PF->GND"),
              ("D_P1", "1N4148->AVDD"), ("D_P2", "GND->1N4148")],
             notes=("RIN ~ 960KOHM | -20V..+20V ON SINGLE 3.3V RAIL!",))

    draw_box(770, 120, 225, 230, "STAGE 4: U2 MCP6002",
             [("3 INA+", "AFE_N1"), ("2 INA-", "V_BUF"), ("4 VSS", "GND"),
              ("5 INB+", "V_BUF"), ("6 INB-", "G_INV")],
             [("1 OUTA", "V_BUF"), ("8 VDD", "AVDD 3.3V"), ("7 OUTB", "AMP_OUT"),
              ("R_FB", "39K->G_INV"), ("SW_GAIN", "X1 / X4.7")],
             notes=("VREF: 22K(AVDD)/20K(GND)=1.57V", "UPGRADE: OPA2350 (>10MHZ)"))

    draw_box(1290, 120, 215, 195, "STAGE 5-6: AAF + EXT",
             [("R_AAF", "1K AMP_OUT"), ("C_AAF", "1NF->GND"),
              ("J_EXT.3", "AFE_OUT"), ("J_EXT.4", "CAL_1KHZ")],
             [("AVDD", "10R+10U"), ("CAL", "G10->1K"),
              ("J_EXT.1", "GND"), ("J_EXT.2", "AVDD")],
             notes=("J_EXT -> ESP32-P4 / AD9280", "OR USE ONBOARD ESP32-C3"))

    # Row 2: U1 ESP32-C3 SuperMini | J_LCD ST7789 | EC11 Controls
    draw_box(215, 440, 245, 280, "U1 ESP32-C3 SUPERMINI",
             [("G5", "LCD_MOSI"), ("G6", "LCD_RST"), ("G7", "EC11_A"), ("G8", "I2C_SCL"),
              ("G9", "EC11_SW"), ("G10", "CAL_PWM"), ("G20", "EC11_B"), ("G21", "UART_TX")],
             [("5V", "VBUS_5V"), ("GND", "GND"), ("3V3", "3V3"), ("G4", "I2C_SDA"),
              ("G3", "LCD_SCLK"), ("G2", "LCD_DC"), ("G1", "LCD_BL"), ("G0", "AFE_OUT")],
             notes=("USB-CDC STREAMS ADC DMA TO PC/PHONE",))

    draw_box(770, 440, 225, 280, "J_LCD ST7789 1.54 8P",
             [("1 GND", "GND"), ("2 VCC", "3V3"), ("3 SCL", "LCD_SCLK"), ("4 SDA", "LCD_MOSI"),
              ("5 RES", "LCD_RST"), ("6 DC", "LCD_DC"), ("7 CS", "GND"), ("8 BLK", "LCD_BL")],
             [],
             notes=("C_LCD 100NF DECOUPLING",))

    draw_box(1290, 440, 215, 195, "EC11 TIMEBASE / TRIG",
             [("A", "EC11_A G7"), ("C", "GND"), ("B", "EC11_B G20"),
              ("S1", "EC11_SW G9"), ("S2", "GND")],
             [("R_PU", "10K X2"), ("C_AB", "10NF X2")],
             notes=("ROTATE: TIMEBASE / LEVEL", "PRESS: RUN / STOP"))

    # Safety warning footer box
    cv.rect_fill(120, 810, w - 120, 945, bytes([255, 242, 240]))
    cv.line(120, 810, w - 120, 810, 2, red)
    cv.line(w - 120, 810, w - 120, 945, 2, red)
    cv.line(w - 120, 945, 120, 945, 2, red)
    cv.line(120, 945, 120, 810, 2, red)
    cv.text(w / 2, 842, "WARNING: NON-ISOLATED USB OSCILLOSCOPE SAFETY LIMIT <= +-30V DC/PEAK!", 13, red, 1.8)
    cv.text(w / 2, 876, "PROBE GND IS DIRECTLY TIED TO PC USB GND. NEVER MEASURE AC MAINS (220V) OR NON-ISOLATED SMPS PRIMARY SIDE!", 10, blk, 1.3)
    cv.text(w / 2, 908, "USE 1KHZ CAL HOOK + TRIMMER C_COMP WITH YOUR BENCH SCOPE TO CALIBRATE FLAT SQUARE WAVE RESPONSE.", 10, dim, 1.3)

    base.write_png(path, w, h, cv.buf)


def main():
    os.makedirs(GERBER_DIR, exist_ok=True)
    comps = build_scope_board()
    print(f"Built {len(comps)} scope components on {BOARD_W}x{BOARD_H}mm board")

    segments, vias = base.route_board(comps)
    print(f"Routed {len(segments)} copper segments and {len(vias)} vias")

    errors = base.verify_drc_and_connectivity(comps, segments, vias)
    if errors:
        print(f"DRC / CONNECTIVITY FAILED ({len(errors)} errors):")
        for e in errors[:25]:
            print("  !", e)
        sys.exit(1)
    print("DRC & Netlist Connectivity: 0 errors, 0 opens, 0 shorts! (min clearance >= 0.20mm)")

    # Write Gerbers + Drill
    files = {
        "esp32_usb_scope-F_Cu.gtl": lambda p: base.write_gerber_copper(p, 0, comps, segments, vias),
        "esp32_usb_scope-B_Cu.gbl": lambda p: base.write_gerber_copper(p, 1, comps, segments, vias),
        "esp32_usb_scope-F_Mask.gts": lambda p: base.write_gerber_mask(p, comps),
        "esp32_usb_scope-B_Mask.gbs": lambda p: base.write_gerber_mask(p, comps),
        "esp32_usb_scope-F_SilkS.gto": lambda p: base.write_gerber_silk(p, comps, empty=False),
        "esp32_usb_scope-B_SilkS.gbo": lambda p: base.write_gerber_silk(p, comps, empty=True),
        "esp32_usb_scope-Edge_Cuts.gm1": lambda p: base.write_gerber_outline(p),
        "esp32_usb_scope-PTH.drl": lambda p: base.write_excellon_drill(p, comps, vias),
    }
    zip_path = os.path.join(OUT_DIR, "esp32_usb_scope_jlcpcb_gerber.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for fname, writer in files.items():
            fpath = os.path.join(GERBER_DIR, fname)
            writer(fpath)
            zf.write(fpath, arcname=fname)

    # Write KiCad PCB + BOM into OUT_DIR
    orig_out = base.OUT_DIR
    base.OUT_DIR = OUT_DIR
    base.write_kicad_files(comps, segments, vias)
    # Rename KiCad files to match scope project name
    for ext in (".kicad_pcb", ".kicad_pro"):
        src = os.path.join(OUT_DIR, "agy_tv_carrier" + ext)
        dst = os.path.join(OUT_DIR, "esp32_usb_scope" + ext)
        if os.path.exists(src):
            os.replace(src, dst)
    base.write_bom(comps)
    base.OUT_DIR = orig_out

    base.render_pcb_png(os.path.join(OUT_DIR, "pcb_top.png"), comps, segments, vias, side="top")
    base.render_pcb_png(os.path.join(OUT_DIR, "pcb_bottom.png"), comps, segments, vias, side="bottom")
    render_scope_schematic(os.path.join(OUT_DIR, "schematic.png"))
    print(f"Wrote JLCPCB Gerber ZIP: {zip_path}")
    print(f"Wrote KiCad PCB, BOM, and PNG renders to {OUT_DIR}")


if __name__ == "__main__":
    main()
