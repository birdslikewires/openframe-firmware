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


def run(mem, timeout, data_flag, script, max_steps=40_000_000):
    """script(t_us, reg) -> 16-bit value of register reg (0x30/0x32) once t_us microseconds have been stalled
    (the controller works in real time: what it shows depends on time passed, not on how often it is read).
    Returns (exit, stalled_us, reads, counter_left)."""
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
    # the relocated gBS pointer the loop reads (mov eax, [gBS] at 0x531: its operand)
    gbs_ptr = struct.unpack_from('<I', mem, 0x532)[0]
    uc.mem_write(gbs_ptr, struct.pack('<I', BS)) if BASE <= gbs_ptr < BASE + len(mem) else None
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
            assert (this, width, bar, off_hi, count) == (PCIIO, 1, 0, 0, 1), 'odd Mem.Read %x %d %d %d' % (this, width, bar, count)
            n = st['reads'][off]
            st['reads'][off] += 1
            uc.mem_write(buf, struct.pack('<H', script(st['stall'], off) & 0xffff))   # a 16-bit read writes 2 bytes
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
        elif not (BASE + 0x4f3 <= addr < BASE + 0x584):
            raise SystemExit('ran outside the loop at 0x%x' % addr)

    uc.hook_add(UC_HOOK_CODE, hook)
    # 0x4f3 is preceded by 'add esp, 0x20' (the command write's arguments): start just after it.
    uc.reg_write(UC_X86_REG_ESP, st['esp0'])
    uc.emu_start(BASE + 0x4f3, BASE + 0x700, count=max_steps)
    assert st['exit'] is not None, 'did not leave the loop'
    assert st['esp'] == st['esp0'], 'stack unbalanced: %x vs %x' % (st['esp'], st['esp0'])
    left = struct.unpack('<I', uc.mem_read(ebp + 0x24, 4))[0]
    return st['exit'], st['stall'], st['reads'][0x30], left


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
        else:
            ok = ok and sn[1] <= so[1]
        if so[0] == 0x584:
            ok = ok and sn[3] > 0
        fails += not ok
        print('%-32s stock: exit %#x, %6d us stalled, %4d reads | new: exit %#x, %6d us stalled, %5d reads  %s'
              % (name, so[0], so[1], so[2], sn[0], sn[1], sn[2], 'ok' if ok else 'FAIL'))
    print('FAILED %d' % fails if fails else 'all %d cases ok' % len(cases))
    sys.exit(1 if fails else 0)


if __name__ == '__main__':
    main()
