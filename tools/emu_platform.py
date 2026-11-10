#!/usr/bin/env python3
"""
Run BlwPlatformDxe (driver/ucode_late.asm) under Unicorn and check its LAN MAC part, scenario by
scenario, together with the microcode and SMBIOS parts it shares an entry point with.

The fake firmware: a system table, boot services with a UEFI 2.0 header (CreateEventEx at +0xC4),
runtime services over an in-memory variable store, Framework MP Services, CPUID/MSRs for the
microcode path, PCI configuration mechanism 1 (root port 00:1c.0, an RTL8168 at 01:00.0 with an
I/O BAR) and the NIC's I/O ports.

Rules, each one an error:
- any boot or runtime service other than AllocatePool, InstallConfigurationTable, LocateProtocol,
  CreateEventEx and GetVariable; in particular ANY SetVariable call (the driver must never write
  a variable);
- any port I/O during the DXE entry (PCI isn't enumerated at dispatch; the emulator only assigns
  the bus number and BAR before it signals ReadyToBoot);
- an entry that returns an error status after creating the event (the core frees the image, and
  the event would call freed memory);
- writes to the flash or the firmware hub's lock registers (mapped read-only, they fault);
- NIC MAC0 writes with decode off or Cfg9346 locked, Cfg9346 left unlocked, command registers not
  restored, an unbalanced stack or callee-saved registers not kept.

Also checked: FirmwareVendor becomes "BirdsLikeWires" in EfiRuntimeServicesData with a valid system
table CRC (or nothing changes if CalculateCrc32 fails), the SMBIOS table (Types 0/1/3/11/127,
chassis 0x22, checksums) and the Type 11 boot count, filled from MonotonicCounter only at ReadyToBoot.

Usage: emu_platform.py <BlwPlatformDxe.efi> <BlwPlatformApp.efi>
"""
import struct, sys, uuid, zlib
from unicorn import (Uc, UcError, UC_ARCH_X86, UC_MODE_32, UC_HOOK_CODE, UC_HOOK_INSN, UC_PROT_READ)
from unicorn.x86_const import *

LOAD = 0x10400000                 # not the image base: the code must be position-independent
ST, BS, RT, MP, STUBS, STACK, POOL = 0x20000000, 0x20001000, 0x20002000, 0x20003000, 0x30000000, 0x40000000, 0x50000000
RET_MAGIC = 0x3000FF00
FWH, FLASH = 0xFFB00000, 0xFFF00000
BS_STUB, RT_STUB = STUBS + 0x1000, STUBS + 0x2000      # stub for slot +n at BS_STUB + n
G = lambda s: uuid.UUID(s).bytes_le
BLW = G('ec87d643-eba4-4bb5-a1e5-3f3e36b20da9')
RTB = G('7ce88fb3-4bd7-4679-87a8-a8d8dee50d2b')
MP_GUID = G('f33261e7-23cb-11d5-bd5c-0080c73c8881')
SMBIOS = G('eb9d2d31-2d88-11d3-9a16-0090273fc14d')
GLOBAL = G('8be4df61-93ca-11d2-aa0d-00e098032b8c')
AMI_VENDOR = 'American Megatrends'
PLACEHOLDER = 'BootCount: ----------'
NIC_IO = 0xE000
MAC = bytes.fromhex('020e8e123456')
CARD = bytes.fromhex('000e8e123456')
UUID = lambda m: bytes.fromhex('00000000000080008000') + m
UNFILLED = (None, b'\xff' * 16)
FILLED = ('000E8E123456', UUID(CARD))
CARD3 = bytes.fromhex('4c5e0c112233')          # a replacement card from another maker
GEN = bytes.fromhex('02a1b2c3d4e5')            # a LAN MAC of-netplan generated (no card)
JUNK = bytes.fromhex('b8fcffffffff')
BS_OK = {0x28: 'GetMemoryMap', 0x2c: 'AllocatePool', 0x30: 'FreePool', 0x6c: 'InstallConfigurationTable',
         0xac: 'LocateProtocol', 0xb8: 'CalculateCrc32', 0xc4: 'CreateEventEx', 0x34: 'CreateEvent',
         0x44: 'CloseEvent', 0x58: 'HandleProtocol', 0x60: 'RegisterProtocolNotify', 0xa8: 'LocateHandleBuffer'}
BLOCKIO = G('964e5b21-6459-11d2-8e39-00a0c969723b')
BIO_AREA = 0x60000000             # fake BlockIo instances and the MMC driver's functions


def mmc_functions():
    """Code that matches driver/mmc_sig.inc: the MMC driver's ReadBlocks and WriteBlocks with their
    relocated dwords (which the signature skips) filled with something else."""
    import os, re
    funcs, cur = {}, None
    for line in open(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'driver', 'mmc_sig.inc')):
        m = re.match(r'mmc_sig_(\w+):', line)
        if m:
            cur = funcs.setdefault(m.group(1), [])
        elif cur is not None and line.strip().startswith('dw'):
            off, ln = (int(x, 16) for x in line.strip()[2:].split(','))
            cur.append([off, ln, b''])
        elif cur is not None and line.strip().startswith('db'):
            cur[-1][2] += bytes(int(x, 16) for x in line.strip()[2:].split(','))
    out = {}
    for name, runs in funcs.items():
        code = bytearray(b'\x5a' * max(o + l for o, l, _ in runs))
        for o, l, d in runs:
            assert len(d) == l
            code[o:o + l] = d
        out[name] = bytes(code)
    return out
# The OF1 test unit's memory map as UEFI types (from its e820, 2026-11-09): RAM tops out at 0x1FAF0000,
# then reserved (graphics stolen memory and a hole) up to 2 GB, then MMIO. 512 MB of DDR2.
OF1_MAP = [(7, 0x0, 0xA0), (7, 0x100000, (0x1EE5C000 - 0x100000) >> 12), (9, 0x1EE5C000, 2),
           (4, 0x1EE5E000, (0x1F9BD000 - 0x1EE5E000) >> 12), (10, 0x1F9BD000, 0x26), (6, 0x1F9E3000, 0x19),
           (10, 0x1F9FC000, 1), (5, 0x1F9FD000, 4), (9, 0x1FA01000, 5), (10, 0x1FA06000, 1), (9, 0x1FA07000, 2),
           (10, 0x1FA09000, 8), (6, 0x1FA11000, 0x1B), (3, 0x1FA2C000, 0xC4),
           (0, 0x1FAF0000, (0x80000000 - 0x1FAF0000) >> 12), (11, 0xE0000000, 0x10000),
           (11, 0xFEC00000, 0x86), (11, 0xFED00000, 1), (11, 0xFED1C000, 4), (11, 0xFEE00000, 0x100),
           (11, 0xFF000000, 0x1000)]
def scaled_map(top):
    """OF1_MAP with RAM ending at top instead of 0x1FAF0000 (the last region stretched or cut)."""
    out = [(7, 0x0, 0xA0), (7, 0x100000, (top - 0x100000) >> 12)]
    if top < 0x80000000:
        out.append((0, top, (0x80000000 - top) >> 12))
    return out + [x for x in OF1_MAP if x[0] == 11]
RT_OK = {0x30: 'GetVariable'}
SAVED = ((UC_X86_REG_EBX, 0x11111111), (UC_X86_REG_ESI, 0x22222222), (UC_X86_REG_EDI, 0x33333333),
         (UC_X86_REG_EBP, 0x44444444))


def run(pe, store=None, cpu_rev=0x20a, cpu_sig=0x106C2, bs_hdr=0xc8, create_fails=False, nic_present=True,
        nic_mac_readonly=False, signals=1, enumerated_at_entry=False, mtc=0x64d, smbios_present=False,
        crc_fails=False, memmap=OF1_MAP, desc_size=48, getmap_fails=False, fsb_msr=0x111,
        bio=(), bio_late=(), create_event_fails=False, register_fails=False):
    """bio: BlockIo instances there before the driver loads, bio_late: ones a driver installs
    after its entry (the notify fires for each). Each is 'mmc' (the MMC driver's functions) or
    'other' (another driver's)."""
    store = dict(store or {})
    if mtc is not None:                                         # the firmware's boot counter
        store[('MonotonicCounter', GLOBAL)] = (7, struct.pack('<I', mtc) if isinstance(mtc, int) else mtc)
    errors, events = [], []
    mu = Uc(UC_ARCH_X86, UC_MODE_32)
    peo = struct.unpack_from('<I', pe, 0x3c)[0]
    entry = struct.unpack_from('<I', pe, peo + 0x28)[0]
    size_img = struct.unpack_from('<I', pe, peo + 0x50)[0]
    mu.mem_map(LOAD, (size_img + 0xfff) & ~0xfff)
    mu.mem_write(LOAD, pe[:size_img])
    for a in (ST, STUBS, STACK, POOL):
        mu.mem_map(a, 0x10000)
    mu.mem_map(BIO_AREA, 0x10000)
    fns = mmc_functions()
    MMC_RD, MMC_WR, OTHER_FN = BIO_AREA + 0x8000, BIO_AREA + 0x9000, BIO_AREA + 0xa000
    mu.mem_write(MMC_RD, fns['read'])
    mu.mem_write(MMC_WR, fns['write'])
    mu.mem_write(OTHER_FN, b'\x55\x8b\xec\x33\xc0\x5d\xc3')            # some other driver's function
    bios = []                                                     # (address, kind) of installed BlockIo

    def install_bio(kind):
        a = BIO_AREA + 0x100 * len(bios)
        rw = (MMC_RD, MMC_WR) if kind == 'mmc' else (OTHER_FN, OTHER_FN)
        mu.mem_write(a, struct.pack('<QIIIII', 0x10000, a + 0x40, OTHER_FN, rw[0], rw[1], OTHER_FN))
        bios.append((a, kind))
    for k in bio:
        install_bio(k)
    mmc_ev = {}
    mu.mem_map(FWH, 0x100000, UC_PROT_READ)
    mu.mem_map(FLASH, 0x100000, UC_PROT_READ)
    mu.mem_write(ST, b'IBI SYST' + struct.pack('<III', 0x20000, 0x48, 0))
    mu.mem_write(ST + 0x100, AMI_VENDOR.encode('utf-16le') + b'\0\0')
    mu.mem_write(ST + 0x18, struct.pack('<II', ST + 0x100, 0x0004000d))   # FirmwareVendor, FirmwareRevision
    mu.mem_write(ST + 0x38, struct.pack('<II', RT, BS))
    if smbios_present:                                          # ACPI 2.0, then the firmware's SMBIOS
        mu.mem_write(ST + 0x40, struct.pack('<II', 2, ST + 0x800))
        mu.mem_write(ST + 0x800, G('8868e871-e4f1-11d3-bc22-0080c73c8881') + struct.pack('<I', ST + 0xa00))
        mu.mem_write(ST + 0x814, SMBIOS + struct.pack('<I', ST + 0x900))
    st_crc = lambda: zlib.crc32(bytes(mu.mem_read(ST, 0x10)) + b'\0' * 4 + bytes(mu.mem_read(ST + 0x14, 0x34)))
    mu.mem_write(ST + 0x10, struct.pack('<I', st_crc()))
    st_orig = bytes(mu.mem_read(ST, 0x48))
    allocs = {}
    freed = set()
    mu.mem_write(BS, b'BOOTSERV' + struct.pack('<II', 0x20000, bs_hdr))
    for off in range(0x18, bs_hdr, 4):
        mu.mem_write(BS + off, struct.pack('<I', BS_STUB + off))
    mu.mem_write(RT, b'RUNTSERV' + struct.pack('<II', 0x20000, 0x58))
    for off in range(0x18, 0x58, 4):
        mu.mem_write(RT + off, struct.pack('<I', RT_STUB + off))
    mu.mem_write(MP + 8, struct.pack('<I', STUBS + 0x10))     # StartupAllAPs
    mu.mem_write(STUBS, b'\xc3' * 0x3000)
    pool = {'next': POOL + 4}
    ev = {}
    cpu = dict(rev=cpu_rev, msr8b=cpu_rev << 32, ap=None, phase='entry')
    cfg = {(0, 0x1c, 0): {0x04: 0x00100000, 0x18: 0x00000000},
           (1, 0, 0): {0x00: 0x816810ec if nic_present else 0x12345678, 0x04: 0x00100000, 0x10: 0}}
    nic = dict(cf8=0, cfg9346=0, mac=bytearray(JUNK), io=0)

    def enumerate_pci():
        cfg[(0, 0x1c, 0)][0x18] = 0x00010100
        cfg[(1, 0, 0)][0x10] = NIC_IO | 1
    if enumerated_at_entry:
        enumerate_pci()

    def rd(a):
        return struct.unpack('<I', mu.mem_read(a, 4))[0]

    def wstring(a):
        out = b''
        while (c := bytes(mu.mem_read(a, 2))) != b'\0\0':
            out += c
            a += 2
        return out.decode('utf-16le')

    def stub(uc, addr, size, _):
        esp = uc.reg_read(UC_X86_REG_ESP)
        a = [rd(esp + 4 + 4 * i) for i in range(7)]
        ret = lambda v: uc.reg_write(UC_X86_REG_EAX, v)
        if addr == STUBS + 0x10:                                   # MP->StartupAllAPs
            cpu['ap'] = (a[1], a[5])
            return ret(0)
        if BS_STUB <= addr < BS_STUB + 0x100:
            off = addr - BS_STUB
            if off not in BS_OK:
                errors.append('unexpected boot service call: BS+%#x' % off)
                return ret(0x80000003)
            events.append(BS_OK[off])
            if off == 0x2c:                                        # AllocatePool(type, size, &buf)
                buf = pool['next']; pool['next'] += (a[1] + 7) & ~7
                allocs[buf] = (a[0], a[1])
                uc.mem_write(a[2], struct.pack('<I', buf))
                return ret(0)
            if off == 0x30:                                        # FreePool(buf)
                if a[0] not in allocs or a[0] in freed:
                    errors.append('FreePool of %#x, not an allocation' % a[0])
                freed.add(a[0])
                return ret(0)
            if off == 0x28:                                        # GetMemoryMap(&size, map, &key, &dsize, &dver)
                if getmap_fails:
                    return ret(0x80000003)
                # an allocation adds an entry to the real map, so the second call is one larger
                n = len(memmap) + (1 if any(t == 4 for t, _ in allocs.values()) else 0)
                need = n * desc_size
                uc.mem_write(a[3], struct.pack('<I', desc_size))
                uc.mem_write(a[4], struct.pack('<I', 1))
                room = rd(a[0])
                uc.mem_write(a[0], struct.pack('<I', need))
                if room < need or not a[1]:
                    return ret(0x80000005)
                if a[1] not in allocs or allocs[a[1]][1] < need:
                    errors.append('GetMemoryMap into a buffer that is not a big enough allocation')
                    return ret(0x80000002)
                entries = list(memmap) + [(4, 0x200000, 1)] * (n - len(memmap))
                for i, (t, start, pages) in enumerate(entries):
                    d = struct.pack('<IIQQQQ', t, 0, start, start, pages, 0xF)
                    uc.mem_write(a[1] + i * desc_size, d + b'\xA5' * (desc_size - len(d)))
                uc.mem_write(a[2], struct.pack('<I', 0x1234))
                return ret(0)
            if off == 0x6c:                                        # InstallConfigurationTable
                if bytes(uc.mem_read(a[0], 16)) != SMBIOS:
                    errors.append('InstallConfigurationTable with a non-SMBIOS GUID')
                cpu['smbios'] = a[1]
                return ret(0)
            if off == 0xb8:                                        # CalculateCrc32(data, size, &crc)
                if crc_fails:
                    return ret(0x80000002)
                uc.mem_write(a[2], struct.pack('<I', zlib.crc32(bytes(uc.mem_read(a[0], a[1])))))
                return ret(0)
            if off == 0xac:                                        # LocateProtocol(guid, reg, &if)
                ok = bytes(uc.mem_read(a[0], 16)) == MP_GUID
                if ok:
                    uc.mem_write(a[2], struct.pack('<I', MP))
                return ret(0 if ok else 0x8000000E)
            if off == 0x34:                                        # CreateEvent(type, tpl, fn, ctx, &ev)
                if create_event_fails:
                    return ret(0x80000009)
                mmc_ev.update(type=a[0], tpl=a[1], fn=a[2], ctx=a[3], handle=0xE7E70002)
                uc.mem_write(a[4], struct.pack('<I', 0xE7E70002))
                return ret(0)
            if off == 0x60:                                        # RegisterProtocolNotify(guid, ev, &reg)
                if bytes(uc.mem_read(a[0], 16)) != BLOCKIO or a[1] != mmc_ev.get('handle'):
                    errors.append('RegisterProtocolNotify for something else')
                if register_fails:
                    return ret(0x80000009)
                if 'handle' in mmc_ev:
                    mmc_ev['registered'] = True
                uc.mem_write(a[2], struct.pack('<I', 0xE7E70003))
                return ret(0)
            if off == 0x44:                                        # CloseEvent(ev)
                if a[0] != mmc_ev.get('handle') or mmc_ev.get('registered'):
                    errors.append('CloseEvent of %#x' % a[0])
                mmc_ev['closed'] = True
                return ret(0)
            if off == 0xa8:                                        # LocateHandleBuffer(ByProtocol, guid, 0, &n, &buf)
                if a[0] != 2 or bytes(uc.mem_read(a[1], 16)) != BLOCKIO:
                    errors.append('LocateHandleBuffer other than ByProtocol BlockIo')
                if not bios:
                    return ret(0x8000000E)
                buf = pool['next']; pool['next'] += 4 * len(bios)
                allocs[buf] = (4, 4 * len(bios))
                uc.mem_write(buf, b''.join(struct.pack('<I', 0xB10000 + i) for i in range(len(bios))))
                uc.mem_write(a[3], struct.pack('<I', len(bios)))
                uc.mem_write(a[4], struct.pack('<I', buf))
                return ret(0)
            if off == 0x58:                                        # HandleProtocol(h, guid, &if)
                i = a[0] - 0xB10000
                if not 0 <= i < len(bios) or bytes(uc.mem_read(a[1], 16)) != BLOCKIO:
                    errors.append('HandleProtocol on %#x' % a[0])
                    return ret(0x80000003)
                uc.mem_write(a[2], struct.pack('<I', bios[i][0]))
                return ret(0)
            if off == 0xc4:                                        # CreateEventEx(type, tpl, fn, ctx, group, &ev)
                ev.update(type=a[0], tpl=a[1], fn=a[2], ctx=a[3], group=bytes(uc.mem_read(a[4], 16)) if a[4] else None)
                if create_fails:
                    ev.clear()
                    return ret(0x80000009)
                uc.mem_write(a[5], struct.pack('<I', 0xE7E70001))
                return ret(0)
        if RT_STUB <= addr < RT_STUB + 0x100:
            off = addr - RT_STUB
            if off not in RT_OK:
                errors.append('unexpected runtime service call: RT+%#x%s' % (off, ' (SetVariable!)' if off == 0x38 else ''))
                return ret(0x80000003)
            key = (wstring(a[0]), bytes(uc.mem_read(a[1], 16)))   # GetVariable(n, g, &attr, &size, data)
            events.append('GetVariable(%s)' % key[0])
            if key not in store:
                return ret(0x8000000E)
            attr, data = store[key]
            if a[2]:
                uc.mem_write(a[2], struct.pack('<I', attr))
            room = rd(a[3])
            uc.mem_write(a[3], struct.pack('<I', len(data)))
            if room < len(data):
                return ret(0x80000005)
            uc.mem_write(a[4], data)
            return ret(0)

    def cpu_hook(uc, addr, size, _):
        op = bytes(uc.mem_read(addr, 2))
        g = uc.reg_read
        if op == b'\x0f\xa2':                                      # cpuid
            leaf = g(UC_X86_REG_EAX)
            uc.reg_write(UC_X86_REG_EAX, cpu_sig if leaf == 1 else 0)
            if leaf == 1:
                cpu['msr8b'] = cpu['rev'] << 32
            for r in (UC_X86_REG_EBX, UC_X86_REG_ECX, UC_X86_REG_EDX):
                uc.reg_write(r, 0x5A5A5A5A)
        elif op == b'\x0f\x32':                                    # rdmsr
            idx = g(UC_X86_REG_ECX)
            if idx == 0xCD and cpu_sig & 0x0fff0ff0 != 0x106c0:
                errors.append('MSR 0xCD read on a CPU that is not an Atom Z5xx')
            if idx not in (0x8B, 0x17, 0xCD):
                errors.append('RDMSR %#x' % idx)
            v = {0x8B: cpu['msr8b'], 0x17: 0, 0xCD: fsb_msr}.get(idx, 0)
            uc.reg_write(UC_X86_REG_EAX, v & 0xffffffff)
            uc.reg_write(UC_X86_REG_EDX, v >> 32)
        elif op == b'\x0f\x30':                                    # wrmsr
            idx, v = g(UC_X86_REG_ECX), (g(UC_X86_REG_EDX) << 32) | g(UC_X86_REG_EAX)
            if idx == 0x79:
                cpu['rev'] = struct.unpack('<I', uc.mem_read(v - 0x30 + 4, 4))[0]
                events.append('microcode %#x loaded' % cpu['rev'])
            elif idx == 0x8B:
                cpu['msr8b'] = v
            else:
                errors.append('WRMSR %#x' % idx)
        else:
            return
        uc.reg_write(UC_X86_REG_EIP, addr + 2)

    def io_check():
        if cpu['phase'] == 'entry' and not enumerated_at_entry:
            errors.append('port I/O during the DXE entry (PCI is not enumerated yet)')
        nic['io'] += 1

    def port_in(uc, port, size, _):
        io_check()
        if port == 0xCFC:
            a = nic['cf8']
            key = ((a >> 16) & 0xff, (a >> 11) & 0x1f, (a >> 8) & 7)
            return cfg.get(key, {}).get(a & 0xfc, 0xffffffff) & ((1 << (8 * size)) - 1)
        if NIC_IO <= port < NIC_IO + 6 and size == 1:
            return nic['mac'][port - NIC_IO]
        errors.append('IN from port %#x size %d' % (port, size))
        return 0xff

    def port_out(uc, port, size, value, _):
        io_check()
        if port == 0xCF8 and size == 4:
            nic['cf8'] = value
            return
        if 0xCFC <= port <= 0xCFF:
            a = nic['cf8']
            key, reg = ((a >> 16) & 0xff, (a >> 11) & 0x1f, (a >> 8) & 7), a & 0xfc
            if reg != 4 or size != 2 or port != 0xCFC or key not in cfg:
                errors.append('config write %s reg %#x+%d size %d' % (key, reg, port - 0xCFC, size))
                return
            cfg[key][reg] = (cfg[key][reg] & 0xffff0000) | (value & 0xffff)
            return
        if port == NIC_IO + 0x50 and size == 1:
            nic['cfg9346'] = value
            return
        if port in (NIC_IO, NIC_IO + 4) and size == 4:
            if not (cfg[(1, 0, 0)][4] & 1 and cfg[(0, 0x1c, 0)][4] & 1):
                errors.append('NIC I/O write with decode off')
            if nic['cfg9346'] != 0xC0:
                errors.append('MAC0 write with Cfg9346 locked')
            if port == NIC_IO + 4 and value >> 16:
                errors.append('MAC4 upper half not zero')
            if not nic_mac_readonly:
                o = port - NIC_IO
                n = 4 if o == 0 else 2
                nic['mac'][o:o + n] = struct.pack('<I', value)[:n]
            events.append('MAC0 write')
            return
        errors.append('OUT to port %#x size %d value %#x' % (port, size, value))

    mu.hook_add(UC_HOOK_CODE, stub, begin=STUBS, end=STUBS + 0x3000)
    mu.hook_add(UC_HOOK_CODE, cpu_hook, begin=LOAD, end=LOAD + size_img)
    mu.hook_add(UC_HOOK_INSN, port_in, None, 1, 0, UC_X86_INS_IN)
    mu.hook_add(UC_HOOK_INSN, port_out, None, 1, 0, UC_X86_INS_OUT)

    def call(fn, args, what):
        sp = STACK + 0xff00 - 4 * len(args)
        mu.mem_write(sp, b''.join(struct.pack('<I', x) for x in args))
        sp -= 4
        mu.mem_write(sp, struct.pack('<I', RET_MAGIC))
        mu.reg_write(UC_X86_REG_ESP, sp)
        for r, v in SAVED:
            mu.reg_write(r, v)
        try:
            mu.emu_start(fn, RET_MAGIC, count=1_000_000)
        except UcError as e:
            errors.append('%s: emulation stopped: %s at eip %#x' % (what, e, mu.reg_read(UC_X86_REG_EIP)))
            return None
        if mu.reg_read(UC_X86_REG_ESP) != sp + 4:
            errors.append('%s: stack unbalanced' % what)
        if not all(mu.reg_read(r) == v for r, v in SAVED):
            errors.append('%s: callee-saved registers not kept' % what)
        return mu.reg_read(UC_X86_REG_EAX)

    status = call(LOAD + entry, [0xAAAA0000, ST], 'entry')
    if cpu['ap']:
        cpu['rev'] = cpu_rev
        call(cpu['ap'][0], [cpu['ap'][1]], 'AP procedure')
    entry_mac = bytes(nic['mac'])
    entry_bio = [rd(a + 0x10) for a, _ in bios]
    for k in bio_late:                                            # a driver installs BlockIo later
        install_bio(k)
        if mmc_ev.get('registered') and 'fn' in mmc_ev:
            call(mmc_ev['fn'], [mmc_ev['handle'], mmc_ev['ctx']], 'BlockIo notify')
    late_bio = [(rd(a + 0x10), rd(a + 0x14)) for a, _ in bios]
    def smbios_now():
        if 'smbios' not in cpu:
            return None
        eps = bytes(mu.mem_read(cpu['smbios'], 31))
        tlen, taddr, n = struct.unpack_from('<HIH', eps, 0x16)
        tbl = bytes(mu.mem_read(taddr, tlen))
        out, o = [], 0
        while o < len(tbl):
            if o + 4 > len(tbl) or b'\0\0' not in tbl[o + tbl[o + 1]:]:
                return dict(structs=out + [('malformed', o, b'', [])], eps_ok=False)
            t, ln, h = struct.unpack_from('<BBH', tbl, o)
            e = tbl.index(b'\0\0', o + ln)
            out.append((t, h, tbl[o:o + ln], [x.decode() for x in tbl[o + ln:e].split(b'\0') if x]))
            o = e + 2
        ok = sum(eps[:0x1f]) & 0xff == 0 and sum(eps[0x10:0x1f]) & 0xff == 0 and n == len(out) and o == tlen
        return dict(structs=out, eps_ok=ok)
    entry_smbios = smbios_now()
    if status is not None and status != 0 and ev:
        errors.append('entry returned %#x with the event registered: the core would free the image' % status)
    if ev:
        cpu['phase'] = 'ReadyToBoot'
        enumerate_pci()
        for _ in range(signals):
            call(ev['fn'], [0xE7E70001, ev['ctx']], 'ReadyToBoot notify')
    for key in ((0, 0x1c, 0), (1, 0, 0)):
        if cfg[key][4] & 0xffff:
            errors.append('command register of %02x:%02x.%d left at %#x' % (key + (cfg[key][4] & 0xffff,)))
    if nic['cfg9346']:
        errors.append('Cfg9346 left at %#x' % nic['cfg9346'])
    vptr = rd(ST + 0x18)
    st_now = bytes(mu.mem_read(ST, 0x48))
    for buf, (t, size) in allocs.items():
        if t != 6 and buf not in freed:
            errors.append('pool allocation %#x (type %d) never freed' % (buf, t))
    in_image = lambda x: LOAD <= x < LOAD + size_img
    wrapped = [(kind, in_image(rd(a + 0x10)), in_image(rd(a + 0x14))) for a, kind in bios]
    return dict(mmc_ev=mmc_ev, wrapped=wrapped, entry_bio=entry_bio, late_bio=late_bio, in_image=in_image,
                bios=bios, mmc_fns=(MMC_RD, MMC_WR, OTHER_FN),
                status=status, ev=ev, events=events, errors=errors, mac=bytes(nic['mac']), entry_mac=entry_mac,
                io=nic['io'], rev=cpu['rev'], smbios=smbios_now(), entry_smbios=entry_smbios,
                vendor=wstring(vptr), vendor_alloc=allocs.get(vptr), crc_ok=rd(ST + 0x10) == st_crc(),
                st_unchanged=st_now == st_orig)


def main():
    dxe, app = (open(p, 'rb').read() for p in sys.argv[1:3])
    Z = bytes(6)
    lm = lambda d, attr=7: {('BlwMac', BLW): (attr, d)}
    EV = dict(type=0x200, tpl=8, ctx=ST, group=RTB)
    cases = [
        # name, image, kwargs, entry status, event expected, MAC0 after, NIC I/O expected
        ('cold boot, MAC stored', dxe, dict(store=lm(CARD)), 0, True, MAC, True),
        ('microcode already current: still resident', dxe, dict(store=lm(CARD), cpu_rev=0x217), 0, True, MAC, True),
        ('CPU the update does not fit: still resident', dxe, dict(store=lm(CARD), cpu_sig=0x106CA), 0, True, MAC, True),
        ('ReadyToBoot signalled twice', dxe, dict(store=lm(CARD), signals=2), 0, True, MAC, True),
        ('nothing stored: NIC untouched', dxe, dict(), 0, True, JUNK, False),
        ('card MAC stored, ReadyToBoot twice', dxe, dict(store=lm(CARD), signals=2), 0, True, MAC, True),
        ('replacement card: LAN MAC, serial number and UUID alike', dxe, dict(store=lm(CARD3), want_t1=('4C5E0C112233', UUID(CARD3))), 0, True, b'\x02' + CARD3[1:], True),
        ('generated MAC: as it is, UUID but no serial number', dxe, dict(store=lm(GEN), want_t1=(None, UUID(GEN))), 0, True, GEN, True),
        ('app build with a generated MAC', app, dict(store=lm(GEN), cpu_rev=0x217, enumerated_at_entry=True, want_t1=(None, UUID(GEN))), 0x80000014, False, GEN, True),
        ('stored value 5 bytes: ignored', dxe, dict(store=lm(CARD[:5])), 0, True, JUNK, False),
        ('stored value 7 bytes: ignored', dxe, dict(store=lm(CARD + b'\0')), 0, True, JUNK, False),
        ('stored value 12 bytes (an earlier 26.11.20 layout): ignored', dxe, dict(store=lm(CARD + MAC)), 0, True, JUNK, False),
        ('stored value 16 bytes (too big): ignored', dxe, dict(store=lm(CARD + MAC + bytes(4))), 0, True, JUNK, False),
        ('old BlwLanMac variable only: ignored', dxe, dict(store={('BlwLanMac', BLW): (7, MAC)}), 0, True, JUNK, False),
        ('SMBIOS already installed, card stored: left alone', dxe, dict(store=lm(CARD), smbios_present=True, want_count=None), 0, True, MAC, True),
        ('stored multicast MAC: ignored', dxe, dict(store=lm(b'\x03' + MAC[1:])), 0, True, JUNK, False),
        ('stored all-zero MAC: ignored', dxe, dict(store=lm(bytes(6))), 0, True, JUNK, False),
        ('same name under another GUID: ignored', dxe, dict(store={('BlwMac', G('8be4df61-93ca-11d2-aa0d-00e098032b8c')): (7, CARD)}), 0, True, JUNK, False),
        ('no RTL8168 behind 00:1c.0', dxe, dict(store=lm(CARD), nic_present=False), 0, True, JUNK, True),
        ('MAC0 does not take the write', dxe, dict(store=lm(CARD), nic_mac_readonly=True), 0, True, JUNK, True),
        ('EFI 1.10 boot services (no CreateEventEx): no event', dxe, dict(store=lm(CARD), bs_hdr=0xc4, want_count=PLACEHOLDER), 0, False, JUNK, False),
        ('CreateEventEx fails, microcode current', dxe, dict(store=lm(CARD), create_fails=True, cpu_rev=0x217, want_count=PLACEHOLDER), 0x80000014, False, JUNK, False),
        ('app build: applies at once, no event', app, dict(store=lm(CARD), cpu_rev=0x217, enumerated_at_entry=True), 0x80000014, False, MAC, True),
        ('app build, nothing stored', app, dict(cpu_rev=0x217, enumerated_at_entry=True), 0x80000014, False, JUNK, False),
        ('MonotonicCounter absent: placeholder stays', dxe, dict(store=lm(CARD), mtc=None, want_count=PLACEHOLDER), 0, True, MAC, True),
        ('MonotonicCounter 8 bytes: placeholder stays', dxe, dict(store=lm(CARD), mtc=bytes(8), want_count=PLACEHOLDER), 0, True, MAC, True),
        ('MonotonicCounter 0', dxe, dict(store=lm(CARD), mtc=0, want_count='BootCount: 0000000000'), 0, True, MAC, True),
        ('MonotonicCounter 0xffffffff', dxe, dict(store=lm(CARD), mtc=0xffffffff, want_count='BootCount: 4294967295'), 0, True, MAC, True),
        ('SMBIOS already installed: no table, no count', dxe, dict(store=lm(CARD), smbios_present=True, want_count=None), 0, True, MAC, True),
        ('CalculateCrc32 fails: vendor and CRC put back', dxe, dict(store=lm(CARD), crc_fails=True, want_vendor=AMI_VENDOR), 0, True, MAC, True),
        ('memory: FSB 400 MT/s', dxe, dict(store=lm(CARD), fsb_msr=0x115, want_mem=(512, 400)), 0, True, MAC, True),
        ('memory: an FSB code the SCH has no DDR2 rate for: speed unknown', dxe, dict(store=lm(CARD), fsb_msr=0x113, want_mem=(512, 0)), 0, True, MAC, True),
        ('memory: not an Atom Z5xx: MSR 0xCD left alone, speed unknown', dxe, dict(store=lm(CARD), cpu_sig=0x206A7, want_mem=(512, 0)), 0, True, MAC, True),
        ('memory: 1 GB (an OF2-sized map)', dxe, dict(store=lm(CARD), memmap=scaled_map(0x3FAF0000), want_mem=(1024, 533)), 0, True, MAC, True),
        ('memory: 256 MB', dxe, dict(store=lm(CARD), memmap=scaled_map(0x0FAF0000), want_mem=(256, 533)), 0, True, MAC, True),
        ('memory: RAM right up to 2 GB', dxe, dict(store=lm(CARD), memmap=scaled_map(0x80000000), want_mem=(2048, 533)), 0, True, MAC, True),
        ('memory: RAM past 2 GB: size unknown', dxe, dict(store=lm(CARD), memmap=scaled_map(0x80000000) + [(7, 0x80000000, 0x100)], want_mem=(0xFFFF, 533)), 0, True, MAC, True),
        ('memory: RAM above 4 GB ignored', dxe, dict(store=lm(CARD), memmap=OF1_MAP + [(7, 0x100000000, 0x1000)], want_mem=(512, 533)), 0, True, MAC, True),
        ('memory: only 64 MB in the map: size unknown', dxe, dict(store=lm(CARD), memmap=scaled_map(0x04000000), want_mem=(0xFFFF, 533)), 0, True, MAC, True),
        ('memory: 40-byte descriptors', dxe, dict(store=lm(CARD), desc_size=40), 0, True, MAC, True),
        ('memory: descriptors too small: size unknown', dxe, dict(store=lm(CARD), desc_size=32, want_mem=(0xFFFF, 533)), 0, True, MAC, True),
        ('memory: GetMemoryMap fails: size unknown', dxe, dict(store=lm(CARD), getmap_fails=True, want_mem=(0xFFFF, 533)), 0, True, MAC, True),
        ('app build replaces the firmware SMBIOS', app, dict(store=lm(CARD), cpu_rev=0x217, enumerated_at_entry=True, smbios_present=True), 0x80000014, False, MAC, True),
        # Internal memory (mmc_fix.inc). want_wrapped: per BlockIo, (kind, ReadBlocks wrapped, WriteBlocks wrapped).
        ('MMC BlockIo installed after dispatch: the notify wraps it', dxe, dict(store=lm(CARD), bio_late=('mmc',), want_wrapped=[('mmc', True, True)], want_late=True), 0, True, MAC, True),
        ('MMC and another driver\'s BlockIo, installed later', dxe, dict(store=lm(CARD), bio_late=('other', 'mmc', 'other'), want_wrapped=[('other', False, False), ('mmc', True, True), ('other', False, False)], want_late=True), 0, True, MAC, True),
        ('MMC BlockIo there before dispatch: wrapped at ReadyToBoot, not before', dxe, dict(store=lm(CARD), bio=('mmc',), want_wrapped=[('mmc', True, True)]), 0, True, MAC, True),
        ('two MMC BlockIo, ReadyToBoot twice', dxe, dict(store=lm(CARD), bio=('mmc',), bio_late=('mmc',), signals=2, want_wrapped=[('mmc', True, True), ('mmc', True, True)]), 0, True, MAC, True),
        ('CreateEvent fails: ReadyToBoot still wraps', dxe, dict(store=lm(CARD), bio_late=('mmc',), create_event_fails=True, want_mmc_ev='none', want_wrapped=[('mmc', True, True)]), 0, True, MAC, True),
        ('RegisterProtocolNotify fails: event closed, ReadyToBoot still wraps', dxe, dict(store=lm(CARD), bio_late=('mmc',), register_fails=True, want_mmc_ev='closed', want_wrapped=[('mmc', True, True)]), 0, True, MAC, True),
        ('no ReadyToBoot event (EFI 1.10): no notify either, nothing wrapped', dxe, dict(store=lm(CARD), bio_late=('mmc',), bs_hdr=0xc4, want_count=PLACEHOLDER, want_wrapped=[('mmc', False, False)]), 0, False, JUNK, False),
        ('CreateEventEx fails: no notify either (the image is freed)', dxe, dict(store=lm(CARD), bio_late=('mmc',), create_fails=True, cpu_rev=0x217, want_count=PLACEHOLDER, want_wrapped=[('mmc', False, False)]), 0x80000014, False, JUNK, False),
        ('app build: leaves the MMC alone (BlwMmcFix.efi does it)', app, dict(store=lm(CARD), cpu_rev=0x217, enumerated_at_entry=True, bio=('mmc',), want_wrapped=[('mmc', False, False)]), 0x80000014, False, MAC, True),
    ]
    bad = 0
    for name, img, kw, want_status, want_ev, want_mac, want_io in cases:
        kw = dict(kw)
        want_count = kw.pop('want_count', 'BootCount: 0000001613')
        want_vendor = kw.pop('want_vendor', 'BirdsLikeWires')
        want_mem = kw.pop('want_mem', (512, 533))
        want_t1 = kw.pop('want_t1', FILLED if kw.get('store') == lm(CARD) and (want_ev or img is app) else UNFILLED)
        want_wrapped = kw.pop('want_wrapped', [])
        want_late = kw.pop('want_late', False)
        want_mmc_ev = kw.pop('want_mmc_ev', 'registered' if img is dxe and want_ev else 'none')
        r = run(img, **kw)
        p = list(r['errors'])
        me = r['mmc_ev']
        got_mmc_ev = ('closed' if me.get('closed') else 'registered' if me.get('registered') else
                      'created' if me else 'none')
        if got_mmc_ev != want_mmc_ev:
            p.append('BlockIo notify %s, expected %s' % (got_mmc_ev, want_mmc_ev))
        if me.get('registered') and (me['type'], me['tpl'], me['ctx']) != (0x200, 8, BS):
            p.append('BlockIo notify event type %#x TPL %d context %#x' % (me['type'], me['tpl'], me['ctx']))
        if img is app and {'CreateEvent', 'LocateHandleBuffer', 'RegisterProtocolNotify'} & set(r['events']):
            p.append('app build touched BlockIo or events: %s' % r['events'])
        if r['wrapped'] != want_wrapped:
            p.append('BlockIo wrapped %s, expected %s' % (r['wrapped'], want_wrapped))
        if any(r['in_image'](x) for x in r['entry_bio']):
            p.append('BlockIo wrapped during the entry')
        if want_late:                                               # the notify alone, before ReadyToBoot
            late = [(k, r['in_image'](a), r['in_image'](b)) for (k, _), (a, b) in zip([(k, 0) for k, *_ in want_wrapped], r['late_bio'])]
            if late != want_wrapped:
                p.append('after the notify, before ReadyToBoot: %s' % late)
        for (a, kind), (k, rw, ww) in zip(r['bios'], r['wrapped']):
            if kind == 'other' and (rw or ww):
                p.append('another driver\'s BlockIo was wrapped')
        if r['status'] != want_status:
            p.append('entry returned %s, expected %#x' % (r['status'] if r['status'] is None else '%#x' % r['status'], want_status))
        if want_ev:
            got = {k: r['ev'].get(k) for k in EV}
            if got != EV:
                p.append('event %s, expected %s' % (got, EV))
        elif r['ev']:
            p.append('unexpected event %s' % r['ev'])
        if img is dxe and r['entry_mac'] != JUNK:
            p.append('MAC0 changed during the DXE entry')
        if r['mac'] != want_mac:
            p.append('MAC0 %s, expected %s' % (r['mac'].hex(':'), want_mac.hex(':')))
        if bool(r['io']) != want_io:
            p.append('NIC/PCI port I/O %s, expected %s' % (bool(r['io']), want_io))
        if kw.get('cpu_sig') is None and r['rev'] != 0x217:
            p.append('microcode %#x after the run' % r['rev'])
        if want_count is None:
            if r['smbios']:
                p.append('SMBIOS installed over an existing table')
            if 'GetVariable(MonotonicCounter)' in r['events']:
                p.append('MonotonicCounter read with no table of ours to fill')
        elif not r['smbios']:
            p.append('SMBIOS not installed')
        else:
            sm = r['smbios']
            types = [(t, h) for t, h, _, _ in sm['structs']]
            if types != [(0, 0), (1, 1), (3, 2), (16, 3), (17, 4), (11, 5), (127, 6)]:
                p.append('SMBIOS structures %s' % types)
            if not sm['eps_ok']:
                p.append('SMBIOS entry point checksum/length/count wrong')
            d = {t: (raw, ss) for t, h, raw, ss in sm['structs']}
            if 3 in d and (d[3][0][5] != 0x22 or d[3][1] != ['OpenPeak']):
                p.append('Type 3 chassis %#x %s' % (d[3][0][5], d[3][1]))
            if 1 in d:
                raw, ss = d[1]
                if ss[:4] != ['OpenPeak', 'OpenFrame 1', 'OF1', 'OpenFrame'] or len(ss) != 5:
                    p.append('Type 1 strings %s' % ss)
                got = (ss[raw[7] - 1] if raw[7] else None, raw[8:24])
                if got != want_t1:
                    p.append('Type 1 serial/UUID %r %s, expected %r %s' % (got[0], got[1].hex(), want_t1[0], want_t1[1].hex()))
                if want_t1[0] is None and ss[4:5] != ['0' * 12]:
                    p.append('serial placeholder changed to %r' % ss[4:5])
            if img is dxe and r['entry_smbios']:
                e1 = [(raw[7], raw[8:24], ss) for t, _, raw, ss in r['entry_smbios']['structs'] if t == 1]
                if e1 != [(0, b'\xff' * 16, ['OpenPeak', 'OpenFrame 1', 'OF1', 'OpenFrame', '0' * 12])]:
                    p.append('Type 1 filled before ReadyToBoot: %s' % e1)
            if 16 in d and d[16][0] != struct.pack('<BBHBBBIHH', 16, 0x0F, 3, 3, 3, 3, 0x200000, 0xFFFE, 1):
                p.append('Type 16 %s' % d[16][0].hex())
            if 17 in d:
                raw, ss = d[17]
                size, speed = struct.unpack_from('<H', raw, 12)[0], struct.unpack_from('<H', raw, 21)[0]
                if (size, speed) != want_mem:
                    p.append('Type 17 size %#x speed %d, expected %s' % (size, speed, want_mem))
                fixed = raw[:12] + raw[14:21] + raw[23:]
                if fixed != struct.pack('<BBHHHHH', 17, 0x1B, 4, 3, 0xFFFE, 64, 64) + bytes([0x0B, 0, 1, 0, 0x13, 0x80, 0, 0, 0, 0, 0]) or ss != ['Onboard']:
                    p.append('Type 17 fixed fields %s %s' % (raw.hex(), ss))
            if 11 in d and d[11][1] != [want_count]:
                p.append('Type 11 %s, expected %r' % (d[11][1], want_count))
            if img is dxe and r['entry_smbios'] and [ss for t, _, _, ss in r['entry_smbios']['structs'] if t == 11] != [[PLACEHOLDER]]:
                p.append('boot count filled before ReadyToBoot')
        if r['vendor'] != want_vendor:
            p.append('FirmwareVendor %r, expected %r' % (r['vendor'], want_vendor))
        if want_vendor == AMI_VENDOR:
            if not r['st_unchanged']:
                p.append('system table changed although the CRC could not be computed')
        else:
            if not r['crc_ok']:
                p.append('system table CRC32 wrong')
            if not r['vendor_alloc'] or r['vendor_alloc'][0] != 6:
                p.append('vendor string not in EfiRuntimeServicesData pool: %s' % (r['vendor_alloc'],))
        print('%-4s %s' % ('ok' if not p else 'FAIL', name))
        for x in p:
            print('       ' + x)
        bad += bool(p)
    print('%d of %d scenarios passed' % (len(cases) - bad, len(cases)))
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
