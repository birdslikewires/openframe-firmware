#!/usr/bin/env python3
"""
Run the OF1 reset-vector microcode path under Unicorn and report every microcode load.

Executes the image's own code from 0xFFFFFC51 (zero MSR 0x8B, ROM-stack call into the
microcode search at 0xFFFFFAA0) to 0xFFFFFC96 (after the revision check), in 32-bit flat
mode as on the real CPU at that point. CPUID, RDMSR and WRMSR are emulated:
CPUID(1) = 0x106C2, MSR 0x17 platform ID = 0 (pf bit 0x1, like this Z520), and a WRMSR to
0x79 'loads' the update at EAX-0x30, setting MSR 0x8B to its revision.

Usage: emu_reset.py <image.bin> [<image.bin> ...]
"""
import sys, struct
from unicorn import Uc, UC_ARCH_X86, UC_MODE_32, UC_HOOK_CODE, UcError
from unicorn.x86_const import (UC_X86_REG_EAX, UC_X86_REG_EBX, UC_X86_REG_ECX, UC_X86_REG_EDX,
                               UC_X86_REG_EIP, UC_X86_REG_ESP)

FLASH = 0xFFF00000
START, STOP = 0xFFFFFC51, 0xFFFFFC96
SIG, PLATFORM_ID = 0x106C2, 0


def run(path):
    img = open(path, 'rb').read()
    mu = Uc(UC_ARCH_X86, UC_MODE_32)
    mu.mem_map(FLASH, 0x100000)
    mu.mem_write(FLASH, img)
    msr = {0x8B: 0xDEAD << 32}
    loads, trace = [], {'n': 0}

    def hook(uc, addr, size, _):
        trace['n'] += 1
        if trace['n'] > 200000:
            uc.emu_stop()
            return
        op = bytes(uc.mem_read(addr, 2))
        g = lambda r: uc.reg_read(r)
        if op == b'\x0f\xa2':                                   # cpuid
            uc.reg_write(UC_X86_REG_EAX, SIG if g(UC_X86_REG_EAX) == 1 else 0)
            uc.reg_write(UC_X86_REG_EBX, 0)
            uc.reg_write(UC_X86_REG_ECX, 0)
            uc.reg_write(UC_X86_REG_EDX, 0)
        elif op == b'\x0f\x32':                                 # rdmsr
            idx = g(UC_X86_REG_ECX)
            v = (PLATFORM_ID << 50) if idx == 0x17 else msr.get(idx, 0)
            uc.reg_write(UC_X86_REG_EAX, v & 0xffffffff)
            uc.reg_write(UC_X86_REG_EDX, v >> 32)
        elif op == b'\x0f\x30':                                 # wrmsr
            idx, v = g(UC_X86_REG_ECX), (g(UC_X86_REG_EDX) << 32) | g(UC_X86_REG_EAX)
            if idx == 0x79:
                hdr = bytes(uc.mem_read(v - 0x30, 48))
                hv, rev, date, sig, cks, ldr, pf = struct.unpack_from('<7I', hdr)
                ok = hv == 1 and sig == SIG and pf & (1 << PLATFORM_ID)
                loads.append((addr, v - 0x30, rev, ok))
                if ok:
                    msr[0x8B] = rev << 32
            else:
                msr[idx] = v
        else:
            return
        uc.reg_write(UC_X86_REG_EIP, addr + 2)

    mu.hook_add(UC_HOOK_CODE, hook)
    mu.reg_write(UC_X86_REG_ESP, 0)
    try:
        mu.emu_start(START, STOP, count=300000)
    except UcError as e:
        print('%s: emulation error %s at 0x%08x' % (path, e, mu.reg_read(UC_X86_REG_EIP)))
        return
    eip = mu.reg_read(UC_X86_REG_EIP)
    print('%s: stopped at 0x%08x (%s), %d instructions' % (path.split('/')[-1], eip,
          'reached revision check end' if eip == STOP else 'DID NOT REACH 0x%08x' % STOP, trace['n']))
    for addr, at, rev, ok in loads:
        print('   WRMSR 0x79 at 0x%08x: update at 0x%08x (file 0x%05x) rev 0x%x %s'
              % (addr, at, at - FLASH, rev, 'accepted' if ok else 'NOT VALID'))
    print('   final MSR 0x8B revision: 0x%x' % (msr[0x8B] >> 32))


if __name__ == '__main__':
    for p in sys.argv[1:]:
        run(p)
