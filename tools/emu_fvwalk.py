#!/usr/bin/env python3
"""Runs the firmware's own file-list walkers under Unicorn on every state a flash write passes
through, to find write orders where a power cut at any moment leaves a chip that boots.

    venv/bin/python -I tools/emu_fvwalk.py of1_efi_o2.img of2_efi_cisco.img [OF2 IMAGE ...]
    venv/bin/python -I tools/emu_fvwalk.py --walk IMAGE        # just list what each walker sees

Two walkers read the main firmware volume's file list (FV_MAIN, 0x10000-0xCFFFF):
- the PEI core's FfsFindNextFile (boot block, run in place at 0xFFF00000 + offset, found through
  its 'PEI SERV' table), which the PEI dispatcher and the DXE loader use;
- the DXE core's FvCheck (EDK1 FwVol.c), which builds the list the DXE dispatcher loads drivers
  from. Its loop (0x10011E10-0x10011EF4 in every OF1 and OF2 core seen) is run on a copy of the
  volume less its header, as the core's own cache is; AllocatePool is the only call out, stubbed.

What they do with a file header, decoded from both cores (State is inverted: erase polarity 1;
"state" below = the highest bit set in ~State):
- 24 bytes of FF: the list ends there (DXE: success; PEI: no more files).
- DATA_VALID (0x04) / MARKED_FOR_UPDATE (0x08) with a good header checksum: a file. DXE doesn't
  check the data checksum.
- DELETED (0x10) with a good header checksum: skipped by its size.
- HEADER_INVALID (0x20), or HEADER_CONSTRUCTION (0x01) for DXE: skipped by 24 bytes only.
- anything else: PEI stops (harmless); DXE fails the whole volume with VOLUME_CORRUPTED, so the
  dispatcher has nothing and the unit doesn't boot. A header with some bytes written and State
  still FF is "anything else": that is what stopped a test unit on 2026-11-16.

A write plan is a list of steps: ('erase', block), or ('prog', offset, value) where value may only
clear bits. Every state is checked: after each step, and for a byte the walkers read or a listed
file contains, every value it can hold part-way through programming (any mix of the bits being
cleared). A state passes if the DXE walk succeeds and both walkers list the files of one of the
plan's good configurations (before, after, or the stock files alone), our spacer files aside. An
erase passes only if nothing in the block is read or listed: part-erased bits are unknown.

The built-in plans:
- install: stock -> stock + spacer + block 10 (OF2-style, or on an OF1): block 10 first, then the
  spacer's header. Order 'state-first' (State FE, the header, FA, F8) and the old 'header-first'.
- update: a block-10 release to the next one. 'erase-in-place' (the current of-firmware-update
  order: erase block 10, bodies, driver header, marker header) and 'gate' (close the spacer, F8 ->
  D8 so it reads as HEADER_INVALID and the walk stops in its own FF data; erase and write block
  10 behind it; open a new spacer header 24 bytes on, State first).
"""
import argparse
import os
import struct
import sys
import uuid

import pefile
from unicorn import Uc, UC_ARCH_X86, UC_MODE_32, UC_HOOK_MEM_READ, UC_HOOK_CODE, UC_HOOK_MEM_INVALID
from unicorn.x86_const import (UC_X86_REG_ESP, UC_X86_REG_EAX, UC_X86_REG_EBX, UC_X86_REG_ESI, UC_X86_REG_EDI,
                               UC_X86_REG_EBP, UC_X86_REG_EIP, UC_X86_REG_ECX, UC_X86_REG_EDX)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_patched as bp  # noqa: E402

FV_MAIN, FV_END, FLASH = 0x10000, 0xD0000, 0xFFF00000
BLOCK = 0x10000
SPACER = uuid.UUID(bp.SPACER_GUID).bytes_le
DXE_LOOP = bytes.fromhex('f645ed087406c6464401eb04c6464400')   # FvCheck: polarity from the attributes
DXE_END = bytes.fromhex('f645fb80')                            # test byte [ebp-5], 0x80: after the loop
STACK, STOP, CACHE, HEAP, DEV = 0x00800000, 0x00900000, 0x20000000, 0x30000000, 0x00A00000


def fail(msg):
    sys.exit('FAIL: ' + msg)


class Walkers:
    """Both walkers of one firmware image, kept loaded; chip bytes are updated in place."""

    def __init__(self, img):
        img = bytes(img)
        # PEI core: FfsFindNextFile from the PEI services table, run in place.
        s = img.find(b'PEI SERV', 0xE0000)
        if s < 0:
            fail('no PEI services table')
        self.pei_find = struct.unpack_from('<I', img, s + 24 + 9 * 4)[0]
        # DXE core: decompressed, at its own base.
        core = [(o, g, n) for o, g, n in bp.fv_files(img)[0] if img[o + 18] == 0x05]
        stream, _ = bp.decompress_file(bytearray(img), core[0][0], core[0][2])
        pe = pefile.PE(data=bytes(stream[bp.pe_offset(stream):]))
        mm = pe.get_memory_mapped_image()
        base = pe.OPTIONAL_HEADER.ImageBase
        i = mm.find(DXE_LOOP)
        if i < 0 or mm[i + 0xE4:i + 0xE8] != DXE_END:
            fail('FvCheck loop not found in the DXE core')
        self.dxe_loop, self.dxe_end = base + i, base + i + 0xE4
        call = base + i + 0xA7                                         # call AllocatePool (push 0x14 before it)
        if mm[i + 0xA5:i + 0xA8] != b'\x6a\x14\xe8':
            fail('FvCheck AllocatePool call not where expected')
        self.dxe_alloc = call + 5 + struct.unpack_from('<i', mm, i + 0xA8)[0]
        hlen = struct.unpack_from('<H', img, FV_MAIN + 0x30)[0]
        self.hlen, self.attrs = hlen, struct.unpack_from('<I', img, FV_MAIN + 0x2C)[0]

        uc = Uc(UC_ARCH_X86, UC_MODE_32)
        uc.mem_map(FLASH, 0x100000)
        uc.mem_map(base, (len(mm) + 0xFFF) & ~0xFFF)
        uc.mem_write(base, mm)
        for a, n in ((STACK - 0x10000, 0x20000), (STOP, 0x1000), (CACHE, 0x100000), (HEAP, 0x100000), (DEV, 0x1000)):
            uc.mem_map(a, n)
        self.uc = uc
        self.reads = set()
        uc.hook_add(UC_HOOK_MEM_READ, self._read, begin=FLASH + FV_MAIN, end=FLASH + FV_END - 1)
        uc.hook_add(UC_HOOK_MEM_READ, self._read, begin=CACHE, end=CACHE + 0x100000 - 1)
        uc.hook_add(UC_HOOK_CODE, self._alloc, begin=self.dxe_alloc, end=self.dxe_alloc)
        self.bad = []
        uc.hook_add(UC_HOOK_MEM_INVALID, lambda u, acc, addr, sz, v, _: self.bad.append(addr) or False)
        self.chip = bytearray(img)
        uc.mem_write(FLASH, bytes(self.chip))
        uc.mem_write(CACHE, bytes(self.chip[FV_MAIN + hlen:FV_END]))

    def _read(self, uc, acc, addr, size, value, _):
        for a in range(addr, addr + size):
            if CACHE <= a < CACHE + 0x100000:
                self.reads.add(FV_MAIN + self.hlen + a - CACHE)
            else:
                self.reads.add(a - FLASH)

    def _alloc(self, uc, addr, size, _):
        p = self.heap
        self.heap += 0x20
        uc.mem_write(p, b'\0' * 0x20)
        ret = struct.unpack('<I', uc.mem_read(uc.reg_read(UC_X86_REG_ESP), 4))[0]
        uc.reg_write(UC_X86_REG_ESP, uc.reg_read(UC_X86_REG_ESP) + 4)
        uc.reg_write(UC_X86_REG_EAX, p)
        uc.reg_write(UC_X86_REG_EIP, ret)

    def set(self, off, value):
        self.chip[off] = value
        self.uc.mem_write(FLASH + off, bytes([value]))
        if FV_MAIN + self.hlen <= off < FV_END:
            self.uc.mem_write(CACHE + off - FV_MAIN - self.hlen, bytes([value]))

    def load(self, img):
        for o in range(FV_MAIN, FV_END):
            if self.chip[o] != img[o]:
                self.set(o, img[o])

    def pei(self):
        """Offsets of the files PEI's FfsFindNextFile returns, in order."""
        uc, out = self.uc, []
        uc.mem_write(DEV, b'\0' * 4)
        for _ in range(400):
            sp = STACK - 0x100
            uc.mem_write(sp, struct.pack('<5I', STOP, DEV + 0x100, 0, FLASH + FV_MAIN, DEV))
            uc.reg_write(UC_X86_REG_ESP, sp)
            uc.emu_start(self.pei_find, STOP, count=2_000_000)
            if uc.reg_read(UC_X86_REG_EAX):
                return out
            out.append(struct.unpack('<I', uc.mem_read(DEV, 4))[0] - FLASH)
        fail('PEI walk does not end')

    def dxe(self):
        """(status, offsets of the files FvCheck lists)."""
        uc = self.uc
        self.heap = HEAP
        ebp = STACK - 0x200
        uc.mem_write(ebp - 0x40, b'\0' * 0x60)
        uc.mem_write(ebp - 0x14, struct.pack('<I', self.attrs))
        uc.mem_write(DEV, b'\0' * 0x100)
        uc.mem_write(DEV + 0x30, struct.pack('<II', CACHE, CACHE + FV_END - FV_MAIN - self.hlen))
        for r, v in ((UC_X86_REG_EBP, ebp), (UC_X86_REG_ESP, ebp - 0x40), (UC_X86_REG_ESI, DEV),
                     (UC_X86_REG_EDI, 0), (UC_X86_REG_EBX, 0), (UC_X86_REG_ECX, 0), (UC_X86_REG_EDX, 0)):
            uc.reg_write(r, v)
        uc.emu_start(self.dxe_loop, self.dxe_end, count=5_000_000)
        if uc.reg_read(UC_X86_REG_EIP) != self.dxe_end:
            fail('DXE walk does not end')
        status = struct.unpack('<I', uc.mem_read(ebp - 8, 4))[0]
        files, head = [], DEV + 0x3C
        e = struct.unpack('<I', uc.mem_read(head, 4))[0]
        while e != head and len(files) < 400:
            ffs = struct.unpack('<I', uc.mem_read(e + 8, 4))[0]
            files.append(FV_MAIN + self.hlen + ffs - CACHE)
            e = struct.unpack('<I', uc.mem_read(e, 4))[0]
        return status, files

    def walk(self):
        self.reads, self.bad = set(), []
        p = self.pei()
        s, d = self.dxe()
        if self.bad:
            fail('walker touched unmapped memory at %s' % ', '.join(hex(a) for a in self.bad[:4]))
        return p, s, d


def size(img, o):
    return int.from_bytes(img[o + 20:o + 23], 'little')


def config(img, offs):
    """What a list of files means to the firmware: each file's bytes but its State, our spacers
    left out (they hold nothing; a closed gate and its successor differ only in place)."""
    out = []
    for o in offs:
        if bytes(img[o:o + 16]) == SPACER:
            continue
        b = bytearray(img[o:o + size(img, o)])
        b[23] = 0
        out.append(bytes(b))
    return tuple(out)


def extents(img, offs):
    s = set()
    for o in offs:
        s.update(range(o, o + max(24, size(img, o))))
    return s


class Plan:
    def __init__(self, name, start, steps, goods):
        self.name, self.start, self.steps, self.goods = name, bytearray(start), steps, goods


def check_plan(w, plan, expect_ok=True, quiet=False):
    """Walk every state of a plan. Returns the list of bad states (empty if all pass)."""
    w.load(plan.start)
    goods = {}
    for label, img in plan.goods:
        goods[config(img, Walkers.listing(w, img))] = label
    bad, states = [], 0
    chip = bytearray(plan.start)

    def judge(where):
        nonlocal states
        states += 1
        p, s, d = w.walk()
        if py_walk(w.chip) != (s == 0, d):
            fail('the Python walk disagrees with the DXE core at %s' % where)
        if s:
            return 'DXE VOLUME_CORRUPTED (0x%x)' % s if s == 0x8000000A else 'DXE status 0x%x' % s
        cd, cp = config(w.chip, d), config(w.chip, p)
        if cd not in goods:
            return 'DXE lists a mix of files (%d)' % len(d)
        # PEI may stop early (it stops at a header DXE skips), but only after the stock files.
        if cp != cd and cp not in goods:
            return 'PEI lists %d files, not a good set' % len(p)
        return None

    def relevant():
        p, s, d = w.walk()
        return w.reads | extents(w.chip, d) | extents(w.chip, p)

    r = judge('start')
    if r:
        bad.append(('start', r))
    rel = relevant()
    for n, st in enumerate(plan.steps):
        if st[0] == 'erase':
            b = st[1]
            hit = [o for o in range(b * BLOCK, (b + 1) * BLOCK) if o in rel]
            if hit:
                bad.append(('step %d erase block %d' % (n, b), 'block is read by the walkers (from 0x%x): '
                            'a part-erased block could hold anything' % hit[0]))
            for o in range(b * BLOCK, (b + 1) * BLOCK):
                if chip[o] != 0xFF:
                    chip[o] = 0xFF
                    w.set(o, 0xFF)
        else:
            _, o, v = st
            cur = chip[o]
            if v & ~cur & 0xFF:
                fail('%s step %d: 0x%x %02x -> %02x needs an erase' % (plan.name, n, o, cur, v))
            if v == cur:
                continue
            if o in rel:
                clear = cur & ~v
                sub = clear
                while True:                     # every partial value: some of the clearing bits cleared
                    sub = (sub - 1) & clear
                    if sub == 0:
                        break
                    w.set(o, cur & ~sub)
                    r = judge('step %d part' % n)
                    if r:
                        bad.append(('step %d: 0x%x part-way %02x -> %02x (reads %02x)' % (n, o, cur, v, cur & ~sub), r))
            chip[o] = v
            w.set(o, v)
            if o in rel:
                r = judge('step %d' % n)
                if r:
                    bad.append(('step %d: 0x%x = %02x' % (n, o, v), r))
                rel = relevant()
            continue
        r = judge('after step %d' % n)
        if r:
            bad.append(('after step %d' % n, r))
        rel = relevant()
    if not quiet:
        verdict = 'all pass' if not bad else '%d BAD' % len(bad)
        print('  %-58s %6d states walked: %s' % (plan.name, states, verdict))
        for where, why in bad[:6]:
            print('      %s: %s' % (where, why))
        if len(bad) > 6:
            print('      ... %d more' % (len(bad) - 6))
    if expect_ok:
        return bad
    if not bad:
        print('      (this order should fail: the check missed it)')
        return [('expected failure', 'not found')]
    return []


def _listing(w, img):
    save = bytearray(w.chip)
    w.load(img)
    p, s, d = w.walk()
    w.load(save)
    if s:
        fail('a good configuration does not walk (0x%x)' % s)
    return d


Walkers.listing = _listing


def files_in(img, lo, hi):
    files, _ = bp.fv_files(bytearray(img))
    return [(o, n) for o, g, n in files if lo <= o < hi]


def py_walk(img):
    """The DXE rules above in Python: (ok, listed offsets). Checked against the real walk."""
    hlen = struct.unpack_from('<H', img, FV_MAIN + 0x30)[0]
    o, out = FV_MAIN + hlen, []
    while o < FV_END:
        h = img[o:o + 24]
        if h == b'\xff' * min(24, FV_END - o):
            return True, out
        st = (~h[23]) & 0xFF
        top = 0x80
        while top and not st & top:
            top >>= 1
        hdr_ok = top in (2, 4, 8, 0x10) and (sum(h) - h[17] - h[23]) & 0xFF == 0
        if not hdr_ok:
            if top in (0x20, 0x01):
                o += 24
                continue
            return False, out
        if top == 2:
            return False, out
        if top != 0x10:
            out.append(o)
        o = (o + size(img, o) + 7) & ~7
    return True, out


def spacer_of(img):
    """The open spacer: the last one the walk lists."""
    ok, offs = py_walk(img)
    s = [o for o in offs if bytes(img[o:o + 16]) == SPACER]
    return s[-1] if s else None


def prog(steps, img, offs):
    for o in offs:
        steps.append(('prog', o, img[o]))


def header_state_first(steps, img, o, extra=()):
    """FE (construction: DXE skips 24 bytes, into FF data), the header, then FA, then F8."""
    steps.append(('prog', o + 23, 0xFE))
    prog(steps, img, list(range(o, o + 23)) + list(extra))
    steps.append(('prog', o + 23, 0xFA))
    steps.append(('prog', o + 23, img[o + 23]))


def stock_files(img, spacer):
    return bytes(img[:spacer]) + b'\xff' * (FV_END - spacer) + bytes(img[FV_END:])


def install_plans(stock, new):
    """stock -> new (= stock + spacer + block 10, nothing of stock's changed)."""
    sp = spacer_of(new)
    body = [o for o in range(0xA0000, FV_END) if new[o] != stock[o]]
    goods = [('stock', stock), ('new', new)]
    plans = []
    # block 10 whole (headers included: unreachable behind the spacer's blank header), then the spacer
    s = []
    prog(s, new, body)
    header_state_first(s, new, sp)
    plans.append((Plan('install, spacer state-first', stock, s, goods), True))
    s = []
    prog(s, new, body)
    prog(s, new, range(sp, sp + 24))
    plans.append((Plan('install, spacer header-first (hardware 2026-11-16)', stock, s, goods), False))
    return plans


def gate_target(old, new):
    """old's chip with its spacer closed (F8 -> D8) and a new spacer header 24 bytes on, reaching
    0xA0000, and new's block 10."""
    g = spacer_of(old)
    t = bytearray(old)
    t[0xA0000:0xB0000] = new[0xA0000:0xB0000]
    t[g + 23] = 0xD8
    n = g + 24
    if set(old[n:n + 48]) != {0xFF}:
        fail('no FF room in the spacer for a new header at 0x%x' % n)
    sz = 0xA0000 - n
    t[n:n + 24] = SPACER + b'\0\0' + bytes([0x01, 0x00]) + sz.to_bytes(3, 'little') + b'\xf8'
    bp.fix_checksums(t, n, sz)
    return t, g, n


def update_plans(old, new):
    goods_new = bytearray(old)
    goods_new[0xA0000:0xB0000] = new[0xA0000:0xB0000]
    sp = spacer_of(old)
    stock = stock_files(old, sp)
    plans = []
    # The current order: erase block 10; bodies (headers blank); driver header; marker header, State last.
    s = [('erase', 10)]
    hdrs = [o for o, n in files_in(new, 0xA0000, 0xB0000)]
    hdr_bytes = set(b for o in hdrs for b in range(o, o + 24))
    prog(s, new, [o for o in range(0xA0000, 0xB0000) if o not in hdr_bytes and new[o] != 0xFF])
    for o in reversed(hdrs):
        prog(s, new, range(o, o + 24))
    plans.append((Plan('update, erase-in-place (of-firmware-update today)', old, s,
                       [('old', old), ('new', goods_new), ('stock', stock)]), False))
    # The gate.
    t, g, n = gate_target(old, new)
    s = [('prog', g + 23, 0xD8), ('erase', 10)]
    prog(s, t, [o for o in range(0xA0000, 0xB0000) if t[o] != 0xFF])
    header_state_first(s, t, n)
    plans.append((Plan('update, gate (close spacer, block 10, new spacer)', old, s,
                       [('old', old), ('new', t), ('stock', stock)]), True))
    # And the next update after that, from the gated chip.
    t2, g2, n2 = gate_target(t, old)
    s = [('prog', g2 + 23, 0xD8), ('erase', 10)]
    prog(s, t2, [o for o in range(0xA0000, 0xB0000) if t2[o] != 0xFF])
    header_state_first(s, t2, n2)
    plans.append((Plan('update, gate again (second spacer closed)', t, s,
                       [('old', t), ('new', t2), ('stock', stock)]), True))
    return plans


def driver_file(rel):
    o, n = files_in(rel, 0xA0000, 0xB0000)[-1]
    if rel[o + 18] != 0x07:
        fail('no driver file at the end of block 10')
    return bytearray(rel[o:o + n])


def stock_block10(stock, version, rel):
    """stock + spacer to 0xA0000 + marker + rel's driver: the OF2-style build's layout."""
    img = bytearray(stock)
    files, fv_end = bp.fv_files(img)
    last = files[-1]
    bp.add_spacer(img, last[0] + last[2], bp.BLOCK10)
    bp.add_marker(img, bp.BLOCK10, fv_end, version)
    m = bp.BLOCK10 + size(img, bp.BLOCK10)
    o = (m + 7) & ~7
    d = driver_file(rel)
    img[o:o + len(d)] = d
    bp.check_file(img, o)
    return img


def next_release(rel, version):
    """rel with block 10 rebuilt: a new marker and a driver that differs (one byte, checksums fixed)."""
    img = bytearray(rel)
    d = driver_file(rel)
    d[len(d) // 2] ^= 0x55
    bp.fix_checksums(d, 0, len(d))
    img[0xA0000:0xB0000] = b'\xff' * BLOCK
    bp.add_marker(img, bp.BLOCK10, FV_END, version)
    o = (bp.BLOCK10 + size(img, bp.BLOCK10) + 7) & ~7
    img[o:o + len(d)] = d
    bp.check_file(img, o)
    return img


def fwplan_plans(chip, rel, name):
    """fwplan.py's own plan for chip -> rel, as a Plan for the real walkers."""
    import fwplan
    steps = fwplan.plan(chip, rel)
    goods, after = fwplan.goods_for(chip, rel)
    cur, out = bytearray(chip), []
    for op, blk, data in steps:
        base = blk * BLOCK
        if op == 'fh':
            out.append(('erase', blk))
            cur[base:base + BLOCK] = b'\xff' * BLOCK
        for i in range(BLOCK):
            if cur[base + i] != data[i]:
                out.append(('prog', base + i, data[i]))
                cur[base + i] = data[i]
    return Plan('fwplan: ' + name, chip, out, [('chip', goods[0]), ('after', goods[1]), ('stock', goods[2])]), after


def show(path):
    img = bytearray(open(path, 'rb').read())
    w = Walkers(img)
    p, s, d = w.walk()
    print('%s\n  PEI: %d files, last at 0x%x\n  DXE: status 0x%x, %d files, last at 0x%x' %
          (path, len(p), p[-1] if p else 0, s, len(d), d[-1] if d else 0))
    for o in d[-4:]:
        print('    0x%06x %s type %02x state %02x size 0x%x' % (o, uuid.UUID(bytes_le=bytes(img[o:o + 16])),
                                                            img[o + 18], img[o + 23], size(img, o)))


def ground_truth(w, stock, rel30, new10):
    """What hardware showed, which the walk must agree with before anything else is believed."""
    ok = True

    def expect(name, img, boots, ours):
        nonlocal ok
        w.load(img)
        p, s, d = w.walk()
        has = any(img[o + 18] == 0x07 and o >= 0xA0000 for o in d)
        good = (s == 0) == boots and (not boots or has == ours)
        ok &= good
        print('  %-58s DXE %s, %s  %s' % (name, 'ok' if s == 0 else 'CORRUPTED' if s == 0x8000000A else hex(s),
                                         'our driver' if has else 'no driver of ours', 'as on hardware' if good else 'NOT AS ON HARDWARE'))

    expect('stock O2', stock, True, False)
    expect('26.11.30', rel30, True, True)
    t = bytearray(rel30)
    t[0xA0000:0xB0000] = b'\xff' * BLOCK
    expect('26.11.30, block 10 erased (stock-like on hardware)', t, True, False)
    for o, n in files_in(rel30, 0xA0000, 0xB0000):
        t[o + 24:o + n] = rel30[o + 24:o + n]
    expect('26.11.30 bodies, headers blank (29-dev, stock-like)', t, True, False)
    sp = spacer_of(new10)
    t = bytearray(stock)
    t[0xA0000:0xB0000] = new10[0xA0000:0xB0000]
    expect('stock + block 10, spacer header blank (.131 step 1)', t, True, False)
    t[sp:sp + 12] = new10[sp:sp + 12]
    expect('+ spacer header bytes 0-11, State FF (.131 step 2: hung)', t, False, False)
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--walk', help='only list what the walkers see in this image')
    ap.add_argument('images', nargs='*', help='of1_efi_o2.img, of2_efi_cisco.img, more stock OF2 images')
    a = ap.parse_args()
    if a.walk:
        show(a.walk)
        return
    if len(a.images) < 2:
        ap.error('need of1_efi_o2.img and of2_efi_cisco.img')
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    rel30 = bytearray(open(os.path.join(here, 'releases', 'of1-blw-26.11.30.bin'), 'rb').read())
    o2 = bytearray(open(a.images[0], 'rb').read())
    failures = 0

    print('Ground truth (OF1 cores):')
    w = Walkers(o2)
    new_of1 = stock_block10(o2, 'BirdsLikeWires OpenFrame EFI 26.11.31-dev9 (OF1)', rel30)
    if not ground_truth(w, o2, rel30, new_of1):
        fail('the walkers disagree with hardware')

    print('OF1 (stock O2 cores):')
    plans = install_plans(o2, new_of1)
    for p, ok in plans:
        failures += bool(check_plan(w, p, ok))
    w = Walkers(rel30)
    nxt = nxt_of1 = next_release(rel30, 'BirdsLikeWires OpenFrame EFI 26.11.31-dev9 (OF1)')
    for p, ok in update_plans(rel30, nxt):
        failures += bool(check_plan(w, p, ok))

    for path in a.images[1:]:
        stock = bytearray(open(path, 'rb').read())
        print('%s:' % os.path.basename(path))
        w = Walkers(stock)
        new = stock_block10(stock, 'BirdsLikeWires OpenFrame EFI 26.11.31-dev9 (OF2)', rel30)
        plans = install_plans(stock, new)
        for p, ok in plans:
            failures += bool(check_plan(w, p, ok))
        nxt = bytearray(new)
        nxt[0xA0000:0xB0000] = rel30[0xA0000:0xB0000]
        for p, ok in update_plans(new, nxt):
            failures += bool(check_plan(w, p, ok))
    print('fwplan.py\'s plans on the real walkers:')
    for path in a.images:
        stock = bytes(open(path, 'rb').read())
        w = Walkers(stock)
        p, after = fwplan_plans(stock, bytes(rel30), os.path.basename(path) + ' -> 26.11.30')
        failures += bool(check_plan(w, p))
        p, after2 = fwplan_plans(bytes(after), bytes(nxt_of1), 'then -> next')
        failures += bool(check_plan(w, p))
        p, _ = fwplan_plans(bytes(after2), bytes(rel30), 'then -> 26.11.30 again')
        failures += bool(check_plan(w, p))
    w = Walkers(rel30)
    p, _ = fwplan_plans(bytes(rel30), bytes(nxt_of1), '26.11.30 release chip -> next')
    failures += bool(check_plan(w, p))
    print('FAIL: %d' % failures if failures else 'all as expected')
    sys.exit(1 if failures else 0)


if __name__ == '__main__':
    main()
