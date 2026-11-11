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

Panel clock (panel_clock.inc): the Poulsbo graphics at 00:02.0 with its registers at BAR0
(fake MMIO, every write logged with PP_CONTROL at the time). After the entry, the boot manager's
stage signal: NBDXE writes its fixed DPLL_B, then the stage notifies run. Checked: the driver's
notify (TPL_CALLBACK, context BootServices) puts the GOP's value back at once, with the panel
registers unlocked and PP_CONTROL restored, and writes nothing else; anything but exactly NBDXE's
value with the GOP's dividers on a Poulsbo GMA with memory decode and a memory BAR0 is left
untouched; ReadyToBoot puts it back if no notify could be registered; the app build does it at once.
NBDXE's loaded image (EFI_LOADED_IMAGE handles: NBDXE's, this driver's, another): the driver
changes the value in its stock clock-writing function to the GOP's, at the entry or as it loads,
so the clock never changes; nothing else in it, and nothing at all for any other code, GUID,
size or path.

Also checked: FirmwareVendor becomes "BirdsLikeWires" in EfiRuntimeServicesData with a valid system
table CRC (or nothing changes if CalculateCrc32 fails), the SMBIOS table (Types 0/1/3/11/127,
chassis 0x22, checksums) and the Type 11 boot count, filled from MonotonicCounter only at ReadyToBoot.

Usage: emu_platform.py <BlwPlatformDxe.efi> <BlwPlatformApp.efi>
"""
import struct, sys, uuid, zlib
from unicorn import (Uc, UcError, UC_ARCH_X86, UC_MODE_32, UC_HOOK_CODE, UC_HOOK_INSN, UC_HOOK_MEM_WRITE, UC_PROT_READ)
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
         0x44: 'CloseEvent', 0x58: 'HandleProtocol', 0x60: 'RegisterProtocolNotify', 0xa8: 'LocateHandleBuffer',
         0x88: 'Stall', 0x64: 'LocateHandle'}
BLOCKIO = G('964e5b21-6459-11d2-8e39-00a0c969723b')
NB_STAGE = G('ef9a3971-c1a0-4a93-bd40-5aa165f2dc3a')   # the boot-manager stage NBDXE's clock write waits for
GMA_MMIO = 0xd0100000             # the graphics device's BAR0 (registers)
GOP_DPLL, NB_DPLL, GOP_FP = 0xd8100003, 0xd8027203, 0x00021005
LOADED_IMAGE = G('5b1b31a1-9562-11d2-8e3f-00a0c969723b')
NBDXE_FILE = G('e4ecd0b2-e277-4f2b-becb-e4d75c9a812e')
NB_IMG = 0x62000000               # NBDXE's loaded image (its clock-writing function at +0x3ca)
NB_IMM = 0x402                    # the value in its DPLL_B write
MMC_FILE = G('dd223ef8-6d1b-490a-a53e-ba86faaeb778')
MMC_IMG = 0x63000000              # MmcMediaDevice's loaded image (its CE-ATA check at +0x72b)
CEA_FN = 0x72b
# The boot-timing development build (-DTIMING) also watches these, and publishes BlwBootTimes.
TM_GUIDS = {G('9042a9de-23dc-4a38-96fb-7aded080516a'): 'GOP ', G('964e5b22-6459-11d2-8e39-00a0c969723b'): 'SFS ',
            G('2b2f68d6-0cd2-44cf-8e8b-bba20b1b5b75'): 'USB ', G('387477c2-69c7-11d2-8e39-00a0c969723b'): 'OUT ',
            G('387477c1-69c7-11d2-8e39-00a0c969723b'): 'IN  ', G('4cf5b200-68b8-4ca5-9eec-b23e3f50029a'): 'PCI ',
            G('2df1e051-906d-4eff-869d-24e65378fb9e'): 'STG1', G('dbc9fd21-fad8-45b0-9e78-27158867cc93'): 'STG3',
            G('8ff925f1-8624-4d38-9ed2-f8f5aa94f84a'): 'STG4'}
BIO_AREA = 0x60000000             # fake BlockIo instances and the MMC driver's functions


def sig_code(name, label):
    """Code matching signature `label` in driver/<name>, its masked dwords filled with something else."""
    import os, re
    runs, cur = [], False
    for line in open(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'driver', name)):
        if re.match(label + r':', line):
            cur = True
        elif cur and line.strip().startswith('dw'):
            off, ln = (int(x, 16) for x in line.strip()[2:].split(','))
            if ln == 0:
                break
            runs.append([off, ln, b''])
        elif cur and line.strip().startswith('db'):
            runs[-1][2] += bytes(int(x, 16) for x in line.strip()[2:].split(','))
    code = bytearray(b'\x5a' * max(o + l for o, l, _ in runs))
    for o, l, d in runs:
        code[o:o + l] = d
    return bytes(code)


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
        bio=(), bio_late=(), create_event_fails=False, register_fails=False,
        gma=True, gma_decode=True, gma_bar=GMA_MMIO, fp=GOP_FP, nb_stage=True, nb_value=NB_DPLL, nb_signals=1,
        app_dpll=NB_DPLL, nb_img='before', nb_variant='stock', li_fails=False, timing=False,
        mmc_img='after', mmc_variant='stock'):
    """gma: the Poulsbo graphics device at 00:02.0 (8086:8108), its memory decode and BAR0.
    The GOP has set pipe B's PLL (fp, GOP_DPLL). nb_stage: after the entry, the boot manager's
    stage signal, on which NBDXE writes nb_value into DPLL_B and then the stage notifies run
    (nb_signals times). An app build starts with app_dpll in DPLL_B.
    nb_img: NBDXE's loaded image there 'before' the entry, loaded 'after' it (the LoadedImage
    notifies run), or 'none'. With an image, NBDXE writes the value its code now holds.
    nb_variant: 'stock', or what differs: 'value' (another fixed value), 'code' (one other byte),
    'guid' (another file), 'small' (ImageSize too small), 'nopath' (no FilePath), 'node' (another
    device-path node type).
    mmc_img: MmcMediaDevice's loaded image there 'before' the entry, loaded 'after' it (as on an
    OF1, where it loads just after this driver), or 'none'. mmc_variant: 'stock', 'code' (one byte
    of the CE-ATA check different), 'guid', 'small', 'nopath', or 'done' (the check already
    starts xor al, al; ret)."""
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
    evs = {}                                                      # CreateEvent handle -> event
    li_queues = {}                                                # registration key -> handles not yet returned
    tm_var = {}
    images = []                                                   # LoadedImage interfaces
    mu.mem_map(NB_IMG, 0x10000)
    nbd = bytearray(0x3000)
    nbd[0x3ca:0x3ca + 0x40] = sig_code('nbdxe_sig.inc', 'nbd_sig')
    if nb_variant == 'value':
        nbd[NB_IMM:NB_IMM + 4] = struct.pack('<I', 0xd8027303)
    if nb_variant == 'code':
        nbd[0x3ca + 0x14] ^= 1
    mu.mem_write(NB_IMG, bytes(nbd))
    nb_orig = bytes(nbd)
    mu.mem_map(MMC_IMG, 0x10000)
    mmcd = bytearray(b'\x90' * 0x34c0)
    cea = sig_code('ceata_sig.inc', 'cea_sig')
    mmcd[CEA_FN:CEA_FN + len(cea)] = cea
    if mmc_variant == 'code':
        mmcd[CEA_FN + 0x30] ^= 1
    if mmc_variant == 'done':
        mmcd[CEA_FN:CEA_FN + 3] = b'\x32\xc0\xc3'
    mu.mem_write(MMC_IMG, bytes(mmcd))
    mmc_orig = bytes(mmcd)

    def make_image(n, file_guid, base, size, node=b'\x04\x06\x14\x00', nopath=None):
        li = NB_IMG + 0x8000 + 0x100 * n
        path = li + 0x80
        mu.mem_write(path, node + file_guid + b'\x7f\xff\x04\x00')
        if nopath is None:
            nopath = nb_variant == 'nopath' and file_guid == NBDXE_FILE
        mu.mem_write(li, struct.pack('<IIIIII', 0x1000, 0, 0, 0, 0 if nopath else path, 0))
        mu.mem_write(li + 0x20, struct.pack('<IIQ', base, 0, size))
        images.append(li)
    def load_nbdxe():
        make_image(len(images), G('11111111-2222-3333-4444-555555555555') if nb_variant == 'guid' else NBDXE_FILE,
                   NB_IMG, 0x200 if nb_variant == 'small' else 0x3000,
                   b'\x04\x07\x14\x00' if nb_variant == 'node' else b'\x04\x06\x14\x00')
    def load_mmc():
        make_image(len(images), G('11111111-2222-3333-4444-555555555555') if mmc_variant == 'guid' else MMC_FILE,
                   MMC_IMG, 0x700 if mmc_variant == 'small' else 0x34c0, nopath=mmc_variant == 'nopath')
    make_image(0, G('a5d5b0c1-7d2f-4c3e-9b1a-26b1e0f17217'), LOAD, 0x3000)   # this driver
    make_image(1, G('99999999-8888-7777-6666-555555555555'), NB_IMG, 0x3000)  # some other image, same memory
    if nb_img == 'before':
        load_nbdxe()
    if mmc_img == 'before':
        load_mmc()
    nb_bad = {'n': 0}
    mmio = {0x6018: GOP_DPLL, 0x6048: fp, 0x61204: 0x00000001, 0x71008: 0x80000000}
    mmio_writes = []                                              # (offset, value, PP_CONTROL then)
    if gma:
        mu.mem_map(GMA_MMIO, 0x80000)
        for o, v in mmio.items():
            mu.mem_write(GMA_MMIO + o, struct.pack('<I', v))

        def mmio_write(uc, access, addr, size, value, _):
            o = addr - GMA_MMIO
            pp = struct.unpack('<I', uc.mem_read(GMA_MMIO + 0x61204, 4))[0]
            mmio_writes.append((o, value & 0xffffffff, pp))
            if cpu['phase'] == 'entry' and not enumerated_at_entry:
                errors.append('display register write during the entry')
        mu.hook_add(UC_HOOK_MEM_WRITE, mmio_write, begin=GMA_MMIO, end=GMA_MMIO + 0x7ffff)
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
    cfg = {(0, 2, 0): {0x00: 0x81088086 if gma else 0xffffffff, 0x04: 0x00100003 if gma_decode else 0x00100000,
                       0x10: gma_bar},
           (0, 0x1c, 0): {0x04: 0x00100000, 0x18: 0x00000000},
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
            if off == 0x88:                                        # Stall(us)
                if a[0] > 100000:
                    errors.append('Stall of %d us' % a[0])
                return ret(0)
            if off == 0x34:                                        # CreateEvent(type, tpl, fn, ctx, &ev)
                if create_event_fails:
                    return ret(0x80000009)
                h = 0xE7E70010 + len(evs)
                evs[h] = dict(type=a[0], tpl=a[1], fn=a[2], ctx=a[3], handle=h)
                uc.mem_write(a[4], struct.pack('<I', h))
                return ret(0)
            if off == 0x60:                                        # RegisterProtocolNotify(guid, ev, &reg)
                g = bytes(uc.mem_read(a[0], 16))
                ok = (BLOCKIO, NB_STAGE, LOADED_IMAGE) + (tuple(TM_GUIDS) if timing else ())
                if g not in ok or a[1] not in evs or 'guid' in evs[a[1]]:
                    errors.append('RegisterProtocolNotify for something else')
                    return ret(0x80000002)
                if register_fails:
                    return ret(0x80000009)
                key = a[1] + 0x100
                evs[a[1]].update(guid=g, registered=True, key=key)
                li_queues[key] = []
                uc.mem_write(a[2], struct.pack('<I', key))
                return ret(0)
            if off == 0x64:                                        # LocateHandle(ByRegisterNotify, 0, key, &size, buf)
                if a[0] != 1 or a[2] not in li_queues:
                    errors.append('LocateHandle other than ByRegisterNotify')
                    return ret(0x80000002)
                q = li_queues[a[2]]
                if not q:
                    return ret(0x8000000E)
                if rd(a[3]) < 4:
                    uc.mem_write(a[3], struct.pack('<I', 4))
                    return ret(0x80000005)
                uc.mem_write(a[4], struct.pack('<I', q.pop(0)))
                uc.mem_write(a[3], struct.pack('<I', 4))
                return ret(0)
            if off == 0x44:                                        # CloseEvent(ev)
                if a[0] not in evs or evs[a[0]].get('registered'):
                    errors.append('CloseEvent of %#x' % a[0])
                else:
                    evs[a[0]]['closed'] = True
                return ret(0)
            if off == 0xa8:                                        # LocateHandleBuffer(ByProtocol, guid, 0, &n, &buf)
                g = bytes(uc.mem_read(a[1], 16))
                if a[0] != 2 or g not in (BLOCKIO, LOADED_IMAGE):
                    errors.append('LocateHandleBuffer other than ByProtocol BlockIo/LoadedImage')
                    return ret(0x80000002)
                hs = ([0xB10000 + i for i in range(len(bios))] if g == BLOCKIO
                      else [] if li_fails else [0xB20000 + i for i in range(len(images))])
                if not hs:
                    return ret(0x8000000E)
                buf = pool['next']; pool['next'] += 4 * len(hs)
                allocs[buf] = (4, 4 * len(hs))
                uc.mem_write(buf, b''.join(struct.pack('<I', h) for h in hs))
                uc.mem_write(a[3], struct.pack('<I', len(hs)))
                uc.mem_write(a[4], struct.pack('<I', buf))
                return ret(0)
            if off == 0x58:                                        # HandleProtocol(h, guid, &if)
                g = bytes(uc.mem_read(a[1], 16))
                if g == BLOCKIO and 0 <= a[0] - 0xB10000 < len(bios):
                    uc.mem_write(a[2], struct.pack('<I', bios[a[0] - 0xB10000][0]))
                    return ret(0)
                if g == LOADED_IMAGE and 0 <= a[0] - 0xB20000 < len(images):
                    uc.mem_write(a[2], struct.pack('<I', images[a[0] - 0xB20000]))
                    return ret(0)
                errors.append('HandleProtocol on %#x' % a[0])
                return ret(0x80000003)
            if off == 0xc4:                                        # CreateEventEx(type, tpl, fn, ctx, group, &ev)
                ev.update(type=a[0], tpl=a[1], fn=a[2], ctx=a[3], group=bytes(uc.mem_read(a[4], 16)) if a[4] else None)
                if create_fails:
                    ev.clear()
                    return ret(0x80000009)
                uc.mem_write(a[5], struct.pack('<I', 0xE7E70001))
                return ret(0)
        if RT_STUB <= addr < RT_STUB + 0x100:
            off = addr - RT_STUB
            if off == 0x38 and timing:                             # SetVariable(name, guid, attr, size, data)
                name, g = wstring(a[0]), bytes(uc.mem_read(a[1], 16))
                if name != 'BlwBootTimes' or g != BLW or a[2] & 1 or a[2] != 6:
                    errors.append('SetVariable(%s, attr %#x) in the timing build' % (name, a[2]))
                    return ret(0x80000002)
                tm_var['data'] = bytes(uc.mem_read(a[4], a[3])) if a[3] else b''
                tm_var['n'] = tm_var.get('n', 0) + 1
                events.append('SetVariable(BlwBootTimes)')
                return ret(0)
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

    def io_check(count=True):
        if cpu['phase'] == 'entry' and not enumerated_at_entry:
            errors.append('port I/O during the DXE entry (PCI is not enumerated yet)')
        if count:
            nic['io'] += 1
    gma_cfg = lambda a: ((a >> 16) & 0xff, (a >> 11) & 0x1f, (a >> 8) & 7) == (0, 2, 0)

    def port_in(uc, port, size, _):
        io_check(not (port == 0xCFC and gma_cfg(nic['cf8'])))
        if port == 0xCFC:
            a = nic['cf8']
            key = ((a >> 16) & 0xff, (a >> 11) & 0x1f, (a >> 8) & 7)
            return cfg.get(key, {}).get(a & 0xfc, 0xffffffff) & ((1 << (8 * size)) - 1)
        if NIC_IO <= port < NIC_IO + 6 and size == 1:
            return nic['mac'][port - NIC_IO]
        errors.append('IN from port %#x size %d' % (port, size))
        return 0xff

    def port_out(uc, port, size, value, _):
        io_check(not (port == 0xCF8 and gma_cfg(value)))
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

    img_is_dxe = struct.unpack_from('<H', pe, peo + 0x5c)[0] == 11
    if gma and not img_is_dxe:
        mu.mem_write(GMA_MMIO + 0x6018, struct.pack('<I', app_dpll))
    status = call(LOAD + entry, [0xAAAA0000, ST], 'entry')
    if cpu['ap']:
        cpu['rev'] = cpu_rev
        call(cpu['ap'][0], [cpu['ap'][1]], 'AP procedure')
    entry_mac = bytes(nic['mac'])
    entry_bio = [rd(a + 0x10) for a, _ in bios]
    def notify(guid, what):
        for e in evs.values():
            if e.get('registered') and e.get('guid') == guid:
                call(e['fn'], [e['handle'], e['ctx']], what)
    if nb_img == 'after':                                         # NBDXE loads after this driver
        load_nbdxe()
        for e in evs.values():
            if e.get('guid') == LOADED_IMAGE:
                li_queues[e['key']].append(0xB20000 + len(images) - 1)
        notify(LOADED_IMAGE, 'LoadedImage notify')
    if mmc_img == 'after':                                        # MmcMediaDevice loads after it
        load_mmc()
        for e in evs.values():
            if e.get('guid') == LOADED_IMAGE:
                li_queues[e['key']].append(0xB20000 + len(images) - 1)
        notify(LOADED_IMAGE, 'LoadedImage notify')
    if timing:                                                    # the watched protocols appear
        for g in TM_GUIDS:
            notify(g, 'timing notify')
    cpu['phase'] = 'BDS'
    for k in bio_late:                                            # a driver installs BlockIo later
        install_bio(k)
        notify(BLOCKIO, 'BlockIo notify')
    enumerate_pci()
    if gma and nb_stage and img_is_dxe:
        nb_wrote = []
        for _ in range(nb_signals):                               # NBDXE's notify runs first, then ours
            v = rd(NB_IMG + NB_IMM) if nb_img != 'none' else nb_value
            nb_wrote.append(v)
            mu.mem_write(GMA_MMIO + 0x6018, struct.pack('<I', v))
            notify(NB_STAGE, 'NB-stage notify')
        nb_bad['after_notify'] = rd(GMA_MMIO + 0x6018)
        nb_bad['wrote'] = nb_wrote
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
    ordered = sorted(evs.values(), key=lambda e: e['handle'])
    mmc_ev = (next((e for e in ordered if e.get('guid') == BLOCKIO), None)
              or next((e for e in ordered if 'guid' not in e), {}))
    nb_ev = next((e for e in evs.values() if e.get('guid') == NB_STAGE), {})
    final = struct.unpack('<I', mu.mem_read(GMA_MMIO + 0x6018, 4))[0] if gma else None
    nb_now = bytes(mu.mem_read(NB_IMG, 0x3000))
    nb_changed = [i for i in range(0x3000) if nb_now[i] != nb_orig[i]]
    mmc_now = bytes(mu.mem_read(MMC_IMG, 0x34c0))
    cea_changed = [(i, mmc_now[i]) for i in range(0x34c0) if mmc_now[i] != mmc_orig[i]]
    return dict(cea_changed=cea_changed, tm_var=tm_var, nb_imm=struct.unpack_from('<I', nb_now, NB_IMM)[0], nb_changed=nb_changed, nb_wrote=nb_bad.get('wrote', []),
                mmc_ev=mmc_ev, nb_ev=nb_ev, evs=evs, mmio_writes=mmio_writes, dpll=final,
                pp_final=struct.unpack('<I', mu.mem_read(GMA_MMIO + 0x61204, 4))[0] if gma else None,
                nb_after_notify=nb_bad.get('after_notify'), wrapped=wrapped, entry_bio=entry_bio, late_bio=late_bio, in_image=in_image,
                bios=bios, mmc_fns=(MMC_RD, MMC_WR, OTHER_FN),
                status=status, ev=ev, events=events, errors=errors, mac=bytes(nic['mac']), entry_mac=entry_mac,
                io=nic['io'], rev=cpu['rev'], smbios=smbios_now(), entry_smbios=entry_smbios,
                vendor=wstring(vptr), vendor_alloc=allocs.get(vptr), crc_ok=rd(ST + 0x10) == st_crc(),
                st_unchanged=st_now == st_orig)


def main():
    timing = '--timing' in sys.argv                               # the -DTIMING development build
    dxe, app = (open(p, 'rb').read() for p in [x for x in sys.argv[1:] if not x.startswith('--')][:2])
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
        ('CreateEvent fails: ReadyToBoot still wraps', dxe, dict(nb_img='none', store=lm(CARD), bio_late=('mmc',), create_event_fails=True, want_mmc_ev='none', want_wrapped=[('mmc', True, True)], want_pclk='rtb'), 0, True, MAC, True),
        ('RegisterProtocolNotify fails: event closed, ReadyToBoot still wraps', dxe, dict(nb_img='none', store=lm(CARD), bio_late=('mmc',), register_fails=True, want_mmc_ev='closed', want_wrapped=[('mmc', True, True)], want_pclk='rtb'), 0, True, MAC, True),
        ('no ReadyToBoot event (EFI 1.10): no notify either, nothing wrapped', dxe, dict(store=lm(CARD), bio_late=('mmc',), bs_hdr=0xc4, want_count=PLACEHOLDER, want_wrapped=[('mmc', False, False)]), 0, False, JUNK, False),
        ('CreateEventEx fails: no notify either (the image is freed)', dxe, dict(store=lm(CARD), bio_late=('mmc',), create_fails=True, cpu_rev=0x217, want_count=PLACEHOLDER, want_wrapped=[('mmc', False, False)]), 0x80000014, False, JUNK, False),
        ('app build: leaves the MMC alone (BlwMmcFix.efi does it)', app, dict(store=lm(CARD), cpu_rev=0x217, enumerated_at_entry=True, bio=('mmc',), want_wrapped=[('mmc', False, False)]), 0x80000014, False, MAC, True),
        # Panel clock (panel_clock.inc). want_pclk: 'notify' (put back by the NB-stage notify itself),
        # 'rtb' (left by the notify, put back at ReadyToBoot), 'app' (put back by the app at once),
        # 'untouched' (no display register written at all).
        ('panel clock: NBDXE\'s value put back by the stage notify, again on a second stage signal', dxe, dict(nb_img='none', store=lm(CARD), nb_signals=2), 0, True, MAC, True),
        ('panel clock: NBDXE already right (or absent): nothing written', dxe, dict(store=lm(CARD), nb_value=GOP_DPLL, want_pclk='untouched'), 0, True, MAC, True),
        ('panel clock: another panel\'s dividers: left alone', dxe, dict(nb_img='none', store=lm(CARD), fp=0x00031108, want_pclk='untouched'), 0, True, MAC, True),
        ('panel clock: 00:02.0 not the Poulsbo graphics: no display access', dxe, dict(nb_img='none', store=lm(CARD), gma=False, want_pclk='untouched'), 0, True, MAC, True),
        ('panel clock: graphics memory decode off: left alone', dxe, dict(nb_img='none', store=lm(CARD), gma_decode=False, want_pclk='untouched'), 0, True, MAC, True),
        ('panel clock: BAR0 unassigned: left alone', dxe, dict(nb_img='none', store=lm(CARD), gma_bar=0, want_pclk='untouched'), 0, True, MAC, True),
        ('panel clock: BAR0 an I/O BAR: left alone', dxe, dict(nb_img='none', store=lm(CARD), gma_bar=GMA_MMIO | 1, want_pclk='untouched'), 0, True, MAC, True),
        ('panel clock: notify not registered: put back at ReadyToBoot', dxe, dict(nb_img='none', store=lm(CARD), register_fails=True, want_mmc_ev='closed', want_pclk='rtb'), 0, True, MAC, True),
        ('panel clock: no stage signal (NBDXE never writes): the GOP\'s clock kept, nothing written', dxe, dict(store=lm(CARD), nb_stage=False, want_pclk='untouched'), 0, True, MAC, True),
        ('panel clock: app build puts it back at once', app, dict(store=lm(CARD), cpu_rev=0x217, enumerated_at_entry=True, want_pclk='app'), 0x80000014, False, MAC, True),
        ('panel clock: app build with the right clock already: nothing written', app, dict(store=lm(CARD), cpu_rev=0x217, enumerated_at_entry=True, app_dpll=GOP_DPLL, want_pclk='untouched'), 0x80000014, False, MAC, True),
        # NBDXE patched in memory (nb_patch). want_nbp: 'patched' (its value now the GOP's, nothing
        # else changed in it, and the clock never changes) or 'unpatched' (its image untouched).
        ('NBDXE loaded before the driver: patched at the entry, the clock never changes', dxe, dict(store=lm(CARD), nb_signals=2), 0, True, MAC, True),
        ('NBDXE loaded after the driver: patched as it loads', dxe, dict(store=lm(CARD), nb_img='after'), 0, True, MAC, True),
        ('NBDXE with another fixed value: left alone, the notify puts the clock back', dxe, dict(store=lm(CARD), nb_variant='value', want_nbp='unpatched', want_pclk='notify', nb_value=0xd8027303), 0, True, MAC, True),
        ('NBDXE with one code byte different: left alone, the notify puts the clock back', dxe, dict(store=lm(CARD), nb_variant='code', want_nbp='unpatched', want_pclk='notify'), 0, True, MAC, True),
        ('the same code under another file GUID: left alone', dxe, dict(store=lm(CARD), nb_variant='guid', want_nbp='unpatched', want_pclk='notify'), 0, True, MAC, True),
        ('NBDXE image smaller than the function: left alone', dxe, dict(store=lm(CARD), nb_variant='small', want_nbp='unpatched', want_pclk='notify'), 0, True, MAC, True),
        ('NBDXE with no FilePath: left alone', dxe, dict(store=lm(CARD), nb_variant='nopath', want_nbp='unpatched', want_pclk='notify'), 0, True, MAC, True),
        ('NBDXE path node not an FV file: left alone', dxe, dict(store=lm(CARD), nb_variant='node', want_nbp='unpatched', want_pclk='notify'), 0, True, MAC, True),
        ('LocateHandleBuffer(LoadedImage) fails: the notify puts the clock back', dxe, dict(store=lm(CARD), li_fails=True, want_nbp='unpatched', want_pclk='notify'), 0, True, MAC, True),
        # MmcMediaDevice's CE-ATA check (ceata.inc). want_cea: 'patched' (its first three bytes
        # xor al, al; ret, nothing else changed) or 'unpatched' (its image untouched).
        ('MmcMediaDevice loads after the driver (as on an OF1): CE-ATA check patched as it loads', dxe, dict(store=lm(CARD), mmc_img='after'), 0, True, MAC, True),
        ('MmcMediaDevice loaded before the driver: patched at the entry, no LoadedImage notify', dxe, dict(store=lm(CARD), mmc_img='before'), 0, True, MAC, True),
        ('NBDXE and MmcMediaDevice both load after the driver: both patched as they load', dxe, dict(store=lm(CARD), nb_img='after', mmc_img='after'), 0, True, MAC, True),
        ('no MmcMediaDevice: NBDXE still patched, nothing else changed', dxe, dict(store=lm(CARD), mmc_img='none'), 0, True, MAC, True),
        ('MmcMediaDevice with one byte of the check different: left alone', dxe, dict(store=lm(CARD), mmc_variant='code'), 0, True, MAC, True),
        ('the same check under another file GUID: left alone', dxe, dict(store=lm(CARD), mmc_variant='guid'), 0, True, MAC, True),
        ('MmcMediaDevice image smaller than the check: left alone', dxe, dict(store=lm(CARD), mmc_variant='small'), 0, True, MAC, True),
        ('MmcMediaDevice with no FilePath: left alone', dxe, dict(store=lm(CARD), mmc_variant='nopath'), 0, True, MAC, True),
        ('MmcMediaDevice check already skipped: left as it is', dxe, dict(store=lm(CARD), mmc_variant='done'), 0, True, MAC, True),
        ('MmcMediaDevice before the driver, NBDXE changed: MMC patched, NBDXE left alone', dxe, dict(store=lm(CARD), mmc_img='before', nb_variant='code', want_nbp='unpatched', want_pclk='notify'), 0, True, MAC, True),
        ('LoadedImage notify not registered: MmcMediaDevice loading later is left alone', dxe, dict(nb_img='none', store=lm(CARD), register_fails=True, want_mmc_ev='closed', want_pclk='rtb'), 0, True, MAC, True),
        ('app build: MmcMediaDevice left alone', app, dict(store=lm(CARD), cpu_rev=0x217, enumerated_at_entry=True, mmc_img='before'), 0x80000014, False, MAC, True),
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
        nb_patchable = kw.get('nb_img', 'before') != 'none' and kw.get('nb_variant', 'stock') == 'stock' and not kw.get('li_fails')
        want_nbp = kw.pop('want_nbp', 'patched' if img is dxe and want_ev and nb_patchable else 'unpatched')
        mmc_img = kw.get('mmc_img', 'after')
        cea_patchable = (mmc_img != 'none' and kw.get('mmc_variant', 'stock') == 'stock' and not kw.get('li_fails') and
                         (mmc_img == 'before' or not (kw.get('register_fails') or kw.get('create_event_fails'))))
        want_cea = kw.pop('want_cea', 'patched' if img is dxe and want_ev and cea_patchable else 'unpatched')
        want_pclk = kw.pop('want_pclk', ('untouched' if want_nbp == 'patched' else 'notify' if want_ev else 'untouched')
                           if img is dxe else 'app')
        # NBDXE (with the 'value' variant) writes its own value; the notify must leave that alone
        if kw.get('nb_variant') == 'value':
            want_pclk = 'value'
        r = run(img, timing=timing, **kw)
        p = list(r['errors'])
        tv = r['tm_var']
        if timing and img is dxe and want_ev:                      # ReadyToBoot ran: the records published
            d = tv.get('data', b'')
            recs = [(d[i:i + 4], struct.unpack_from('<I', d, i + 4)[0]) for i in range(0, len(d) - 7, 8)]
            tags = [t for t, _ in recs]
            if not tv.get('n') or len(d) % 8 or not recs:         # once per ReadyToBoot; the last has all
                p.append('BlwBootTimes written %s times, %d bytes' % (tv.get('n'), len(d)))
            elif tags[0] != b'ENTR' or tags[-1] != b'RTB ' or b'ENTE' not in tags:
                p.append('timing records %s' % tags)
            else:
                ts = [t for _, t in recs]
                if ts != sorted(ts):
                    p.append('timestamps go backwards: %s' % ts)
                if not (kw.get('register_fails') or kw.get('create_event_fails')):
                    for g, tag in TM_GUIDS.items():
                        if tag.encode() not in tags:
                            p.append('no %r record' % tag)
                    pre = tags[tags.index(b'PRE ') + 1:tags.index(b'PREE')] if b'PRE ' in tags and b'PREE' in tags else None
                    want_pre = [b'\xc1\xb0\xd5\xa5', b'\x99\x99\x99\x99'] + \
                        ([{'guid': b'\x11' * 4, 'nopath': b'LI??', 'node': b'LI??'}.get(kw.get('nb_variant'), NBDXE_FILE[:4])]
                         if kw.get('nb_img', 'before') == 'before' else []) + \
                        ([b'\x11' * 4 if kw.get('mmc_variant') == 'guid' else b'LI??' if kw.get('mmc_variant') == 'nopath' else MMC_FILE[:4]]
                         if kw.get('mmc_img', 'after') == 'before' else [])
                    if not kw.get('li_fails') and pre != want_pre:
                        p.append('images before the entry %s, expected %s' % (pre, want_pre))
                    if kw.get('nb_img') == 'after' and NBDXE_FILE[:4] not in tags:
                        p.append('NBDXE\'s load not recorded: %s' % tags)
        elif tv:
            p.append('BlwBootTimes written without ReadyToBoot')
        # NBDXE patch
        if want_nbp == 'patched':
            if r['nb_imm'] != GOP_DPLL or [i for i in r['nb_changed'] if not NB_IMM <= i < NB_IMM + 4]:
                p.append('NBDXE value %#x, bytes changed %s' % (r['nb_imm'], r['nb_changed'][:8]))
            if any(v != GOP_DPLL for v in r['nb_wrote']):
                p.append('NBDXE still wrote %s' % ['%#x' % v for v in r['nb_wrote']])
        elif r['nb_changed']:
            p.append('NBDXE image changed at %s' % r['nb_changed'][:8])
        # MmcMediaDevice's CE-ATA check (ceata.inc)
        if want_cea == 'patched':
            if r['cea_changed'] != [(CEA_FN, 0x32), (CEA_FN + 1, 0xc0), (CEA_FN + 2, 0xc3)]:
                p.append('MmcMediaDevice changed at %s, expected its CE-ATA check to start xor al, al; ret' % r['cea_changed'][:6])
        elif r['cea_changed']:
            p.append('MmcMediaDevice changed at %s' % r['cea_changed'][:6])
        if not timing and want_nbp == 'patched' and want_cea == 'patched' and kw.get('nb_img', 'before') == 'before' and \
                kw.get('mmc_img', 'after') == 'before' and any(e.get('guid') == LOADED_IMAGE for e in r['evs'].values()):
            p.append('LoadedImage notify registered although both images were patched at the entry')
        # Panel clock
        w = r['mmio_writes']
        if want_pclk == 'value':                                  # another fixed value: not ours to change
            if w:
                p.append('display registers written: %s' % ['%#x=%#x' % (o, v) for o, v, _ in w])
        elif want_pclk == 'untouched':
            if w:
                p.append('display registers written: %s' % ['%#x=%#x' % (o, v) for o, v, _ in w])
        else:
            if r['dpll'] != GOP_DPLL:
                p.append('DPLL_B %#x at the end, expected the GOP\'s %#x' % (r['dpll'] or 0, GOP_DPLL))
            if want_pclk == 'notify' and r['nb_after_notify'] != GOP_DPLL:
                p.append('DPLL_B %#x after the stage notify' % (r['nb_after_notify'] or 0))
            if want_pclk == 'rtb' and r['nb_after_notify'] != NB_DPLL:
                p.append('DPLL_B changed by a notify that should not exist')
            if any(o not in (0x6018, 0x61204) for o, _, _ in w):
                p.append('other display registers written: %s' % ['%#x' % o for o, _, _ in w])
            if not any(o == 0x6018 for o, _, _ in w) or any(o == 0x6018 and pp >> 16 != 0xabcd for o, _, pp in w):
                p.append('DPLL_B written without the panel registers unlocked')
            if r['pp_final'] != 0x00000001:
                p.append('PP_CONTROL left at %#x' % r['pp_final'])
        ne = r['nb_ev']
        if ne and (ne['type'], ne['tpl'], ne['ctx']) != (0x200, 8, BS):
            p.append('stage notify event type %#x TPL %d context %#x' % (ne['type'], ne['tpl'], ne['ctx']))
        if img is dxe and want_ev and want_mmc_ev == 'registered' and not ne.get('registered'):
            p.append('no notify registered for the boot-manager stage')
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
