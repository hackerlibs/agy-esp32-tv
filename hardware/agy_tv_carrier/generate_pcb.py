#!/usr/bin/env python3
"""AGY TV Carrier Board v1.0 — Full Through-Hole 2-Layer PCB & Gerber Generator.

Generates a JLCPCB-ready (<= 100x100mm free-coupon eligible) 2-layer carrier PCB
for:
  - ESP32-C3 SuperMini (2x8P 2.54mm headers, 15.24mm spacing, verified pinout)
  - 1.54" 8-pin ST7789 240x240 IPS LCD module (GND,VCC,SCL,SDA,RES,DC,CS,BLK)
  - EC11 vertical rotary encoder with push switch (verified Alps EC11E footprint)
  - 5-key ADC resistor-ladder keypad on GPIO0 (ADC1_CH0)
  - Passive electromagnetic buzzer driver (S8050 NPN + 1N4148 flyback diode)
  - DS3231 RTC header + parallel I2C expansion header (GPIO4=SDA, GPIO8=SCL)
  - USB-TTL debug header (GPIO20/21) + EC11_B isolate jumper + 6-pin Scope/LA header

Outputs in hardware/agy_tv_carrier/:
  - gerber/* and agy_tv_carrier_jlcpcb_gerber.zip  (RS-274X + Excellon drill)
  - agy_tv_carrier.kicad_pcb / .kicad_pro          (KiCad 8 compatible)
  - bom.csv                                         (Hand-solder BOM + LCSC refs)
  - pcb_top.png, pcb_bottom.png, schematic.png      (Visual inspection previews)

Includes an independent geometric DRC and union-find netlist connectivity checker:
refuses to emit Gerbers if any net is disconnected or any clearance is violated.
"""
import csv
import heapq
import math
import os
import struct
import sys
import uuid
import zipfile
import zlib
from collections import defaultdict

OUT_DIR = os.path.dirname(os.path.abspath(__file__))
GERBER_DIR = os.path.join(OUT_DIR, "gerber")

BOARD_W = 96.0  # mm (<= 100mm for JLCPCB free 10x10cm coupon)
BOARD_H = 76.0  # mm
GRID = 0.635    # mm (25 mil routing grid; 2.54mm = 4 grid steps!)
GX = int(round(BOARD_W / GRID))
GY = int(round(BOARD_H / GRID))

TRACE_PWR = 0.50   # mm (20 mil)
TRACE_SIG = 0.30   # mm (12 mil)
VIA_DRILL = 0.40   # mm
VIA_PAD = 0.85     # mm
MIN_CLEARANCE = 0.20  # mm (JLCPCB min is 0.127mm -> huge safety margin)


# ------------------------------------------------------------------ data model

class Pad:
    def __init__(self, comp, num, x, y, drill, dia, net="", shape="circle", label=""):
        self.comp = comp
        self.num = str(num)
        self.x = float(x)
        self.y = float(y)
        self.drill = float(drill)
        self.dia = float(dia)
        self.net = net
        self.shape = shape  # "circle" or "rect"
        self.label = label


class Component:
    def __init__(self, ref, value, footprint, x, y, lcsc="", desc="",
                 silk_rects=None, silk_lines=None, silk_circles=None, silk_texts=None):
        self.ref = ref
        self.value = value
        self.footprint = footprint
        self.x = float(x)
        self.y = float(y)
        self.lcsc = lcsc
        self.desc = desc
        self.pads = []
        self.silk_rects = silk_rects or []      # [(x0,y0,x1,y1)]
        self.silk_lines = silk_lines or []      # [(x0,y0,x1,y1)]
        self.silk_circles = silk_circles or []  # [(cx,cy,r)]
        self.silk_texts = silk_texts or []      # [(x,y,text,size_mm)]


class Segment:
    def __init__(self, layer, x0, y0, x1, y1, width, net):
        self.layer = layer  # 0 = F.Cu, 1 = B.Cu
        self.x0, self.y0 = float(x0), float(y0)
        self.x1, self.y1 = float(x1), float(y1)
        self.width = float(width)
        self.net = net


class Via:
    def __init__(self, x, y, net):
        self.x = float(x)
        self.y = float(y)
        self.drill = VIA_DRILL
        self.dia = VIA_PAD
        self.net = net


# ------------------------------------------------------------------ footprint builders

def add_header(comps, ref, value, x0, y0, pins, horiz=True, pitch=2.54, lcsc="", desc="", label_side="top", show_ref=True):
    """1xN 2.54mm through-hole pin header."""
    c = Component(ref, value, f"PinHeader_1x{len(pins):02d}_P2.54mm", x0, y0, lcsc=lcsc, desc=desc)
    for i, (pin_lbl, net) in enumerate(pins):
        px = x0 + (i * pitch if horiz else 0.0)
        py = y0 + (0.0 if horiz else i * pitch)
        shape = "rect" if i == 0 else "circle"
        c.pads.append(Pad(ref, i + 1, px, py, drill=1.0, dia=1.75, net=net, shape=shape, label=pin_lbl))
        if pin_lbl:
            if horiz:
                # Stagger alternating rows on tight 2.54mm pitch so 3-char labels never touch
                dy = 2.1 if (i % 2 == 0) else 3.4
                ty = py - dy if label_side == "top" else py + dy
                c.silk_texts.append((px, ty, pin_lbl, 0.78))
            else:
                tx = px - 3.6 if label_side == "left" else px + 3.6
                c.silk_texts.append((tx, py, pin_lbl, 0.85))
    x1 = x0 + ((len(pins) - 1) * pitch if horiz else 0.0)
    y1 = y0 + (0.0 if horiz else (len(pins) - 1) * pitch)
    c.silk_rects.append((x0 - 1.35, y0 - 1.35, x1 + 1.35, y1 + 1.35))
    if show_ref:
        if horiz:
            c.silk_texts.append(((x0 + x1) / 2, y0 - 5.1 if label_side == "top" else y0 + 5.1, ref, 1.05))
        else:
            c.silk_texts.append((x0, y0 - 2.6, ref, 1.05))
    comps.append(c)
    return c


def add_resistor(comps, ref, value, x, y, net1, net2, horiz=True, pitch=10.16, lcsc="", desc=""):
    """1/4W axial through-hole resistor (400 mil = 10.16mm pitch)."""
    c = Component(ref, value, "R_Axial_DIN0207_L6.3mm_P10.16mm", x, y, lcsc=lcsc, desc=desc)
    if horiz:
        p1, p2 = (x, y), (x + pitch, y)
        c.silk_rects.append((x + 1.9, y - 1.35, x + pitch - 1.9, y + 1.35))
        c.silk_texts.append((x + pitch / 2, y - 2.3, f"{ref} {value}", 0.9))
    else:
        p1, p2 = (x, y), (x, y + pitch)
        c.silk_rects.append((x - 1.35, y + 1.9, x + 1.35, y + pitch - 1.9))
        c.silk_texts.append((x + 3.2, y + pitch / 2, f"{ref}", 0.9))
    c.pads.append(Pad(ref, 1, p1[0], p1[1], drill=0.85, dia=1.65, net=net1))
    c.pads.append(Pad(ref, 2, p2[0], p2[1], drill=0.85, dia=1.65, net=net2))
    comps.append(c)
    return c


def add_diode(comps, ref, value, x, y, net_anode, net_cathode, horiz=True, pitch=10.16, lcsc="", desc=""):
    """DO-35 axial diode (1N4148). Pin 1 = Anode, Pin 2 = Cathode (marked with bar)."""
    c = Component(ref, value, "D_DO-35_P10.16mm", x, y, lcsc=lcsc, desc=desc)
    if horiz:
        p1, p2 = (x, y), (x + pitch, y)
        c.silk_rects.append((x + 2.2, y - 1.2, x + pitch - 2.2, y + 1.2))
        c.silk_lines.append((x + pitch - 3.0, y - 1.2, x + pitch - 3.0, y + 1.2))  # cathode bar
        c.silk_lines.append((x + pitch - 3.3, y - 1.2, x + pitch - 3.3, y + 1.2))
        c.silk_texts.append((x + pitch / 2, y - 2.2, f"{ref} {value} K->", 0.9))
    else:
        p1, p2 = (x, y), (x, y + pitch)
        c.silk_rects.append((x - 1.2, y + 2.2, x + 1.2, y + pitch - 2.2))
        c.silk_lines.append((x - 1.2, y + pitch - 3.0, x + 1.2, y + pitch - 3.0))
        c.silk_texts.append((x + 3.0, y + pitch / 2, ref, 0.9))
    c.pads.append(Pad(ref, 1, p1[0], p1[1], drill=0.85, dia=1.65, net=net_anode, label="A"))
    c.pads.append(Pad(ref, 2, p2[0], p2[1], drill=0.85, dia=1.65, net=net_cathode, shape="rect", label="K"))
    comps.append(c)
    return c


def add_cap_ceramic(comps, ref, value, x, y, net1, net2, horiz=True, pitch=5.08, lcsc="", desc=""):
    """Ceramic disc/MLCC through-hole cap (200 mil = 5.08mm pitch)."""
    c = Component(ref, value, "C_Disc_D5.0mm_P5.08mm", x, y, lcsc=lcsc, desc=desc)
    if horiz:
        p1, p2 = (x, y), (x + pitch, y)
        c.silk_rects.append((x - 0.5, y - 1.4, x + pitch + 0.5, y + 1.4))
        c.silk_texts.append((x + pitch / 2, y - 2.3, f"{ref} {value}", 0.85))
    else:
        p1, p2 = (x, y), (x, y + pitch)
        c.silk_rects.append((x - 1.4, y - 0.5, x + 1.4, y + pitch + 0.5))
        c.silk_texts.append((x + 3.0, y + pitch / 2, ref, 0.85))
    c.pads.append(Pad(ref, 1, p1[0], p1[1], drill=0.85, dia=1.65, net=net1))
    c.pads.append(Pad(ref, 2, p2[0], p2[1], drill=0.85, dia=1.65, net=net2))
    comps.append(c)
    return c


def add_cap_elec(comps, ref, value, x, y, net_pos, net_neg, pitch=2.54, lcsc="", desc=""):
    """Radial electrolytic cap (D5mm, P2.54mm). Pin 1 = + (square)."""
    c = Component(ref, value, "CP_Radial_D5.0mm_P2.54mm", x, y, lcsc=lcsc, desc=desc)
    cx, cy = x + pitch / 2, y
    c.silk_circles.append((cx, cy, 2.7))
    c.silk_texts.append((x - 1.8, y - 1.8, "+", 1.0))
    c.silk_texts.append((cx, y - 3.7, f"{ref} {value}", 0.85))
    c.pads.append(Pad(ref, 1, x, y, drill=0.85, dia=1.65, net=net_pos, shape="rect", label="+"))
    c.pads.append(Pad(ref, 2, x + pitch, y, drill=0.85, dia=1.65, net=net_neg, label="-"))
    comps.append(c)
    return c


def add_transistor_to92(comps, ref, value, x, y, net_e, net_b, net_c, lcsc="", desc=""):
    """S8050 NPN TO-92 wide 2.54mm bent-lead footprint: 1=E, 2=B, 3=C (flat side facing -Y)."""
    c = Component(ref, value, "TO-92_Inline_Wide_P2.54mm", x, y, lcsc=lcsc, desc=desc)
    c.pads.append(Pad(ref, 1, x, y, drill=0.85, dia=1.65, net=net_e, shape="rect", label="E"))
    c.pads.append(Pad(ref, 2, x + 2.54, y, drill=0.85, dia=1.65, net=net_b, label="B"))
    c.pads.append(Pad(ref, 3, x + 5.08, y, drill=0.85, dia=1.65, net=net_c, label="C"))
    c.silk_rects.append((x - 1.1, y - 2.0, x + 6.18, y + 2.2))
    c.silk_lines.append((x - 1.1, y - 1.3, x + 6.18, y - 1.3))  # flat face indicator
    c.silk_texts.append((x + 2.54, y - 3.1, f"{ref} {value}", 0.9))
    c.silk_texts.append((x, y + 3.1, "E", 0.85))
    c.silk_texts.append((x + 2.54, y + 3.1, "B", 0.85))
    c.silk_texts.append((x + 5.08, y + 3.1, "C", 0.85))
    comps.append(c)
    return c


def add_buzzer_12mm(comps, ref, value, cx, cy, net_pos, net_neg, lcsc="", desc=""):
    """12mm passive electromagnetic buzzer, 7.62mm (300 mil) pin pitch."""
    c = Component(ref, value, "Buzzer_12x9.5mm_P7.62mm", cx, cy, lcsc=lcsc, desc=desc)
    c.pads.append(Pad(ref, 1, cx - 3.81, cy, drill=1.0, dia=1.8, net=net_pos, shape="rect", label="+"))
    c.pads.append(Pad(ref, 2, cx + 3.81, cy, drill=1.0, dia=1.8, net=net_neg, label="-"))
    c.silk_circles.append((cx, cy, 6.2))
    c.silk_texts.append((cx - 3.81, cy - 2.5, "+", 1.1))
    c.silk_texts.append((cx, cy - 7.4, f"{ref} {value}", 0.95))
    comps.append(c)
    return c


def add_tactile_6x6(comps, ref, label, cx, cy, net_a, net_b, lcsc="", desc=""):
    """Standard 6x6mm 4-pin DIP tactile switch (6.5mm x 4.5mm hole grid).
    Top two pins (1,2) are internally shorted; bottom two pins (3,4) are internally shorted.
    We connect Pin 1 & 2 to net_a and Pin 3 & 4 to net_b (or leave 2/4 on same net).
    """
    c = Component(ref, label, "SW_PUSH_6mm_H4.3mm", cx, cy, lcsc=lcsc, desc=desc)
    dx, dy = 3.25, 2.25
    # Only route to Pin 1 and Pin 3; Pins 2 and 4 are internal duplicates (marked NC to avoid routing through switch)
    c.pads.append(Pad(ref, 1, cx - dx, cy - dy, drill=1.0, dia=1.75, net=net_a))
    c.pads.append(Pad(ref, 2, cx + dx, cy - dy, drill=1.0, dia=1.75, net=""))
    c.pads.append(Pad(ref, 3, cx - dx, cy + dy, drill=1.0, dia=1.75, net=net_b))
    c.pads.append(Pad(ref, 4, cx + dx, cy + dy, drill=1.0, dia=1.75, net=""))
    c.silk_rects.append((cx - 3.1, cy - 3.1, cx + 3.1, cy + 3.1))
    c.silk_texts.append((cx, cy - 4.4, ref, 0.9))
    c.silk_texts.append((cx, cy + 4.4, label, 0.85))
    comps.append(c)
    return c


def add_ec11(comps, ref, value, ax, ay, net_a, net_c, net_b, net_s1, net_s2, lcsc="", desc=""):
    """Official KiCad RotaryEncoder_Alps_EC11E-Switch_Vertical_H20mm footprint.
    Origin (ax, ay) = Pad A:
      A=(0,0), C=(0,2.5), B=(0,5.0)
      S2=(14.5,0), S1=(14.5,5.0)
      MP1=(7.5,8.1), MP2=(7.5,-3.1)  (circular 2.4mm plated mounting holes)
    """
    c = Component(ref, value, "RotaryEncoder_Alps_EC11E-Switch_Vertical_H20mm", ax, ay, lcsc=lcsc, desc=desc)
    c.pads.append(Pad(ref, "A", ax + 0.0, ay + 0.0, drill=1.05, dia=1.8, net=net_a, shape="rect", label="A"))
    c.pads.append(Pad(ref, "C", ax + 0.0, ay + 2.5, drill=1.05, dia=1.8, net=net_c, label="C"))
    c.pads.append(Pad(ref, "B", ax + 0.0, ay + 5.0, drill=1.05, dia=1.8, net=net_b, label="B"))
    c.pads.append(Pad(ref, "S2", ax + 14.5, ay + 0.0, drill=1.05, dia=1.8, net=net_s2, label="S2"))
    c.pads.append(Pad(ref, "S1", ax + 14.5, ay + 5.0, drill=1.05, dia=1.8, net=net_s1, label="S1"))
    c.pads.append(Pad(ref, "MP1", ax + 7.5, ay + 8.1, drill=2.4, dia=3.4, net="GND"))
    c.pads.append(Pad(ref, "MP2", ax + 7.5, ay - 3.1, drill=2.4, dia=3.4, net="GND"))
    # Body outline (12mm x 12.5mm centered at (7.5, 2.5))
    c.silk_rects.append((ax + 1.5, ay - 3.7, ax + 13.5, ay + 8.7))
    c.silk_circles.append((ax + 7.5, ay + 2.5, 3.0))  # shaft
    c.silk_texts.append((ax + 7.5, ay - 5.2, f"{ref} {value}", 1.0))
    comps.append(c)
    return c


def add_mounting_hole(comps, ref, cx, cy):
    c = Component(ref, "M3", "MountingHole_3.2mm_M3", cx, cy, desc="M3 mounting hole")
    c.pads.append(Pad(ref, 1, cx, cy, drill=3.2, dia=5.2, net=""))
    c.silk_circles.append((cx, cy, 3.0))
    comps.append(c)
    return c


# ------------------------------------------------------------------ build design

def build_board():
    comps = []

    # 4 corner M3 mounting holes
    for idx, (mx, my) in enumerate([(4.5, 4.5), (BOARD_W - 4.5, 4.5),
                                    (4.5, BOARD_H - 4.5), (BOARD_W - 4.5, BOARD_H - 4.5)], 1):
        add_mounting_hole(comps, f"H{idx}", mx, my)

    # 1. ESP32-C3 SuperMini (USB-C pointing UP at top-left, 15.24mm row spacing)
    u1_x, u1_y = 11.43, 10.16  # grid-aligned (18*0.635, 16*0.635)
    u1_l_pins = [
        ("G5", "LCD_MOSI"),
        ("G6", "LCD_RST"),
        ("G7", "EC11_A"),
        ("G8", "I2C_SCL"),
        ("G9", "EC11_SW"),
        ("G10", "BUZ_PWM"),
        ("G20", "UART_RX"),
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
        ("G0", "ADC_KEY"),
    ]
    add_header(comps, "U1_L", "ESP32-C3-SM-L", u1_x, u1_y, u1_l_pins, horiz=False,
               lcsc="C2883692", desc="1x8P 2.54mm Female Header (ESP32-C3 SuperMini Left)", label_side="left", show_ref=False)
    add_header(comps, "U1_R", "ESP32-C3-SM-R", u1_x + 15.24, u1_y, u1_r_pins, horiz=False,
               lcsc="C2883692", desc="1x8P 2.54mm Female Header (ESP32-C3 SuperMini Right)", label_side="right", show_ref=False)
    # SuperMini outline silkscreen
    outline = Component("U1", "ESP32-C3 SuperMini", "Module_ESP32-C3_SuperMini", u1_x + 7.62, u1_y + 8.89,
                        desc="ESP32-C3 SuperMini Module (plug into U1_L/U1_R, USB-C UP)")
    outline.silk_rects.append((u1_x - 1.4, u1_y - 3.6, u1_x + 16.64, u1_y + 19.3))
    outline.silk_rects.append((u1_x + 3.6, u1_y - 4.4, u1_x + 11.6, u1_y - 0.8))  # USB-C indicator
    outline.silk_texts.append((u1_x + 7.62, u1_y - 5.4, "USB-C TOP", 0.95))
    outline.silk_texts.append((u1_x + 7.62, u1_y + 8.9, "ESP32-C3", 1.0))
    comps.append(outline)

    # Power decoupling caps beside U1_R
    add_cap_elec(comps, "C1", "10uF", 34.29, 10.16, "3V3", "GND", lcsc="C134785", desc="10uF 16V Electrolytic (3V3 Bulk)")
    add_cap_ceramic(comps, "C2", "0.1uF", 34.29, 16.51, "3V3", "GND", horiz=True, lcsc="C192578", desc="0.1uF 50V Ceramic (3V3 Decoupling)")

    # 2. 1.54" 8-pin ST7789 LCD Header (top-center, horizontal)
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
    add_header(comps, "J_LCD", "ST7789-8P", 45.72, 10.16, lcd_pins, horiz=True,
               lcsc="C2883692", desc="1x8P 2.54mm Female Header (1.54\" ST7789 IPS LCD)", label_side="top")
    add_cap_ceramic(comps, "C3", "0.1uF", 45.72, 16.51, "3V3", "GND", horiz=True, lcsc="C192578", desc="0.1uF 50V Ceramic (LCD VCC Decoupling)")

    # 3. DS3231 RTC + I2C Expansion Headers (top-right)
    i2c_pins = [("GND", "GND"), ("3V3", "3V3"), ("SDA", "I2C_SDA"), ("SCL", "I2C_SCL")]
    add_header(comps, "J_RTC", "DS3231-4P", 73.66, 10.16, i2c_pins, horiz=False,
               lcsc="C2883690", desc="1x4P 2.54mm Female Header (DS3231 RTC Module)", label_side="left")
    add_header(comps, "J_I2C", "I2C-EXP-4P", 83.82, 10.16, i2c_pins, horiz=False,
               lcsc="C2883690", desc="1x4P 2.54mm Pin Header (I2C Expansion / PCF8574)", label_side="right")
    add_resistor(comps, "R_SDA", "4.7k", 71.12, 24.13, "3V3", "I2C_SDA", horiz=True, lcsc="C58209", desc="4.7k 1/4W I2C SDA Pull-up (DNP if RTC has pull-up)")
    add_resistor(comps, "R_SCL", "4.7k", 71.12, 29.21, "3V3", "I2C_SCL", horiz=True, lcsc="C58209", desc="4.7k 1/4W I2C SCL Pull-up (DNP if RTC has pull-up)")

    # 4. 6-Pin Oscilloscope / Logic Analyzer Test Header (middle-center)
    la_pins = [
        ("GND", "GND"),
        ("CLK", "LCD_SCLK"),
        ("DAT", "LCD_MOSI"),
        ("DC", "LCD_DC"),
        ("RST", "LCD_RST"),
        ("BL", "LCD_BL"),
    ]
    add_header(comps, "J_LA", "SCOPE-LA-6P", 45.72, 24.13, la_pins, horiz=True,
               lcsc="C2337", desc="1x6P 2.54mm Male Header (Oscilloscope / Logic Analyzer Test Points)", label_side="bottom")

    # 5. USB-TTL Debug Header + EC11_B Jumper (middle-left)
    uart_pins = [("GND", "GND"), ("TX", "UART_TX"), ("RX", "UART_RX")]
    add_header(comps, "J_UART", "USB-TTL-3P", 10.16, 36.83, uart_pins, horiz=False,
               lcsc="C2337", desc="1x3P 2.54mm Male Header (USB-TTL 3.3V Debug)", label_side="left")
    jp_pins = [("B", "EC11_B_SIG"), ("RX", "UART_RX")]
    add_header(comps, "JP_B", "EC11B-JMP", 17.78, 36.83, jp_pins, horiz=False,
               lcsc="C2337", desc="1x2P 2.54mm Jumper (Short to connect EC11_B to GPIO20)", label_side="right")

    # 6. Passive Electromagnetic Buzzer Driver (middle-center-left)
    add_buzzer_12mm(comps, "BZ1", "12mm-PASSIVE", 34.29, 39.37, "3V3", "BUZ_C",
                    lcsc="C95299", desc="12mm 3.3V Passive Electromagnetic Buzzer (7.62mm pitch)")
    add_diode(comps, "D1", "1N4148", 45.72, 34.29, "BUZ_C", "3V3", horiz=True,
              lcsc="C2128", desc="1N4148 DO-35 Flyback Diode (Cathode to 3V3!)")
    add_transistor_to92(comps, "Q1", "S8050", 48.26, 40.64, "GND", "BUZ_B", "BUZ_C",
                        lcsc="C2146", desc="S8050 NPN TO-92 Buzzer Driver (E-B-C)")
    add_resistor(comps, "R_B1", "1k", 25.40, 49.53, "BUZ_PWM", "BUZ_B", horiz=True,
                 lcsc="C61268", desc="1k 1/4W Buzzer Base Resistor")
    add_resistor(comps, "R_B2", "10k", 39.37, 49.53, "BUZ_B", "GND", horiz=True,
                 lcsc="C61270", desc="10k 1/4W Buzzer Base Pull-down")

    # 7. EC11 Rotary Encoder + Pull-ups & RC Debounce (middle-right)
    # EC11 A at (73.66, 40.64) -> S2 at (88.16, 40.64), S1 at (88.16, 45.64)
    add_ec11(comps, "EC11", "EC11E-SW", 73.66, 41.91,
             net_a="EC11_A_RAW", net_c="GND", net_b="EC11_B_RAW",
             net_s1="EC11_SW_RAW", net_s2="GND",
             lcsc="C470609", desc="Alps EC11E Vertical Rotary Encoder w/ Push Switch")
    # EC11 RC filter + pull-ups (just left of EC11, x=58..69)
    add_resistor(comps, "R_EA", "1k", 58.42, 34.29, "EC11_A_RAW", "EC11_A", horiz=True,
                 lcsc="C61268", desc="1k 1/4W EC11_A Series Debounce")
    add_resistor(comps, "R_EB", "1k", 58.42, 39.37, "EC11_B_RAW", "EC11_B_SIG", horiz=True,
                 lcsc="C61268", desc="1k 1/4W EC11_B Series Debounce")
    add_resistor(comps, "R_ESW", "1k", 58.42, 44.45, "EC11_SW_RAW", "EC11_SW", horiz=True,
                 lcsc="C61268", desc="1k 1/4W EC11_SW Series Debounce")
    add_resistor(comps, "R_PUA", "10k", 58.42, 49.53, "3V3", "EC11_A_RAW", horiz=True,
                 lcsc="C61270", desc="10k 1/4W EC11_A Pull-up")
    add_resistor(comps, "R_PUB", "10k", 71.12, 54.61, "3V3", "EC11_B_RAW", horiz=True,
                 lcsc="C61270", desc="10k 1/4W EC11_B Pull-up")
    add_cap_ceramic(comps, "C_EA", "10nF", 62.23, 54.61, "EC11_A", "GND", horiz=True,
                    lcsc="C192574", desc="0.01uF (103) Ceramic EC11_A Filter")
    add_cap_ceramic(comps, "C_EB", "10nF", 71.12, 59.69, "EC11_B_SIG", "GND", horiz=True,
                    lcsc="C192574", desc="0.01uF (103) Ceramic EC11_B Filter")
    add_cap_ceramic(comps, "C_ESW", "100nF", 81.28, 59.69, "EC11_SW", "GND", horiz=True,
                    lcsc="C192578", desc="0.1uF (104) Ceramic EC11_SW Debounce")

    # 8. 5-Key ADC Resistor-Ladder Keypad along bottom edge (y = 68.58)
    add_resistor(comps, "R_AD0", "10k", 8.89, 53.34, "3V3", "ADC_KEY", horiz=True,
                 lcsc="C61270", desc="10k 1% ADC Keypad Top Pull-up")
    add_cap_ceramic(comps, "C_AD", "100nF", 22.86, 53.34, "ADC_KEY", "GND", horiz=True,
                    lcsc="C192578", desc="0.1uF (104) ADC Keypad Filter Cap")

    sw_specs = [
        ("SW1", "BACK", 12.70, "GND", None),
        ("SW2", "UP",   22.86, "AD_K2", ("R_AD1", "2.2k 1%", "C61269")),
        ("SW3", "OK",   33.02, "AD_K3", ("R_AD2", "5.6k 1%", "C61272")),
        ("SW4", "DOWN", 43.18, "AD_K4", ("R_AD3", "12k 1%",  "C61271")),
        ("SW5", "MUTE", 53.34, "AD_K5", ("R_AD4", "33k 1%",  "C61273")),
    ]
    for sref, slbl, sx, net_b, rinfo in sw_specs:
        add_tactile_6x6(comps, sref, slbl, sx, 68.58, "ADC_KEY", net_b,
                        lcsc="C318884", desc=f"6x6mm 4-pin DIP Tactile Switch ({slbl})")
        if rinfo is not None:
            rref, rval, rlcsc = rinfo
            add_resistor(comps, rref, rval, sx, 57.15, net_b, "GND", horiz=False, pitch=5.08,
                         lcsc=rlcsc, desc=f"{rval} 1/4W ADC Ladder Resistor (stand-up 5.08mm pitch)")

    # Board title silkscreen
    title = Component("LOGO", "AGY-TV-CARRIER", "Silkscreen", 48.0, 3.0)
    title.silk_texts.append((48.0, 2.5, "AGY TV CARRIER v1.0 (ESP32-C3 SUPERMINI + ST7789)", 1.2))
    comps.append(title)
    return comps


# ------------------------------------------------------------------ A* 2-layer grid router

def to_grid(x, y):
    return int(round(x / GRID)), int(round(y / GRID))


def from_grid(gx, gy):
    return round(gx * GRID, 4), round(gy * GRID, 4)


def mark_disc(grid_blocked, cx, cy, radius_mm, val):
    gx0 = max(0, int(math.floor((cx - radius_mm) / GRID)))
    gx1 = min(GX, int(math.ceil((cx + radius_mm) / GRID)))
    gy0 = max(0, int(math.floor((cy - radius_mm) / GRID)))
    gy1 = min(GY, int(math.ceil((cy + radius_mm) / GRID)))
    r2 = radius_mm * radius_mm
    for gx in range(gx0, gx1 + 1):
        wx = gx * GRID
        for gy in range(gy0, gy1 + 1):
            wy = gy * GRID
            if (wx - cx) ** 2 + (wy - cy) ** 2 <= r2:
                grid_blocked[(gx, gy)] = val


def route_board(comps):
    """Route all nets using a 2-layer Manhattan A* router with strict clearance keepouts."""
    all_pads = [p for c in comps for p in c.pads]
    nets = defaultdict(list)
    for p in all_pads:
        if p.net:
            nets[p.net].append(p)

    # Occupied cells per layer: (layer, gx, gy) -> net_name (or "__HOLE__" for non-net pads)
    # Also keep via clearance map.
    pad_owner = {}  # (gx, gy) -> set of net names (all through-hole pads occupy BOTH layers)
    pad_clear = {}  # (gx, gy) -> set of net names whose pad clearance covers this cell

    edge_margin = 2  # keep traces >= 1.27mm from board edge
    # Register pad keepouts
    for p in all_pads:
        net_tag = p.net if p.net else f"__NPAD_{p.comp}_{p.num}"
        r_body = p.dia / 2.0
        r_clr = r_body + MIN_CLEARANCE + TRACE_PWR / 2.0 + 0.12
        gx0 = max(0, int(math.floor((p.x - r_clr) / GRID)))
        gx1 = min(GX, int(math.ceil((p.x + r_clr) / GRID)))
        gy0 = max(0, int(math.floor((p.y - r_clr) / GRID)))
        gy1 = min(GY, int(math.ceil((p.y + r_clr) / GRID)))
        for gx in range(gx0, gx1 + 1):
            wx = gx * GRID
            for gy in range(gy0, gy1 + 1):
                wy = gy * GRID
                d = math.hypot(wx - p.x, wy - p.y)
                if d <= r_clr:
                    pad_clear.setdefault((gx, gy), set()).add(net_tag)
                if d <= r_body + 0.15:
                    pad_owner.setdefault((gx, gy), set()).add(net_tag)

    # Routed copper occupancy: occ[layer][(gx, gy)] = net
    occ = [{}, {}]
    # Keepout around routed segments: clr_occ[layer][(gx, gy)] = set of nets
    clr_occ = [{}, {}]
    # Vias occupy both layers
    via_cells = {}  # (gx, gy) -> net

    segments = []
    vias = []

    def can_step(layer, gx, gy, net, width):
        if gx < edge_margin or gx > GX - edge_margin or gy < edge_margin or gy > GY - edge_margin:
            return False
        # Pad clearance check (both layers because pads are through-hole!)
        p_nets = pad_clear.get((gx, gy))
        if p_nets:
            for pn in p_nets:
                if pn != net:
                    return False
        # Routed copper & clearance check on this layer
        o = occ[layer].get((gx, gy))
        if o is not None and o != net:
            return False
        c_nets = clr_occ[layer].get((gx, gy))
        if c_nets:
            for cn in c_nets:
                if cn != net:
                    return False
        # Via clearance check
        vn = via_cells.get((gx, gy))
        if vn is not None and vn != net:
            return False
        return True

    def can_place_via(gx, gy, net):
        # Do not place a via right on top of a through-hole pad or near another net on either layer
        if (gx, gy) in pad_clear:
            return False
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                nx, ny = gx + dx, gy + dy
                p_nets = pad_clear.get((nx, ny))
                if p_nets and any(pn != net for pn in p_nets):
                    return False
                for lyr in (0, 1):
                    o = occ[lyr].get((nx, ny))
                    if o is not None and o != net:
                        return False
                    cn = clr_occ[lyr].get((nx, ny))
                    if cn and any(x != net for x in cn):
                        return False
                vn = via_cells.get((nx, ny))
                if vn is not None and vn != net:
                    return False
        return True

    def mark_path(path, net, width):
        # path is list of (layer, gx, gy)
        # Convert straight runs to segments and layer switches to vias
        clear_cells = 1  # 0.635mm grid step -> 1 cell halo guarantees >= 0.635 - width >= 0.135mm; check 1-ring
        # Actually for 0.5mm power trace, center-to-center of 1 grid (0.635mm) gives gap 0.135mm < 0.20mm!
        # So for 0.5mm traces or adjacent cells, we reserve diagonal/orthogonal halo cleanly:
        # With GRID = 0.635mm:
        #   - 2 grid steps (1.27mm) center-to-center: 0.3mm traces gap = 0.97mm, 0.5mm traces gap = 0.77mm!
        #   - 1 grid step (0.635mm) center-to-center: 0.3mm traces gap = 0.335mm >= 0.20mm (SAFE!).
        #   - 0.5mm trace vs 0.3mm trace at 1 grid step: gap = 0.635 - 0.40 = 0.235mm >= 0.20mm (SAFE!).
        #   - 0.5mm trace vs 0.5mm trace at 1 grid step: gap = 0.635 - 0.50 = 0.135mm -> reserve 1-cell halo for PWR!
        halo = 1 if width >= 0.45 else 0
        for layer, gx, gy in path:
            occ[layer][(gx, gy)] = net
            if halo:
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        if dx == 0 and dy == 0:
                            continue
                        clr_occ[layer].setdefault((gx + dx, gy + dy), set()).add(net)

        i = 0
        while i < len(path):
            if i + 1 < len(path) and path[i][0] != path[i + 1][0]:
                gx, gy = path[i][1], path[i][2]
                wx, wy = from_grid(gx, gy)
                vias.append(Via(wx, wy, net))
                via_cells[(gx, gy)] = net
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        for lyr in (0, 1):
                            clr_occ[lyr].setdefault((gx + dx, gy + dy), set()).add(net)
                i += 1
                continue
            j = i
            lyr = path[i][0]
            if i + 1 < len(path) and path[i + 1][0] == lyr:
                ddx = path[i + 1][1] - path[i][1]
                ddy = path[i + 1][2] - path[i][2]
                while j + 1 < len(path) and path[j + 1][0] == lyr:
                    if (path[j + 1][1] - path[j][1], path[j + 1][2] - path[j][2]) != (ddx, ddy):
                        break
                    j += 1
            if j > i:
                x0, y0 = from_grid(path[i][1], path[i][2])
                x1, y1 = from_grid(path[j][1], path[j][2])
                segments.append(Segment(lyr, x0, y0, x1, y1, width, net))
            i = j if j > i else i + 1

    # Order nets: short signal nets first, then longer signal nets, then 3V3 and GND last
    def net_sort_key(item):
        name, plist = item
        if name == "GND":
            return (3, len(plist), name)
        if name == "3V3":
            return (2, len(plist), name)
        xs = [p.x for p in plist]
        ys = [p.y for p in plist]
        span = (max(xs) - min(xs)) + (max(ys) - min(ys))
        return (1, span, name)

    sorted_nets = sorted(nets.items(), key=net_sort_key)

    for net, plist in sorted_nets:
        if len(plist) < 2:
            continue
        width = TRACE_PWR if net in ("3V3", "GND", "VBUS_5V") else TRACE_SIG

        # Build MST over pads of this net so we route shortest hops first
        connected = [plist[0]]
        remaining = list(plist[1:])
        net_grid_points = {to_grid(plist[0].x, plist[0].y)}

        # Add exact stub from pad center to nearest grid point if slightly off-grid (e.g. EC11/tactile)
        pad_grid = {}
        for p in plist:
            gp = to_grid(p.x, p.y)
            pad_grid[id(p)] = gp
            wx, wy = from_grid(*gp)
            if math.hypot(wx - p.x, wy - p.y) > 0.005:
                segments.append(Segment(0, p.x, p.y, wx, wy, width, net))

        while remaining:
            # Pick closest remaining pad to any already-connected pad
            best_pair = None
            best_d = 1e9
            for rp in remaining:
                for cp in connected:
                    d = abs(rp.x - cp.x) + abs(rp.y - cp.y)
                    if d < best_d:
                        best_d = d
                        best_pair = (rp, cp)
            target_pad, _ = best_pair
            remaining.remove(target_pad)
            connected.append(target_pad)

            sx, sy = pad_grid[id(target_pad)]
            targets = set(net_grid_points)
            # Also allow connecting to any already-routed cell of the SAME net!
            for lyr in (0, 1):
                for (gx, gy), owner in occ[lyr].items():
                    if owner == net:
                        targets.add((gx, gy))

            if (sx, sy) in targets:
                continue

            # Compute fast heuristic: Manhattan distance to nearest target
            tlist = list(targets)

            def h(gx, gy):
                return min(abs(gx - tx) + abs(gy - ty) for tx, ty in tlist)

            # A* search from (sx, sy) on either layer (through-hole pad exists on both layers!)
            pq = []
            dist = {}
            prev = {}
            for start_lyr in (0, 1):
                st = (start_lyr, sx, sy)
                dist[st] = 0
                heapq.heappush(pq, (h(sx, sy), 0, start_lyr, sx, sy))

            found = None
            expansions = 0
            while pq and expansions < 60000:
                f, g, lyr, gx, gy = heapq.heappop(pq)
                if g > dist.get((lyr, gx, gy), 1e9):
                    continue
                expansions += 1
                if (gx, gy) in targets and (gx, gy) != (sx, sy):
                    # Verify we end on a layer where this net exists (pad on both, or trace on lyr)
                    if (gx, gy) in net_grid_points or occ[lyr].get((gx, gy)) == net:
                        found = (lyr, gx, gy)
                        break

                # 4 orthogonal neighbors
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nx, ny = gx + dx, gy + dy
                    if not can_step(lyr, nx, ny, net, width):
                        continue
                    # Preferred direction: F.Cu (0) horizontal, B.Cu (1) vertical
                    step_cost = 10 if ((lyr == 0 and dy == 0) or (lyr == 1 and dx == 0)) else 14
                    # Slight bonus for reusing own net's trace
                    if occ[lyr].get((nx, ny)) == net:
                        step_cost = 2
                    ng = g + step_cost
                    nst = (lyr, nx, ny)
                    if ng < dist.get(nst, 1e9):
                        dist[nst] = ng
                        prev[nst] = (lyr, gx, gy)
                        heapq.heappush(pq, (ng + h(nx, ny) * 10, ng, lyr, nx, ny))

                # Layer switch via
                other = 1 - lyr
                if can_place_via(gx, gy, net):
                    ng = g + 45  # via penalty keeps via count low
                    nst = (other, gx, gy)
                    if ng < dist.get(nst, 1e9):
                        dist[nst] = ng
                        prev[nst] = (lyr, gx, gy)
                        heapq.heappush(pq, (ng + h(gx, gy) * 10, ng, other, gx, gy))

            if found is None:
                raise RuntimeError(f"Failed to route net '{net}' from {target_pad.comp}.{target_pad.num} ({sx},{sy})")

            # Reconstruct path
            path = []
            cur = found
            while cur in prev:
                path.append(cur)
                cur = prev[cur]
            path.append(cur)
            path.reverse()
            mark_path(path, net, width)
            net_grid_points.add((sx, sy))

    return segments, vias


# ------------------------------------------------------------------ independent DRC & connectivity verifier

def pt_seg_dist(px, py, x0, y0, x1, y1):
    dx, dy = x1 - x0, y1 - y0
    l2 = dx * dx + dy * dy
    if l2 == 0.0:
        return math.hypot(px - x0, py - y0)
    t = max(0.0, min(1.0, ((px - x0) * dx + (py - y0) * dy) / l2))
    return math.hypot(px - (x0 + t * dx), py - (y0 + t * dy))


def seg_seg_dist(ax0, ay0, ax1, ay1, bx0, by0, bx1, by1):
    # Check intersection first
    def ccw(px, py, qx, qy, rx, ry):
        return (ry - py) * (qx - px) - (qy - py) * (rx - px)
    d1 = ccw(ax0, ay0, ax1, ay1, bx0, by0)
    d2 = ccw(ax0, ay0, ax1, ay1, bx1, by1)
    d3 = ccw(bx0, by0, bx1, by1, ax0, ay0)
    d4 = ccw(bx0, by0, bx1, by1, ax1, ay1)
    if ((d1 > 0 and d2 < 0) or (d1 < 0 and d2 > 0)) and ((d3 > 0 and d4 < 0) or (d3 < 0 and d4 > 0)):
        return 0.0
    return min(
        pt_seg_dist(ax0, ay0, bx0, by0, bx1, by1),
        pt_seg_dist(ax1, ay1, bx0, by0, bx1, by1),
        pt_seg_dist(bx0, by0, ax0, ay0, ax1, ay1),
        pt_seg_dist(bx1, by1, ax0, ay0, ax1, ay1),
    )


def verify_drc_and_connectivity(comps, segments, vias):
    """Independent geometric DRC + union-find netlist connectivity check."""
    pads = [p for c in comps for p in c.pads]
    errors = []

    # 1. Board edge clearance
    for p in pads:
        if min(p.x - p.dia / 2, BOARD_W - p.x - p.dia / 2, p.y - p.dia / 2, BOARD_H - p.y - p.dia / 2) < 0.5:
            errors.append(f"EDGE: pad {p.comp}.{p.num} too close to board edge")

    # 2. Pad-to-pad clearance (different nets)
    for i in range(len(pads)):
        a = pads[i]
        for j in range(i + 1, len(pads)):
            b = pads[j]
            if a.net and b.net and a.net == b.net:
                continue
            gap = math.hypot(a.x - b.x, a.y - b.y) - (a.dia + b.dia) / 2.0
            if gap < MIN_CLEARANCE - 0.02:
                errors.append(f"PAD-PAD gap {gap:.3f}mm: {a.comp}.{a.num}({a.net}) vs {b.comp}.{b.num}({b.net})")

    # 3. Segment-to-pad clearance (different nets)
    for seg in segments:
        for p in pads:
            if p.net == seg.net:
                continue
            d = pt_seg_dist(p.x, p.y, seg.x0, seg.y0, seg.x1, seg.y1)
            gap = d - p.dia / 2.0 - seg.width / 2.0
            if gap < MIN_CLEARANCE - 0.02:
                errors.append(f"SEG-PAD gap {gap:.3f}mm: net={seg.net} L{seg.layer} vs {p.comp}.{p.num}({p.net})")

    # 4. Segment-to-segment clearance on same layer (different nets)
    by_layer = [[s for s in segments if s.layer == 0], [s for s in segments if s.layer == 1]]
    for lyr in (0, 1):
        sl = by_layer[lyr]
        for i in range(len(sl)):
            a = sl[i]
            for j in range(i + 1, len(sl)):
                b = sl[j]
                if a.net == b.net:
                    continue
                d = seg_seg_dist(a.x0, a.y0, a.x1, a.y1, b.x0, b.y0, b.x1, b.y1)
                gap = d - (a.width + b.width) / 2.0
                if gap < MIN_CLEARANCE - 0.02:
                    errors.append(f"SEG-SEG gap {gap:.3f}mm on L{lyr}: {a.net} vs {b.net}")

    # 5. Via-to-pad & via-to-segment clearance (different nets)
    for v in vias:
        for p in pads:
            if p.net == v.net:
                continue
            gap = math.hypot(v.x - p.x, v.y - p.y) - (v.dia + p.dia) / 2.0
            if gap < MIN_CLEARANCE - 0.02:
                errors.append(f"VIA-PAD gap {gap:.3f}mm: via({v.net}) vs {p.comp}.{p.num}({p.net})")
        for seg in segments:
            if seg.net == v.net:
                continue
            gap = pt_seg_dist(v.x, v.y, seg.x0, seg.y0, seg.x1, seg.y1) - (v.dia + seg.width) / 2.0
            if gap < MIN_CLEARANCE - 0.02:
                errors.append(f"VIA-SEG gap {gap:.3f}mm: via({v.net}) vs seg({seg.net})")

    # 6. Connectivity check per net via Union-Find on geometric overlap
    nets = defaultdict(list)
    for p in pads:
        if p.net:
            nets[p.net].append(p)
    for net, npads in nets.items():
        if len(npads) < 2:
            continue
        nsegs = [s for s in segments if s.net == net]
        nvias = [v for v in vias if v.net == net]
        # Nodes: pads (0..P-1), vias (P..P+V-1), segs (P+V..P+V+S-1)
        P, V, S = len(npads), len(nvias), len(nsegs)
        parent = list(range(P + V + S))

        def find(k):
            while parent[k] != k:
                parent[k] = parent[parent[k]]
                k = parent[k]
            return k

        def union(a, b):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        # Connect segs to pads, vias, and other same-layer segs
        for si, seg in enumerate(nsegs):
            sn = P + V + si
            for pi, p in enumerate(npads):
                if pt_seg_dist(p.x, p.y, seg.x0, seg.y0, seg.x1, seg.y1) <= p.dia / 2.0 + seg.width / 2.0:
                    union(sn, pi)
            for vi, v in enumerate(nvias):
                if pt_seg_dist(v.x, v.y, seg.x0, seg.y0, seg.x1, seg.y1) <= v.dia / 2.0 + seg.width / 2.0:
                    union(sn, P + vi)
            for sj in range(si + 1, S):
                s2 = nsegs[sj]
                if s2.layer == seg.layer:
                    if seg_seg_dist(seg.x0, seg.y0, seg.x1, seg.y1, s2.x0, s2.y0, s2.x1, s2.y1) <= (seg.width + s2.width) / 2.0:
                        union(sn, P + V + sj)

        roots = {find(i) for i in range(P)}
        if len(roots) > 1:
            errors.append(f"OPEN NET '{net}': {len(roots)} disconnected islands across {P} pads")

    return errors


# ------------------------------------------------------------------ Stroke vector font for Gerber silkscreen

STROKE_FONT = {
    "A": [((0, 1), (0.5, 0)), ((0.5, 0), (1, 1)), ((0.2, 0.65), (0.8, 0.65))],
    "B": [((0, 0), (0, 1)), ((0, 0), (0.75, 0)), ((0.75, 0), (0.95, 0.2)), ((0.95, 0.2), (0.75, 0.45)),
          ((0, 0.45), (0.75, 0.45)), ((0.75, 0.45), (1, 0.7)), ((1, 0.7), (0.75, 1)), ((0, 1), (0.75, 1))],
    "C": [((1, 0.15), (0.7, 0)), ((0.7, 0), (0.2, 0)), ((0.2, 0), (0, 0.25)), ((0, 0.25), (0, 0.75)),
          ((0, 0.75), (0.2, 1)), ((0.2, 1), (0.7, 1)), ((0.7, 1), (1, 0.85))],
    "D": [((0, 0), (0, 1)), ((0, 0), (0.65, 0)), ((0.65, 0), (1, 0.3)), ((1, 0.3), (1, 0.7)),
          ((1, 0.7), (0.65, 1)), ((0, 1), (0.65, 1))],
    "E": [((1, 0), (0, 0)), ((0, 0), (0, 1)), ((0, 1), (1, 1)), ((0, 0.5), (0.8, 0.5))],
    "F": [((1, 0), (0, 0)), ((0, 0), (0, 1)), ((0, 0.5), (0.8, 0.5))],
    "G": [((1, 0.15), (0.7, 0)), ((0.7, 0), (0.2, 0)), ((0.2, 0), (0, 0.25)), ((0, 0.25), (0, 0.75)),
          ((0, 0.75), (0.2, 1)), ((0.2, 1), (0.85, 1)), ((0.85, 1), (1, 0.75)), ((1, 0.75), (1, 0.55)), ((0.55, 0.55), (1, 0.55))],
    "H": [((0, 0), (0, 1)), ((1, 0), (1, 1)), ((0, 0.5), (1, 0.5))],
    "I": [((0.2, 0), (0.8, 0)), ((0.5, 0), (0.5, 1)), ((0.2, 1), (0.8, 1))],
    "J": [((0.8, 0), (0.8, 0.75)), ((0.8, 0.75), (0.55, 1)), ((0.55, 1), (0.2, 1)), ((0.2, 1), (0, 0.75))],
    "K": [((0, 0), (0, 1)), ((1, 0), (0, 0.55)), ((0.25, 0.42), (1, 1))],
    "L": [((0, 0), (0, 1)), ((0, 1), (1, 1))],
    "M": [((0, 1), (0, 0)), ((0, 0), (0.5, 0.55)), ((0.5, 0.55), (1, 0)), ((1, 0), (1, 1))],
    "N": [((0, 1), (0, 0)), ((0, 0), (1, 1)), ((1, 1), (1, 0))],
    "O": [((0.2, 0), (0.8, 0)), ((0.8, 0), (1, 0.25)), ((1, 0.25), (1, 0.75)), ((1, 0.75), (0.8, 1)),
          ((0.8, 1), (0.2, 1)), ((0.2, 1), (0, 0.75)), ((0, 0.75), (0, 0.25)), ((0, 0.25), (0.2, 0))],
    "P": [((0, 1), (0, 0)), ((0, 0), (0.75, 0)), ((0.75, 0), (1, 0.2)), ((1, 0.2), (1, 0.4)),
          ((1, 0.4), (0.75, 0.55)), ((0, 0.55), (0.75, 0.55))],
    "Q": [((0.2, 0), (0.8, 0)), ((0.8, 0), (1, 0.25)), ((1, 0.25), (1, 0.75)), ((1, 0.75), (0.8, 1)),
          ((0.8, 1), (0.2, 1)), ((0.2, 1), (0, 0.75)), ((0, 0.75), (0, 0.25)), ((0, 0.25), (0.2, 0)), ((0.6, 0.7), (1, 1))],
    "R": [((0, 1), (0, 0)), ((0, 0), (0.75, 0)), ((0.75, 0), (1, 0.2)), ((1, 0.2), (1, 0.4)),
          ((1, 0.4), (0.75, 0.55)), ((0, 0.55), (0.75, 0.55)), ((0.5, 0.55), (1, 1))],
    "S": [((1, 0.15), (0.75, 0)), ((0.75, 0), (0.2, 0)), ((0.2, 0), (0, 0.2)), ((0, 0.2), (0.2, 0.45)),
          ((0.2, 0.45), (0.8, 0.55)), ((0.8, 0.55), (1, 0.8)), ((1, 0.8), (0.75, 1)), ((0.75, 1), (0.2, 1)), ((0.2, 1), (0, 0.85))],
    "T": [((0, 0), (1, 0)), ((0.5, 0), (0.5, 1))],
    "U": [((0, 0), (0, 0.75)), ((0, 0.75), (0.25, 1)), ((0.25, 1), (0.75, 1)), ((0.75, 1), (1, 0.75)), ((1, 0.75), (1, 0))],
    "V": [((0, 0), (0.5, 1)), ((0.5, 1), (1, 0))],
    "W": [((0, 0), (0.2, 1)), ((0.2, 1), (0.5, 0.5)), ((0.5, 0.5), (0.8, 1)), ((0.8, 1), (1, 0))],
    "X": [((0, 0), (1, 1)), ((1, 0), (0, 1))],
    "Y": [((0, 0), (0.5, 0.5)), ((1, 0), (0.5, 0.5)), ((0.5, 0.5), (0.5, 1))],
    "Z": [((0, 0), (1, 0)), ((1, 0), (0, 1)), ((0, 1), (1, 1))],
    "0": [((0.2, 0), (0.8, 0)), ((0.8, 0), (1, 0.25)), ((1, 0.25), (1, 0.75)), ((1, 0.75), (0.8, 1)),
          ((0.8, 1), (0.2, 1)), ((0.2, 1), (0, 0.75)), ((0, 0.75), (0, 0.25)), ((0, 0.25), (0.2, 0)), ((0.15, 0.85), (0.85, 0.15))],
    "1": [((0.25, 0.2), (0.55, 0)), ((0.55, 0), (0.55, 1)), ((0.2, 1), (0.9, 1))],
    "2": [((0, 0.2), (0.25, 0)), ((0.25, 0), (0.8, 0)), ((0.8, 0), (1, 0.2)), ((1, 0.2), (1, 0.4)),
          ((1, 0.4), (0, 1)), ((0, 1), (1, 1))],
    "3": [((0, 0.15), (0.25, 0)), ((0.25, 0), (0.8, 0)), ((0.8, 0), (1, 0.2)), ((1, 0.2), (0.75, 0.48)),
          ((0.35, 0.48), (0.75, 0.48)), ((0.75, 0.48), (1, 0.75)), ((1, 0.75), (0.8, 1)), ((0.8, 1), (0.25, 1)), ((0.25, 1), (0, 0.85))],
    "4": [((0.75, 1), (0.75, 0)), ((0.75, 0), (0, 0.68)), ((0, 0.68), (1, 0.68))],
    "5": [((1, 0), (0, 0)), ((0, 0), (0, 0.45)), ((0, 0.45), (0.75, 0.45)), ((0.75, 0.45), (1, 0.65)),
          ((1, 0.65), (0.75, 1)), ((0.75, 1), (0.2, 1)), ((0.2, 1), (0, 0.85))],
    "6": [((0.85, 0), (0.25, 0)), ((0.25, 0), (0, 0.3)), ((0, 0.3), (0, 0.8)), ((0, 0.8), (0.25, 1)),
          ((0.25, 1), (0.8, 1)), ((0.8, 1), (1, 0.75)), ((1, 0.75), (0.8, 0.5)), ((0.8, 0.5), (0, 0.5))],
    "7": [((0, 0), (1, 0)), ((1, 0), (0.35, 1))],
    "8": [((0.25, 0), (0.75, 0)), ((0.75, 0), (1, 0.2)), ((1, 0.2), (0.75, 0.48)), ((0.25, 0.48), (0.75, 0.48)),
          ((0.25, 0.48), (0, 0.2)), ((0, 0.2), (0.25, 0)), ((0.75, 0.48), (1, 0.75)), ((1, 0.75), (0.75, 1)),
          ((0.75, 1), (0.25, 1)), ((0.25, 1), (0, 0.75)), ((0, 0.75), (0.25, 0.48))],
    "9": [((0.15, 1), (0.75, 1)), ((0.75, 1), (1, 0.7)), ((1, 0.7), (1, 0.2)), ((1, 0.2), (0.75, 0)),
          ((0.75, 0), (0.2, 0)), ((0.2, 0), (0, 0.25)), ((0, 0.25), (0.2, 0.5)), ((0.2, 0.5), (1, 0.5))],
    "-": [((0.15, 0.5), (0.85, 0.5))],
    "+": [((0.5, 0.15), (0.5, 0.85)), ((0.15, 0.5), (0.85, 0.5))],
    ".": [((0.45, 0.9), (0.55, 1.0))],
    "_": [((0, 1), (1, 1))],
    "/": [((0.1, 1), (0.9, 0))],
    "(": [((0.7, 0), (0.35, 0.25)), ((0.35, 0.25), (0.35, 0.75)), ((0.35, 0.75), (0.7, 1))],
    ")": [((0.3, 0), (0.65, 0.25)), ((0.65, 0.25), (0.65, 0.75)), ((0.65, 0.75), (0.3, 1))],
    ">": [((0.2, 0.15), (0.85, 0.5)), ((0.85, 0.5), (0.2, 0.85))],
    "%": [((0.1, 1), (0.9, 0)), ((0.15, 0.15), (0.3, 0.15)), ((0.7, 0.85), (0.85, 0.85))],
    " ": [],
}


def text_to_strokes(cx, cy, text, size_mm):
    """Centered vector stroke lines in PCB coordinates (y down)."""
    s = text.upper()
    cw = size_mm * 0.72
    gap = size_mm * 0.28
    total_w = len(s) * cw + max(0, len(s) - 1) * gap
    x0 = cx - total_w / 2.0
    y0 = cy - size_mm / 2.0
    lines = []
    for i, ch in enumerate(s):
        ox = x0 + i * (cw + gap)
        for (ax, ay), (bx, by) in STROKE_FONT.get(ch, []):
            lines.append((ox + ax * cw, y0 + ay * size_mm, ox + bx * cw, y0 + by * size_mm))
    return lines


# ------------------------------------------------------------------ RS-274X Gerber & Excellon Drill writer

def gcoord(x_mm, y_mm):
    # Gerber uses standard Cartesian (+Y UP), so flip Y = BOARD_H - y_mm
    ix = int(round(x_mm * 10000))
    iy = int(round((BOARD_H - y_mm) * 10000))
    return f"X{ix}Y{iy}"


def write_gerber_copper(path, layer_idx, comps, segments, vias):
    pads = [p for c in comps for p in c.pads]
    # Collect unique apertures
    ap_map = {}
    ap_defs = []

    def get_ap(kind, val):
        key = (kind, round(val, 4))
        if key not in ap_map:
            code = 10 + len(ap_defs)
            ap_map[key] = code
            if kind == "C":
                ap_defs.append(f"%ADD{code}C,{val:.4f}*%")
            elif kind == "R":
                ap_defs.append(f"%ADD{code}R,{val:.4f}X{val:.4f}*%")
        return ap_map[key]

    for p in pads:
        get_ap("R" if p.shape == "rect" else "C", p.dia)
    for v in vias:
        get_ap("C", v.dia)
    for s in segments:
        if s.layer == layer_idx:
            get_ap("C", s.width)

    lines = [
        "G04 AGY TV Carrier Board v1.0*",
        "%FSLAX44Y44*%",
        "%MOMM*%",
        "%LPD*%",
    ] + ap_defs + ["G01*"]

    cur_ap = None

    def select_ap(code):
        nonlocal cur_ap
        if code != cur_ap:
            lines.append(f"D{code}*")
            cur_ap = code

    # Flash pads
    for p in pads:
        select_ap(get_ap("R" if p.shape == "rect" else "C", p.dia))
        lines.append(f"{gcoord(p.x, p.y)}D03*")
    # Flash vias
    for v in vias:
        select_ap(get_ap("C", v.dia))
        lines.append(f"{gcoord(v.x, v.y)}D03*")
    # Draw segments
    for s in segments:
        if s.layer != layer_idx:
            continue
        select_ap(get_ap("C", s.width))
        lines.append(f"{gcoord(s.x0, s.y0)}D02*")
        lines.append(f"{gcoord(s.x1, s.y1)}D01*")

    lines.append("M02*")
    with open(path, "w", encoding="ascii") as f:
        f.write("\n".join(lines) + "\n")


def write_gerber_mask(path, comps):
    pads = [p for c in comps for p in c.pads]
    expansion = 0.10  # 0.05mm per side solder mask expansion
    ap_map = {}
    ap_defs = []

    def get_ap(kind, val):
        key = (kind, round(val, 4))
        if key not in ap_map:
            code = 10 + len(ap_defs)
            ap_map[key] = code
            if kind == "C":
                ap_defs.append(f"%ADD{code}C,{val:.4f}*%")
            else:
                ap_defs.append(f"%ADD{code}R,{val:.4f}X{val:.4f}*%")
        return ap_map[key]

    for p in pads:
        get_ap("R" if p.shape == "rect" else "C", p.dia + expansion)

    lines = ["G04 Solder Mask*", "%FSLAX44Y44*%", "%MOMM*%", "%LPD*%"] + ap_defs + ["G01*"]
    cur_ap = None
    for p in pads:
        code = get_ap("R" if p.shape == "rect" else "C", p.dia + expansion)
        if code != cur_ap:
            lines.append(f"D{code}*")
            cur_ap = code
        lines.append(f"{gcoord(p.x, p.y)}D03*")
    lines.append("M02*")
    with open(path, "w", encoding="ascii") as f:
        f.write("\n".join(lines) + "\n")


def write_gerber_silk(path, comps, empty=False):
    lines = ["G04 Silkscreen*", "%FSLAX44Y44*%", "%MOMM*%", "%LPD*%", "%ADD10C,0.1500*%", "G01*", "D10*"]
    if not empty:
        for c in comps:
            for x0, y0, x1, y1 in c.silk_rects:
                pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
                lines.append(f"{gcoord(*pts[0])}D02*")
                for pt in pts[1:]:
                    lines.append(f"{gcoord(*pt)}D01*")
            for x0, y0, x1, y1 in c.silk_lines:
                lines.append(f"{gcoord(x0, y0)}D02*")
                lines.append(f"{gcoord(x1, y1)}D01*")
            for cx, cy, r in c.silk_circles:
                segs = 28
                for k in range(segs + 1):
                    ang = 2 * math.pi * k / segs
                    px, py = cx + r * math.cos(ang), cy + r * math.sin(ang)
                    lines.append(f"{gcoord(px, py)}{'D02' if k == 0 else 'D01'}*")
            for tx, ty, txt, sz in c.silk_texts:
                for x0, y0, x1, y1 in text_to_strokes(tx, ty, txt, sz):
                    lines.append(f"{gcoord(x0, y0)}D02*")
                    lines.append(f"{gcoord(x1, y1)}D01*")
    lines.append("M02*")
    with open(path, "w", encoding="ascii") as f:
        f.write("\n".join(lines) + "\n")


def write_gerber_outline(path):
    lines = [
        "G04 Board Outline (96.0 x 76.0 mm)*",
        "%FSLAX44Y44*%",
        "%MOMM*%",
        "%LPD*%",
        "%ADD10C,0.1000*%",
        "G01*",
        "D10*",
        f"{gcoord(0, 0)}D02*",
        f"{gcoord(BOARD_W, 0)}D01*",
        f"{gcoord(BOARD_W, BOARD_H)}D01*",
        f"{gcoord(0, BOARD_H)}D01*",
        f"{gcoord(0, 0)}D01*",
        "M02*",
    ]
    with open(path, "w", encoding="ascii") as f:
        f.write("\n".join(lines) + "\n")


def write_excellon_drill(path, comps, vias):
    holes = defaultdict(list)
    for c in comps:
        for p in c.pads:
            if p.drill > 0:
                holes[round(p.drill, 2)].append((p.x, p.y))
    for v in vias:
        holes[round(v.drill, 2)].append((v.x, v.y))

    lines = ["M48", "; AGY TV Carrier Board v1.0 Excellon Drill", "METRIC,TZ"]
    tool_map = {}
    for idx, dia in enumerate(sorted(holes.keys()), 1):
        tool_map[dia] = f"T{idx:02d}"
        lines.append(f"T{idx:02d}C{dia:.3f}")
    lines.append("%")
    lines.append("G90")
    lines.append("G05")
    for dia in sorted(holes.keys()):
        lines.append(tool_map[dia])
        for x, y in holes[dia]:
            # Excellon coordinates match Gerber Cartesian (+Y UP)
            lines.append(f"X{x:.3f}Y{BOARD_H - y:.3f}")
    lines.append("T0")
    lines.append("M30")
    with open(path, "w", encoding="ascii") as f:
        f.write("\n".join(lines) + "\n")


# ------------------------------------------------------------------ KiCad 8 .kicad_pcb & .kicad_pro writer

def write_kicad_files(comps, segments, vias):
    pro_path = os.path.join(OUT_DIR, "agy_tv_carrier.kicad_pro")
    with open(pro_path, "w", encoding="utf-8") as f:
        f.write('{"meta":{"filename":"agy_tv_carrier.kicad_pro","version":1},"board":{"design_settings":{"rules":{"min_clearance":0.2,"min_track_width":0.2}}}}\n')

    # Collect unique nets
    net_names = [""] + sorted({p.net for c in comps for p in c.pads if p.net})
    net_id = {n: i for i, n in enumerate(net_names)}

    out = [
        '(kicad_pcb (version 20240108) (generator "agy_tv_generate_pcb")',
        '  (general (thickness 1.6))',
        '  (paper "A4")',
        '  (layers',
        '    (0 "F.Cu" signal)',
        '    (31 "B.Cu" signal)',
        '    (36 "B.SilkS" user "B.Silkscreen")',
        '    (37 "F.SilkS" user "F.Silkscreen")',
        '    (38 "B.Mask" user)',
        '    (39 "F.Mask" user)',
        '    (44 "Edge.Cuts" user)',
        '  )',
        '  (setup (pad_to_mask_clearance 0.05))',
    ]
    for n, idx in net_id.items():
        out.append(f'  (net {idx} "{n}")')

    # Board outline
    pts = [(0, 0), (BOARD_W, 0), (BOARD_W, BOARD_H), (0, BOARD_H)]
    for i in range(4):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % 4]
        out.append(f'  (gr_line (start {x0:.3f} {y0:.3f}) (end {x1:.3f} {y1:.3f}) (stroke (width 0.15) (type solid)) (layer "Edge.Cuts"))')

    # Footprints
    for c in comps:
        out.append(f'  (footprint "AGY_TV:{c.footprint}" (layer "F.Cu") (at {c.x:.3f} {c.y:.3f})')
        out.append(f'    (property "Reference" "{c.ref}" (at 0 -2 0) (layer "F.SilkS") (effects (font (size 1 1) (thickness 0.15))))')
        out.append(f'    (property "Value" "{c.value}" (at 0 2 0) (layer "F.Fab") (effects (font (size 1 1) (thickness 0.15))))')
        for x0, y0, x1, y1 in c.silk_rects:
            out.append(f'    (fp_rect (start {x0 - c.x:.3f} {y0 - c.y:.3f}) (end {x1 - c.x:.3f} {y1 - c.y:.3f}) (stroke (width 0.15) (type solid)) (layer "F.SilkS"))')
        for x0, y0, x1, y1 in c.silk_lines:
            out.append(f'    (fp_line (start {x0 - c.x:.3f} {y0 - c.y:.3f}) (end {x1 - c.x:.3f} {y1 - c.y:.3f}) (stroke (width 0.15) (type solid)) (layer "F.SilkS"))')
        for cx, cy, r in c.silk_circles:
            out.append(f'    (fp_circle (center {cx - c.x:.3f} {cy - c.y:.3f}) (end {cx - c.x + r:.3f} {cy - c.y:.3f}) (stroke (width 0.15) (type solid)) (layer "F.SilkS"))')
        for p in c.pads:
            sh = "rect" if p.shape == "rect" else "circle"
            nid = net_id.get(p.net, 0)
            out.append(
                f'    (pad "{p.num}" thru_hole {sh} (at {p.x - c.x:.3f} {p.y - c.y:.3f}) '
                f'(size {p.dia:.3f} {p.dia:.3f}) (drill {p.drill:.3f}) (layers "*.Cu" "*.Mask") '
                f'(net {nid} "{p.net}"))'
            )
        out.append('  )')

    # Segments
    for s in segments:
        lname = "F.Cu" if s.layer == 0 else "B.Cu"
        nid = net_id[s.net]
        out.append(f'  (segment (start {s.x0:.3f} {s.y0:.3f}) (end {s.x1:.3f} {s.y1:.3f}) (width {s.width:.3f}) (layer "{lname}") (net {nid}))')

    # Vias
    for v in vias:
        nid = net_id[v.net]
        out.append(f'  (via (at {v.x:.3f} {v.y:.3f}) (size {v.dia:.3f}) (drill {v.drill:.3f}) (layers "F.Cu" "B.Cu") (net {nid}))')

    out.append(')')
    pcb_path = os.path.join(OUT_DIR, "agy_tv_carrier.kicad_pcb")
    with open(pcb_path, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")


# ------------------------------------------------------------------ BOM CSV

def write_bom(comps):
    grouped = defaultdict(list)
    meta = {}
    for c in comps:
        if c.ref in ("LOGO", "H1", "H2", "H3", "H4"):
            continue
        key = (c.value, c.footprint, c.lcsc, c.desc)
        grouped[key].append(c.ref)
        meta[key] = (c.value, c.footprint, c.lcsc, c.desc)

    bom_path = os.path.join(OUT_DIR, "bom.csv")
    with open(bom_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Designator", "Qty", "Value", "Footprint", "LCSC_Ref", "Description"])
        for key, refs in sorted(grouped.items(), key=lambda kv: kv[1][0]):
            val, fp, lcsc, desc = meta[key]
            w.writerow([",".join(refs), len(refs), val, fp, lcsc, desc])


# ------------------------------------------------------------------ Pure-Python PNG visual renderer (for PCB & Schematic)

def write_png(path, w, h, buf):
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    raw = bytearray()
    stride = w * 3
    for y in range(h):
        raw.append(0)
        raw.extend(buf[y * stride:(y + 1) * stride])
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + chunk(b"IEND", b""))


class Canvas:
    def __init__(self, w, h, bg):
        self.w, self.h = w, h
        self.buf = bytearray(bg * (w * h))

    def px(self, x, y, col):
        if 0 <= x < self.w and 0 <= y < self.h:
            i = (y * self.w + x) * 3
            self.buf[i:i + 3] = col

    def disc(self, cx, cy, r, col):
        ix0, ix1 = max(0, int(cx - r - 1)), min(self.w - 1, int(cx + r + 1))
        iy0, iy1 = max(0, int(cy - r - 1)), min(self.h - 1, int(cy + r + 1))
        r2 = r * r
        for y in range(iy0, iy1 + 1):
            dy2 = (y - cy) ** 2
            for x in range(ix0, ix1 + 1):
                if (x - cx) ** 2 + dy2 <= r2:
                    self.px(x, y, col)

    def rect_fill(self, x0, y0, x1, y1, col):
        ix0, ix1 = max(0, int(min(x0, x1))), min(self.w - 1, int(max(x0, x1)))
        iy0, iy1 = max(0, int(min(y0, y1))), min(self.h - 1, int(max(y0, y1)))
        for y in range(iy0, iy1 + 1):
            for x in range(ix0, ix1 + 1):
                self.px(x, y, col)

    def line(self, x0, y0, x1, y1, width, col):
        dist = math.hypot(x1 - x0, y1 - y0)
        steps = max(1, int(dist * 1.5))
        r = max(0.5, width / 2.0)
        for k in range(steps + 1):
            t = k / steps
            self.disc(x0 + (x1 - x0) * t, y0 + (y1 - y0) * t, r, col)

    def text(self, cx, cy, txt, size_px, col, thickness=1.2):
        for x0, y0, x1, y1 in text_to_strokes(cx, cy, txt, size_px):
            self.line(x0, y0, x1, y1, thickness, col)


def render_pcb_png(path, comps, segments, vias, side="top"):
    scale = 12.0  # 12 px / mm -> 1152 x 912 px crisp render
    pad_px = 30
    w = int(BOARD_W * scale) + 2 * pad_px
    h = int(BOARD_H * scale) + 2 * pad_px
    cv = Canvas(w, h, bytes([18, 22, 28]))

    def to_px(x, y):
        if side == "bottom":
            x = BOARD_W - x
        return pad_px + x * scale, pad_px + y * scale

    # Solder mask green substrate
    cv.rect_fill(pad_px, pad_px, pad_px + BOARD_W * scale, pad_px + BOARD_H * scale, bytes([16, 74, 42]))

    # Copper traces: draw far layer dimmed first, then near layer bright
    near_lyr = 0 if side == "top" else 1
    far_lyr = 1 - near_lyr
    col_far = bytes([28, 108, 66])
    col_near = bytes([220, 68, 62]) if side == "top" else bytes([62, 136, 226])

    for s in segments:
        if s.layer == far_lyr:
            x0, y0 = to_px(s.x0, s.y0)
            x1, y1 = to_px(s.x1, s.y1)
            cv.line(x0, y0, x1, y1, s.width * scale, col_far)
    for s in segments:
        if s.layer == near_lyr:
            x0, y0 = to_px(s.x0, s.y0)
            x1, y1 = to_px(s.x1, s.y1)
            cv.line(x0, y0, x1, y1, s.width * scale, col_near)

    # Vias
    for v in vias:
        vx, vy = to_px(v.x, v.y)
        cv.disc(vx, vy, v.dia * scale / 2, bytes([212, 184, 96]))
        cv.disc(vx, vy, v.drill * scale / 2, bytes([18, 22, 28]))

    # Pads (gold/HASL ring + dark drill)
    for c in comps:
        for p in c.pads:
            px, py = to_px(p.x, p.y)
            r = p.dia * scale / 2
            if p.shape == "rect":
                cv.rect_fill(px - r, py - r, px + r, py + r, bytes([228, 198, 102]))
            else:
                cv.disc(px, py, r, bytes([228, 198, 102]))
            if p.drill > 0:
                cv.disc(px, py, p.drill * scale / 2, bytes([18, 22, 28]))

    # Silkscreen (top side)
    if side == "top":
        silk = bytes([242, 245, 248])
        for c in comps:
            for x0, y0, x1, y1 in c.silk_rects:
                p0, p1 = to_px(x0, y0), to_px(x1, y1)
                cv.line(p0[0], p0[1], p1[0], p0[1], 1.5, silk)
                cv.line(p1[0], p0[1], p1[0], p1[1], 1.5, silk)
                cv.line(p1[0], p1[1], p0[0], p1[1], 1.5, silk)
                cv.line(p0[0], p1[1], p0[0], p0[1], 1.5, silk)
            for x0, y0, x1, y1 in c.silk_lines:
                p0, p1 = to_px(x0, y0), to_px(x1, y1)
                cv.line(p0[0], p0[1], p1[0], p1[1], 1.5, silk)
            for cx, cy, r in c.silk_circles:
                segs = 28
                for k in range(segs):
                    a0 = 2 * math.pi * k / segs
                    a1 = 2 * math.pi * (k + 1) / segs
                    p0 = to_px(cx + r * math.cos(a0), cy + r * math.sin(a0))
                    p1 = to_px(cx + r * math.cos(a1), cy + r * math.sin(a1))
                    cv.line(p0[0], p0[1], p1[0], p1[1], 1.5, silk)
            for tx, ty, txt, sz in c.silk_texts:
                pt = to_px(tx, ty)
                cv.text(pt[0], pt[1], txt, sz * scale, silk, thickness=1.3)

    # Board outline border
    edge = bytes([255, 220, 60])
    cv.line(pad_px, pad_px, pad_px + BOARD_W * scale, pad_px, 2, edge)
    cv.line(pad_px + BOARD_W * scale, pad_px, pad_px + BOARD_W * scale, pad_px + BOARD_H * scale, 2, edge)
    cv.line(pad_px + BOARD_W * scale, pad_px + BOARD_H * scale, pad_px, pad_px + BOARD_H * scale, 2, edge)
    cv.line(pad_px, pad_px + BOARD_H * scale, pad_px, pad_px, 2, edge)

    write_png(path, w, h, cv.buf)


def render_schematic_png(path):
    """Draw a clean, publication-grade reference schematic diagram PNG with zero overlap."""
    w, h = 1680, 1080
    cv = Canvas(w, h, bytes([250, 250, 246]))
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

    # Frame + title block
    cv.line(20, 20, w - 20, 20, 2, blk)
    cv.line(w - 20, 20, w - 20, h - 20, 2, blk)
    cv.line(w - 20, h - 20, 20, h - 20, 2, blk)
    cv.line(20, h - 20, 20, 20, 2, blk)
    cv.text(w / 2, 48, "AGY TV CARRIER BOARD V1.0 - COMPLETE REFERENCE SCHEMATIC", 17, blk, 2.0)
    cv.text(w / 2, 74, "ESP32-C3 SUPERMINI | 1.54 ST7789 8P | EC11 ENCODER | 5-KEY ADC | BUZZER | DS3231 RTC | 96X76MM 2-LAYER", 10, dim, 1.3)

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

    # Row 1: U1 ESP32-C3 SuperMini | J_LCD ST7789 | J_RTC + J_I2C
    draw_box(180, 115, 220, 280, "U1 ESP32-C3 SUPERMINI",
             [("G5", "LCD_MOSI"), ("G6", "LCD_RST"), ("G7", "EC11_A"), ("G8", "I2C_SCL"),
              ("G9", "EC11_SW"), ("G10", "BUZ_PWM"), ("G20", "UART_RX"), ("G21", "UART_TX")],
             [("5V", "VBUS_5V"), ("GND", "GND"), ("3V3", "3V3"), ("G4", "I2C_SDA"),
              ("G3", "LCD_SCLK"), ("G2", "LCD_DC"), ("G1", "LCD_BL"), ("G0", "ADC_KEY")],
             notes=("C1 10UF + C2 0.1UF ON 3V3-GND",))

    draw_box(740, 115, 230, 280, "J_LCD ST7789 1.54 8P",
             [("1 GND", "GND"), ("2 VCC", "3V3"), ("3 SCL", "LCD_SCLK"), ("4 SDA", "LCD_MOSI"),
              ("5 RES", "LCD_RST"), ("6 DC", "LCD_DC"), ("7 CS", "GND"), ("8 BLK", "LCD_BL")],
             [],
             notes=("C3 0.1UF AT PIN1-2 | SPI MODE 3",))

    draw_box(1220, 115, 280, 165, "J_RTC DS3231 + J_I2C",
             [("1 GND", "GND"), ("2 VCC", "3V3"), ("3 SDA", "I2C_SDA"), ("4 SCL", "I2C_SCL")],
             [],
             notes=("R_SDA 4.7K + R_SCL 4.7K TO 3V3", "DNP IF DS3231 HAS PULLUPS"))

    # Row 2: Passive Buzzer Driver | EC11 Rotary Encoder | J_LA Scope/LA Header
    draw_box(180, 465, 270, 210, "BUZZER DRIVER (LEDC1)",
             [("IN", "BUZ_PWM"), ("BASE", "R_B1 1K"), ("PD", "R_B2 10K")],
             [("Q1", "S8050 E-B-C"), ("D1", "1N4148 K-3V3"), ("BZ1", "12MM 3V3")],
             notes=("3V3 -> BZ1(+) / D1(K)", "BZ1(-) / D1(A) -> Q1(C) -> Q1(E) GND"))

    draw_box(760, 465, 260, 210, "EC11 ENCODER + RC",
             [("A", "EC11_A (G7)"), ("C", "GND"), ("B", "JP_B -> G20"), ("S1", "EC11_SW (G9)"), ("S2", "GND")],
             [("R_PU", "10K X2"), ("R_SER", "1K X3"), ("C_AB", "10NF X2"), ("C_SW", "100NF"), ("MP1/2", "GND")],
             notes=("SHORT JP_B FOR NORMAL EC11_B USE",))

    draw_box(1260, 360, 260, 225, "J_LA SCOPE / LA 6P",
             [("1 GND", "GND"), ("2 CLK", "LCD_SCLK"), ("3 DAT", "LCD_MOSI"),
              ("4 DC", "LCD_DC"), ("5 RST", "LCD_RST"), ("6 BL", "LCD_BL")],
             [],
             notes=("CLIP SCOPE OR 24MHZ LA HERE",))

    # Row 3: 5-Key ADC Keypad | J_UART USB-TTL Debug
    draw_box(240, 760, 760, 210, "5-KEY ADC RESISTOR-LADDER KEYPAD (GPIO0 / ADC1_CH0)",
             [("TOP PU", "R_AD0 10K -> 3V3"), ("FILTER", "C_AD 100NF -> GND"),
              ("SW1 BACK", "0 OHM -> 0.00V"), ("SW2 UP", "R_AD1 2.2K -> 0.60V")],
             [("SW3 OK", "R_AD2 5.6K -> 1.18V"), ("SW4 DOWN", "R_AD3 12K -> 1.80V"),
              ("SW5 MUTE", "R_AD4 33K -> 2.53V"), ("IDLE", "NO PRESS -> 3.30V")],
             notes=("USE 1% RESISTORS + ADC_ATTEN_DB_12 (WIFI SAFE ON ADC1)",))

    draw_box(1260, 680, 260, 140, "J_UART USB-TTL 3.3V",
             [("1 GND", "GND"), ("2 TX", "UART_TX (G21)"), ("3 RX", "UART_RX (G20)")],
             [],
             notes=("3.3V LOGIC ONLY! NEVER 5V", "OPEN JP_B IF USING UART RX"))

    write_png(path, w, h, cv.buf)


# ------------------------------------------------------------------ main

def main():
    os.makedirs(GERBER_DIR, exist_ok=True)
    comps = build_board()
    print(f"Built {len(comps)} components on {BOARD_W}x{BOARD_H}mm board")

    segments, vias = route_board(comps)
    print(f"Routed {len(segments)} copper segments and {len(vias)} vias")

    errors = verify_drc_and_connectivity(comps, segments, vias)
    if errors:
        print(f"DRC / CONNECTIVITY FAILED ({len(errors)} errors):")
        for e in errors[:25]:
            print("  !", e)
        sys.exit(1)
    print("DRC & Netlist Connectivity: 0 errors, 0 opens, 0 shorts! (min clearance >= 0.20mm)")

    # Write Gerbers + Drill
    files = {
        "agy_tv_carrier-F_Cu.gtl": lambda p: write_gerber_copper(p, 0, comps, segments, vias),
        "agy_tv_carrier-B_Cu.gbl": lambda p: write_gerber_copper(p, 1, comps, segments, vias),
        "agy_tv_carrier-F_Mask.gts": lambda p: write_gerber_mask(p, comps),
        "agy_tv_carrier-B_Mask.gbs": lambda p: write_gerber_mask(p, comps),
        "agy_tv_carrier-F_SilkS.gto": lambda p: write_gerber_silk(p, comps, empty=False),
        "agy_tv_carrier-B_SilkS.gbo": lambda p: write_gerber_silk(p, comps, empty=True),
        "agy_tv_carrier-Edge_Cuts.gm1": lambda p: write_gerber_outline(p),
        "agy_tv_carrier-PTH.drl": lambda p: write_excellon_drill(p, comps, vias),
    }
    zip_path = os.path.join(OUT_DIR, "agy_tv_carrier_jlcpcb_gerber.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for fname, writer in files.items():
            fpath = os.path.join(GERBER_DIR, fname)
            writer(fpath)
            zf.write(fpath, arcname=fname)

    write_kicad_files(comps, segments, vias)
    write_bom(comps)
    render_pcb_png(os.path.join(OUT_DIR, "pcb_top.png"), comps, segments, vias, side="top")
    render_pcb_png(os.path.join(OUT_DIR, "pcb_bottom.png"), comps, segments, vias, side="bottom")
    render_schematic_png(os.path.join(OUT_DIR, "schematic.png"))
    print(f"Wrote JLCPCB Gerber ZIP: {zip_path}")
    print(f"Wrote KiCad PCB, BOM, and PNG renders to {OUT_DIR}")


if __name__ == "__main__":
    main()
