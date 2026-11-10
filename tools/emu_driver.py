#!/usr/bin/env python3
"""
Run the BlwUcodeLate PE32 under Unicorn with a fake EFI system table, boot services
(LocateProtocol at +0xAC) and Framework MP Services (StartupAllAPs at +8), and report what
it does: microcode loads on the BSP, the LocateProtocol GUID, StartupAllAPs arguments, the
AP procedure's load, the return value and stack balance.

Usage: emu_driver.py <driver.efi> [current_revision_hex] [smbios_dump_out]
"""
import sys, struct, uuid
from unicorn import Uc, UC_ARCH_X86, UC_MODE_32, UC_HOOK_CODE
from unicorn.x86_const import *

LOAD = 0x10400000            # deliberately not the image base: the code must be position-independent
ST, BS, MP, STUBS, STACK = 0x20000000, 0x20001000, 0x20002000, 0x30000000, 0x40000000
RET_MAGIC = 0x30000F00
MP_GUID = uuid.UUID('f33261e7-23cb-11d5-bd5c-0080c73c8881').bytes_le


def main():
    pe = open(sys.argv[1], 'rb').read()
    cur = int(sys.argv[2], 16) if len(sys.argv) > 2 else 0x20a
    peo = struct.unpack_from('<I', pe, 0x3c)[0]
    entry = struct.unpack_from('<I', pe, peo + 0x28)[0]
    size_img = struct.unpack_from('<I', pe, peo + 0x50)[0]
    mu = Uc(UC_ARCH_X86, UC_MODE_32)
    mu.mem_map(LOAD, (size_img + 0xfff) & ~0xfff)
    mu.mem_write(LOAD, pe[:size_img])
    for a in (ST, STUBS, STACK):
        mu.mem_map(a, 0x10000)
    mu.mem_write(ST + 0x3c, struct.pack('<I', BS))
    import os
    if os.environ.get('SMBIOS_PRESENT'):                         # ACPI 2.0 table, then an SMBIOS one
        ct = ST + 0x800
        mu.mem_write(ST + 0x40, struct.pack('<II', 2, ct))
        mu.mem_write(ct, uuid.UUID('8868e871-e4f1-11d3-bc22-0080c73c8881').bytes_le + struct.pack('<I', 0x1234))
        mu.mem_write(ct + 20, uuid.UUID('eb9d2d31-2d88-11d3-9a16-0090273fc14d').bytes_le + struct.pack('<I', 0x5678))
    elif os.environ.get('OTHER_TABLES'):                         # two non-SMBIOS tables
        ct = ST + 0x800
        mu.mem_write(ST + 0x40, struct.pack('<II', 2, ct))
        mu.mem_write(ct, uuid.UUID('8868e871-e4f1-11d3-bc22-0080c73c8881').bytes_le + struct.pack('<I', 0x1234))
        mu.mem_write(ct + 20, uuid.UUID('eb9d2d30-2d88-11d3-9a16-0090273fc14d').bytes_le + struct.pack('<I', 0x5678))
    mu.mem_write(BS + 0xac, struct.pack('<I', STUBS + 0x00))     # LocateProtocol
    mu.mem_write(BS + 0x2c, struct.pack('<I', STUBS + 0x20))     # AllocatePool
    mu.mem_write(BS + 0x6c, struct.pack('<I', STUBS + 0x30))     # InstallConfigurationTable
    mu.mem_write(BS + 0xb8, struct.pack('<I', STUBS + 0x40))     # CalculateCrc32
    mu.mem_map(0x50000000, 0x10000)                              # pool
    pool = {'next': 0x50000004}                                  # deliberately not 16-aligned
    mu.mem_write(MP + 0x08, struct.pack('<I', STUBS + 0x10))     # StartupAllAPs
    mu.mem_write(STUBS, b'\xc3' * 0x100)
    msr = {0x8B: cur << 32, 0x17: 0}
    log = []
    state = {'cpu': 'BSP', 'rev': cur}      # the CPU's loaded revision; CPUID(1) copies it into MSR 0x8B

    def rd(a):
        return struct.unpack('<I', mu.mem_read(a, 4))[0]

    def hook(uc, addr, size, _):
        g = uc.reg_read
        if addr == STUBS:                                       # LocateProtocol(Guid, Reg, &If)
            esp = g(UC_X86_REG_ESP)
            guid, reg, out = rd(esp + 4), rd(esp + 8), rd(esp + 12)
            ok = bytes(uc.mem_read(guid, 16)) == MP_GUID
            log.append('LocateProtocol(%s, reg=%#x) -> %s' % (uuid.UUID(bytes_le=bytes(uc.mem_read(guid, 16))),
                                                               reg, 'MP' if ok else 'NOT FOUND'))
            if ok:
                uc.mem_write(out, struct.pack('<I', MP))
            uc.reg_write(UC_X86_REG_EAX, 0 if ok else 0x8000000E)
            return
        if addr == STUBS + 0x10:                                # StartupAllAPs(...)
            esp = g(UC_X86_REG_ESP)
            a = [rd(esp + 4 * i) for i in range(1, 8)]
            log.append('StartupAllAPs(This=%#x, Proc=%#x, Single=%d, Wait=%#x, Timeout=%d, Args=%#x, Failed=%#x)'
                       % tuple(a))
            state['ap_call'] = (a[1], a[5])
            uc.reg_write(UC_X86_REG_EAX, 0)
            return
        if addr == STUBS + 0x20:                                # AllocatePool(Type, Size, &Buf)
            esp = g(UC_X86_REG_ESP)
            ptype, size, out = rd(esp + 4), rd(esp + 8), rd(esp + 12)
            buf = pool['next']; pool['next'] += (size + 7) & ~7
            uc.mem_write(out, struct.pack('<I', buf))
            log.append('AllocatePool(type %d, %d bytes) -> %#x' % (ptype, size, buf))
            uc.reg_write(UC_X86_REG_EAX, 0)
            return
        if addr == STUBS + 0x30:                                # InstallConfigurationTable(Guid, Table)
            esp = g(UC_X86_REG_ESP)
            guid, table = rd(esp + 4), rd(esp + 8)
            log.append('InstallConfigurationTable(%s, %#x)' % (uuid.UUID(bytes_le=bytes(uc.mem_read(guid, 16))), table))
            eps = bytes(uc.mem_read(table, 31))
            tlen, taddr = struct.unpack_from('<HI', eps, 0x16)
            state['smbios'] = (table, eps, bytes(uc.mem_read(taddr, tlen)), taddr)
            uc.reg_write(UC_X86_REG_EAX, 0)
            return
        if addr == STUBS + 0x40:                                # CalculateCrc32(Data, Size, &Crc)
            import zlib
            esp = g(UC_X86_REG_ESP)
            data, size, out = rd(esp + 4), rd(esp + 8), rd(esp + 12)
            uc.mem_write(out, struct.pack('<I', zlib.crc32(bytes(uc.mem_read(data, size)))))
            log.append('CalculateCrc32(%#x, %d bytes)' % (data, size))
            uc.reg_write(UC_X86_REG_EAX, 0)
            return
        op = bytes(uc.mem_read(addr, 2))
        if op == b'\x0f\xa2':
            leaf = g(UC_X86_REG_EAX)
            uc.reg_write(UC_X86_REG_EAX, 0x106C2 if leaf == 1 else 0)
            if leaf == 1:
                msr[0x8B] = state['rev'] << 32
            for r in (UC_X86_REG_EBX, UC_X86_REG_ECX, UC_X86_REG_EDX):
                uc.reg_write(r, 0x5A5A5A5A)                     # clobbered, as on hardware
        elif op == b'\x0f\x32':
            v = msr.get(g(UC_X86_REG_ECX), 0)
            uc.reg_write(UC_X86_REG_EAX, v & 0xffffffff)
            uc.reg_write(UC_X86_REG_EDX, v >> 32)
        elif op == b'\x0f\x30':
            idx, v = g(UC_X86_REG_ECX), (g(UC_X86_REG_EDX) << 32) | g(UC_X86_REG_EAX)
            if idx == 0x79:
                hdr = bytes(uc.mem_read(v - 0x30, 48))
                rev = struct.unpack_from('<I', hdr, 4)[0]
                log.append('%s: WRMSR 0x79 update at %#x (16-aligned: %s, rev 0x%x)'
                           % (state['cpu'], v - 0x30, (v - 0x30) % 16 == 0, rev))
                state['rev'] = rev
            else:
                msr[idx] = v
        else:
            return
        uc.reg_write(UC_X86_REG_EIP, addr + 2)

    mu.hook_add(UC_HOOK_CODE, hook)

    def call(fn, args):
        sp = STACK + 0xff00
        sp -= 4 * len(args)
        mu.mem_write(sp, b''.join(struct.pack('<I', x) for x in args))
        sp -= 4
        mu.mem_write(sp, struct.pack('<I', RET_MAGIC))
        mu.reg_write(UC_X86_REG_ESP, sp)
        for r, v in ((UC_X86_REG_EBX, 0x11111111), (UC_X86_REG_ESI, 0x22222222),
                     (UC_X86_REG_EDI, 0x33333333), (UC_X86_REG_EBP, 0x44444444)):
            mu.reg_write(r, v)
        mu.emu_start(fn, RET_MAGIC, count=100000)
        saved = all(mu.reg_read(r) == v for r, v in ((UC_X86_REG_EBX, 0x11111111), (UC_X86_REG_ESI, 0x22222222),
                                                       (UC_X86_REG_EDI, 0x33333333), (UC_X86_REG_EBP, 0x44444444)))
        return mu.reg_read(UC_X86_REG_EAX), mu.reg_read(UC_X86_REG_ESP) == sp + 4, saved

    rax, bal, saved = call(LOAD + entry, [0xAAAA0000, ST])
    log.append('entry returned %#x, stack balanced: %s, callee-saved regs kept: %s' % (rax, bal, saved))
    if 'ap_call' in state:
        state['cpu'] = 'AP'
        state['rev'] = cur
        proc, arg = state['ap_call']
        _, bal, _ = call(proc, [arg])
        log.append('AP procedure returned, stack balanced: %s' % bal)
    print('\n'.join(log))
    if 'smbios' in state:
        table, eps, tbl, taddr = state['smbios']
        print('SMBIOS entry point at %#x (16-aligned: %s), table at %#x (= EPS+32: %s), %d bytes'
              % (table, table % 16 == 0, taddr, taddr == table + 32, len(tbl)))
        print('  anchors %r %r, EPS checksum ok: %s, intermediate checksum ok: %s'
              % (eps[:4], eps[0x10:0x15], sum(eps[:0x1f]) & 0xff == 0, sum(eps[0x10:0x1f]) & 0xff == 0))
        if len(sys.argv) > 3:                                   # dmidecode --from-dump format
            d = bytearray(eps) + b'\0' + tbl
            struct.pack_into('<I', d, 0x18, 32)
            d[0x15] = 0; d[0x15] = (-sum(d[0x10:0x1f])) & 0xff
            d[4] = 0; d[4] = (-sum(d[0:0x1f])) & 0xff
            open(sys.argv[3], 'wb').write(d)
            print('  dmidecode dump written to', sys.argv[3])


if __name__ == '__main__':
    main()
