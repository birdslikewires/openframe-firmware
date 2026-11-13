"""Runs SdHost's SendCommand wait loop, stock and as mmcinit.inc changes it, under Unicorn against
a fake controller, and checks the changed loop leaves the same way as the stock one in every case,
with no timeout shorter than before.

    venv/bin/python -I tools/emu_mmcinit.py releases/of1-blw-26.11.27.bin driver/mmcinit_sig.inc

The loop starts at RVA 0x4f3 (mov [ebp-4], edi) with ebx = 1, edi = 0, esi = PciIo, and the
frame's DataBuffer flag ([ebp+0x14]) and timeout in ms ([ebp+0x24]). It leaves to 0x558 (an
error), 0x54c (timeout) or, done, 0x584 (the stock code via 0x547's check).
"""
import re
import struct
import sys
import pefile
import uefi_firmware
from unicorn import Uc, UC_ARCH_X86, UC_MODE_32, UC_HOOK_CODE
from unicorn.x86_const import UC_X86_REG_EAX, UC_X86_REG_EBX, UC_X86_REG_ECX, UC_X86_REG_EDX, \
    UC_X86_REG_ESI, UC_X86_REG_EDI, UC_X86_REG_EBP, UC_X86_REG_ESP, UC_X86_REG_EIP
sys.path.insert(0, __file__.rsplit('/', 1)[0])
from mmc_sig import find_pe  # noqa: E402
from mmcinit_sig import SDH_GUID  # noqa: E402

BASE = 0x10000000
STACK = 0x00200000
PCIIO = 0x00300000
BS = 0x00310000
RET_READ = 0x00320000          # fake PciIo->Mem.Read and BS->Stall: a ret each, hooked
RET_STALL = 0x00320010
WAIT_BASE = 0x00400000          # the driver's sdh_wait, assembled alone (glue variant)


def inc_values(path):
    v, cur, data = {}, None, {}
    for line in open(path):
        m = re.match(r'(\w+)\s+equ\s+(\w+)', line)
        if m:
            v[m.group(1)] = int(m.group(2), 0)
        m = re.match(r'(\w+):\s*$', line)
        if m:
            cur = m.group(1)
            data[cur] = []
        m = re.match(r'\s+(db|dw)\s+(.*)', line)
        if m and cur:
            vals = [int(x.strip(), 0) for x in m.group(2).split(',')]
            data[cur] += [(m.group(1), x) for x in vals]
    return v, data


def sig_ok(mem, rva, items):
    """mmc_sig_match on the generated runs."""
    i = 0
    while True:
        off, n = items[i][1], items[i + 1][1]
        i += 2
        if n == 0:
            return True
        b = bytes(x for _, x in items[i:i + n])
        i += n
        if bytes(mem[rva + off:rva + off + n]) != b:
            return False


def run(mem, timeout, data_flag, script, max_steps=40_000_000, wait=None, sick=0):
    """script(t_us, reg) -> 16-bit value of register reg (0x30/0x32) once t_us microseconds have been stalled
    (the controller works in real time: what it shows depends on time passed, not on how often it is read).
    With wait (sdh_wait's bytes, its sdw_sick flag last), the glue variant: the flag starts at sick.
    Register 0x24 (Present State, 32-bit) is script(t_us, 0x24) too.
    Returns (exit, stalled_us, reads, counter_left, sick flag after)."""
    uc = Uc(UC_ARCH_X86, UC_MODE_32)
    uc.mem_map(BASE, (len(mem) + 0xfff) & ~0xfff)
    uc.mem_write(BASE, bytes(mem))
    uc.mem_map(STACK - 0x10000, 0x20000)
    uc.mem_map(PCIIO, 0x1000)
    uc.mem_map(BS, 0x1000)
    uc.mem_map(RET_READ & ~0xfff, 0x1000)
    uc.mem_write(RET_READ, b'\xc3')
    uc.mem_write(RET_STALL, b'\xc3')
    uc.mem_write(PCIIO + 8, struct.pack('<I', RET_READ))
    uc.mem_write(BS + 0x88, struct.pack('<I', RET_STALL))
    if wait is not None:
        uc.mem_map(WAIT_BASE, 0x1000)
        uc.mem_write(WAIT_BASE, wait[:-1] + bytes([sick]))
    # the relocated gBS pointer the loop reads (mov eax, [gBS] at 0x531: its operand; the glue's at 0x4f6)
    gbs_ptr = struct.unpack_from('<I', mem, 0x4f7 if wait is not None else 0x532)[0]
    assert BASE <= gbs_ptr < BASE + len(mem), 'gBS pointer %#x outside the image' % gbs_ptr
    uc.mem_write(gbs_ptr, struct.pack('<I', BS))
    ebp = STACK
    uc.mem_write(ebp - 0x10, b'\xaa' * 0x10)                # locals: garbage, as on a real stack
    uc.mem_write(ebp + 0x14, struct.pack('<I', data_flag))
    uc.mem_write(ebp + 0x24, struct.pack('<I', timeout))
    uc.reg_write(UC_X86_REG_EBP, ebp)
    uc.reg_write(UC_X86_REG_ESP, ebp - 0x40)
    uc.reg_write(UC_X86_REG_EBX, 1)
    uc.reg_write(UC_X86_REG_EDI, 0)
    uc.reg_write(UC_X86_REG_ESI, PCIIO)
    uc.reg_write(UC_X86_REG_EAX, 0x11111111)
    st = dict(stall=0, reads={0x30: 0, 0x32: 0}, exit=None, esp0=ebp - 0x40 + 0x20)

    def hook(uc, addr, size, _):
        esp = uc.reg_read(UC_X86_REG_ESP)
        if addr == RET_READ:
            this, width, bar, off, off_hi, count, buf = struct.unpack('<7I', uc.mem_read(esp + 4, 28))   # Offset is a UINT64
            want_w = 2 if off == 0x24 else 1
            assert (this, width, bar, off_hi, count) == (PCIIO, want_w, 0, 0, 1) and off in (0x24, 0x30, 0x32), \
                'odd Mem.Read %x width %d bar %d off %x count %d' % (this, width, bar, off, count)
            st['reads'][off] = st['reads'].get(off, 0) + 1
            v = script(st['stall'], off)
            uc.mem_write(buf, struct.pack('<I', v & 0xffffffff) if width == 2 else struct.pack('<H', v & 0xffff))
            uc.reg_write(UC_X86_REG_EAX, 0)
            for r in (UC_X86_REG_ECX, UC_X86_REG_EDX):
                uc.reg_write(r, 0xdeadbeef)                   # caller-saved: clobbered
        elif addr == RET_STALL:
            us = struct.unpack('<I', uc.mem_read(esp + 4, 4))[0]
            st['stall'] += us
            uc.reg_write(UC_X86_REG_EAX, 0)
            for r in (UC_X86_REG_ECX, UC_X86_REG_EDX):
                uc.reg_write(r, 0xdeadbeef)
        elif addr - BASE in (0x54c, 0x558, 0x584):
            st['exit'] = addr - BASE
            st['esp'] = esp
            uc.emu_stop()
        elif not (BASE + 0x4f3 <= addr < BASE + 0x584) and not (wait is not None and WAIT_BASE <= addr < WAIT_BASE + len(wait)):
            raise SystemExit('ran outside the loop at 0x%x' % addr)

    uc.hook_add(UC_HOOK_CODE, hook)
    # 0x4f3 is preceded by 'add esp, 0x20' (the command write's arguments): start just after it.
    uc.reg_write(UC_X86_REG_ESP, st['esp0'])
    uc.emu_start(BASE + 0x4f3, BASE + 0x700, count=max_steps)
    assert st['exit'] is not None, 'did not leave the loop'
    assert st['esp'] == st['esp0'], 'stack unbalanced: %x vs %x' % (st['esp'], st['esp0'])
    left = struct.unpack('<I', uc.mem_read(ebp + 0x24, 4))[0]
    flag = uc.mem_read(WAIT_BASE + len(wait) - 1, 1)[0] if wait is not None else None
    return st['exit'], st['stall'], st['reads'].get(0x30, 0), left, flag


def main():
    img, inc = sys.argv[1], sys.argv[2]
    pe = pefile.PE(data=find_pe(uefi_firmware.AutoParser(open(img, 'rb').read()).parse(), SDH_GUID))
    pe.relocate_image(BASE)
    stock = bytearray(pe.get_memory_mapped_image(ImageBase=BASE))
    v, data = inc_values(inc)
    assert sig_ok(stock, v['SDH_SIG_RVA'], data['sdh_sig']), 'signature does not match the stock driver'
    new = bytearray(stock)
    new[v['SDH_NEW_RVA']:v['SDH_NEW_RVA'] + v['SDH_NEW_LEN']] = bytes(x for _, x in data['sdh_new'])
    new[v['SDH_BR1_RVA']] = v['SDH_BR1_NEW']
    new[v['SDH_BR2_RVA']] = v['SDH_BR2_NEW']
    assert not sig_ok(new, v['SDH_SIG_RVA'], data['sdh_sig']), 'signature still matches once changed'

    poll_us = data['sdh_new'][1][1]                             # push imm8: the new loop's stall
    CC, TC, ERR = 1, 2, 0x8000
    cases = [
        # name, timeout ms, data flag, script(t_us, reg)
        ('done at once', 100, 0, lambda t, r: CC if r == 0x30 else 0),
        ('done after 30 us', 100, 0, lambda t, r: CC if (r == 0x30 and t >= 30) else 0),
        ('done after 1.5 ms', 100, 0, lambda t, r: CC if (r == 0x30 and t >= 1500) else 0),
        ('done after 99.5 ms (of 100)', 100, 0, lambda t, r: CC if (r == 0x30 and t >= 99500) else 0),
        ('data: CC at 10 us, TC at 400 us', 100, 1, lambda t, r: ((CC if t >= 10 else 0) | (TC if t >= 400 else 0)) if r == 0x30 else 0),
        ('data: CC and TC at 2 ms', 100, 1, lambda t, r: (CC | TC) if (r == 0x30 and t >= 2000) else 0),
        ('data: TC only, no CC', 3, 1, lambda t, r: TC if r == 0x30 else 0),
        ('data: CC but never TC', 3, 1, lambda t, r: CC if r == 0x30 else 0),
        ('CC never, other bits set', 3, 0, lambda t, r: 0xfefe if r == 0x30 else 0),
        ('error at once', 100, 0, lambda t, r: 0x0001 if r == 0x32 else 0),
        ('error after 700 us', 100, 1, lambda t, r: (0x0010 if t >= 700 else 0) if r == 0x32 else CC),
        ('error bits above 0x1ff ignored', 2, 0, lambda t, r: 0xfe00 if r == 0x32 else 0),
        ('timeout 1', 1, 0, lambda t, r: 0),
        ('timeout 5', 5, 0, lambda t, r: 0),
        ('timeout 300', 300, 0, lambda t, r: 0),
        ('error bit 15 with CC', 100, 0, lambda t, r: ERR | CC if r == 0x32 else CC),
    ]
    fails = 0
    # 1. The in-place loop (26.11.27-dev1 to dev3): the same exits as the stock loop.
    print('in-place loop (poll %d us):' % poll_us)
    for name, t, d, sc in cases:
        so = run(stock, t, d, sc)
        sn = run(new, t, d, sc)
        ok = so[0] == sn[0]
        if so[0] == 0x54c and sn[0] == 0x584:
            # The stock loop looks last at t - 1 ms and then gives up at t without looking again, so it
            # misses a command that finishes in that last millisecond. Fine only if it really did
            # finish within the caller's timeout.
            ok = sn[1] <= t * 1000 and sn[3] > 0
        elif so[0] == 0x54c:                                    # timeout: not sooner than before
            ok = ok and sn[1] >= so[1] and sn[3] == 0
        else:                                                   # done or error: seen within a poll of stock
            ok = ok and sn[1] <= so[1] + poll_us
        if so[0] == 0x584:
            ok = ok and sn[3] > 0
        fails += not ok
        print('  %-40s stock: exit %#x, %6d us | new: exit %#x, %6d us  %s'
              % (name, so[0], so[1], sn[0], sn[1], 'ok' if ok else 'FAIL'))

    # 2. The glue and the driver's sdh_wait (from 26.11.27-dev4), assembled alone from the same source.
    wait = assemble_wait()
    g = bytearray(stock)
    glue = bytearray(bytes(x for _, x in data['sdh_glue']))
    glue[v['SDH_GLUE_GBS']:v['SDH_GLUE_GBS'] + 4] = stock[v['SDH_GBS_RVA']:v['SDH_GBS_RVA'] + 4]
    struct.pack_into('<i', glue, v['SDH_GLUE_CALL'], WAIT_BASE - (BASE + v['SDH_GLUE_RVA'] + v['SDH_GLUE_CALL'] + 4))
    g[v['SDH_GLUE_RVA']:v['SDH_GLUE_RVA'] + len(glue)] = glue
    wpoll = 10
    print('glue + sdh_wait (poll %d us, %d bytes):' % (wpoll, len(wait)))
    for name, t, d, sc in cases:
        so = run(stock, t, d, sc)
        sn = run(g, t, d, sc, wait=wait)
        ok = so[0] == sn[0] or (so[0] == 0x54c and sn[0] == 0x584 and sn[1] <= t * 1000)
        if so[0] == 0x54c and sn[0] == 0x54c:
            ok = ok and sn[1] >= so[1]                          # timeout: not sooner than before
        elif sn[0] != 0x54c:
            ok = ok and sn[1] <= so[1] + wpoll
        ok = ok and sn[4] == 0
        fails += not ok
        print('  %-40s stock: exit %#x, %6d us | new: exit %#x, %6d us  %s'
              % (name, so[0], so[1], sn[0], sn[1], 'ok' if ok else 'FAIL'))
    # The settle wait: Present State's inhibit bits (0x24, bits 0-1) after the command is done.
    busy = lambda done_at, free_at, bits=3: (lambda t, r: (CC if t >= done_at else 0) if r == 0x30 else
                                             (bits if t < free_at else 0) if r == 0x24 else 0)
    settle = [
        # name, timeout, data, script, sick before, exit, min us, max us, sick after
        ('done, lines free at once', 100, 0, busy(30, 0), 0, 0x584, 30, 40, 0),
        ('done at 30 us, lines busy to 300 us', 100, 0, busy(30, 300), 0, 0x584, 300, 310, 0),
        ('done at 30 us, lines busy to 5 ms', 100, 0, busy(30, 5000), 0, 0x584, 5000, 5010, 0),
        ('only the command line busy, to 300 us', 100, 0, busy(30, 300, 1), 0, 0x584, 300, 310, 0),
        ('only the data line busy, to 300 us', 100, 0, busy(30, 300, 2), 0, 0x584, 300, 310, 0),
        ('other Present State bits set: not busy', 100, 0, busy(30, 10 ** 9, 0xfff0000c), 0, 0x584, 30, 40, 0),
        ('busy to 19.9 ms: still within the cap', 100, 0, busy(30, 19900), 0, 0x584, 19900, 19910, 0),
        ('a line never frees: 20 ms cap, then sick', 100, 0, busy(30, 10 ** 9), 0, 0x584, 20000, 20050, 1),
        ('a line never frees, already sick: 1 ms cap', 100, 0, busy(30, 10 ** 9), 1, 0x584, 1000, 1050, 1),
        ('sick, but lines free at 200 us', 100, 0, busy(30, 200), 1, 0x584, 200, 210, 1),
        ('dead chip: command timeout error at once', 100, 0, lambda t, r: 0x0001 if r == 0x32 else 0, 0, 0x558, 0, 0, 0),
        ('dead chip: no status ever (timeout 100)', 100, 0, lambda t, r: 0, 0, 0x54c, 100000, 100100, 0),
        ('dead chip: data never arrives, DTO error', 100, 1, lambda t, r: (0x0010 if t >= 500 else 0) if r == 0x32 else CC, 0, 0x558, 500, 510, 0),
        ('error while a line is busy: no settle', 100, 0, lambda t, r: 0x0001 if r == 0x32 else 3 if r == 0x24 else 0, 0, 0x558, 0, 0, 0),
    ]
    for name, t, d, sc, sick, want_exit, lo, hi, want_sick in settle:
        sn = run(g, t, d, sc, wait=wait, sick=sick)
        ok = sn[0] == want_exit and lo <= sn[1] <= hi and sn[4] == want_sick
        fails += not ok
        print('  %-40s exit %#x, %6d us, sick %d (want %#x, %d-%d us, sick %d)  %s'
              % (name, sn[0], sn[1], sn[4], want_exit, lo, hi, want_sick, 'ok' if ok else 'FAIL'))
    total = 2 * len(cases) + len(settle)
    print('FAILED %d of %d' % (fails, total) if fails else 'all %d cases ok' % total)
    sys.exit(1 if fails else 0)


def assemble_wait():
    """driver/sdh_wait.inc on its own (bits 32, org 0): its sdw_sick flag is the last byte."""
    import os, subprocess, tempfile
    drv = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'driver')
    with tempfile.TemporaryDirectory() as d:
        src, out = os.path.join(d, 'w.asm'), os.path.join(d, 'w.bin')
        open(src, 'w').write('bits 32\norg 0\n%include "sdh_wait.inc"\n')
        subprocess.run(['nasm', '-f', 'bin', '-w+error', '-I', drv + '/', '-o', out, src], check=True)
        b = open(out, 'rb').read()
    assert b[-1] == 0, 'sdw_sick is not the last byte'
    return b


if __name__ == '__main__':
    main()
