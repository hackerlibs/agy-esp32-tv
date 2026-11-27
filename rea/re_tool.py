#!/usr/bin/env python3
"""Scratch reverse-engineering helper for the backed-up "xiao-dianshi" firmware.

Linear-sweep disassembly of the IROM text segment with capstone, plus a few
helpers to resolve call targets and print windows of code.
"""
import pickle
import re
import struct
import sys

from capstone import Cs, CS_ARCH_RISCV, CS_MODE_RISCV32, CS_MODE_RISCVC

BIN = '/Users/xlisp/CppPro/esp32_tv/backup/esp32c3_original_4MB.bin'
data = open(BIN, 'rb').read()
segments = pickle.load(open('/tmp/fw_segments.pkl', 'rb'))

IROM_LA, IROM_LEN, IROM_OFF = segments[2]
IRAM_LA, IRAM_LEN, IRAM_OFF = segments[4]

md = Cs(CS_ARCH_RISCV, CS_MODE_RISCV32 | CS_MODE_RISCVC)
md.detail = True

_cache = {}


def disasm_range(la, blob):
    """Linear sweep that survives embedded data: skip 2 bytes on an undecodable spot."""
    if la in _cache:
        return _cache[la]
    out = []
    pos = 0
    while pos < len(blob):
        progressed = False
        for ins in md.disasm(blob[pos:], la + pos):
            out.append((ins.address, ins.size, ins.mnemonic, ins.op_str))
            pos += ins.size
            progressed = True
        if not progressed:
            out.append((la + pos, 2, '.half', '0x%04x' % struct.unpack_from('<H', blob, pos)[0]))
            pos += 2
    _cache[la] = out
    return out


def irom():
    return disasm_range(IROM_LA, data[IROM_OFF:IROM_OFF + IROM_LEN])


def iram():
    return disasm_range(IRAM_LA, data[IRAM_OFF:IRAM_OFF + IRAM_LEN])


def call_target(ins_list, i):
    """Resolve the target of the call at index i, or None if it is not a call."""
    addr, size, mn, ops = ins_list[i]
    if mn in ('jal', 'c.jal'):
        m = re.search(r'(0x[0-9a-f]+)$', ops)
        if m and ops.startswith('ra,'):
            return int(m.group(1), 16)
        return None
    if mn == 'jalr':
        # call pseudo: auipc ra, hi ; jalr ra, lo(ra)
        if i > 0 and ins_list[i - 1][2] == 'auipc' and ins_list[i - 1][3].startswith('ra,'):
            auipc_addr, _, _, au = ins_list[i - 1]
            hi = int(au.split(',')[1], 0)
            m = re.search(r'(-?0x[0-9a-f]+|-?\d+)\(ra\)', ops)
            if m and ops.startswith('ra,'):
                return (auipc_addr + (hi << 12) + int(m.group(1), 0)) & 0xFFFFFFFF
    return None


def imm_li(ins):
    """If ins is `li rd, imm` / `addi rd, zero, imm` / `c.li rd, imm`, return (rd, imm)."""
    _, _, mn, ops = ins
    if mn == 'li' or mn == 'c.li':
        m = re.match(r'(\w+),\s*(-?0x[0-9a-f]+|-?\d+)$', ops)
        if m:
            return m.group(1), int(m.group(2), 0)
    if mn == 'addi':
        m = re.match(r'(\w+),\s*zero,\s*(-?0x[0-9a-f]+|-?\d+)$', ops)
        if m:
            return m.group(1), int(m.group(2), 0)
    return None


def show(ins_list, i0, i1, annotate=True):
    for j in range(max(0, i0), min(len(ins_list), i1)):
        addr, size, mn, ops = ins_list[j]
        note = ''
        t = call_target(ins_list, j)
        if t is not None:
            note = '   ; -> 0x%08x' % t
        print('  %08x: %-8s %-28s%s' % (addr, mn, ops, note))


def index_by_addr(ins_list):
    return {a: k for k, (a, _, _, _) in enumerate(ins_list)}


if __name__ == '__main__':
    print('IROM 0x%08x len 0x%x' % (IROM_LA, IROM_LEN))
    print('IRAM 0x%08x len 0x%x' % (IRAM_LA, IRAM_LEN))
