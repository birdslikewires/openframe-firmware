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
import os, re, struct, sys, uuid, zlib
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
         0x88: 'Stall', 0x64: 'LocateHandle', 0x90: 'ConnectController', 0x38: 'SetTimer', 0x48: 'CheckEvent'}
BLOCKIO = G('964e5b21-6459-11d2-8e39-00a0c969723b')
NB_STAGE = G('ef9a3971-c1a0-4a93-bd40-5aa165f2dc3a')   # the boot-manager stage NBDXE's clock write waits for
PCIIO_GUID = G('4cf5b200-68b8-4ca5-9eec-b23e3f50029a')
SFS_GUID = G('964e5b22-6459-11d2-8e39-00a0c969723b')   # usb_first.inc (tested on its own: emu_usbfirst.py)
PCI_READ_STUB = STUBS + 0x40      # every fake PciIo's Pci.Read (mmc_pin.inc)
PCIIO_AREA = STUBS + 0x2800       # the fake PciIo instances, 0x80 apart
# The keyboard-timing development build (-DKBDTIME, kbdtime.inc): the console's input and devices.
CONIN = STUBS + 0x2f00            # ST->ConIn: Reset, ReadKeyStroke, WaitForKey
CONIN_RKS = STUBS + 0x50          # its ReadKeyStroke: hands out the queued keys
KT_PATHS = STUBS + 0x2e00         # device paths: USB at +0, ACPI (a PS/2-like device) at +0x40
KBD_PREFIX = 'KbdTime: '
TEXTIN_GUID = G('387477c1-69c7-11d2-8e39-00a0c969723b')
DP_GUID = G('09576e91-6d3f-11d2-8e39-00a0c969723b')
KT_TSC_KHZ = 1329993
GOP_BLT_STUB = STUBS + 0x70       # the display driver's own Blt: succeeds, draws nothing (IEGD's, on the splash)
# The OF2's mini-PCIe SSDT (acpi_rp2.inc): the firmware's ACPI Support protocol.
ACPISUP_GUID = G('dbff9d55-89b7-46da-bddf-677d3dc0241d')
ACPISUP_INST = STUBS + 0x2d00      # GetAcpiTable, SetAcpiTable, PublishTables
ACPI_SET_STUB, ACPI_OTHER_STUB = STUBS + 0x90, STUBS + 0x94
RP2_AML = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'driver', 'of2_rp2.aml'), 'rb').read()
# The panel line (panel_id.inc): USB I/O on the USB devices, up to three, 0x40 apart.
USBIO_GUID = G('2b2f68d6-0cd2-44cf-8e8b-bba20b1b5b75')
USBIO_AREA = STUBS + 0x2d40
USB_DESC_STUB, USB_STR_STUB, USB_OTHER_STUB = STUBS + 0xa0, STUBS + 0xa8, STUBS + 0xb0
USB_CTRL_STUB = STUBS + 0xb8
# A USB device: (vid, pid, iProduct, string), the string as text, None (every transfer for it fails,
# a device error) or bytes (the whole string descriptor the device returns, for odd ones). The
# firmware's UsbGetStringDescriptor is AMI's: it fails with NOT_FOUND for a device's only language
# (its language search is inverted), so the driver must not call it; strings come by control transfers.
PANEL_PREFIX, PANEL_WIDTH = 'Panel: ', 7
PANEL_PLACEHOLDER = PANEL_PREFIX + '-' * PANEL_WIDTH
TOUCH_PREFIX, TOUCH_WIDTH = 'Touch: ', 12
TOUCH_PLACEHOLDER = TOUCH_PREFIX + '-' * TOUCH_WIDTH
TOUCH = lambda ver: (0x04b4, 0x1974, 2, 'OpenPeak Touchscreen ' + ver)    # (vid, pid, iProduct, string)
panel_line = lambda p_, t_: (PANEL_PREFIX + p_.ljust(PANEL_WIDTH), TOUCH_PREFIX + t_.ljust(TOUCH_WIDTH))
CONOUT = STUBS + 0x2c00           # ST->ConOut: only SetCursorPosition is ever called
CONOUT_CUR = STUBS + 0x80
BLT_BUF = 0x0b000000              # a BltBuffer for the Blt checks (2 MB)
# The OF1's PCI functions, as (bus, dev, fn, class dword at 0x08): graphics, three SD hosts (08/05), USB, the NIC.
OF1_PCI = ((0, 0x02, 0, 0x03000007), (0, 0x1e, 0, 0x08050101), (0, 0x1e, 1, 0x08050101), (0, 0x1e, 2, 0x08050101),
           (0, 0x1d, 7, 0x0c032002), (1, 0x00, 0, 0x02000006))
GMA_MMIO = 0xd0100000             # the graphics device's BAR0 (registers)
GOP_DPLL, NB_DPLL, GOP_FP = 0xd8100003, 0xd8027203, 0x00021005
LOADED_IMAGE = G('5b1b31a1-9562-11d2-8e3f-00a0c969723b')
NBDXE_FILE = G('e4ecd0b2-e277-4f2b-becb-e4d75c9a812e')
NB_IMG = 0x62000000               # NBDXE's loaded image (its clock-writing function at +0x3ca)
NB_IMM = 0x402                    # the value in its DPLL_B write
MMC_FILE = G('dd223ef8-6d1b-490a-a53e-ba86faaeb778')
MMC_IMG = 0x63000000              # MmcMediaDevice's loaded image (its CE-ATA check at +0x72b)
CEA_FN = 0x72b
MMW_SITES = (0x170b, 0x187d)       # MmcMediaDevice's two 50 ms waits after CMD0 (mmcinit.inc)
MMW_SIGS = ((0x16f4, 'mmw_sig1'), (0x1869, 'mmw_sig2'))
SDH_FILE = G('89c24dfa-a105-4e8d-afe6-0ba7d40972ae')
SDH_IMG = 0x64000000              # SdHost's loaded image (its SendCommand wait at +0x4f0-0x5a0)
SDH_SIZE = 0xbea
SDH_SIG = 0x4f0


def _assemble_wait():
    import os, subprocess, tempfile
    drv = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'driver')
    with tempfile.TemporaryDirectory() as d:
        src, out = os.path.join(d, 'w.asm'), os.path.join(d, 'w.bin')
        open(src, 'w').write('bits 32\norg 0\n%include "sdh_wait.inc"\n')
        subprocess.run(['nasm', '-f', 'bin', '-w+error', '-I', drv + '/', '-o', out, src], check=True)
        return open(out, 'rb').read()


SDH_WAIT = _assemble_wait()       # sdh_wait as the driver carries it (its sdw_sick flag 0)
# The USB trace build (-DUSBTRACE): SmmControl, UHCD and USBRT, all below 512 MB (the build only
# follows pointers there).
SMMC_GUID = G('8d12e231-c667-4fd1-98f2-2449a7e7b2e5')
UHCD_FILE = G('580dd900-385d-11d7-883a-00500473d4eb')
USBRT_FILE = G('04eaaaa1-29a1-11d7-8838-00500473d4eb')
USB_AREA = 0x08000000             # UHCD +0, USBRT copies +0x10000 (readable) and +0x20000 (SMRAM closed)
SMMC_INST, SMMC_TRIG = USB_AREA + 0x30000, 0x30000020
# The on-screen trace build (-DSCREEN): a 32-bit framebuffer behind a fake Graphics Output protocol.
GOP_GUID = G('9042a9de-23dc-4a38-96fb-7aded080516a')
FB, FB_W, FB_H = 0x0a000000, 800, 480
ST_END = 0x9ed0                               # the screen trace's part of USBRT's .reloc section ends here...
F_RVA, F_SPACE = 0x9ed0, 0xb0                 # ...and usbrt.inc's F stub has the rest
RC16_CDB = bytes.fromhex('9e10' + '00' * 8 + '00000020' + '0000')
G_SITES = ((0x6f95, 'c74308808208' '00'), (0x6fc4, 'c74008808100' '80'), (0x6fcd, 'c74008808000' '80'),
           (0x6ffa, 'c74008808100' '80'), (0x7003, 'c74008808000' '80'))
G_FAKE = USB_AREA + 0x33000                   # the fake gUsbData: transfer status byte, command parameters
TABLE_FILLS = ((0x7401, 0x6ee4, 0x25), (0x2765, 0x20a7, 0x25),
               (0x7408, 0x70b2, 0x29), (0x276c, 0x26c1, 0x29), (0x740f, 0x72b7, 0x2d), (0x2773, 0x2329, 0x2d))
SAVE_FB = 0x09000000                          # what the screen showed while a fake transfer ran (one ring line)
GOP_INST = USB_AREA + 0x31000                 # +0x0c Mode; Mode at +0x100 (+8 Info, +0x10 base); Info at +0x200
HEXFONT = {}
def hexfont():
    import os, re
    if not HEXFONT:
        rows = [int(x, 16) for x in re.findall(r'0x([0-9a-f]{2})', open(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'driver', 'hexfont.inc')).read())]
        for i, ch in enumerate('0123456789ABCDEF'):
            HEXFONT[i] = rows[i * 8:i * 8 + 8]
    return HEXFONT
def read_screen(fb, row0=None):
    """The digits on the trace lines (from a framebuffer's bytes, or a dict of pixels): '.' blank. With
    row0, fb holds just the one line drawn at y = row0 (bytes starting at that row)."""
    base = row0 or 0
    px = (lambda x, y: struct.unpack_from('<I', fb, ((y - base) * FB_W + x) * 4)[0]) if isinstance(fb, (bytes, bytearray)) else \
         (lambda x, y: fb.get((x, y), 0))
    out = []
    for y in ((row0,) if row0 is not None else (8, 32, 56, 80, 104, 128, 152, 176, 200, 224, 248)):
        line = ''
        for x in range(8, 776, 16):
            rows = [sum(0x80 >> c for c in range(8) if px(x + 2 * c, y + 2 * r)) for r in range(8)]
            line += next((ch for i, ch in enumerate('0123456789ABCDEF') if hexfont()[i] == rows), '.' if not any(rows) else '?')
        out.append(line.rstrip('.'))
    return out
def render(fb, x, y, value, n):
    """What st_hex draws: value's low n hex digits at (x, y), 16x16 a digit, into a dict of pixels."""
    if x + n * 16 > FB_W or y + 16 > FB_H:
        return
    for i in range(n):
        g = hexfont()[(value >> 4 * (n - 1 - i)) & 0xf]
        for row in range(16):
            for col in range(16):
                fb[(x + i * 16 + col, y + row)] = 0xffffff if (g[row // 2] << (col // 2)) & 0x80 else 0
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


def inc_bytes(name, label):
    """The db bytes after `label` in driver/<name>, up to the next label."""
    import os, re
    out, cur = b'', False
    for line in open(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'driver', name)):
        if re.match(label + r':', line):
            cur = True
        elif cur and re.match(r'\w+:', line):
            break
        elif cur and line.strip().startswith('db'):
            out += bytes(int(x, 16) for x in line.strip()[2:].split(','))
    return out


GOP_FILE = G('2b13e5f0-e82e-4209-898a-8a53e8570db9')
GOP_IMG, GOP_SIZE = 0x65000000, 0x1a000         # the display driver's loaded image (fake, from splash_sig.inc)
GOP_VARIANTS = ('sharp', 'samsung', 'older', 'ours', 'code', 'cfg', 'cedced', 'small', 'guid', 'nopath')


def gsp_sigs():
    """driver/splash_sig.inc: its constants (with 'rvas', each place the handlers can be), the code
    runs, and each configuration's (numbers offset, first pixel offset, runs)."""
    import os, re
    k, code, cfgs, cur = {}, [], [], None
    for line in open(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'driver', 'splash_sig.inc')):
        t = line.split(';')[0].strip()
        m = re.match(r'(GSP_\w+)\s+equ\s+(0x[0-9a-f]+)', t)
        if m:
            k[m.group(1)] = int(m.group(2), 16)
        elif t.startswith('dd') and cur is None and 'rvas' not in k:
            k['rvas'] = [x for x in (int(y, 16) for y in t[2:].split(',')) if x]
        elif t == 'gsp_code_sig:':
            cur = code
        elif t == 'gsp_cfgs:':
            cur = None
        elif t.startswith('dw'):
            v = [int(x, 16) for x in t[2:].split(',')]
            if len(v) == 4:
                cfgs.append((v[1], v[2], []))
                cur = cfgs[-1][2]
            elif len(v) == 2 and v[1]:
                cur.append([v[0], v[1], b''])
        elif t.startswith('db'):
            cur[-1][2] += bytes(int(x, 16) for x in t[2:].split(','))
    return k, code, cfgs


def splash_bytes():
    """What splash.inc writes: the numbers, then the pixels and ENDSSDATA (build_ucode_driver.py's defaults)."""
    import os, sys as _s
    _s.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import build_ucode_driver as bud
    return bud.splash_data(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'logo',
                                        'logo-blw-pecking-bird-204x82.png'), '274,199')


def gop_fake(variant):
    """A display driver's loaded image as splash.inc sees it, and the image it should leave (None:
    unchanged). 'sharp' and 'samsung': the stock configurations, with another splash; 'older': an
    older build's (the OpenPeak OF2's: its handlers at the second place, its own configuration);
    'ours': ours already (an OF1 release); the rest each differ from 'sharp' in one way: 'code' one
    byte of a handler, 'cfg' one byte of the panel settings, 'cedced' the reserve's end marker."""
    k, code, cfgs = gsp_sigs()
    cfg, base = {'samsung': (cfgs[1], 'samsung'), 'older': (cfgs[2], 'older')}.get(variant, (cfgs[0], 'sharp'))
    crva = k['rvas'][1] if variant == 'older' else k['GSP_CODE_RVA']
    img = bytearray(b'\x5a' * GOP_SIZE)
    for o, ln, d in code:
        img[crva + o:crva + o + ln] = d
    value, px, runs = cfg
    for o, ln, d in runs:
        img[k['GSP_CFG_RVA'] + o:k['GSP_CFG_RVA'] + o + ln] = d
    v, p_, end = k['GSP_CFG_RVA'] + value, k['GSP_CFG_RVA'] + px, k['GSP_END_RVA']
    sp = splash_bytes()
    old, w, h = {'samsung': (b'0x0,0x144,0xC7,0x99,0x51', 0x99, 0x51),
                 'older': (b'0x0,0xFA,0xD4,0x12C,0x38', 0x12c, 0x38)}.get(base, (b'0x0,0x168,0xBF,0x50,0x51', 0x50, 0x51))
    img[v:v + 24] = old
    img[p_:end] = bytes((i * 7 + 3) & 0xff for i in range(w * h * 3)) + b'ENDSSDATA' + bytes(end - p_ - w * h * 3 - 9)
    want = bytearray(img)
    want[v:v + 24] = sp[:24]
    want[p_:end] = sp[24:] + bytes(end - p_ - (len(sp) - 24))
    if variant == 'ours':
        img = bytearray(want)
    if variant == 'code':
        img[k['GSP_CODE_RVA'] + code[1][0] + 0x20] ^= 1
    if variant == 'cfg':
        panel = max(runs, key=lambda r: r[1])                    # the run with the panel settings ($dtdc01...)
        assert b'$dtdc01' in panel[2]
        img[k['GSP_CFG_RVA'] + panel[0] + panel[2].index(b'$dtdc01') + 12] ^= 1
    if variant == 'cedced':
        img[end] ^= 1
    patched = variant in ('sharp', 'samsung', 'older', 'ours')
    return bytes(img), (bytes(want) if patched else None)


SHELL_FILE = G('c57ad6b7-0515-40a8-9d21-551652854e37')
SHELL_IMG = 0x66000000            # the shell's loaded image (fake, from shell_sig.inc)
CORE_IMG, CORE_SIZE = 0x67000000, 0x5be80   # the DXE core's loaded image (textmode.inc: its text-mode table)
CORE_FILE = G('5ae3f37e-4eae-41ae-8240-35465b5e81eb')
CORE_TABLE = struct.pack('<15I', 80, 25, 800, 480, 1, 80, 50, 1280, 1024, 2, 100, 31, 800, 600, 3)
SHELL_VARIANTS = ('of1', 'of2', 'ours', 'code', 'small', 'guid', 'nopath')


def shell_fake(variant):
    """The shell's loaded image as countdown.inc sees it (its countdown function from shell_sig.inc,
    the rest filler), the image it should leave (None: unchanged), and its ImageSize. 'of1' and
    'of2': the two stock builds; 'ours': the OF1 build with the countdown already 0 (our releases);
    'code': one byte of the function different; 'small', 'guid', 'nopath': as for the others."""
    import os, re
    builds, cur = [], None
    for line in open(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'driver', 'shell_sig.inc')):
        t = line.split(';')[0].strip()
        if t == 'shl_cds:':
            cur = 'cd'
        elif t == 'shl_sorts:':
            cur = None
        elif cur and t.startswith('dd'):
            v = [int(x, 16) for x in t[2:].split(',')]
            if len(v) == 2:
                builds.append((v[0], v[1], []))
        elif cur and t.startswith('dw'):
            o, ln = (int(x, 16) for x in t[2:].split(','))
            if ln:
                builds[-1][2].append([o, ln, b''])
        elif cur and t.startswith('db'):
            builds[-1][2][-1][2] += bytes(int(x, 16) for x in t[2:].split(','))
    rva, need, runs = builds[1] if variant == 'of2' else builds[0]
    size = need + 0x100
    img = bytearray(b'\x5a' * size)
    for o, ln, d in runs:
        img[rva + o:rva + o + ln] = d
    for site in (0x0e, 0x36):
        img[rva + site] = 0x00 if variant == 'ours' else 0x32
    if variant == 'code':
        img[rva + runs[0][0] + 2] ^= 1
    want = bytearray(img)
    for site in (0x0e, 0x36):
        want[rva + site] = 0
    ok = variant in ('of1', 'of2', 'ours')
    return bytes(img), (bytes(want) if ok else None), (need - 1 if variant == 'small' else size)


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


def blt_check(mu, call, blt):
    """Drives the Blt in place (escwin.inc's ew_blt) through random operations, each against a
    model of Graphics Output on an 800x480 BGR8 framebuffer; returns what went wrong."""
    import random
    rng = random.Random(7)
    pitch = FB_W * 4
    fb = bytearray(mu.mem_read(FB, FB_W * FB_H * 4))
    bad = []
    def px(i):
        return struct.pack('<I', (i * 2654435761) & 0xffffffff)
    def on(x, y, w, h):
        return 0 <= x and 0 <= y and w > 0 and h > 0 and x + w <= FB_W and y + h <= FB_H
    def rect(fb_, x, y, w, h):
        return b''.join(bytes(fb_[(y + r) * pitch + x * 4:(y + r) * pitch + (x + w) * 4]) for r in range(h))
    for n in range(160):
        op = rng.choice([0, 1, 2, 3, 3, 4]) if n % 10 else 5
        w, h = rng.choice([1, 8, 19, 37, 160, 800]), rng.choice([1, 19, 40, 480])
        if op != 5 and rng.random() < 0.15:
            w = rng.choice([0, w])
            h = rng.choice([0, h]) if w else h
        sx, sy = rng.randrange(0, FB_W), rng.randrange(0, FB_H)
        dx, dy = rng.randrange(0, FB_W), rng.randrange(0, FB_H)
        if rng.random() < 0.7:                                    # mostly rectangles on the screen
            w, h = max(0, min(w, FB_W - max(sx, dx))), max(0, min(h, FB_H - max(sy, dy)))
        if op == 5:                                               # a console scroll: one text row up
            op, sx, sy, dx, dy, w, h = 3, 0, 19, 0, 0, FB_W, FB_H - 19
        delta = rng.choice([0, w * 4, w * 4 + 12]) if op in (1, 2) else 0
        d = delta or w * 4
        bx, by = rng.choice([0, 3]), rng.choice([0, 2])           # where in the BltBuffer
        buf = bytearray(b''.join(px(rng.randrange(1 << 30)) for _ in range(max(1, (by + h + 1) * (d // 4 + bx + 1)))))[:0x1ff000]
        mu.mem_write(BLT_BUF, bytes(buf))
        if op == 0:
            args = [GOP_INST, BLT_BUF, 0, 0, 0, dx, dy, w, h, delta]
        elif op == 1:
            args = [GOP_INST, BLT_BUF, 1, sx, sy, bx, by, w, h, delta]
        elif op == 2:
            args = [GOP_INST, BLT_BUF, 2, bx, by, dx, dy, w, h, delta]
        else:
            args = [GOP_INST, BLT_BUF, op, sx, sy, dx, dy, w, h, 0]
        st = call(blt, args, 'Blt')
        valid = op in (0, 1, 2, 3) and w > 0 and h > 0 and (on(sx, sy, w, h) if op in (1, 3) else True) and \
            (on(dx, dy, w, h) if op in (0, 2, 3) else True)
        want_st = 0 if valid else 0x80000002
        if st != want_st:
            bad.append('op %d (%d,%d)->(%d,%d) %dx%d: status %#x, expected %#x' % (op, sx, sy, dx, dy, w, h, st, want_st))
            fb = bytearray(mu.mem_read(FB, FB_W * FB_H * 4))
            continue
        if valid:
            if op == 0:
                for r in range(h):
                    fb[(dy + r) * pitch + dx * 4:(dy + r) * pitch + (dx + w) * 4] = buf[0:4] * w
            elif op == 2:
                for r in range(h):
                    o = (by + r) * d + bx * 4
                    fb[(dy + r) * pitch + dx * 4:(dy + r) * pitch + (dx + w) * 4] = buf[o:o + w * 4]
            elif op == 3:
                src = rect(fb, sx, sy, w, h)
                for r in range(h):
                    fb[(dy + r) * pitch + dx * 4:(dy + r) * pitch + (dx + w) * 4] = src[r * w * 4:(r + 1) * w * 4]
            elif op == 1:
                want_buf = bytearray(buf)
                src = rect(fb, sx, sy, w, h)
                for r in range(h):
                    o = (by + r) * d + bx * 4
                    want_buf[o:o + w * 4] = src[r * w * 4:(r + 1) * w * 4]
                got = bytes(mu.mem_read(BLT_BUF, len(buf)))
                if got != bytes(want_buf):
                    bad.append('VideoToBltBuffer (%d,%d) %dx%d delta %d: buffer wrong' % (sx, sy, w, h, delta))
        now = bytes(mu.mem_read(FB, FB_W * FB_H * 4))
        if now != bytes(fb):
            i = next(i for i in range(len(fb)) if now[i] != fb[i])
            bad.append('op %d (%d,%d)->(%d,%d) %dx%d delta %d: framebuffer wrong at pixel (%d,%d)' % (op, sx, sy, dx, dy, w, h, delta, (i % pitch) // 4, i // pitch))
            fb = bytearray(now)
    return bad


def run(pe, store=None, cpu_rev=0x20a, cpu_sig=0x106C2, bs_hdr=0xc8, create_fails=False, nic_present=True,
        nic_mac_readonly=False, signals=1, enumerated_at_entry=False, mtc=0x64d, smbios_present=False,
        crc_fails=False, memmap=OF1_MAP, desc_size=48, getmap_fails=False, fsb_msr=0x111,
        bio=(), bio_late=(), create_event_fails=False, register_fails=False,
        gma=True, gma_decode=True, gma_bar=GMA_MMIO, fp=GOP_FP, nb_stage=True, nb_value=NB_DPLL, nb_signals=1,
        app_dpll=NB_DPLL, nb_img='before', nb_variant='stock', li_fails=False, timing=False,
        mmc_img='after', mmc_variant='stock', mmw_variant='stock', sdh_img='after', sdh_variant='stock', pin_variant='of1', profile=False, tm_ticks=70, usbtrace=False, uhcd_variant='stock', usbrt_variant='stock', usbrt_img='after', screen=False, screen_variant='stock',
        fake_tsc_hi=0, gop_img='before', gop_variant='sharp', gop_dpll=GOP_DPLL, shell_img='after', shell_variant='of1', flash_model=None,
        kbdtime=False, kt_inputs=(('split', 1900), ('usb', 2600)), kt_keys=((0x17, 0), (0, 0x61)), kt_conin=True, kt_late=(),
        kt_shell_t=4600, kt_rks_t=4700, kt_rks_error=False, kt_timer_fails=False, kt_settimer_fails=False, kt_timer_never=False,
        kt_gop='ok', kt_blt_test=False, core_variant='stock', acpi_sup='present', usb_devs=()):
    """The console (every run): ST->ConIn, whose ReadKeyStroke hands out kt_keys ((ms it can be read,
    scan, char), or (scan, char) from the start) or always fails (kt_rks_error); Simple Text
    Inputs appearing at kt_inputs' times ('split': ConSplitter's, no device path; 'usb'; 'ps2':
    an ACPI path); the shell loading at kt_shell_t and making its one ReadKeyStroke at kt_rks_t
    (twice: the second must go through). The clock moves with Stall; timers (EVT_TIMER, SetTimer
    relative) go off on it (kt_timer_never: never). The display (kt_gop): 'ok' an 800x480 BGR8
    framebuffer whose own Blt draws nothing; 'none' (no Graphics Output), 'rgb' (RGB8 pixels),
    'small' (FrameBufferSize short of the screen), 'high' (above 4 GB), 'ppsl' (a scan line
    narrower than the screen), 'nomode'. kt_blt_test: then drive whatever Blt is in place through
    random operations against a model of Graphics Output."""
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
    for rva, label in MMW_SIGS:                                   # the waits after CMD0 (mmcinit.inc)
        c = sig_code('mmcinit_sig.inc', label)
        mmcd[rva:rva + len(c)] = c
    if mmw_variant == 'sd_changed':                               # one byte of the SD path's signature
        mmcd[MMW_SIGS[0][0] + 0x08] ^= 1
    if mmw_variant == 'mmc_changed':                              # one byte of the MMC path's
        mmcd[MMW_SIGS[1][0] + 0x0c] ^= 1
    if mmw_variant == 'done':                                     # already 1 ms: the signature no longer matches
        for a in MMW_SITES:
            mmcd[a:a + 4] = struct.pack('<I', 1000)
    mu.mem_write(MMC_IMG, bytes(mmcd))
    mmc_orig = bytes(mmcd)
    mu.mem_map(SDH_IMG, 0x1000)                                   # SdHost: its SendCommand wait loop
    sdhd = bytearray(b'\x90' * SDH_SIZE)
    c = sig_code('mmcinit_sig.inc', 'sdh_sig')
    sdhd[SDH_SIG:SDH_SIG + len(c)] = c
    if sdh_variant == 'code':                                     # one byte of the loop different
        sdhd[SDH_SIG + 0x50] ^= 1
    mu.mem_write(SDH_IMG, bytes(sdhd))
    sdh_orig = bytes(sdhd)

    def make_image(n, file_guid, base, size, node=b'\x04\x06\x14\x00', nopath=None):
        li = NB_IMG + 0x8000 + 0x100 * n
        path = li + 0x80
        mu.mem_write(path, node + file_guid + b'\x7f\xff\x04\x00')
        if nopath is None:
            nopath = nb_variant == 'nopath' and file_guid == NBDXE_FILE
        mu.mem_write(li, struct.pack('<IIIIII', 0x1000, 0, 0, 0, 0 if nopath else path, 0))
        mu.mem_write(li + 0x20, struct.pack('<IIQ', base, 0, size))
        images.append(li)
    mu.mem_map(GOP_IMG, GOP_SIZE)
    gop_orig, gop_want = gop_fake(gop_variant)
    mu.mem_write(GOP_IMG, gop_orig)
    def load_gop():
        make_image(len(images), G('11111111-2222-3333-4444-555555555555') if gop_variant == 'guid' else GOP_FILE,
                   GOP_IMG, 0x19000 if gop_variant == 'small' else GOP_SIZE, nopath=gop_variant == 'nopath')
    shell_orig, shell_want, shell_size = shell_fake(shell_variant)
    mu.mem_map(SHELL_IMG, (len(shell_orig) + 0xfff) & ~0xfff)
    mu.mem_write(SHELL_IMG, shell_orig)
    def load_shell():
        make_image(len(images), G('11111111-2222-3333-4444-555555555555') if shell_variant == 'guid' else SHELL_FILE,
                   SHELL_IMG, shell_size, nopath=shell_variant == 'nopath')
    def load_nbdxe():
        make_image(len(images), G('11111111-2222-3333-4444-555555555555') if nb_variant == 'guid' else NBDXE_FILE,
                   NB_IMG, 0x200 if nb_variant == 'small' else 0x3000,
                   b'\x04\x07\x14\x00' if nb_variant == 'node' else b'\x04\x06\x14\x00')
    def load_mmc():
        make_image(len(images), G('11111111-2222-3333-4444-555555555555') if mmc_variant == 'guid' else MMC_FILE,
                   MMC_IMG, 0x700 if mmc_variant == 'small' else 0x34c0, nopath=mmc_variant == 'nopath')
    def load_sdh():
        make_image(len(images), G('11111111-2222-3333-4444-555555555555') if sdh_variant == 'guid' else SDH_FILE,
                   SDH_IMG, 0x500 if sdh_variant == 'small' else SDH_SIZE, nopath=sdh_variant == 'nopath')
    mu.mem_map(USB_AREA, 0x40000)
    rt = bytearray(0x9f80)                                        # USBRT: the sites usbrt.inc changes
    rt[0x1381:0x1389] = bytes.fromhex('53566a61e8aafeff')
    rt[0x3f95:0x3fae] = bytes.fromhex('8b0df09800108a81430e0000fec0b20af6ea8881e30d0000c3')
    rt[0x56be:0x56d0] = bytes.fromhex('bb030100006835820000895d08e8b1bcffff')
    rt[0x5307:0x5318] = bytes.fromhex('3d06290000752a686a040100e869c0ffff')
    if usbrt_variant == 'a_changed':
        rt[0x56be + 16] ^= 1                                      # in the call: only a full-length check sees it
    if usbrt_variant == 'b_changed':
        rt[0x5307 + 16] ^= 1
    if usbrt_variant == 'c_changed':
        rt[0x3f9b + 18] ^= 1                                      # its last byte (ret)
    rt[0x1381:0x13ae] = bytes.fromhex('53566a61e8aafeffff8b7424108ad85980e310eb116a61e897feffff24103ad85974038ad84e85f675eb5e5bc3')
    # IoRead8: port 0x61 only (anything else halts), and bit 4 toggles on each read
    ior = bytes.fromhex('837c240461' '7401' 'f4' 'e800000000' '58' '80701010' '8a4010' 'c3')
    rt[0x1234:0x1234 + len(ior)] = ior
    rt[0x4811:0x4816] = bytes.fromhex('e817fdffff')                  # call the transfer function (+0x452d)
    rt[0x4816:0x481a] = bytes.fromhex('83c410c3')                    # (the test's caller pops the four arguments)
    rt[0x452d] = 0xc3
    rt[0x4c61:0x4c69] = bytes.fromhex('576a01e8a9ddffff')               # REQUEST SENSE: push edi; push 1; call +0x2a12
    rt[0x4c69:0x4c71] = bytes.fromhex('595f' 'b8020402' '00c3')        # ...pop ecx; pop edi; return sense 2/04/02
    rt[0x2a12:0x2a18] = b'\xb8' + struct.pack('<I', USB_AREA + 0x3a000) + b'\xc3'   # (+0x2a12 returns a buffer)
    # StartUnit (+0x4e84), the real bytes (E's 10 s at +0x4eba is inside it), its relocated dwords at
    # a fake gUsbData pointer; what it calls
    su = bytearray.fromhex('578b7c2408f6474520740433c05fc3566a01e877dbffff8bf085f6597507b8ff000000eb48c6061bc6460401'
                           'e8d7f2ffffa1f098001066c780e40d00001027a1f098001089b0e60d0000a1f098001057c680ea0d00000c'
                           'e8cefaffff6a0156e805dcffff57e873fdffff83c4105e5fc3')
    for off in (50, 64, 75):
        su[off:off + 4] = struct.pack('<I', USB_AREA + 0x7200)
    rt[0x4e84:0x4ef4] = su
    # F: READ CAPACITY's command and its call into the dispatcher; the parameter-clearing function
    # (its relocated dword per copy, below); G: the five control-transfer tokens
    rt[0x4a01:0x4a09] = bytes.fromhex('c60625e883f7ffff')
    rt[0x4a50:0x4a65] = bytes.fromhex('57c780f00d0000080000' '00e84effffff3bc3598b0d')
    rt[0x418c:0x41a2] = bytes.fromhex('578b3d' '00000000' '81c7e60d000033c0ababababab5fc3')
    for a_, h_ in G_SITES:
        rt[a_:a_ + 7] = bytes.fromhex(h_)
    if usbrt_variant == 'f_changed':
        rt[0x4a50 + 20] ^= 1
    # H: the command-block builder's clear (two rep stos), which F needs gone
    rt[0x47a6:0x47c4] = bytes.fromhex('84db8bb0e60d00007614' '8bcf8bfe8bf1c1e90233c0f3ab' '8bce83e103f3aa')
    if usbrt_variant == 'h_changed':
        rt[0x47a6 + 29] ^= 1
    if usbrt_variant == 'g_changed':
        rt[0x7003 + 6] ^= 1
    # D: the "BOT reset"'s argument pushes; E: the 5 s default and the 10 s overrides (+0x4eba is in StartUnit)
    rt[0x437e:0x439b] = bytes.fromhex('6a0083ea106a00c1fa0468ff0000006bd241576a2181c1f40100005651')
    rt[0x3382:0x338b] = bytes.fromhex('66c780120e00008813')
    for a_ in (0x4a0e, 0x57fa):
        rt[a_:a_ + 9] = bytes.fromhex('66c780e40d00001027')
    if usbrt_variant == 'd_changed':
        rt[0x437e + 28] ^= 1                                      # the reset's last push (push ecx -> push edx)
    if usbrt_variant == 'e_changed':
        rt[0x57fa + 8] ^= 1                                       # the last override's value
    for a_ in (0x49ae, 0x2aed):
        rt[a_] = 0xc3
    rt[0x56d0:0x56d5] = bytes.fromhex('595e5bc9c3')                 # identify's pop ecx; (then the test returns)
    # the controllers' function-table fills (their relocated targets per copy, below) and fakes behind them
    for site, _, slot in TABLE_FILLS:
        rt[site:site + 3] = bytes([0xc7, 0x40, slot])
    rt[0x6ee4:0x6eeb] = bytes.fromhex('0f318b44241cc3')              # EHCI control: rdtsc (time passes); return wLength
    rt[0x20a7:0x20af] = bytes.fromhex('0f318b44241c40c3')            # UHCI control: ... wLength + 1
    # EHCI bulk: rdtsc; status byte := 2 (a bulk timeout); return its length (argument 5). UHCI bulk:
    # status byte := 4; length + 1
    for a_, f_ in ((0x70b2, b'\x0f\x31\xc6\x05' + struct.pack('<I', G_FAKE + 0xe14) + b'\x02\x8b\x44\x24\x14\xc3'),
                   (0x26c1, b'\x0f\x31\xc6\x05' + struct.pack('<I', G_FAKE + 0xe14) + b'\x04\x8b\x44\x24\x14\x40\xc3')):
        rt[a_:a_ + len(f_)] = f_
    # EHCI interrupt: first copies the four ring lines (y=128..215) to SAVE_FB + 0x10000, as they are
    # while it runs; returns 0x33
    fake_int_e = (b'\x56\x57\x51' + b'\xbe' + struct.pack('<I', FB + 128 * 800 * 4) + b'\xbf' + struct.pack('<I', SAVE_FB + 0x10000) +
                  b'\xb9' + struct.pack('<I', 88 * 800) + b'\xfc\xf3\xa5\x59\x5f\x5e\xb8\x33\x00\x00\x00\xc3')
    rt[0x72b7:0x72b7 + len(fake_int_e)] = fake_int_e
    # UHCI interrupt: first copies the screen's bottom ring line (y=200..215) to SAVE_FB, as it is
    # while this transfer runs; returns 0x11 (a nop first: tables_changed's +1 lands on the push)
    fake_int_u = (b'\x90\x56\x57\x51' + b'\xbe' + struct.pack('<I', FB + 200 * 800 * 4) + b'\xbf' + struct.pack('<I', SAVE_FB) +
                              b'\xb9' + struct.pack('<I', 16 * 800) + b'\xfc\xf3\xa5\x59\x5f\x5e\xb8\x11\x00\x00\x00\xc3')
    rt[0x2329:0x2329 + len(fake_int_u)] = fake_int_u
    if screen_variant == 'sense_changed':                         # another REQUEST SENSE: push 2, not push 1
        rt[0x4c63] = 2
    assert len(rt) == 0x9f80, 'fixture slices changed the image size'
    rt_copies = [USB_AREA + 0x10000, USB_AREA + 0x20000]          # the normal copy, the SMM copy
    for base in rt_copies:
        rt[0x52ad:0x52b1] = struct.pack('<I', 0x100098f0 if screen_variant == 'unrelocated' else base + 0x98f0)
        for site, tgt, _ in TABLE_FILLS:
            rt[site + 3:site + 7] = struct.pack('<I', base + tgt + (1 if screen_variant == 'tables_changed' and site == 0x2773 else 0))
        rt[0x98f0:0x98f4] = struct.pack('<I', G_FAKE)                 # gUsbData
        rt[0x418f:0x4193] = struct.pack('<I', base + 0x98f0)          # (relocated)
        mu.mem_write(base, b'\xff' * 0x9f80 if (usbrt_variant == 'closed' and base == rt_copies[1]) else bytes(rt))
    rt_orig = [bytes(mu.mem_read(a, 0x9f80)) for a in rt_copies]
    mu.mem_write(USB_AREA + 0x7200, struct.pack('<I', USB_AREA + 0x7000))  # the fake gUsbData pointer
    if not screen and kt_gop != 'none':                           # the display (escwin.inc's ew_show)
        mu.mem_map(FB, (FB_W * FB_H * 4 + 0xfff) & ~0xfff)
        mu.mem_map(BLT_BUF, 0x200000)
        mu.mem_write(GOP_INST + 8, struct.pack('<II', GOP_BLT_STUB, 0 if kt_gop == 'nomode' else GOP_INST + 0x100))
        mu.mem_write(GOP_INST + 0x100, struct.pack('<IIIIQI', 1, 0, GOP_INST + 0x200, 0x24,
                                                   FB | (1 << 32 if kt_gop == 'high' else 0),
                                                   FB_W * FB_H * 4 - (4 if kt_gop == 'small' else 0)))
        mu.mem_write(GOP_INST + 0x200, struct.pack('<IIII', 0, FB_W, FB_H, 0 if kt_gop == 'rgb' else 1) +
                     b'\0' * 16 + struct.pack('<I', FB_W - 1 if kt_gop == 'ppsl' else FB_W))
        mu.mem_write(FB, bytes(range(256)) * (FB_W * FB_H * 4 // 256))   # the splash, as far as anyone can tell
    if screen:                                                    # the framebuffer and the GOP's Mode/Info
        mu.mem_map(FB, (FB_W * FB_H * 4 + 0xfff) & ~0xfff)
        mu.mem_write(GOP_INST + 0x0c, struct.pack('<I', GOP_INST + 0x100))
        mu.mem_write(GOP_INST + 0x100, struct.pack('<IIIIQI', 1, 0, GOP_INST + 0x200, 0x24,
                                                   0 if screen_variant in ('late_mode', 'no_fb') else FB, FB_W * FB_H * 4))
        mu.mem_write(GOP_INST + 0x200, struct.pack('<IIII', 0, FB_W, FB_H, 2 if screen_variant == 'bitmask' else 1) +
                     b'\0' * 16 + struct.pack('<I', FB_W))
        fake_tsc = {'t': 0}
        g_ = bytearray(0xe40)
        g_[0xe14] = 0x04                                          # control stall
        mu.mem_write(G_FAKE, bytes(g_))
        def slow_hook(uc, addr, size, _):                         # the fake control transfers' rdtsc: 0x3000000 ticks pass
            fake_tsc['t'] += 0x3000000
            uc.reg_write(UC_X86_REG_EAX, fake_tsc['t'] & 0xffffffff); uc.reg_write(UC_X86_REG_EDX, fake_tsc_hi)
            uc.reg_write(UC_X86_REG_EIP, addr + 2)
        for base in rt_copies:
            for a_ in (0x6ee4, 0x20a7, 0x70b2, 0x26c1):
                mu.hook_add(UC_HOOK_CODE, slow_hook, begin=base + a_, end=base + a_)
        mu.mem_map(SAVE_FB, 0x60000)
        def rdtsc_hook(uc, addr, size, _):                        # rdtsc inside the copied stubs: a fake TSC
            if bytes(uc.mem_read(addr, 2)) == b'\x0f\x31':
                fake_tsc['t'] += 0x1000
                uc.reg_write(UC_X86_REG_EAX, fake_tsc['t']); uc.reg_write(UC_X86_REG_EDX, fake_tsc_hi)
                uc.reg_write(UC_X86_REG_EIP, addr + 2)
        def hook_stub_rdtsc():                                    # once the stubs are in: hook just their rdtscs
            for base in rt_copies:
                area = bytes(mu.mem_read(base + 0x9920, ST_END - 0x9920))
                i = area.find(b'\x0f\x31')
                while i >= 0:
                    mu.hook_add(UC_HOOK_CODE, rdtsc_hook, begin=base + 0x9920 + i, end=base + 0x9920 + i)
                    i = area.find(b'\x0f\x31', i + 1)

    def load_usbrt():
        for base in rt_copies:
            make_image(len(images), G('11111111-2222-3333-4444-555555555555') if usbrt_variant == 'guid' else USBRT_FILE,
                       base, 0x5000 if usbrt_variant == 'small' else 0x9f80, nopath=usbrt_variant == 'nopath')
            for e in evs.values():
                if e.get('guid') == LOADED_IMAGE:
                    li_queues[e['key']].append(0xB20000 + len(images) - 1)
            notify(LOADED_IMAGE, 'LoadedImage notify')
    make_image(0, G('a5d5b0c1-7d2f-4c3e-9b1a-26b1e0f17217'), LOAD, 0x3000)   # this driver
    make_image(1, G('99999999-8888-7777-6666-555555555555'), NB_IMG, 0x3000)  # some other image, same memory
    if core_variant != 'none':                                    # the DXE core (textmode.inc), loaded before everything
        mu.mem_map(CORE_IMG, (CORE_SIZE + 0xfff) & ~0xfff)
        tbl = bytearray(CORE_TABLE)
        if core_variant == 'table':
            tbl[0x2c] ^= 1                                        # 100x31's height
        if core_variant == 'done':
            tbl[0:4] = struct.pack('<I', 100)
        if core_variant == 'cols90':
            tbl[0:4] = struct.pack('<I', 90)
        mu.mem_write(CORE_IMG + 0x163ac, bytes(tbl))
        mu.mem_write(CORE_IMG + 0x5336, b'\x8b\x88' + struct.pack('<I', CORE_IMG + 0x163ac + (4 if core_variant == 'code' else 0)) +
                     b'\x89\x0a\x8b\x80' + struct.pack('<I', CORE_IMG + 0x163b0))
        make_image(len(images), G('11111111-2222-3333-4444-555555555555') if core_variant == 'guid' else CORE_FILE,
                   CORE_IMG, CORE_SIZE - (0x100 if core_variant == 'small' else 0), nopath=core_variant == 'nopath')
    if nb_img == 'before':
        load_nbdxe()
    if mmc_img == 'before':
        load_mmc()
    if sdh_img == 'before':
        load_sdh()
    if usbrt_img == 'before':
        for base in rt_copies:
            make_image(len(images), USBRT_FILE, base, 0x9f80, nopath=False)
    if gop_img == 'before':                                       # the display driver (as on an OF1: before us)
        load_gop()
    if shell_img == 'before':                                     # (not on hardware: the shell loads at BDS)
        load_shell()
    nb_bad = {'n': 0}
    mmio = {0x6018: gop_dpll, 0x6048: fp, 0x61204: 0x00000001, 0x71008: 0x80000000}
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
    if flash_model:                                               # the boot block's ROM name (model.inc)
        mu.mem_write(FLASH + 0xea268, {'of1': b'0ABPW400.rom', 'of2': b'0ABPW005.rom', 'bad': b'0ABPW006.rom',
                                       'near': b'0ABPW004.rom', 'half': b'1ABPW005.rom'}[flash_model])
    mu.mem_write(ST, b'IBI SYST' + struct.pack('<III', 0x20000, 0x48, 0))
    mu.mem_write(ST + 0x100, AMI_VENDOR.encode('utf-16le') + b'\0\0')
    mu.mem_write(ST + 0x18, struct.pack('<II', ST + 0x100, 0x0004000d))   # FirmwareVendor, FirmwareRevision
    mu.mem_write(ST + 0x38, struct.pack('<II', RT, BS))
    if smbios_present:                                          # ACPI 2.0, then the firmware's SMBIOS
        mu.mem_write(ST + 0x40, struct.pack('<II', 2, ST + 0x800))
        mu.mem_write(ST + 0x800, G('8868e871-e4f1-11d3-bc22-0080c73c8881') + struct.pack('<I', ST + 0xa00))
        mu.mem_write(ST + 0x814, SMBIOS + struct.pack('<I', ST + 0x900))
    st_crc = lambda: zlib.crc32(bytes(mu.mem_read(ST, 0x10)) + b'\0' * 4 + bytes(mu.mem_read(ST + 0x14, 0x34)))
    kt = dict(t=500, inputs=[], keys=[k if len(k) == 3 else (0,) + tuple(k) for k in kt_keys], stub_calls=0)
    if kt_conin:                                                  # the console's input
        mu.mem_write(ST + 0x24, struct.pack('<I', CONIN))
    mu.mem_write(ST + 0x2c, struct.pack('<I', CONOUT))            # and output
    mu.mem_write(ST + 0x10, struct.pack('<I', st_crc()))
    st_orig = bytes(mu.mem_read(ST, 0x48))
    allocs = {}
    freed = set()
    mu.mem_write(BS, b'BOOTSERV' + struct.pack('<II', 0x20000, bs_hdr))
    for off in range(0x18, bs_hdr, 4):
        mu.mem_write(BS + off, struct.pack('<I', BS_STUB + off))
    mu.mem_write(BS + 0x10, struct.pack('<I', zlib.crc32(bytes(mu.mem_read(BS, bs_hdr)))))   # with its CRC field 0
    bs_orig = bytes(mu.mem_read(BS, bs_hdr))
    mu.mem_write(RT, b'RUNTSERV' + struct.pack('<II', 0x20000, 0x58))
    for off in range(0x18, 0x58, 4):
        mu.mem_write(RT + off, struct.pack('<I', RT_STUB + off))
    mu.mem_write(MP + 8, struct.pack('<I', STUBS + 0x10))     # StartupAllAPs
    mu.mem_write(STUBS, b'\xc3' * 0x3000)
    mu.mem_write(ACPISUP_INST, struct.pack('<III', ACPI_OTHER_STUB, ACPI_SET_STUB, ACPI_OTHER_STUB))
    assert len(usb_devs) <= 3
    for i_ in range(len(usb_devs)):
        slots = [USB_OTHER_STUB] * 13
        slots[0], slots[6], slots[10] = USB_CTRL_STUB, USB_DESC_STUB, USB_STR_STUB   # +0x00, +0x18, +0x28
        mu.mem_write(USBIO_AREA + 0x40 * i_, struct.pack('<13I', *slots))
    acpi = dict(locates=0, sets=[])
    usb = dict(desc=0, ctl=[], allocs=[])
    if True:                                                      # the console's input and device paths
        mu.mem_write(CONOUT + 0x1c, struct.pack('<I', CONOUT_CUR))
        mu.mem_write(CONIN, struct.pack('<III', STUBS + 0x60, CONIN_RKS, 0))
        mu.mem_write(KT_PATHS, bytes([1, 1, 6, 0, 0, 0x1d, 3, 5, 6, 0, 1, 0, 0x7f, 0xff, 4, 0]))   # PCI, USB, end
        mu.mem_write(KT_PATHS + 0x40, bytes([2, 1, 12, 0]) + struct.pack('<II', 0x0303d041, 0) + bytes([0x7f, 0xff, 4, 0]))
    pci_devs = [] if pin_variant == 'none' else [d for d in OF1_PCI if not (pin_variant == 'no_sd' and d[3] >> 16 == 0x0805)]
    if pin_variant == 'other_class':                              # an 08/05 function with another class code
        pci_devs = [(b_, d_, f_, (0x08800001 if c_ >> 16 == 0x0805 else c_)) for b_, d_, f_, c_ in pci_devs]
    for i_, _ in enumerate(pci_devs):
        mu.mem_write(PCIIO_AREA + 0x80 * i_, b'\0' * 0x80)
        mu.mem_write(PCIIO_AREA + 0x80 * i_ + 0x18, struct.pack('<I', PCI_READ_STUB))
    pin = dict(stage=0, conns=[])
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
        if addr == PCI_READ_STUB:                                  # PciIo->Pci.Read(This, Width, Offset, Count, Buf)
            i_ = (a[0] - PCIIO_AREA) // 0x80
            if not (0 <= i_ < len(pci_devs)) or (a[0] - PCIIO_AREA) % 0x80:
                errors.append('Pci.Read on %#x' % a[0])
                return ret(0x80000002)
            if (a[1], a[2], a[3]) != (2, 0x08, 1):
                errors.append('Pci.Read width %d offset %#x count %d' % (a[1], a[2], a[3]))
            if pin_variant == 'read_fails':
                return ret(0x80000007)
            uc.mem_write(a[4], struct.pack('<I', pci_devs[i_][3]))
            return ret(0)
        if addr == ACPI_SET_STUB:                                  # SetAcpiTable(This, Table, Checksum, Version, &Handle)
            ln = rd(a[1] + 4) if a[1] else 0
            acpi['sets'].append(dict(this=a[0], table=bytes(uc.mem_read(a[1], min(ln, 0x1000))) if a[1] else None,
                                     checksum=a[2] & 0xff, version=a[3], handle=rd(a[4]) if a[4] else None))
            if acpi_sup == 'fails':
                return ret(0x80000009)
            uc.mem_write(a[4], struct.pack('<I', 0x5D7))
            return ret(0)
        if addr == ACPI_OTHER_STUB:
            errors.append('ACPI Support: GetAcpiTable or PublishTables called')
            return ret(0x80000003)
        if addr in (USB_DESC_STUB, USB_STR_STUB, USB_CTRL_STUB):
            i_ = (a[0] - USBIO_AREA) // 0x40
            if not (0 <= i_ < len(usb_devs)) or (a[0] - USBIO_AREA) % 0x40:
                errors.append('UsbIo call on %#x' % a[0])
                return ret(0x80000002)
            vid, pid, iprod, text = usb_devs[i_]
            if addr == USB_DESC_STUB:                              # UsbGetDeviceDescriptor(This, &Desc)
                usb['desc'] += 1
                uc.mem_write(a[1], struct.pack('<BBHBBBBHHHBBBB', 18, 1, 0x0110, 0, 0, 0, 8, vid, pid, 0x0100, 1, iprod, 0, 1))
                return ret(0)
            if addr == USB_STR_STUB:                               # AMI's: never finds the language
                errors.append('UsbGetStringDescriptor called (AMI\'s fails for a device\'s only language)')
                return ret(0x8000000E)
            # UsbControlTransfer(This, &Request, Direction, Timeout, Data, DataLength, &Status)
            req = bytes(uc.mem_read(a[1], 8))
            usb['ctl'].append((i_, req, a[2], a[3], a[5]))
            want_req = bytes([0x80, 6, iprod, 3]) + struct.pack('<HH', 0x0409, a[5])
            if (vid, pid) != (0x04b4, 0x1974):
                errors.append('control transfer to %04x:%04x, not the touchscreen' % (vid, pid))
            if req != want_req or a[2] != 0 or not 0 < a[3] <= 5000 or not 0 < a[5] <= 128 or not a[6]:
                errors.append('control transfer %s dir %d timeout %d length %d status %#x' % (req.hex(), a[2], a[3], a[5], a[6]))
            if text is None:                                       # a device error: no data
                uc.mem_write(a[6], struct.pack('<I', 0x10))        # EFI_USB_ERR_TIMEOUT
                return ret(0x80000007)
            desc = text if isinstance(text, bytes) else bytes([2 + 2 * len(text), 3]) + text.encode('utf-16le')
            uc.mem_write(a[4], desc[:a[5]])                       # as much as asked for, or a short packet
            uc.mem_write(a[6], struct.pack('<I', 0))
            return ret(0)
        if addr == USB_OTHER_STUB:
            errors.append('UsbIo: a transfer or other descriptor called')
            return ret(0x80000003)
        if addr == GOP_BLT_STUB:                                   # the display driver's own Blt
            kt['own_blt'] = kt.get('own_blt', 0) + 1
            return ret(0)
        if addr == CONOUT_CUR:                                     # ConOut->SetCursorPosition(This, col, row)
            kt.setdefault('cursor', []).append((a[1], a[2]))
            return ret(0)
        if addr == CONIN_RKS:                                      # ConIn->ReadKeyStroke(This, &Key)
            kt['stub_calls'] += 1
            if a[0] != CONIN:
                errors.append('ReadKeyStroke with This %#x' % a[0])
            if kt_rks_error:
                return ret(0x80000007)
            if not kt['keys'] or kt['keys'][0][0] > kt['t']:      # (when it can be read, scan, char)
                return ret(0x80000006)
            uc.mem_write(a[1], struct.pack('<HH', *kt['keys'].pop(0)[1:]))
            return ret(0)
        if addr == SMMC_TRIG:                                      # SmmControl->Trigger, the real one
            tm_var.setdefault('trig_calls', []).append(tuple(a[:5]))
            return ret(0x5a5a0000 + len(tm_var['trig_calls']))
        if BS_STUB <= addr < BS_STUB + 0x100:
            off = addr - BS_STUB
            if off == 0x38 and a[0] in evs and evs[a[0]]['type'] == 0x80000000:   # SetTimer on a plain timer
                e_ = evs[a[0]]
                if a[1] != 2 or e_.get('closed'):
                    errors.append('SetTimer type %d on %#x' % (a[1], a[0]))
                if kt_settimer_fails:
                    return ret(0x80000002)
                e_.update(due=None if kt_timer_never else kt['t'] + (a[2] | a[3] << 32) // 10000, fired=False)
                kt['timer_set'] = (kt['t'], a[2] | a[3] << 32)
                return ret(0)
            if off == 0x48:                                        # CheckEvent(ev)
                e_ = evs.get(a[0])
                if not e_ or e_.get('closed') or e_.get('fn'):
                    errors.append('CheckEvent on %#x' % a[0])
                    return ret(0x80000002)
                if e_.get('due') is not None and kt['t'] >= e_['due'] and not e_['fired']:
                    e_['fired'] = True
                    return ret(0)
                return ret(0x80000006)
            if off == 0x38 and profile:                            # SetTimer(ev, type, lo, hi)
                tm_var.setdefault('timer', []).append((a[0], a[1], a[2], a[3]))
                return ret(0)
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
            if off == 0xac and screen and bytes(uc.mem_read(a[0], 16)) == GOP_GUID:
                uc.mem_write(a[2], struct.pack('<I', GOP_INST))
                return ret(0)
            if off == 0xac and usbtrace and bytes(uc.mem_read(a[0], 16)) == SMMC_GUID:
                return ret(0x8000000E)                             # not installed yet
            if off == 0xac and not screen and bytes(uc.mem_read(a[0], 16)) == GOP_GUID:
                if kt_gop == 'none':
                    return ret(0x8000000E)
                uc.mem_write(a[2], struct.pack('<I', GOP_INST))
                return ret(0)
            if off == 0xac and bytes(uc.mem_read(a[0], 16)) == ACPISUP_GUID:
                acpi['locates'] += 1
                if acpi_sup == 'none':
                    return ret(0x8000000E)
                uc.mem_write(a[2], struct.pack('<I', ACPISUP_INST))
                return ret(0)
            if off == 0xac:                                        # LocateProtocol(guid, reg, &if)
                ok = bytes(uc.mem_read(a[0], 16)) == MP_GUID
                if ok:
                    uc.mem_write(a[2], struct.pack('<I', MP))
                return ret(0 if ok else 0x8000000E)
            if off == 0x88:                                        # Stall(us)
                if a[0] > 100000:
                    errors.append('Stall of %d us' % a[0])
                if True:                                           # the scenario's clock moves on
                    kt['t'] += a[0] // 1000
                    kt['stalls'] = kt.get('stalls', 0) + 1
                return ret(0)
            if off == 0x34:                                        # CreateEvent(type, tpl, fn, ctx, &ev)
                if create_event_fails or (kt_timer_fails and a[0] == 0x80000000):
                    return ret(0x80000009)
                h = 0xE7E70010 + len(evs)
                evs[h] = dict(type=a[0], tpl=a[1], fn=a[2], ctx=a[3], handle=h)
                uc.mem_write(a[4], struct.pack('<I', h))
                return ret(0)
            if off == 0x60:                                        # RegisterProtocolNotify(guid, ev, &reg)
                g = bytes(uc.mem_read(a[0], 16))
                ok = (BLOCKIO, NB_STAGE, LOADED_IMAGE) + (tuple(TM_GUIDS) if timing else ()) + ((SMMC_GUID,) if usbtrace else ()) + (TEXTIN_GUID,)
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
            if off == 0x90:                                        # ConnectController(h, drivers, path, recursive)
                pin['conns'].append((a[0], a[1], a[2], a[3], pin['stage']))
                return ret(0)
            if off == 0xa8:                                        # LocateHandleBuffer(ByProtocol, guid, 0, &n, &buf)
                g = bytes(uc.mem_read(a[1], 16))
                if a[0] == 2 and g == SFS_GUID:                    # no file systems here
                    events.append('LocateHandleBuffer(SimpleFileSystem)')
                    return ret(0x8000000E)
                if a[0] == 2 and g == USBIO_GUID:                  # the USB devices (panel_id.inc)
                    events[-1] = 'LocateHandleBuffer(UsbIo)'       # not BlockIo: the app build may
                    if not usb_devs:
                        return ret(0x8000000E)
                    buf = pool['next']; pool['next'] += 4 * len(usb_devs)
                    allocs[buf] = (4, 4 * len(usb_devs))
                    usb['allocs'].append(buf)
                    uc.mem_write(buf, b''.join(struct.pack('<I', 0xB60000 + i) for i in range(len(usb_devs))))
                    uc.mem_write(a[3], struct.pack('<I', len(usb_devs)))
                    uc.mem_write(a[4], struct.pack('<I', buf))
                    return ret(0)
                if a[0] == 2 and g == TEXTIN_GUID:                 # the console inputs so far
                    if not kt['inputs']:
                        return ret(0x8000000E)
                    buf = pool['next']; pool['next'] += 4 * len(kt['inputs'])
                    allocs[buf] = (4, 4 * len(kt['inputs']))
                    uc.mem_write(buf, b''.join(struct.pack('<I', 0xB50000 + i) for i in range(len(kt['inputs']))))
                    uc.mem_write(a[3], struct.pack('<I', len(kt['inputs'])))
                    uc.mem_write(a[4], struct.pack('<I', buf))
                    return ret(0)
                if a[0] != 2 or g not in (BLOCKIO, LOADED_IMAGE, PCIIO_GUID):
                    errors.append('LocateHandleBuffer other than ByProtocol BlockIo/LoadedImage/PciIo')
                    return ret(0x80000002)
                hs = ([0xB10000 + i for i in range(len(bios))] if g == BLOCKIO
                      else ([] if pin_variant == 'locate_fails' else [0xB40000 + i for i in range(len(pci_devs))]) if g == PCIIO_GUID
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
                if g == SMMC_GUID and usbtrace and a[0] == 0xB30000:
                    uc.mem_write(a[2], struct.pack('<I', SMMC_INST))
                    return ret(0)
                if g == LOADED_IMAGE and 0 <= a[0] - 0xB20000 < len(images):
                    uc.mem_write(a[2], struct.pack('<I', images[a[0] - 0xB20000]))
                    return ret(0)
                if g == DP_GUID and 0 <= a[0] - 0xB50000 < len(kt['inputs']):
                    kind = kt['inputs'][a[0] - 0xB50000]
                    if kind == 'split':                            # ConSplitter's own: no device path
                        return ret(0x80000003)
                    uc.mem_write(a[2], struct.pack('<I', KT_PATHS + (0 if kind == 'usb' else 0x40)))
                    return ret(0)
                if g == USBIO_GUID and 0 <= a[0] - 0xB60000 < len(usb_devs):
                    uc.mem_write(a[2], struct.pack('<I', USBIO_AREA + 0x40 * (a[0] - 0xB60000)))
                    return ret(0)
                if g == PCIIO_GUID and 0 <= a[0] - 0xB40000 < len(pci_devs):
                    uc.mem_write(a[2], struct.pack('<I', PCIIO_AREA + 0x80 * (a[0] - 0xB40000)))
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
                import re as _re
                ok_name = name == 'BlwBootTimes' or (profile and (name == 'BlwProfImg' or _re.fullmatch(r'BlwProf[0-9A-F]{2}', name))) \
                    or (usbtrace and _re.fullmatch(r'BlwUsbTr[0-9A-F]{2}', name))
                if not ok_name or g != BLW or a[2] & 1 or a[2] != 6:
                    errors.append('SetVariable(%s, attr %#x) in the timing build' % (name, a[2]))
                    return ret(0x80000002)
                if name != 'BlwBootTimes':
                    tm_var.setdefault('prof', {})[name] = bytes(uc.mem_read(a[4], a[3])) if a[3] else b''
                    events.append('SetVariable(%s)' % name)
                    return ret(0)
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
    if kbdtime:                                                   # kt_ms's rdtsc: the scenario's clock
        at = pe.find(bytes.fromhex('510f31b9') + struct.pack('<I', KT_TSC_KHZ))
        if at < 0:
            errors.append('kbdtime: kt_ms not found')
        else:
            def kt_rdtsc(uc, addr, size, _):
                tsc = kt['t'] * KT_TSC_KHZ + 1000
                uc.reg_write(UC_X86_REG_EAX, tsc & 0xffffffff); uc.reg_write(UC_X86_REG_EDX, tsc >> 32)
                uc.reg_write(UC_X86_REG_EIP, addr + 2)
            mu.hook_add(UC_HOOK_CODE, kt_rdtsc, begin=LOAD + at + 1, end=LOAD + at + 1)
    # cpuid, rdmsr and wrmsr: hooked at each place they occur in the image (an instruction only
    # fires its hook where it starts, so a byte pair inside other code or data never triggers it)
    image_now = bytes(mu.mem_read(LOAD, size_img))
    for op in (b'\x0f\xa2', b'\x0f\x32', b'\x0f\x30'):
        i = image_now.find(op)
        while i >= 0:
            mu.hook_add(UC_HOOK_CODE, cpu_hook, begin=LOAD + i, end=LOAD + i)
            i = image_now.find(op, i + 1)
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
            mu.emu_start(fn, RET_MAGIC, count=5_000_000)
        except UcError as e:
            errors.append('%s: emulation stopped: %s at eip %#x' % (what, e, mu.reg_read(UC_X86_REG_EIP)))
            return None
        if mu.reg_read(UC_X86_REG_EIP) != RET_MAGIC:              # (the count ran out: not a stack fault)
            errors.append('%s: still running after 5,000,000 instructions, at eip %#x' % (what, mu.reg_read(UC_X86_REG_EIP)))
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
    def notify(guid, what):                                       # highest TPL first, as the core does
        for e in sorted(evs.values(), key=lambda e: -e.get('tpl', 0)):
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
    if sdh_img == 'after':                                        # SdHost loads after it
        load_sdh()
        for e in evs.values():
            if e.get('guid') == LOADED_IMAGE:
                li_queues[e['key']].append(0xB20000 + len(images) - 1)
        notify(LOADED_IMAGE, 'LoadedImage notify')
    if timing:                                                    # the watched protocols appear
        for g in TM_GUIDS:
            notify(g, 'timing notify')
    if True:                                                      # console inputs appear (BDS connects them)
        for kind, t in kt_inputs:
            kt['t'] = t
            kt['inputs'].append(kind)
            notify(TEXTIN_GUID, 'Simple Text Input notify')
    if gop_img == 'after':                                        # the display driver loads after it
        load_gop()
        for e in evs.values():
            if e.get('guid') == LOADED_IMAGE:
                li_queues[e['key']].append(0xB20000 + len(images) - 1)
        notify(LOADED_IMAGE, 'LoadedImage notify')
    if shell_img == 'after':                                      # the shell loads (at BDS, from the boot option)
        kt['t'] = kt_shell_t
        load_shell()
        for e in evs.values():
            if e.get('guid') == LOADED_IMAGE:
                li_queues[e['key']].append(0xB20000 + len(images) - 1)
        notify(LOADED_IMAGE, 'LoadedImage notify')
    if usbrt_img == 'after':                                      # USBRT's two copies load (as on an OF1)
        load_usbrt()
        if screen:
            hook_stub_rdtsc()
        ident = tm_var['ident'] = []
        for k_, base in enumerate(rt_copies):
            if (usbrt_variant == 'closed' and k_ == 1) or usbrt_variant == 'a_changed':
                continue                                          # (a_changed breaks identify's own call)
            dev = USB_AREA + 0x39000
            mu.mem_write(dev, b'\0' * 0x100)
            mu.mem_write(USB_AREA + 0x3a000, b'\0' * 0x20)
            mu.mem_write(USB_AREA + 0x7000 + 0xde4, b'\0\0')
            at = USB_AREA + 0x38000 + 0x600 + 0x20 * k_            # push ebp; mov ebp, esp; sub esp, 16; push ebx; push esi;
            mu.mem_write(at, b'\x55\x89\xe5\x83\xec\x10\x53\x56\xbe' + struct.pack('<I', dev) +   # mov esi, dev; mov ebx, 0x103;
                         b'\xbb\x03\x01\x00\x00\xb8' + struct.pack('<I', base + 0x56c3) + b'\xff\xe0')  # jmp +0x56c3
            call(at, [], 'identify\'s first step (copy %d)' % k_)
            cdb = bytes(mu.mem_read(USB_AREA + 0x3a000, 5))
            ident.append((k_, cdb[0], cdb[4], struct.unpack('<H', mu.mem_read(USB_AREA + 0x7000 + 0xde4, 2))[0]))
        # READ CAPACITY's call into the dispatcher, run (F): a stand-in dispatcher answers as a caddy
        # would: FFFFFFFF ('big', a drive over 2 TiB), a normal capacity ('small'), or nothing ('fail')
        rcap = tm_var['rcap'] = []
        disp = {'on': False, 'log': [], 'case': None}
        def disp_hook(uc, addr, size, _):
            if not disp['on']:
                return
            r32 = lambda a: struct.unpack('<I', uc.mem_read(a, 4))[0]
            cmdp, dea, deb = r32(G_FAKE + 0xde6), uc.mem_read(G_FAKE + 0xdea, 1)[0], uc.mem_read(G_FAKE + 0xdeb, 1)[0]
            dec, df0 = r32(G_FAKE + 0xdec), r32(G_FAKE + 0xdf0)
            # the command as it goes out: USBRT's builder (+0x476d) moves it up 15 bytes, then (unless
            # H took its rep stos out) clears as many bytes at the start as it is long
            blk = bytearray(uc.mem_read(cmdp, 31))
            blk[15:15 + dea] = blk[0:dea]
            if bytes(uc.mem_read(addr - 0x49ae + 0x47bb, 2)) == b'\xf3\xab':
                blk[0:dea] = bytes(dea)
            cdb = bytes(blk[15:31])
            disp['log'].append((cdb[:dea], deb, dec, df0, cmdp, uc.reg_read(UC_X86_REG_ESP) & 3))
            uc.mem_write(cmdp, b'USBC' + bytes(range(0x41, 0x41 + 27)))   # (USBRT builds the command block in place)
            ret = 0
            if cdb[0] == 0x25 and disp['case'] != 'fail':
                uc.mem_write(dec, bytes.fromhex('ffffffff00000200' if disp['case'] == 'big' else '003fffff00000200'))
                ret = 8
            elif cdb[0] == 0x9e:
                uc.mem_write(dec, bytes.fromhex('00000001d1c0beaf00000200') + b'\x11' * 20)
                ret = 32
            esp = uc.reg_read(UC_X86_REG_ESP)                     # (the dispatcher's own ret)
            uc.reg_write(UC_X86_REG_EAX, ret)
            uc.reg_write(UC_X86_REG_EIP, r32(esp))
            uc.reg_write(UC_X86_REG_ESP, esp + 4)
        for base in rt_copies:
            mu.hook_add(UC_HOOK_CODE, disp_hook, begin=base + 0x49ae, end=base + 0x49ae)
        cmdbuf, databuf = USB_AREA + 0x3b000, USB_AREA + 0x3b100
        for k_, base in enumerate(rt_copies):
            if usbrt_variant == 'closed' and k_ == 1:
                continue
            target = base + 0x4a60 + struct.unpack('<i', mu.mem_read(base + 0x4a5c, 4))[0]
            for case in ('big', 'small', 'fail'):
                mu.mem_write(cmdbuf, b'\x25' + b'\0' * 31)          # as READ CAPACITY sets things up
                # (a failed command leaves the buffer as it was: here, a stale FFFFFFFF answer)
                mu.mem_write(databuf, (bytes.fromhex('ffffffff00000200') if case == 'fail' else b'') + b'\0' * 64)
                mu.mem_write(G_FAKE + 0xde6, struct.pack('<I', cmdbuf) + bytes([12, 0x80]) + struct.pack('<II', databuf, 8))
                mu.mem_write(G_FAKE + 0xdcd, struct.pack('<I', databuf))
                disp.update(on=True, log=[], case=case)
                ret = call(target, [USB_AREA + 0x39000], 'READ CAPACITY (copy %d, %s)' % (k_, case))
                disp['on'] = False
                rcap.append((k_, case, target - base, ret, list(disp['log']), bytes(mu.mem_read(databuf, 8)),
                             bytes(mu.mem_read(G_FAKE + 0xde6, 14))))
    if usbtrace:                                                  # UHCD, SmmControl
        uh = bytearray(0x68e0)
        uh[0x4fcd] = 0xa1 if uhcd_variant == 'stock' else 0x8b      # another UHCD: requests not decoded
        uh[0x4fce:0x4fd2] = struct.pack('<I', USB_AREA + 0x62b8)
        uh[0x62bc:0x62c0] = struct.pack('<I', USB_AREA + 0x7000)          # gUsbData
        mu.mem_write(USB_AREA, bytes(uh))
        mu.mem_write(USB_AREA + 0x7000 + 0xdd9, struct.pack('<I', USB_AREA + 0x6700))   # the request
        make_image(len(images), UHCD_FILE, USB_AREA, 0x68e0, nopath=False)
        for e in evs.values():
            if e.get('guid') == LOADED_IMAGE:
                li_queues[e['key']].append(0xB20000 + len(images) - 1)
        notify(LOADED_IMAGE, 'LoadedImage notify')
        mu.mem_write(SMMC_INST, struct.pack('<I', SMMC_TRIG))
        for e in evs.values():
            if e.get('guid') == SMMC_GUID:
                li_queues[e['key']].append(0xB30000)
        notify(SMMC_GUID, 'SmmControl notify')
        code = USB_AREA + 0x38000
        def trig(smi, fn, sub):
            mu.mem_write(USB_AREA + 0x6700, bytes([fn, sub]))
            mu.mem_write(USB_AREA + 0x6600, bytes([smi]) + b'\0\0\0' + struct.pack('<I', 1))
            st = (b'\x68' + struct.pack('<I', USB_AREA + 0x435) + b'\x55\x89\xe5\x6a\x00\x6a\x00' +
                  b'\x68' + struct.pack('<I', USB_AREA + 0x6604) + b'\x68' + struct.pack('<I', USB_AREA + 0x6600) +
                  b'\x68' + struct.pack('<I', SMMC_INST) + b'\xff\x15' + struct.pack('<I', SMMC_INST) +
                  b'\x83\xc4\x14\x5d\x83\xc4\x04\xc3')
            mu.mem_write(code, st)
            return call(code, [], 'UHCD SMI')
        def stall(us):                                            # one stub per value (no code rewritten)
            at = code + 0x100 + (0x40 if us != 1000 else 0)
            st = b'\x68' + struct.pack('<I', us) + b'\xff\x15' + struct.pack('<I', BS + 0x88) + b'\x83\xc4\x04\xc3'
            mu.mem_write(at, st)
            call(at, [], 'Stall')
            return at + 11
        if screen and screen_variant == 'late_mode':             # the console has set the mode by now
            mu.mem_write(GOP_INST + 0x110, struct.pack('<Q', FB))
        ut_expect = []
        for smi, fn, sub in ((0x31, 0x27, 0x05), (0x31, 0x2e, 0x00), (0x32, 0x99, 0x99)):
            r_ = trig(smi, fn, sub)
            ut_expect.append(('T', USB_AREA + 0x435, 0x54000000 | smi | ((fn | sub << 8) << 8 if smi == 0x31 and uhcd_variant == 'stock' else 0), r_))
        for k in range(5):
            sret = stall(1000)
        sret2 = stall(50000)
        tm_var['ut_stall_ret2'] = sret2
        tm_var['ut_expect'] = ut_expect
        tm_var['ut_stall_ret'] = sret
        if screen:
            nfd = {'k': 0}
            def fd(base, n):                                       # push n; call FixedDelay; add esp, 4; ret
                at = code + 0x200 + 0x20 * nfd['k']                # each at its own address (no code rewritten)
                nfd['k'] += 1
                mu.mem_write(at, b'\x6a' + bytes([n]) + b'\xb8' + struct.pack('<I', base + 0x1381) + b'\xff\xd0\x83\xc4\x04\xc3')
                call(at, [], 'FixedDelay')
                return at + 9
            def cbw(base, op, length):                             # the four arguments, then jump to the send
                buf = USB_AREA + 0x38800
                mu.mem_write(buf, b'USBC' + struct.pack('<II', 7, length) + bytes([0x80, 0, 10, op]) + b'\0' * 15)
                at = code + 0x300
                mu.mem_write(at, b'\x6a\x1f\x68' + struct.pack('<I', buf) + b'\x6a\x00\x68' + struct.pack('<I', 0xde) +
                             b'\xb8' + struct.pack('<I', base + 0x4811) + b'\xff\xe0')
                call(at, [], 'command send')
            sc = tm_var['screen'] = {'fd': [], 'cbw': []}
            live = [b for b in rt_copies if not (usbrt_variant == 'closed' and b == rt_copies[1])]
            for base, n in [(live[0], 5)] + [(live[-1], 3), (live[-1], 4)]:
                sc['fd'].append((base, fd(base, n), n))
            for op, ln in ((0x12, 0x24), (0x25, 8)):
                cbw(live[-1], op, ln)
                sc['cbw'].append((live[-1], op, ln))
            sc['sense'] = []
            for k in range(2):                                     # push 0xde; call REQUEST SENSE; add esp, 4; ret
                at = code + 0x500 + 0x20 * k
                mu.mem_write(at, b'\x68\xde\x00\x00\x00\xb8' + struct.pack('<I', live[-1] + 0x4c61) + b'\xff\xd0\x83\xc4\x04\xc3')
                sc['sense'].append((live[-1], call(at, [], 'REQUEST SENSE')))
            sc['ctl'] = []                                         # through the (maybe patched) function tables
            # (kind: 0 control, 2 bulk, 3 interrupt; UHCI?; address; port; arguments 3, 4, 5, 7)
            # every kind is on the screen at one of the two snapshots (the 5th's start, the 7th's start)
            xfers = [(0, 0, 0x10, 4, 0x0680, 0, 0x0100, 8), (0, 1, 3, 2, 0x0680, 0, 0x0200, 0x20), (2, 0, 5, 4, 0x80, 0x1234, 0x200, 0),
                     (2, 1, 3, 2, 0x00, 0x5678, 0x1f, 0), (3, 0, 6, 5, 0x81, 0x4321, 8, 0), (0, 0, 5, 4, 0x0500, 0, 5, 0),
                     (3, 1, 7, 1, 0x82, 0x8765, 4, 0)]
            for k, (kind, u, addr, port, a3, a4, a5, a7) in enumerate(xfers):
                dev = USB_AREA + 0x35000 + 0x100 * k
                mu.mem_write(dev + 6, bytes([addr, 0, 0, port]))
                site = {0: (0x7401, 0x2765), 2: (0x7408, 0x276c), 3: (0x740f, 0x2773)}[kind][u]
                at = code + 0xa00 + 0x40 * k                    # (not 0x600+: the identify test runs code there)
                mu.mem_write(at, b''.join(b'\x68' + struct.pack('<I', v) for v in (a7, USB_AREA + 0x36000, a5, a4, a3, dev, USB_AREA + 0x37000)) +
                             b'\xa1' + struct.pack('<I', live[-1] + site + 3) + b'\xff\xd0\x83\xc4\x1c\xc3')
                sc['ctl'].append((live[-1], kind, u, addr, port, a3, a4, a5, a7, call(at, [], 'transfer')))
            sc['saved_line'] = bytes(mu.mem_read(SAVE_FB, 16 * 800 * 4))
            sc['saved_ring'] = bytes(mu.mem_read(SAVE_FB + 0x10000, 88 * 800 * 4))
            sc['fb_before_rtb'] = bytes(mu.mem_read(FB, FB_W * FB_H * 4))
    if profile:                                                   # timer ticks with fake interrupt frames
        tev = [e for e in evs.values() if e.get('type') == 0x80000200]
        img = bytes(mu.mem_read(LOAD, 0x3000))
        callpos = next(i for i in range(0x100, 0x3000 - 8) if img[i] == 0xe8) + 5   # just after a call rel32
        nocall = next(i for i in range(0x100, 0x3000) if img[i - 5] != 0xe8 and img[i - 6] != 0xff and img[i - 3] != 0xff and img[i - 2] != 0xff)
        cs = mu.reg_read(UC_X86_REG_CS)
        prof_expect = []
        for k in range(tm_ticks if tev else 0):
            sp = STACK + 0x4000
            mu.mem_write(sp, b'\0' * 0x800)
            eip = LOAD + 0x200 + k
            frame = sp + 4 + 8 + 0x40
            mu.mem_write(frame - 0x20, struct.pack('<III', LOAD + 0x999, cs, 0x7))   # a decoy: EFLAGS impossible
            mu.mem_write(frame - 0x10, struct.pack('<III', 0x10, cs, 0x246))         # a decoy: EIP implausible
            if k % 5 != 4:                                        # every fifth: no frame at all
                mu.mem_write(frame, struct.pack('<III', eip, cs, 0x246))
                mu.mem_write(frame + 12, struct.pack('<IIII', 0x12345678, LOAD + nocall, LOAD + callpos, 0xd0100000))
                prof_expect.append((eip, LOAD + callpos, 0))
            else:
                prof_expect.append((0, 0, 0))
            mu.mem_write(sp, struct.pack('<III', RET_MAGIC, tev[0]['handle'], 0))
            mu.reg_write(UC_X86_REG_ESP, sp)
            for r, v in SAVED:
                mu.reg_write(r, v)
            try:
                mu.emu_start(tev[0]['fn'], RET_MAGIC, count=1_000_000)
            except UcError as e:
                errors.append('profile tick: %s at %#x' % (e, mu.reg_read(UC_X86_REG_EIP)))
                break
            if mu.reg_read(UC_X86_REG_ESP) != sp + 4 or not all(mu.reg_read(r) == v for r, v in SAVED):
                errors.append('profile tick: stack or registers not kept')
        tm_var['expect'] = prof_expect[:512]
        tm_var['tev'] = tev
    cpu['phase'] = 'BDS'
    for k in bio_late:                                            # a driver installs BlockIo later
        install_bio(k)
        notify(BLOCKIO, 'BlockIo notify')
    enumerate_pci()
    if gma and nb_stage and img_is_dxe:
        nb_wrote = []
        for _ in range(nb_signals):                               # NBDXE's notify runs first, then ours
            if nb_img != 'none' and bytes(mu.mem_read(NB_IMG + 0x3ca, 1)) == b'\xc3':
                nb_wrote.append(None)                             # its function returns at once
            else:
                v = rd(NB_IMG + NB_IMM) if nb_img != 'none' else nb_value
                nb_wrote.append(v)
                mu.mem_write(GMA_MMIO + 0x6018, struct.pack('<I', v))
            pin['stage'] += 1
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
        kt['t'] = 4000
        for _ in range(signals):
            call(ev['fn'], [0xE7E70001, ev['ctx']], 'ReadyToBoot notify')
    if True:                                                      # the shell asks for its one key, twice
        kt['t'] = kt_rks_t
        kt['rks'] = []
        conin = rd(ST + 0x24)
        if conin:
            for _ in range(2):
                mu.mem_write(STACK + 0x8000, b'\xee' * 4)
                stalls0 = kt.get('stalls', 0)
                st_ = call(rd(conin + 4), [conin, STACK + 0x8000], 'the shell\'s ReadKeyStroke')
                kt['rks'].append((st_, bytes(mu.mem_read(STACK + 0x8000, 4)), kt['stub_calls']))
                kt.setdefault('rks_t', []).append((kt['t'], kt.get('stalls', 0) - stalls0))
        for kind, t in kt_late:                                   # a keyboard that comes later
            kt['t'] = t
            kt['inputs'].append(kind)
            notify(TEXTIN_GUID, 'Simple Text Input notify')
        kt['conin_rks'] = rd(CONIN + 4)
        kt['blt'] = 'none' if kt_gop == 'none' or screen else 'ours' if LOAD <= rd(GOP_INST + 8) < LOAD + size_img else 'own' if rd(GOP_INST + 8) == GOP_BLT_STUB else '%#x' % rd(GOP_INST + 8)
        if kt_blt_test:
            kt['blt_errors'] = blt_check(mu, call, rd(GOP_INST + 8))
        kt['timers'] = [(e_.get('due'), e_.get('closed', False)) for e_ in evs.values() if e_.get('type') == 0x80000000]
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
              or next((e for e in ordered if 'guid' not in e and e.get('type') != 0x80000000), {}))
    nb_ev = next((e for e in evs.values() if e.get('guid') == NB_STAGE), {})
    final = struct.unpack('<I', mu.mem_read(GMA_MMIO + 0x6018, 4))[0] if gma else None
    nb_now = bytes(mu.mem_read(NB_IMG, 0x3000))
    nb_changed = [i for i in range(0x3000) if nb_now[i] != nb_orig[i]]
    if usbtrace and screen and 'screen' in tm_var:
        sc = tm_var['screen']
        live = [b for b in rt_copies if not (usbrt_variant == 'closed' and b == rt_copies[1])]
        at = USB_AREA + 0x38000 + 0x400
        mu.mem_write(at, b'\x6a\x06\xb8' + struct.pack('<I', live[-1] + 0x1381) + b'\xff\xd0\x83\xc4\x04\xc3')
        call(at, [], 'FixedDelay after ReadyToBoot')
        sc['fb_end'] = bytes(mu.mem_read(FB, FB_W * FB_H * 4))
        sc['rt_end'] = [bytes(mu.mem_read(b, 0x9f80)) for b in rt_copies]
    if usbtrace:
        tm_var['bs_stall'] = rd(BS + 0x88)
        hs = rd(BS + 0x0c); crc = rd(BS + 0x10)
        hdr = bytearray(mu.mem_read(BS, hs)); hdr[0x10:0x14] = b'\0' * 4
        tm_var['bs_crc_ok'] = zlib.crc32(bytes(hdr)) == crc and bytes(mu.mem_read(BS, hs)) == bs_orig
        tm_var['trig_now'] = rd(SMMC_INST)

    mmc_now = bytes(mu.mem_read(MMC_IMG, 0x34c0))
    mmw_bytes = set(a + k for a in MMW_SITES for k in range(4))
    cea_changed = [(i, mmc_now[i]) for i in range(0x34c0) if mmc_now[i] != mmc_orig[i] and i not in mmw_bytes]
    mmw_changed = [(i, mmc_now[i]) for i in range(0x34c0) if mmc_now[i] != mmc_orig[i] and i in mmw_bytes]
    sdh_now = bytes(mu.mem_read(SDH_IMG, SDH_SIZE))
    sdh_target = None                                             # what the glue's call reaches (mmcinit.inc)
    if sdh_now[0x4f6] == 0xa1 and sdh_now[0x4fd] == 0xe8:
        try:
            t_ = SDH_IMG + 0x4f6 + 12 + struct.unpack_from('<i', sdh_now, 0x4f6 + 8)[0]
            sdh_target = bytes(mu.mem_read(t_, len(SDH_WAIT)))
        except Exception:
            sdh_target = b''
    sdh_changed = [(i, sdh_now[i]) for i in range(SDH_SIZE) if sdh_now[i] != sdh_orig[i]]
    gop_now = bytes(mu.mem_read(GOP_IMG, GOP_SIZE))
    shell_now = bytes(mu.mem_read(SHELL_IMG, len(shell_orig)))
    rt_now = [bytes(mu.mem_read(a, 0x9f80)) for a in rt_copies]
    core_cols = struct.unpack('<I', mu.mem_read(CORE_IMG + 0x163ac, 4))[0] if core_variant != 'none' else None
    core_rest = bytes(mu.mem_read(CORE_IMG + 0x163b0, 56)) == CORE_TABLE[4:] if core_variant not in ('none', 'table') else None
    usb['unfreed'] = [b for b in usb['allocs'] if b not in freed]
    return dict(acpi=acpi, usb=usb, kt=kt, core_cols=core_cols, core_rest=core_rest, shell_orig=shell_orig, shell_want=shell_want, shell_now=shell_now, gop_orig=gop_orig, gop_want=gop_want, gop_now=gop_now, ident=tm_var.get('ident'), rcap=tm_var.get('rcap'), rt_orig=rt_orig, rt_now=rt_now, pin_conns=pin['conns'], pin_devs=pci_devs, cea_changed=cea_changed, mmw_changed=mmw_changed, sdh_changed=sdh_changed, sdh_target=sdh_target, sdh_orig=sdh_orig, sdh_now=sdh_now, tm_var=tm_var, nb_imm=struct.unpack_from('<I', nb_now, NB_IMM)[0], nb_first=nb_now[0x3ca], nb_changed=nb_changed, nb_wrote=nb_bad.get('wrote', []),
                mmc_ev=mmc_ev, nb_ev=nb_ev, evs=evs, mmio_writes=mmio_writes, dpll=final,
                pp_final=struct.unpack('<I', mu.mem_read(GMA_MMIO + 0x61204, 4))[0] if gma else None,
                nb_after_notify=nb_bad.get('after_notify'), wrapped=wrapped, entry_bio=entry_bio, late_bio=late_bio, in_image=in_image,
                bios=bios, mmc_fns=(MMC_RD, MMC_WR, OTHER_FN),
                status=status, ev=ev, events=events, errors=errors, mac=bytes(nic['mac']), entry_mac=entry_mac,
                io=nic['io'], rev=cpu['rev'], smbios=smbios_now(), entry_smbios=entry_smbios,
                vendor=wstring(vptr), vendor_alloc=allocs.get(vptr), crc_ok=rd(ST + 0x10) == st_crc(),
                st_unchanged=st_now == st_orig)


SYSTEM = ['OpenPeak', 'OpenFrame 1', 'OF1', 'OpenFrame']


def main():
    first_fail = '--first-fail' in sys.argv                       # stop at the first failing scenario (mutation runs)
    only = sys.argv[sys.argv.index('--only') + 1] if '--only' in sys.argv else None   # run just the matching scenarios
    screen = '--screen' in sys.argv                               # ... and -DSCREEN
    usbtrace = '--usbtrace' in sys.argv or screen                 # ... and -DUSBTRACE
    profile = '--profile' in sys.argv or usbtrace                 # ... with -DPROFILE as well
    timing = '--timing' in sys.argv or profile                    # the -DTIMING development build
    kbdtime = '--kbdtime' in sys.argv                             # the -DKBDTIME development build
    no_cmd0 = '--cmd0' not in sys.argv                            # only a -DMMI_CMD0 build changes the CMD0 waits
    global SYSTEM                                                 # SMBIOS Type 1 the build was given (build_ucode_driver.py --system)
    if '--system' in sys.argv:
        SYSTEM = sys.argv[sys.argv.index('--system') + 1].split(',')
    files = [x for i, x in enumerate(sys.argv[1:], 1) if not x.startswith('--') and sys.argv[i - 1] not in ('--only', '--system')]
    dxe, app = (open(p, 'rb').read() for p in files[:2])
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
        ('Samsung panel (Cisco OF2): NBDXE writes nothing, the display driver\'s own clock kept', dxe, dict(store=lm(CARD), fp=0x00031008, gop_dpll=0xd8200003, gop_variant='samsung', want_pclk='untouched'), 0, True, MAC, True),
        ('Samsung panel, NBDXE not patched: its value left, not swapped for the Sharp panel\'s', dxe, dict(store=lm(CARD), fp=0x00031008, gop_dpll=0xd8200003, nb_variant='code', want_nbp='unpatched', want_pclk='untouched'), 0, True, MAC, True),
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
        ('MmcMediaDevice loaded before the driver: patched at the entry', dxe, dict(store=lm(CARD), mmc_img='before'), 0, True, MAC, True),
        ('NBDXE, MmcMediaDevice, SdHost, USBRT, the display driver and the shell all loaded before the driver: all patched at the entry, no LoadedImage notify', dxe, dict(store=lm(CARD), mmc_img='before', sdh_img='before', usbrt_img='before', shell_img='before'), 0, True, MAC, True),
        ('all but SdHost loaded before the driver: the LoadedImage notify stays for it', dxe, dict(store=lm(CARD), mmc_img='before', usbrt_img='before'), 0, True, MAC, True),
        ('all but the display driver loaded before the driver: the notify stays, its splash written as it loads', dxe, dict(store=lm(CARD), mmc_img='before', sdh_img='before', usbrt_img='before', shell_img='before', gop_img='after'), 0, True, MAC, True),
        ('all but the shell loaded before the driver: the notify stays, its countdown made 0 as it loads', dxe, dict(store=lm(CARD), mmc_img='before', sdh_img='before', usbrt_img='before', shell_img='after'), 0, True, MAC, True),
        # mmcinit.inc: MmcMediaDevice's waits after CMD0, SdHost's command wait. want_mmw / want_sdh:
        # 'patched' (exactly the expected bytes) or 'unpatched' (untouched).
        ('SdHost loaded before the driver: its wait changed at the entry', dxe, dict(store=lm(CARD), sdh_img='before'), 0, True, MAC, True),
        ('no SdHost: the rest still changed', dxe, dict(store=lm(CARD), sdh_img='none'), 0, True, MAC, True),
        ('SdHost with one byte of the loop different: left alone', dxe, dict(store=lm(CARD), sdh_variant='code'), 0, True, MAC, True),
        ('the same loop under another file GUID: SdHost left alone', dxe, dict(store=lm(CARD), sdh_variant='guid'), 0, True, MAC, True),
        ('SdHost image smaller than the loop: left alone', dxe, dict(store=lm(CARD), sdh_variant='small'), 0, True, MAC, True),
        ('SdHost with no FilePath: left alone', dxe, dict(store=lm(CARD), sdh_variant='nopath'), 0, True, MAC, True),
        ('MmcMediaDevice SD-path wait not stock: neither wait changed, the CE-ATA check still skipped', dxe, dict(store=lm(CARD), mmw_variant='sd_changed'), 0, True, MAC, True),
        ('MmcMediaDevice MMC-path wait not stock: neither wait changed', dxe, dict(store=lm(CARD), mmw_variant='mmc_changed'), 0, True, MAC, True),
        ('MmcMediaDevice waits already 1 ms: left as they are', dxe, dict(store=lm(CARD), mmw_variant='done'), 0, True, MAC, True),

        ('NBDXE and MmcMediaDevice both load after the driver: both patched as they load', dxe, dict(store=lm(CARD), nb_img='after', mmc_img='after'), 0, True, MAC, True),
        ('no MmcMediaDevice: NBDXE still patched, nothing else changed', dxe, dict(store=lm(CARD), mmc_img='none'), 0, True, MAC, True),
        ('MmcMediaDevice with one byte of the CE-ATA check different: check left alone, the waits after CMD0 still changed', dxe, dict(store=lm(CARD), mmc_variant='code'), 0, True, MAC, True),
        ('the same check under another file GUID: left alone', dxe, dict(store=lm(CARD), mmc_variant='guid'), 0, True, MAC, True),
        ('MmcMediaDevice image smaller than the check: left alone', dxe, dict(store=lm(CARD), mmc_variant='small'), 0, True, MAC, True),
        ('MmcMediaDevice with no FilePath: left alone', dxe, dict(store=lm(CARD), mmc_variant='nopath'), 0, True, MAC, True),
        ('MmcMediaDevice check already skipped: left as it is', dxe, dict(store=lm(CARD), mmc_variant='done'), 0, True, MAC, True),
        ('MmcMediaDevice before the driver, NBDXE changed: MMC patched, NBDXE left alone', dxe, dict(store=lm(CARD), mmc_img='before', nb_variant='code', want_nbp='unpatched', want_pclk='notify'), 0, True, MAC, True),
        ('LoadedImage notify not registered: MmcMediaDevice loading later is left alone', dxe, dict(nb_img='none', store=lm(CARD), register_fails=True, want_mmc_ev='closed', want_pclk='rtb'), 0, True, MAC, True),
        # mmc_pin.inc: the SD host controllers connected at the first stage signal
        ('MMC pin: no PCI functions at all: nothing connected', dxe, dict(store=lm(CARD), pin_variant='none'), 0, True, MAC, True),
        ('MMC pin: no SD host controller: nothing connected', dxe, dict(store=lm(CARD), pin_variant='no_sd'), 0, True, MAC, True),
        ('MMC pin: class 08 but not subclass 05: nothing connected', dxe, dict(store=lm(CARD), pin_variant='other_class'), 0, True, MAC, True),
        ('MMC pin: Pci.Read fails: nothing connected, pool freed', dxe, dict(store=lm(CARD), pin_variant='read_fails'), 0, True, MAC, True),
        ('MMC pin: LocateHandleBuffer(PciIo) fails: nothing connected', dxe, dict(store=lm(CARD), pin_variant='locate_fails'), 0, True, MAC, True),
        ('MMC pin: stage signalled twice: connected once', dxe, dict(nb_img='none', store=lm(CARD), nb_signals=2), 0, True, MAC, True),
        ('USB trace build: another UHCD layout, requests not decoded', dxe, dict(store=lm(CARD), uhcd_variant='other'), 0, True, MAC, True),
        # USBRT (usbrt.inc): both copies load after the driver in every scenario (as on an OF1).
        ('USBRT: both copies get the shorter waits as they load', dxe, dict(store=lm(CARD)), 0, True, MAC, True),
        ('USBRT loaded before the driver: changed at the entry', dxe, dict(store=lm(CARD), usbrt_img='before'), 0, True, MAC, True),
        ('no USBRT: nothing else affected', dxe, dict(store=lm(CARD), usbrt_img='none'), 0, True, MAC, True),
        # The display driver's splash (splash.inc): written before it is drawn, only into a display driver we know
        ('display driver (Sharp panel) loaded before the driver: our splash written at the entry', dxe, dict(store=lm(CARD), gop_img='before'), 0, True, MAC, True),
        ('display driver (Sharp panel) loads after the driver: our splash written as it loads', dxe, dict(store=lm(CARD), gop_img='after'), 0, True, MAC, True),
        ('display driver with the Samsung panel (Cisco OF2), before: our splash, its panel settings kept', dxe, dict(store=lm(CARD), gop_variant='samsung'), 0, True, MAC, True),
        ('display driver with the Samsung panel, after: our splash as it loads', dxe, dict(store=lm(CARD), gop_variant='samsung', gop_img='after'), 0, True, MAC, True),
        ('display driver of an older build (the OpenPeak OF2\'s), before: our splash', dxe, dict(store=lm(CARD), gop_variant='older'), 0, True, MAC, True),
        ('display driver of an older build, after: our splash as it loads', dxe, dict(store=lm(CARD), gop_variant='older', gop_img='after'), 0, True, MAC, True),
        ('display driver already with our splash (an OF1 release): the same bytes written again', dxe, dict(store=lm(CARD), gop_variant='ours'), 0, True, MAC, True),
        ('display driver with one handler byte different: left alone', dxe, dict(store=lm(CARD), gop_variant='code'), 0, True, MAC, True),
        ('display driver with one panel setting different: left alone', dxe, dict(store=lm(CARD), gop_variant='cfg'), 0, True, MAC, True),
        ('display driver whose reserve does not end with CEDCED: left alone', dxe, dict(store=lm(CARD), gop_variant='cedced'), 0, True, MAC, True),
        ('display driver image smaller than the handlers: left alone', dxe, dict(store=lm(CARD), gop_variant='small'), 0, True, MAC, True),
        ('another file with the same code: left alone', dxe, dict(store=lm(CARD), gop_variant='guid', gop_img='after'), 0, True, MAC, True),
        ('display driver with no FilePath: left alone', dxe, dict(store=lm(CARD), gop_variant='nopath'), 0, True, MAC, True),
        ('no display driver: nothing else affected', dxe, dict(store=lm(CARD), gop_img='none'), 0, True, MAC, True),
        # The model (model.inc): the boot block's ROM name says which OpenFrame this is
        ('boot block says 0ABPW400 (an OF1): OpenFrame 1, OF1', dxe, dict(store=lm(CARD), flash_model='of1'), 0, True, MAC, True),
        ('boot block says 0ABPW005 (an OF2): OpenFrame 2, OF2', dxe, dict(store=lm(CARD), flash_model='of2'), 0, True, MAC, True),
        ('OF2, no MAC stored yet: OpenFrame 2, OF2, serial and UUID unfilled', dxe, dict(flash_model='of2'), 0, True, JUNK, False),
        ('boot block says 0ABPW006 (a do-not-use OF2 build): left as built', dxe, dict(store=lm(CARD), flash_model='bad'), 0, True, MAC, True),
        ('boot block says 0ABPW004: left as built', dxe, dict(store=lm(CARD), flash_model='near'), 0, True, MAC, True),
        ('boot block says 1ABPW005: left as built', dxe, dict(store=lm(CARD), flash_model='half'), 0, True, MAC, True),
        # The OF2's mini-PCIe slot routing (acpi_rp2.inc): an SSDT through ACPI Support, OF2 only
        ('OF2: the mini-PCIe SSDT, once, at entry', dxe, dict(store=lm(CARD), flash_model='of2', want_ssdt=[(True, 1, 6, 0)]), 0, True, MAC, True),
        ('OF2, no ACPI Support: no SSDT (looked for again at ReadyToBoot)', dxe, dict(store=lm(CARD), flash_model='of2', acpi_sup='none', want_ssdt=[]), 0, True, MAC, True),
        ('OF2, SetAcpiTable fails at entry and at ReadyToBoot: tried twice, boot goes on', dxe, dict(store=lm(CARD), flash_model='of2', acpi_sup='fails', want_ssdt=[(True, 1, 6, 0)] * 2), 0, True, MAC, True),
        ('OF1: no SSDT, ACPI Support not looked for', dxe, dict(store=lm(CARD), flash_model='of1'), 0, True, MAC, True),
        ('do-not-use OF2 build (0ABPW006): no SSDT', dxe, dict(store=lm(CARD), flash_model='bad'), 0, True, MAC, True),
        # The panel and touch lines (panel_id.inc): the panel from the touchscreen's version, else the display driver's configuration
        ('panel: the Cisco (Samsung display, touch S01_02)', dxe, dict(store=lm(CARD), flash_model='of2', gop_variant='samsung', usb_devs=[TOUCH('S01_02')], want_panel=panel_line('Samsung', 'Samsung 1.2')), 0, True, MAC, True),
        ('panel: a Sharp unit (Sharp display, touch Hyup05_3)', dxe, dict(store=lm(CARD), usb_devs=[TOUCH('Hyup05_3')], want_panel=panel_line('Sharp', 'Hyup 5.3')), 0, True, MAC, True),
        ('panel: a Sharp panel on the Samsung display driver (.138 that morning): Sharp', dxe, dict(store=lm(CARD), gop_variant='samsung', usb_devs=[TOUCH('Hyup05_3')], want_panel=panel_line('Sharp', 'Hyup 5.3')), 0, True, MAC, True),
        ('panel: no USB devices: from the display', dxe, dict(store=lm(CARD), want_panel=panel_line('Sharp', 'unknown')), 0, True, MAC, True),
        ('panel: a stick and Bluetooth first, then the touchscreen', dxe, dict(store=lm(CARD), usb_devs=[(0x0781, 0x5580, 2, 'Extreme'), (0x0a5c, 0x219a, 2, 'BCM2046 Bluetooth'), TOUCH('S01_02')], want_panel=panel_line('Samsung', 'Samsung 1.2')), 0, True, MAC, True),
        ('panel: the touchscreen\'s string can\'t be read', dxe, dict(store=lm(CARD), usb_devs=[(0x04b4, 0x1974, 2, None)], want_panel=panel_line('Sharp', 'unknown')), 0, True, MAC, True),
        ('panel: a product string of 62 characters: read', dxe, dict(store=lm(CARD), usb_devs=[(0x04b4, 0x1974, 2, 'x' * 53 + ' Hyup05_3')], want_panel=panel_line('Sharp', 'Hyup 5.3')), 0, True, MAC, True),
        ('panel: a product string of 63 characters: not read', dxe, dict(store=lm(CARD), gop_variant='samsung', usb_devs=[(0x04b4, 0x1974, 2, 'x' * 54 + ' Hyup05_3')], want_panel=panel_line('Samsung', 'unknown')), 0, True, MAC, True),
        ('panel: an empty product string: touch unknown', dxe, dict(store=lm(CARD), usb_devs=[(0x04b4, 0x1974, 2, b'\x02\x03')], want_panel=panel_line('Sharp', 'unknown')), 0, True, MAC, True),
        ('panel: a descriptor that isn\'t a string: touch unknown', dxe, dict(store=lm(CARD), usb_devs=[(0x04b4, 0x1974, 2, b'\x0e\x01' + 'S01_02'.encode('utf-16le'))], want_panel=panel_line('Sharp', 'unknown')), 0, True, MAC, True),
        ('panel: an odd string length: whole characters only', dxe, dict(store=lm(CARD), usb_devs=[(0x04b4, 0x1974, 2, b'\x0f\x03' + 'a S01_02'.encode('utf-16le')[:13])], want_panel=panel_line('Samsung', 'S01_')), 0, True, MAC, True),
        ('panel: a version of 13 characters: not taken', dxe, dict(store=lm(CARD), gop_variant='samsung', usb_devs=[TOUCH('ABCDEFGHIJKLM')], want_panel=panel_line('Samsung', 'unknown')), 0, True, MAC, True),
        ('panel: a product string with no space: all of it', dxe, dict(store=lm(CARD), usb_devs=[(0x04b4, 0x1974, 2, 'H2')], want_panel=panel_line('Sharp', 'H2')), 0, True, MAC, True),
        ('panel: a touchscreen version of another letter: from the display', dxe, dict(store=lm(CARD), gop_variant='samsung', usb_devs=[TOUCH('X9')], want_panel=panel_line('Samsung', 'X9')), 0, True, MAC, True),
        ('panel: an unknown display driver and no touchscreen: unknown', dxe, dict(store=lm(CARD), gop_variant='guid', want_panel=panel_line('unknown', 'unknown')), 0, True, MAC, True),
        ('touch: a Samsung panel on the Hyup board (sysinfo\'s SH) -> Hyup 1.2', dxe, dict(store=lm(CARD), usb_devs=[TOUCH('SH01_02')], want_panel=panel_line('Samsung', 'Hyup 1.2')), 0, True, MAC, True),
        ('touch: SHyup: the Hyup board too -> Hyup 2.1', dxe, dict(store=lm(CARD), usb_devs=[TOUCH('SHyup02_1')], want_panel=panel_line('Samsung', 'Hyup 2.1')), 0, True, MAC, True),
        ('touch: zeros: one digit kept -> Samsung 0.0', dxe, dict(store=lm(CARD), usb_devs=[TOUCH('S00_00')], want_panel=panel_line('Samsung', 'Samsung 0.0')), 0, True, MAC, True),
        ('touch: dots and three groups -> Hyup 1.2.3', dxe, dict(store=lm(CARD), usb_devs=[TOUCH('Hyup1.2.3')], want_panel=panel_line('Sharp', 'Hyup 1.2.3')), 0, True, MAC, True),
        ('touch: 13 characters tidied -> as read', dxe, dict(store=lm(CARD), usb_devs=[TOUCH('S1.2.3')], want_panel=panel_line('Samsung', 'S1.2.3')), 0, True, MAC, True),
        ('touch: too long tidied: as read -> S12345_6789', dxe, dict(store=lm(CARD), usb_devs=[TOUCH('S12345_6789')], want_panel=panel_line('Samsung', 'S12345_6789')), 0, True, MAC, True),
        ('touch: an empty group: as read -> S01__02', dxe, dict(store=lm(CARD), usb_devs=[TOUCH('S01__02')], want_panel=panel_line('Samsung', 'S01__02')), 0, True, MAC, True),
        ('touch: a letter after the numbers: as read -> Hyup05_3a', dxe, dict(store=lm(CARD), usb_devs=[TOUCH('Hyup05_3a')], want_panel=panel_line('Sharp', 'Hyup05_3a')), 0, True, MAC, True),
        ('touch: no digit after the prefix: as read -> Sx1', dxe, dict(store=lm(CARD), usb_devs=[TOUCH('Sx1')], want_panel=panel_line('Samsung', 'Sx1')), 0, True, MAC, True),
        ('touch: another spelling: as read -> HYUP05_3', dxe, dict(store=lm(CARD), usb_devs=[TOUCH('HYUP05_3')], want_panel=panel_line('Sharp', 'HYUP05_3')), 0, True, MAC, True),
        ('panel: the Da Vinci (Sharp display driver, touch S01_02): Samsung', dxe, dict(store=lm(CARD), flash_model='of2', usb_devs=[TOUCH('S01_02')], want_panel=panel_line('Samsung', 'Samsung 1.2')), 0, True, MAC, True),
        ('panel: a display driver whose code differs (no bird) still says Sharp', dxe, dict(store=lm(CARD), gop_variant='code', usb_devs=[TOUCH('Hyup05_3')], want_panel=panel_line('Sharp', 'Hyup 5.3')), 0, True, MAC, True),
        # The shell's countdown (countdown.inc): 0 as it loads, for either shell build we know
        ('OF1 shell loads at BDS: its countdown made 0', dxe, dict(store=lm(CARD), shell_variant='of1'), 0, True, MAC, True),
        ('OF2 shell loads at BDS: its countdown made 0', dxe, dict(store=lm(CARD), shell_variant='of2'), 0, True, MAC, True),
        ('OF2 shell already loaded at the entry: made 0 then', dxe, dict(store=lm(CARD), shell_variant='of2', shell_img='before'), 0, True, MAC, True),
        ('our OF1 release\'s shell (countdown already 0): 0 written again', dxe, dict(store=lm(CARD), shell_variant='ours'), 0, True, MAC, True),
        ('shell with one byte of the function different: left alone', dxe, dict(store=lm(CARD), shell_variant='code'), 0, True, MAC, True),
        ('shell image smaller than the function: left alone', dxe, dict(store=lm(CARD), shell_variant='small'), 0, True, MAC, True),
        ('another file with the shell\'s code: left alone', dxe, dict(store=lm(CARD), shell_variant='guid'), 0, True, MAC, True),
        ('shell with no FilePath: left alone', dxe, dict(store=lm(CARD), shell_variant='nopath'), 0, True, MAC, True),
        ('no shell: nothing else affected', dxe, dict(store=lm(CARD), shell_img='none'), 0, True, MAC, True),
        ('display driver loads after the driver, LoadedImage notify not registered: left alone', dxe, dict(nb_img='none', store=lm(CARD), gop_img='after', register_fails=True, want_mmc_ev='closed', want_pclk='rtb'), 0, True, MAC, True),
        ('USBRT\'s 500 ms wait not stock: only the ready loop changed', dxe, dict(store=lm(CARD), usbrt_variant='a_changed'), 0, True, MAC, True),
        ('USBRT\'s ready-loop wait not stock: neither it nor the tries changed', dxe, dict(store=lm(CARD), usbrt_variant='b_changed'), 0, True, MAC, True),
        ('USBRT\'s tries setter not stock: neither it nor the wait changed', dxe, dict(store=lm(CARD), usbrt_variant='c_changed'), 0, True, MAC, True),
        ('USBRT\'s SMM copy unreadable (SMRAM closed): only the other copy changed', dxe, dict(store=lm(CARD), usbrt_variant='closed'), 0, True, MAC, True),
        ('the same code under another file GUID: USBRT left alone', dxe, dict(store=lm(CARD), usbrt_variant='guid'), 0, True, MAC, True),
        ('USBRT image smaller than the sites: left alone', dxe, dict(store=lm(CARD), usbrt_variant='small'), 0, True, MAC, True),
        ('USBRT with no FilePath: left alone', dxe, dict(store=lm(CARD), usbrt_variant='nopath'), 0, True, MAC, True),
        ('USBRT\'s reset arguments not stock: the reset left as it is, the rest changed', dxe, dict(store=lm(CARD), usbrt_variant='d_changed'), 0, True, MAC, True),
        ('one of USBRT\'s four timeouts not stock: none of them changed', dxe, dict(store=lm(CARD), usbrt_variant='e_changed'), 0, True, MAC, True),
        ('READ CAPACITY\'s dispatcher call not stock: no READ CAPACITY(16) stub', dxe, dict(store=lm(CARD), usbrt_variant='f_changed'), 0, True, MAC, True),
        ('one control-transfer token not stock: CErr left at 0 in all five', dxe, dict(store=lm(CARD), usbrt_variant='g_changed'), 0, True, MAC, True),
        ('the command-block builder not stock: neither H nor F (F needs H)', dxe, dict(store=lm(CARD), usbrt_variant='h_changed'), 0, True, MAC, True),
        ('screen trace build: USBRT not relocated yet: no hooks, nothing drawn from SMM', dxe, dict(store=lm(CARD), screen_variant='unrelocated'), 0, True, MAC, True),
        ('screen trace build: TSC past 5 minutes: the SMM hooks draw nothing', dxe, dict(store=lm(CARD), fake_tsc_hi=0x5d), 0, True, MAC, True),
        ('screen trace build: no framebuffer yet when GOP appears, one by the first USB request: drawn', dxe, dict(store=lm(CARD), screen_variant='late_mode'), 0, True, MAC, True),
        ('screen trace build: never a framebuffer: nothing drawn anywhere', dxe, dict(store=lm(CARD), screen_variant='no_fb'), 0, True, MAC, True),
        ('screen trace build: a bit-mask pixel format: nothing drawn anywhere', dxe, dict(store=lm(CARD), screen_variant='bitmask'), 0, True, MAC, True),
        ('screen trace build: REQUEST SENSE not stock: no hooks at all', dxe, dict(store=lm(CARD), screen_variant='sense_changed'), 0, True, MAC, True),
        ('screen trace build: a function-table fill not stock: no table hooks, the rest in', dxe, dict(store=lm(CARD), screen_variant='tables_changed'), 0, True, MAC, True),
        ('app build: MmcMediaDevice left alone', app, dict(store=lm(CARD), cpu_rev=0x217, enumerated_at_entry=True, mmc_img='before'), 0x80000014, False, MAC, True),
    ]
    NR = 0x80000006
    ESC, NOKEY = b'\x17\0\0\0', b'\xee' * 4
    cases += [                                                    # textmode.inc
        ('text console: the DXE core\'s first mode made 100 columns', dxe, dict(store=lm(CARD)), 0, True, MAC, True),
        ('text console: already 100: left, counted done', dxe, dict(store=lm(CARD), core_variant='done'), 0, True, MAC, True),
        ('text console: the core with no FilePath: still found by its signature', dxe, dict(store=lm(CARD), core_variant='nopath'), 0, True, MAC, True),
        ('text console: another file GUID: left alone', dxe, dict(store=lm(CARD), core_variant='guid'), 0, True, MAC, True),
        ('text console: a table that differs: left alone', dxe, dict(store=lm(CARD), core_variant='table'), 0, True, MAC, True),
        ('text console: QueryMode\'s reference not the table: left alone', dxe, dict(store=lm(CARD), core_variant='code'), 0, True, MAC, True),
        ('text console: another ImageSize: left alone', dxe, dict(store=lm(CARD), core_variant='small'), 0, True, MAC, True),
        ('text console: no core image at all: nothing else affected', dxe, dict(store=lm(CARD), core_variant='none'), 0, True, MAC, True),
        ('text console: a first mode of 90 columns: left alone', dxe, dict(store=lm(CARD), core_variant='cols90'), 0, True, MAC, True),
        ('text console: already 100, everything else loaded before the driver: no LoadedImage notify', dxe,
         dict(store=lm(CARD), core_variant='done', mmc_img='before', sdh_img='before', usbrt_img='before', shell_img='before'), 0, True, MAC, True),
        ('text console: the core not ours, everything else loaded before the driver: the LoadedImage notify stays', dxe,
         dict(store=lm(CARD), core_variant='guid', mmc_img='before', sdh_img='before', usbrt_img='before', shell_img='before'), 0, True, MAC, True),
    ]
    KB = dict(store=lm(CARD), kt_inputs=(('split', 1900), ('usb', 3430)), kt_shell_t=3627, kt_rks_t=3670)
    if not kbdtime:                                               # escwin.inc: Esc at boot, reliably
        cases += [
            ('Esc window: no USB keyboard: one read, no wait (as before), the display left alone', dxe,
             dict(store=lm(CARD), kt_inputs=(('split', 1900),), kt_keys=(), kt_rks_t=3670,
                  want_esc=([(NR, NOKEY, 1), (NR, NOKEY, 2)], [(3670, 0), (3670, 0)]), want_timers=[], want_blt=('own', [])), 0, True, MAC, True),
            ('Esc window: Esc caught: our Blt draws as Graphics Output\'s must (random fills and copies against a model)', dxe,
             dict(KB, kt_keys=((0, 0x17, 0),), kt_blt_test=True, want_blt=('ours', [(0, 0)])), 0, True, MAC, True),
            ('Esc window: Esc caught, no Graphics Output: Esc still to the shell, nothing else', dxe,
             dict(KB, kt_keys=((0, 0x17, 0),), kt_gop='none', want_esc=([(0, ESC, 1), (NR, NOKEY, 2)], [(3670, 0), (3670, 0)]), want_blt=('none', [])), 0, True, MAC, True),
            ('Esc window: Esc caught, RGB pixels: the display driver\'s Blt left', dxe,
             dict(KB, kt_keys=((0, 0x17, 0),), kt_gop='rgb', want_blt=('own', [])), 0, True, MAC, True),
            ('Esc window: Esc caught, framebuffer short of the screen: left', dxe,
             dict(KB, kt_keys=((0, 0x17, 0),), kt_gop='small', want_blt=('own', [])), 0, True, MAC, True),
            ('Esc window: Esc caught, framebuffer above 4 GB: left', dxe,
             dict(KB, kt_keys=((0, 0x17, 0),), kt_gop='high', want_blt=('own', [])), 0, True, MAC, True),
            ('Esc window: Esc caught, a scan line narrower than the screen: left', dxe,
             dict(KB, kt_keys=((0, 0x17, 0),), kt_gop='ppsl', want_blt=('own', [])), 0, True, MAC, True),
            ('Esc window: Esc caught, no Mode: left', dxe,
             dict(KB, kt_keys=((0, 0x17, 0),), kt_gop='nomode', want_blt=('own', [])), 0, True, MAC, True),
            ('Esc window: keyboard, nobody presses: until 1 s after the keyboard, then on; timer closed', dxe,
             dict(KB, kt_keys=(), want_esc=([(NR, NOKEY, 77), (NR, NOKEY, 78)], [(4430, 76), (4430, 0)]), want_timers=[(4430, True)],
                  want_blt=('own', [])), 0, True, MAC, True),
            ('Esc window: Esc 300 ms in: handed to the shell at once', dxe,
             dict(KB, kt_keys=((3970, 0x17, 0),), want_esc=([(0, ESC, 31), (NR, NOKEY, 32)], [(3970, 30), (3970, 0)]), want_timers=[(4430, True)],
                  want_blt=('ours', [(0, 0)])), 0, True, MAC, True),
            ('Esc window: Esc already waiting at the look', dxe,
             dict(KB, kt_keys=((0, 0x17, 0),), want_esc=([(0, ESC, 1), (NR, NOKEY, 2)], [(3670, 0), (3670, 0)])), 0, True, MAC, True),
            ('Esc window: other keys mashed, then Esc: the others dropped, Esc to the shell', dxe,
             dict(KB, kt_keys=((3700, 0, 0x61), (3800, 0, 0x62), (4000, 0x17, 0)), want_esc=([(0, ESC, 36), (NR, NOKEY, 37)], [(4000, 33), (4000, 0)])), 0, True, MAC, True),
            ('Esc window: only other keys: dropped, the window runs out', dxe,
             dict(KB, kt_keys=((3700, 0, 0x61),), want_esc=([(NR, NOKEY, 78), (NR, NOKEY, 79)], [(4430, 76), (4430, 0)])), 0, True, MAC, True),
            ('Esc window: Esc after the window: not taken; the next call gets it', dxe,
             dict(KB, kt_keys=((4500, 0x17, 0),), want_esc=([(NR, NOKEY, 77), (NR, NOKEY, 78)], [(4430, 76), (4430, 0)])), 0, True, MAC, True),
            ('Esc window: the shell looks after the window is over (slow boot): no wait', dxe,
             dict(KB, kt_keys=(), kt_rks_t=4600, want_esc=([(NR, NOKEY, 1), (NR, NOKEY, 2)], [(4600, 0), (4600, 0)])), 0, True, MAC, True),
            ('Esc window: no timer (CreateEvent fails): 1 s from the look instead', dxe,
             dict(KB, kt_keys=(), kt_timer_fails=True, want_esc=([(NR, NOKEY, 101), (NR, NOKEY, 102)], [(4670, 100), (4670, 0)]), want_timers=[]), 0, True, MAC, True),
            ('Esc window: SetTimer fails: the timer closed, 1 s from the look', dxe,
             dict(KB, kt_keys=(), kt_settimer_fails=True, want_esc=([(NR, NOKEY, 101), (NR, NOKEY, 102)], [(4670, 100), (4670, 0)]), want_timers=[(None, True)]), 0, True, MAC, True),
            ('Esc window: the timer never goes off: 3 s at most', dxe,
             dict(KB, kt_keys=(), kt_timer_never=True, want_esc=([(NR, NOKEY, 301), (NR, NOKEY, 302)], [(6670, 300), (6670, 0)])), 0, True, MAC, True),
            ('Esc window: the keyboard appears after the look: no wait', dxe,
             dict(KB, kt_inputs=(('split', 1900),), kt_late=(('usb', 5200),), kt_keys=(), want_esc=([(NR, NOKEY, 1), (NR, NOKEY, 2)], [(3670, 0), (3670, 0)])), 0, True, MAC, True),
            ('Esc window: a non-USB input is no keyboard: no wait', dxe,
             dict(KB, kt_inputs=(('split', 1900), ('ps2', 3430)), kt_keys=(), want_esc=([(NR, NOKEY, 1), (NR, NOKEY, 2)], [(3670, 0), (3670, 0)])), 0, True, MAC, True),
            ('Esc window: the console\'s ReadKeyStroke fails: taken as no key, the window runs out', dxe,
             dict(KB, kt_rks_error=True, want_esc=([(NR, NOKEY, 77), (0x80000007, NOKEY, 78)], [(4430, 76), (4430, 0)])), 0, True, MAC, True),
            ('Esc window: 100 other keys waiting, then Esc: read 64 at a time, Esc found', dxe,
             dict(KB, kt_keys=((0, 0, 0x78),) * 100 + ((0, 0x17, 0),), want_esc=([(0, ESC, 101), (NR, NOKEY, 102)], [(3680, 1), (3680, 0)])), 0, True, MAC, True),
            ('Esc window: no ConIn at the shell: nothing replaced', dxe,
             dict(KB, kt_conin=False, want_esc=([], None)), 0, True, MAC, True),
            ('Esc window: a shell that isn\'t ours: not hooked, no wait', dxe,
             dict(KB, shell_variant='code', want_shell='unpatched', kt_keys=(),
                  want_esc=([(NR, NOKEY, 1), (NR, NOKEY, 2)], [(3670, 0), (3670, 0)])), 0, True, MAC, True),
        ]
    if kbdtime:                                                   # kbdtime.inc (-DKBDTIME)
        cases += [
            ('kbdtime: USB keyboard before the shell, Esc and "a" waiting: both counted, 2 s waited, the shell told none, then straight through',
             dxe, dict(store=lm(CARD), want_kbd='I01900U02600R04000S04600F04700N02K00170000C02u01P04700W02',
                       want_rks=[(NR, b'\xee' * 4, 203), (NR, b'\xee' * 4, 204)], want_hooked=True), 0, True, MAC, True),
            ('kbdtime: no keyboard: 2 s waited, nothing',
             dxe, dict(store=lm(CARD), kt_inputs=(('split', 1900),), kt_keys=(),
                       want_kbd='I01900U-----R04000S04600F04700N00K--------C01u00P-----W00', want_rks=[(NR, b'\xee' * 4, 201), (NR, b'\xee' * 4, 202)]), 0, True, MAC, True),
            ('kbdtime: Esc comes 600 ms into the window, "a" 1300 ms: both counted, the first timed',
             dxe, dict(store=lm(CARD), kt_keys=((5300, 0x17, 0), (6000, 0, 0x61)),
                       want_kbd='I01900U02600R04000S04600F04700N02K00170000C02u01P05300W00', want_rks=[(NR, b'\xee' * 4, 203), (NR, b'\xee' * 4, 204)]), 0, True, MAC, True),
            ('kbdtime: a key only after the window: not seen',
             dxe, dict(store=lm(CARD), kt_keys=((7000, 0x17, 0),),
                       want_kbd='I01900U02600R04000S04600F04700N00K--------C02u01P-----W00', want_rks=[(NR, b'\xee' * 4, 201), (NR, b'\xee' * 4, 202)]), 0, True, MAC, True),
            ('kbdtime: the keyboard comes after the shell',
             dxe, dict(store=lm(CARD), kt_inputs=(('split', 1900),), kt_keys=(), kt_late=(('usb', 5200),),
                       want_kbd='I01900U05200R04000S04600F04700N00K--------C02u01P-----W00'), 0, True, MAC, True),
            ('kbdtime: a non-USB input (ACPI path) is not counted as USB',
             dxe, dict(store=lm(CARD), kt_inputs=(('split', 1900), ('ps2', 2000)), kt_keys=((0, 0x41),),
                       want_kbd='I01900U-----R04000S04600F04700N01K00000041C02u00P04700W01'), 0, True, MAC, True),
            ('kbdtime: no ConIn when the shell loads: nothing replaced',
             dxe, dict(store=lm(CARD), kt_conin=False, want_kbd='I01900U02600R04000S04600F-----N--K--------C02u01P-----W--', want_rks=[], want_hooked=False), 0, True, MAC, True),
            ('kbdtime: a shell that isn\'t ours: not hooked',
             dxe, dict(store=lm(CARD), shell_variant='code', want_shell='unpatched',
                       want_kbd='I01900U02600R04000S-----F-----N--K--------C02u01P-----W--', want_rks=[(0, b'\x17\0\0\0', 1), (0, b'\0\0\x61\0', 2)], want_hooked=False), 0, True, MAC, True),
            ('kbdtime: 250 keys waiting: 200 read, no wait, 99 shown',
             dxe, dict(store=lm(CARD), kt_keys=((0, 0x30),) * 250,
                       want_kbd='I01900U02600R04000S04600F04700N99K00000030C02u01P04700W99', want_rks=[(NR, b'\xee' * 4, 200), (0, b'\0\0\x30\0', 201)]), 0, True, MAC, True),
            ('kbdtime: SMBIOS already there: nothing written, still hooked and harmless',
             dxe, dict(store=lm(CARD), smbios_present=True, want_count=None, want_rks=[(NR, b'\xee' * 4, 203), (NR, b'\xee' * 4, 204)], want_hooked=True), 0, True, MAC, True),
        ]
    bad = 0
    if only:
        cases = [c for c in cases if only in c[0]]
    for name, img, kw, want_status, want_ev, want_mac, want_io in cases:
        kw = dict(kw)
        want_count = kw.pop('want_count', 'BootCount: 0000001613')
        want_panel = kw.pop('want_panel', None)                # panel_id.inc's line, when the scenario says
        want_ssdt = kw.pop('want_ssdt', None)                  # acpi_rp2.inc: SetAcpiTable calls, when the scenario says
        want_kbd = kw.pop('want_kbd', None)                    # kbdtime.inc's string, when the scenario says
        want_rks = kw.pop('want_rks', None)
        want_hooked = kw.pop('want_hooked', None)
        want_esc = kw.pop('want_esc', None)                    # escwin.inc: ([(status, key, stub calls)] x2, [(ms, stalls)] x2)
        want_timers = kw.pop('want_timers', None)
        want_blt = kw.pop('want_blt', None)                    # (whose Blt is in place, cursor moves) after the run
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
        mmw_patchable = (mmc_img != 'none' and kw.get('mmc_variant', 'stock') in ('stock', 'code', 'done') and
                         kw.get('mmw_variant', 'stock') == 'stock' and not kw.get('li_fails') and
                         (mmc_img == 'before' or not (kw.get('register_fails') or kw.get('create_event_fails'))))
        want_mmw = kw.pop('want_mmw', 'patched' if img is dxe and want_ev and mmw_patchable and not no_cmd0 else 'unpatched')
        sdh_img = kw.get('sdh_img', 'after')
        sdh_patchable = (sdh_img != 'none' and kw.get('sdh_variant', 'stock') == 'stock' and not kw.get('li_fails') and
                         (sdh_img == 'before' or not (kw.get('register_fails') or kw.get('create_event_fails'))))
        want_sdh = kw.pop('want_sdh', 'patched' if img is dxe and want_ev and sdh_patchable else 'unpatched')
        gop_img = kw.get('gop_img', 'before')
        gop_patchable = (gop_img != 'none' and not kw.get('li_fails') and
                         (gop_img == 'before' or not (kw.get('register_fails') or kw.get('create_event_fails'))))
        want_gop = kw.pop('want_gop', 'patched' if img is dxe and want_ev and gop_patchable else 'unpatched')
        shell_img = kw.get('shell_img', 'after')
        shell_patchable = (shell_img != 'none' and not kw.get('li_fails') and
                           (shell_img == 'before' or not (kw.get('register_fails') or kw.get('create_event_fails'))))
        want_shell = kw.pop('want_shell', 'patched' if img is dxe and want_ev and shell_patchable else 'unpatched')
        want_pclk = kw.pop('want_pclk', ('untouched' if want_nbp == 'patched' else 'notify' if want_ev else 'untouched')
                           if img is dxe else 'app')
        # NBDXE (with the 'value' variant) writes its own value; the notify must leave that alone
        if kw.get('nb_variant') == 'value':
            want_pclk = 'value'
        r = run(img, timing=timing, profile=profile and img is dxe, usbtrace=usbtrace and img is dxe, screen=screen and img is dxe,
                kbdtime=kbdtime and img is dxe, **kw)
        p = list(r['errors'])
        tv = r['tm_var']
        if timing and img is dxe and want_ev:                      # ReadyToBoot ran: the records published
            d = tv.get('data', b'')
            recs = [(d[i:i + 4], struct.unpack_from('<I', d, i + 4)[0]) for i in range(0, len(d) - 7, 8)]
            tags = [t for t, _ in recs]
            # usb_first.inc's records follow ReadyToBoot's (here: no file systems, UE0E), ending UFSn
            last = max([i for i, t in enumerate(tags) if t == b'RTB '] or [-1])
            uf = tags[last + 1:] if last >= 0 else []
            if uf and (not uf[-1].startswith(b'UFS') or not all(t[:1] in (b'U', b'N') for t in uf)):
                p.append('records after ReadyToBoot not usb_first\'s: %s' % uf)
            tags = tags[:last + 1] if uf else tags
            if not tv.get('n') or len(d) % 8 or not recs:         # once per ReadyToBoot; the last has all
                p.append('BlwBootTimes written %s times, %d bytes' % (tv.get('n'), len(d)))
            elif tags[0] != b'ENTR' or tags[-1] != b'RTB ' or b'ENTE' not in tags:
                p.append('timing records %s' % tags)
            else:
                ts = [(t - recs[0][1]) & 0xffffffff for _, t in recs]   # TSC >> 10 wraps (the host's TSC here)
                if ts != sorted(ts):
                    p.append('timestamps go backwards: %s' % ts)
                if not (kw.get('register_fails') or kw.get('create_event_fails')):
                    for g, tag in TM_GUIDS.items():
                        if tag.encode() not in tags:
                            p.append('no %r record' % tag)
                    pre = tags[tags.index(b'PRE ') + 1:tags.index(b'PREE')] if b'PRE ' in tags and b'PREE' in tags else None
                    want_pre = [b'\xc1\xb0\xd5\xa5', b'\x99\x99\x99\x99'] + \
                        ([{'guid': b'\x11' * 4, 'nopath': b'LI??'}.get(kw.get('core_variant'), CORE_FILE[:4])]
                         if kw.get('core_variant', 'stock') != 'none' else []) + \
                        ([{'guid': b'\x11' * 4, 'nopath': b'LI??', 'node': b'LI??'}.get(kw.get('nb_variant'), NBDXE_FILE[:4])]
                         if kw.get('nb_img', 'before') == 'before' else []) + \
                        ([b'\x11' * 4 if kw.get('mmc_variant') == 'guid' else b'LI??' if kw.get('mmc_variant') == 'nopath' else MMC_FILE[:4]]
                         if kw.get('mmc_img', 'after') == 'before' else []) + \
                        ([b'\x11' * 4 if kw.get('sdh_variant') == 'guid' else b'LI??' if kw.get('sdh_variant') == 'nopath' else SDH_FILE[:4]]
                         if kw.get('sdh_img', 'after') == 'before' else []) + \
                        ([USBRT_FILE[:4]] * 2 if kw.get('usbrt_img', 'after') == 'before' else []) + \
                        ([{'guid': b'\x11' * 4, 'nopath': b'LI??'}.get(kw.get('gop_variant'), GOP_FILE[:4])]
                         if kw.get('gop_img', 'before') == 'before' else []) + \
                        ([{'guid': b'\x11' * 4, 'nopath': b'LI??'}.get(kw.get('shell_variant'), SHELL_FILE[:4])]
                         if kw.get('shell_img', 'after') == 'before' else [])
                    if not kw.get('li_fails') and pre != want_pre:
                        p.append('images before the entry %s, expected %s' % (pre, want_pre))
                    if kw.get('nb_img') == 'after' and NBDXE_FILE[:4] not in tags:
                        p.append('NBDXE\'s load not recorded: %s' % tags)
        elif tv.get('data') or tv.get('prof'):
            p.append('BlwBootTimes written without ReadyToBoot')
        if usbtrace and img is dxe and want_ev and not (kw.get('register_fails') or kw.get('create_event_fails')):
            if tv.get('bs_stall') != BS_STUB + 0x88 or not tv.get('bs_crc_ok'):
                p.append('BS->Stall %#x, CRC ok %s at the end' % (tv.get('bs_stall') or 0, tv.get('bs_crc_ok')))
            if tv.get('trig_now') != SMMC_TRIG:
                p.append('SmmControl->Trigger %#x at the end' % (tv.get('trig_now') or 0))
            tc = tv.get('trig_calls', [])
            if [c[0] for c in tc] != [SMMC_INST] * 3 or [c[1] for c in tc] != [USB_AREA + 0x6600] * 3:
                p.append('real Trigger called with %s' % tc)
            raw = b''.join(tv.get('prof', {}).get('BlwUsbTr%02X' % i, b'') for i in range(32))
            recs = [struct.unpack_from('<IIIII', raw, i) for i in range(0, len(raw), 20)]
            T = [('T', c, inf, ex) for st_, en, c, inf, ex in recs if inf >> 24 == 0x54]
            if T != tv.get('ut_expect'):
                p.append('T records %s, expected %s' % ([(a, hex(b), hex(c_), hex(d)) for a, b, c_, d in T], tv.get('ut_expect')))
            S = [(c, inf & 0xffffff, ex) for st_, en, c, inf, ex in recs if inf >> 24 == 0x53 and c in (tv.get('ut_stall_ret'), tv.get('ut_stall_ret2'))]
            if S != [(tv.get('ut_stall_ret'), 1000, 5), (tv.get('ut_stall_ret2'), 50000, 1)]:
                p.append('S records %s' % S)
            C = [(en, inf & 0xff) for st_, en, c, inf, ex in recs if inf >> 24 == 0x43]
            stock = lambda m: (1 if m[0x1381:0x1386] == bytes.fromhex('53566a61e8') else 0) | \
                              (2 if m[0x530e:0x5313] == bytes.fromhex('686a040100') else 0)
            bases = [USB_AREA + 0x10000, USB_AREA + 0x20000]
            seen = kw.get('usbrt_img', 'after') == 'after' and kw.get('usbrt_variant', 'stock') not in ('guid', 'nopath', 'small')
            want_C = ([(b_, stock(o)) for b_, o in zip(bases, r['rt_orig'])] +
                      [(b_, 0x80 | stock(n)) for b_, n in zip(bases, r['rt_now'])]) if seen else []
            if C[:len(want_C)] != want_C or (want_C and (C[4:] != want_C[2:] * ((len(C) - 4) // 2) or len(C) % 2)) or (not want_C and C):
                p.append('C records %s, expected %s' % ([(hex(a), hex(b)) for a, b in C], [(hex(a), hex(b)) for a, b in want_C]))
            if any(en < st_ for st_, en, c, inf, ex in recs if inf >> 24 in (0x53, 0x54)):
                p.append('a record ends before it starts')
            if screen:
                sc = tv.get('screen', {})
                sv = kw.get('screen_variant', 'stock')
                uv_ = kw.get('usbrt_variant', 'stock')
                seen_ = kw.get('usbrt_img', 'after') == 'after' and uv_ not in ('guid', 'nopath', 'small')
                I = [(en, inf & 0xff) for st_, en, c, inf, ex in recs if inf >> 24 == 0x49]
                want_I = [(b_, 2 if (sv == 'unrelocated' or (uv_ == 'closed' and k_ == 1)) else 3 if sv == 'sense_changed' else
                           5 if sv == 'tables_changed' else 1)
                          for k_, b_ in enumerate([USB_AREA + 0x10000, USB_AREA + 0x20000])] if seen_ else []
                if I != want_I:
                    p.append('I records %s, expected %s' % ([(hex(a), b) for a, b in I], [(hex(a), b) for a, b in want_I]))
                hooked = [b_ for b_, res in want_I if res in (1, 5)]
                tabled = [b_ for b_, res in want_I if res == 1]       # ...and the function tables too
                for b_, kind, u, addr, port, a3, a4, a5, a7, ret_ in sc.get('ctl', []):
                    want_ret = {0: a7 + u, 2: a5 + u, 3: 0x11 if u else 0x33}[kind]
                    if ret_ != want_ret:
                        p.append('transfer (kind %d) returned %s, not %#x' % (kind, ret_, want_ret))
                for k_, b_ in enumerate([USB_AREA + 0x10000, USB_AREA + 0x20000]):
                    m_ = sc['rt_end'][k_]
                    if b_ in hooked:
                        t1 = b_ + 0x1386 + struct.unpack_from('<i', m_, 0x1382)[0]
                        t2 = b_ + 0x4816 + struct.unpack_from('<i', m_, 0x4812)[0]
                        t3 = b_ + 0x4c66 + struct.unpack_from('<i', m_, 0x4c62)[0]
                        for site, tgt, _ in TABLE_FILLS:
                            to = struct.unpack_from('<I', m_, site + 3)[0]
                            if (b_ in tabled) != (b_ + 0x9920 <= to < b_ + ST_END) or (b_ not in tabled and m_[site:site + 7] != r['rt_orig'][k_][site:site + 7]):
                                p.append('copy %d: table fill at %#x stores %#x' % (k_, site, to))
                        if m_[0x1381] != 0xe9 or m_[0x1386:0x138a] != b'\x90' * 4 or m_[0x4811] != 0xe8 or \
                                m_[0x4c61] != 0xe9 or m_[0x4c66:0x4c69] != b'\x90' * 3 or \
                                not (b_ + 0x9920 <= t1 < b_ + ST_END and b_ + 0x9920 <= t2 < b_ + ST_END and b_ + 0x9920 <= t3 < b_ + ST_END):
                            p.append('copy %d hooks %s %s' % (k_, m_[0x1381:0x138a].hex(), m_[0x4811:0x4816].hex()))
                    elif m_[0x1381:0x138a] != r['rt_orig'][k_][0x1381:0x138a] or m_[0x4811:0x4816] != r['rt_orig'][k_][0x4811:0x4816] \
                            or m_[0x4c61:0x4c69] != r['rt_orig'][k_][0x4c61:0x4c69] \
                            or m_[0x9920:ST_END] != r['rt_orig'][k_][0x9920:ST_END] \
                            or any(m_[s_:s_ + 7] != r['rt_orig'][k_][s_:s_ + 7] for s_, _, _ in TABLE_FILLS):
                        p.append('copy %d changed although not hooked' % k_)
                Gr = [(en, (inf >> 8) & 1, inf & 0xff, c, ex) for st_, en, c, inf, ex in recs if inf >> 24 == 0x47]
                good = (FB, 1, 1, FB_W, FB_W << 16 | FB_H)
                bad0 = (0, 0, 1, 0, 0)
                want_G = {'late_mode': [bad0, good], 'no_fb': [bad0], 'bitmask': [(FB, 0, 2, 0, 0)]}.get(sv, [good])
                if Gr != want_G:
                    p.append('G records %s, expected %s' % ([tuple(hex(v) for v in g) for g in Gr], [tuple(hex(v) for v in g) for g in want_G]))
                drawable = sv not in ('no_fb', 'bitmask')
                exp = {}
                T_ = [(st_, en, inf) for st_, en, c, inf, ex in recs if inf >> 24 == 0x54] if drawable else []
                if T_:
                    st_, en, inf = T_[-1]
                    fs = (inf >> 8) & 0xffff
                    render(exp, 8, 8, ((fs & 0xff) << 8) | (fs >> 8), 4)
                    render(exp, 88, 8, st_ >> 10, 5)
                    render(exp, 184, 8, (en - st_) >> 10, 5)
                    render(exp, 280, 8, len(T_), 4)
                smm = kw.get('fake_tsc_hi', 0) < 0x5d and drawable
                fdh = [(b_, ret_, n_) for b_, ret_, n_ in sc.get('fd', []) if b_ in hooked]
                if fdh and smm:                                   # (identify's wait counts too)
                    b_, ret_, n_ = fdh[-1]
                    waited = sum(1 for k_, *_ in (r.get('ident') or []) if [USB_AREA + 0x10000, USB_AREA + 0x20000][k_] == b_)
                    render(exp, 8, 32, (ret_ - b_) & 0xfffff, 5)
                    render(exp, 88, 32, n_, 5)
                    render(exp, 184, 32, sum(1 for x in fdh if x[0] == b_) + waited, 6)
                cb = [(b_, op, ln) for b_, op, ln in sc.get('cbw', []) if b_ in hooked]
                if cb and smm:
                    b_, op, ln = cb[-1]
                    render(exp, 8, 56, op, 2)
                    render(exp, 56, 56, ln, 8)
                    mine = [x for x in cb if x[0] == b_]
                    render(exp, 184 + 16, 56, len(mine), 6)
                    # READ CAPACITY(16)s F's stub sent on this copy (the READ CAPACITY test: 'big' answers
                    # that went through the stub; whether it should be in is checked with the USBRT bytes)
                    render(exp, 320, 56, sum(1 for k_, case, off, *_ in (r.get('rcap') or []) if case == 'big' and off == F_RVA + 8
                                             and [USB_AREA + 0x10000, USB_AREA + 0x20000][k_] == b_), 4)
                    ring = [0] * 16
                    for i_, x in enumerate(mine):
                        ring[i_ & 15] = x[1]
                    for i_ in range(16):
                        render(exp, 8 + 48 * i_, 80, ring[(len(mine) + i_) & 15], 2)
                sn = [(b_, v or 0) for b_, v in sc.get('sense', []) if b_ in hooked]
                if sv != 'sense_changed' and any(v != 0x20402 for _, v in sc.get('sense', [])):
                    p.append('REQUEST SENSE returned %s' % [hex(v) if v is not None else None for _, v in sc.get('sense', [])])
                if sn and smm:
                    render(exp, 8, 104, sn[-1][1], 6)
                    render(exp, 120, 104, len(sn), 6)
                ct = [x for x in sc.get('ctl', []) if x[0] in tabled]
                FIELDS = ((8, 4), (88, 4), (168, 4), (248, 4), (328, 4), (408, 4), (488, 4), (568, 4))
                # the status byte as each transfer starts and ends (the bulk fakes set it: EHCI 2, UHCI 4)
                st_at = []
                e14 = 0x04
                for x in sc.get('ctl', []):
                    s0 = e14
                    if x[1] == 2:
                        e14 = 4 if x[2] else 2
                    st_at.append((s0, e14))
                def ring_entry(i_, done=True):
                    b_, kind, u, addr, port, a3, a4, a5, a7, ret_ = sc['ctl'][i_]
                    t = (0x10 if u else 0x30) | {0: 1, 2: 2, 3: 3}[kind] | st_at[i_][1 if done else 0] << 8
                    return (t, port << 8 | addr, a3, a5, a4, a7, ((ret_ or 0) & 0xffff) if done else 0xeeee,
                            (0x30 if kind in (0, 2) else 0) if done else 0)
                def ring_at(n_, inprog):                          # the 4 lines after n_ transfers started (the last maybe running)
                    ring = [(0,) * 8] * 4
                    for i_ in range(n_):
                        ring[i_ & 3] = ring_entry(i_, done=not (inprog and i_ == n_ - 1))
                    return [ring[(n_ + li) & 3] for li in range(4)]
                def draw_ring(d, lines, ys):
                    for vals, y in zip(lines, ys):
                        for v, (x, nd) in zip(vals, FIELDS):
                            render(d, x, y, v, nd)
                if ct and smm:                                    # lines 6-9: the last 4 transfers; 10: errors; 11: port status
                    draw_ring(exp, ring_at(len(ct), False), (128, 152, 176, 200))
                    # (nothing is drawn below the ring)
                    # while the 5th ran: all four ring lines; while the 7th (last) ran: the bottom line
                    for snap, n_, ys, row0 in (('saved_ring', 5, (128, 152, 176, 200), 128), ('saved_line', 7, (200,), 200)):
                        mid = {}
                        draw_ring(mid, ring_at(n_, True)[-len(ys):], ys)
                        sv_ = sc.get(snap, b'')
                        bad_mid = [(x, y) for y in range(ys[0], ys[-1] + 16) for x in range(0, 800)
                                   if struct.unpack_from('<I', sv_, ((y - row0) * 800 + x) * 4)[0] != mid.get((x, y), 0)]
                        if bad_mid:
                            p.append('while transfer %d ran the screen showed %s, expected %s' % (n_, [read_screen(sv_, y) for y in ys] if False else
                                     [read_screen(sv_[(y - row0) * 800 * 4:], y) for y in ys], [read_screen(mid, y) for y in ys]))
                fbb = sc.get('fb_before_rtb', b'')
                want_fb = bytearray(272 * FB_W * 4)
                for (x, y), v in exp.items():
                    struct.pack_into('<I', want_fb, (y * FB_W + x) * 4, v)
                badpx = []
                if fbb[:len(want_fb)] != bytes(want_fb):
                    badpx = [(x, y) for y in range(0, 272) for x in range(0, 800)
                             if struct.unpack_from('<I', fbb, (y * FB_W + x) * 4)[0] != exp.get((x, y), 0)]
                if badpx:
                    p.append('framebuffer differs at %d pixels: drawn %s, expected %s' % (len(badpx), read_screen(fbb), read_screen(exp)))
                if sc.get('fb_end') != fbb:
                    p.append('drawn after ReadyToBoot')
        if profile and img is dxe and want_ev and not (kw.get('register_fails') or kw.get('create_event_fails')):
            tev = tv.get('tev') or []
            if len(tev) != 1 or tev[0]['tpl'] != 16:
                p.append('profile timer event %s' % tev)
            tm = tv.get('timer', [])
            if not tm or tm[0][1:] != (1, 100000, 0) or tm[-1][1:] != (0, 0, 0):
                p.append('SetTimer calls %s' % tm)
            pr = tv.get('prof', {})
            imgs = pr.get('BlwProfImg', b'')
            tab = [struct.unpack_from('<III', imgs, i) for i in range(0, len(imgs), 12)]
            if not kw.get('li_fails') and (G('a5d5b0c1-7d2f-4c3e-9b1a-26b1e0f17217')[:4], LOAD, 0x3000) not in [(struct.pack('<I', t[0]), t[1], t[2]) for t in tab]:
                p.append('this driver not in the image table %s' % tab[:3])
            want_n = len(tab)
            if kw.get('mmc_img', 'after') == 'after' and (struct.unpack('<I', MMC_FILE[:4])[0], MMC_IMG, 0x700 if kw.get('mmc_variant') == 'small' else 0x34c0) not in tab \
                    and kw.get('mmc_variant', 'stock') not in ('nopath', 'guid'):
                p.append('MmcMediaDevice (loaded later) not in the image table')
            samples = b''.join(pr.get('BlwProf%02X' % i, b'') for i in range(16))
            got = [struct.unpack_from('<IIII', samples, i) for i in range(0, len(samples), 16)]
            want = tv.get('expect', [])
            if kw.get('li_fails'):                                # images before the entry unknown: no return addresses
                want = [(e, 0, 0) for e, _, _ in want]
            if [g[1:] for g in got] != want:
                p.append('samples %s, expected %s' % ([g[1:] for g in got][:3], want[:3]))
            if any(set(k for k in pr if not k.startswith('BlwUsbTr')) - {'BlwProfImg'} - {'BlwProf%02X' % i for i in range((len(want) + 63) // 64)}):
                p.append('sample variables %s' % sorted(pr))
            if got and [(g[0] - got[0][0]) & 0xffffffff for g in got] != sorted((g[0] - got[0][0]) & 0xffffffff for g in got):
                p.append('sample timestamps not in order')
        # NBDXE patch
        if want_nbp == 'patched':
            if r['nb_changed'] != [0x3ca] or r['nb_first'] != 0xc3:
                p.append('NBDXE first byte %#x, bytes changed %s' % (r['nb_first'], r['nb_changed'][:8]))
            if any(v is not None for v in r['nb_wrote']):
                p.append('NBDXE still wrote %s' % ['%#x' % v for v in r['nb_wrote'] if v is not None])
        elif r['nb_changed']:
            p.append('NBDXE image changed at %s' % r['nb_changed'][:8])
        # USBRT's waits (usbrt.inc): per copy, A (500 ms -> 10 ms) and B+C (1 s -> 200 ms, tries x5)
        uv = kw.get('usbrt_variant', 'stock')
        ui = kw.get('usbrt_img', 'after')
        urt_ok = img is dxe and want_ev and ui != 'none' and uv not in ('guid', 'small', 'nopath') and not kw.get('li_fails') and \
            (ui == 'before' or not (kw.get('register_fails') or kw.get('create_event_fails')))
        for k, (o_, n_) in enumerate(zip(r['rt_orig'], r['rt_now'])):
            readable = not (k == 1 and uv == 'closed')
            wa = urt_ok and readable and uv != 'a_changed'
            wbc = urt_ok and readable and uv not in ('b_changed', 'c_changed')
            want = bytearray(o_)
            if wa:
                want[0x56c4:0x56c8] = (0x29a).to_bytes(4, 'little')
            if wbc:
                want[0x530f:0x5313] = (0x3415).to_bytes(4, 'little')
                want[0x3fa4] = 50
            if urt_ok and readable and uv != 'd_changed':        # D: push 0; imul; push edi; push 0xff21
                want[0x4388:0x4393] = bytes.fromhex('6a006bd241576821ff0000')
            if urt_ok and readable and uv != 'e_changed':        # E: 20 s everywhere
                for a_ in (0x3382, 0x4a0e, 0x4eba, 0x57fa):
                    want[a_ + 7:a_ + 9] = (20000).to_bytes(2, 'little')
            base_ = [USB_AREA + 0x10000, USB_AREA + 0x20000][k]
            if urt_ok and readable and uv != 'h_changed':        # H: the builder's clear gone
                want[0x47bb:0x47bd] = b'\x90\x90'
                want[0x47c2:0x47c4] = b'\x90\x90'
            if urt_ok and readable and uv not in ('f_changed', 'h_changed'):   # F: the call to the stub; the stub (run below), its base
                want[0x4a5c:0x4a60] = struct.pack('<i', F_RVA + 8 - 0x4a60)
                want[F_RVA:0x9f80] = n_[F_RVA:0x9f80]
                if struct.unpack_from('<I', n_, F_RVA)[0] != base_:
                    p.append('USBRT copy %d: F stub base %#x' % (k, struct.unpack_from('<I', n_, F_RVA)[0]))
            if urt_ok and readable and uv != 'g_changed':        # G: CErr = 3 in the five control tokens
                for a_, _ in G_SITES:
                    want[a_ + 4] |= 0x0c
            n_ = bytes(n_[:0x1251]) + bytes([o_[0x1251]]) + bytes(n_[0x1252:])   # (the fake IoRead8's own toggle byte)
            if screen and img is dxe:
                masked = set(range(0x1381, 0x138a)) | set(range(0x4812, 0x4816)) | set(range(0x4c61, 0x4c69)) | \
                    set(range(0x9920, ST_END)) | set(i for s_, _, _ in TABLE_FILLS for i in range(s_ + 3, s_ + 7))
                n_ = bytes(o_[i] if i in masked else n_[i] for i in range(len(n_)))
            if n_ != bytes(want):
                p.append('USBRT copy %d changed at %s, expected %s' % (k, [hex(i) for i in range(len(o_)) if o_[i] != n_[i]][:8],
                                                                     [hex(i) for i in range(len(o_)) if o_[i] != want[i]][:8]))
        # READ CAPACITY, run (F): READ CAPACITY(16) after an FFFFFFFF answer only, with its exact command,
        # and READ CAPACITY(10)'s answer and result back for USBRT
        rc10 = (b'\x25' + b'\0' * 11, 0x80, USB_AREA + 0x3b100, 8, USB_AREA + 0x3b000, 0)
        rc16 = (RC16_CDB, 0x80, USB_AREA + 0x3b100, 32, USB_AREA + 0x3b000, 0)
        for k_, case, off, ret_, log, data8, params in r.get('rcap') or []:
            hooked = urt_ok and not (k_ == 1 and uv == 'closed') and uv not in ('f_changed', 'h_changed')
            if off != (F_RVA + 8 if hooked else 0x49ae):
                p.append('copy %d: READ CAPACITY calls +%#x' % (k_, off))
            want_log = [rc10] + ([rc16] if hooked and case == 'big' else [])
            if log != want_log:
                p.append('copy %d %s: dispatcher saw %s, expected %s' % (k_, case, [(l[0].hex(),) + tuple(l[1:]) for l in log],
                                                                       [(w[0].hex(),) + tuple(w[1:]) for w in want_log]))
            if ret_ != (0 if case == 'fail' else 8):
                p.append('copy %d %s: READ CAPACITY returned %s' % (k_, case, ret_))
            want8 = {'big': 'ffffffff00000200', 'small': '003fffff00000200', 'fail': 'ffffffff00000200'}[case]
            if data8.hex() != want8:
                p.append('copy %d %s: USBRT then reads %s, not %s' % (k_, case, data8.hex(), want8))
        # identify's first step, run: it only waits (A's 10 ms), and sends no command
        for k_, op, start_bit, tmo in r.get('ident', []) or []:
            if (op, tmo) != (0, 0):
                p.append('copy %d: identify\'s first step sent a command (%02x)' % (k_, op))
        # MmcMediaDevice's CE-ATA check (ceata.inc)
        if want_cea == 'patched':
            if r['cea_changed'] != [(CEA_FN, 0x32), (CEA_FN + 1, 0xc0), (CEA_FN + 2, 0xc3)]:
                p.append('MmcMediaDevice changed at %s, expected its CE-ATA check to start xor al, al; ret' % r['cea_changed'][:6])
        elif r['cea_changed']:
            p.append('MmcMediaDevice changed at %s' % r['cea_changed'][:6])
        # The MMC pinned to fs0 (mmc_pin.inc): at the first stage signal, every SD host controller
        # (class 08/05) connected recursively, once, and nothing else; never in an app build or without
        # the stage notify.
        stage_seen = img is dxe and want_ev and kw.get('gma', True) and kw.get('nb_stage', True) and not kw.get('register_fails') \
            and not kw.get('create_event_fails')
        want_conns = [(0xB40000 + i_, 0, 0, 1, 1) for i_, d_ in enumerate(r['pin_devs']) if d_[3] >> 16 == 0x0805] \
            if stage_seen and kw.get('pin_variant', 'of1') not in ('read_fails', 'locate_fails') else []
        if r['pin_conns'] != want_conns:
            p.append('ConnectController calls %s, expected %s' % (r['pin_conns'], want_conns))
        # MmcMediaDevice's waits after CMD0 and SdHost's command wait (mmcinit.inc)
        want_w = [(a + k, b) for a in MMW_SITES for k, b in enumerate(struct.pack('<I', 1000)) if b != struct.pack('<I', 50000)[k]]
        if want_mmw == 'patched':
            if r['mmw_changed'] != want_w:
                p.append('MmcMediaDevice waits changed %s, expected %s' % (r['mmw_changed'], want_w))
        elif r['mmw_changed']:
            p.append('MmcMediaDevice waits changed at %s' % r['mmw_changed'][:6])
        # The shell's countdown (countdown.inc): exactly its two operands 0, or nothing
        if want_shell == 'patched' and r['shell_want'] is not None:
            if r['shell_now'] != r['shell_want']:
                d = [i for i in range(len(r['shell_now'])) if r['shell_now'][i] != r['shell_want'][i]]
                p.append('shell not as expected at %d bytes, first %#x' % (len(d), d[0]))
        elif r['shell_now'] != r['shell_orig']:
            d = [i for i in range(len(r['shell_now'])) if r['shell_now'][i] != r['shell_orig'][i]]
            p.append('shell changed at %d bytes, first %#x' % (len(d), d[0]))
        # The display driver's splash (splash.inc): exactly the numbers, pixels and zeros, or nothing
        if want_gop == 'patched' and r['gop_want'] is not None:
            if r['gop_now'] != r['gop_want']:
                d = [i for i in range(GOP_SIZE) if r['gop_now'][i] != r['gop_want'][i]]
                p.append('display driver not as expected at %d bytes, first %#x' % (len(d), d[0]))
        elif r['gop_now'] != r['gop_orig']:
            d = [i for i in range(GOP_SIZE) if r['gop_now'][i] != r['gop_orig'][i]]
            p.append('display driver changed at %d bytes, first %#x' % (len(d), d[0]))
        if want_sdh == 'patched':
            # The glue (mmcinit.inc): the template, SdHost's own gBS operand (from 0x532), and a call
            # whose target holds exactly sdh_wait (sdh_wait.inc, assembled alone).
            glue = bytearray(inc_bytes('mmcinit_sig.inc', 'sdh_glue'))
            glue[1:5] = r['sdh_orig'][0x532:0x536]
            glue[8:12] = r['sdh_now'][0x4f6 + 8:0x4f6 + 12]
            want_img = bytearray(r['sdh_orig'])
            want_img[0x4f6:0x4f6 + len(glue)] = glue
            want_s = [(i, want_img[i]) for i in range(SDH_SIZE) if want_img[i] != r['sdh_orig'][i]]
            if r['sdh_changed'] != want_s:
                p.append('SdHost changed %s, expected %s' % (r['sdh_changed'][:8], want_s[:8]))
            elif r['sdh_target'] != SDH_WAIT:
                p.append('the glue\'s call does not reach sdh_wait')
        elif r['sdh_changed']:
            p.append('SdHost changed at %s' % r['sdh_changed'][:6])
        all_else = not timing and want_nbp == 'patched' and want_cea == 'patched' and kw.get('nb_img', 'before') == 'before' and \
            kw.get('mmc_img', 'after') == 'before' and kw.get('usbrt_img', 'after') == 'before' and \
            want_gop == 'patched' and kw.get('gop_img', 'before') == 'before' and \
            want_shell == 'patched' and kw.get('shell_img', 'after') == 'before' and \
            (want_mmw == 'patched' or no_cmd0) and want_sdh == 'patched' and kw.get('sdh_img', 'after') == 'before'
        li_notify = any(e.get('guid') == LOADED_IMAGE for e in r['evs'].values())
        cw_ok = kw.get('core_variant', 'stock') in ('stock', 'nopath', 'done')
        if all_else and cw_ok and li_notify:
            p.append('LoadedImage notify registered although every image was patched at the entry')
        if all_else and not cw_ok and not li_notify:
            p.append('no LoadedImage notify although the DXE core was not patched')
        # Panel clock
        w = r['mmio_writes']
        if want_pclk == 'value':                                  # another fixed value: not ours to change
            if w:
                p.append('display registers written: %s' % ['%#x=%#x' % (o, v) for o, v, _ in w])
        elif want_pclk == 'untouched':
            if w:
                p.append('display registers written: %s' % ['%#x=%#x' % (o, v) for o, v, _ in w])
            if want_nbp == 'patched' and kw.get('nb_stage', True) and r['dpll'] != kw.get('gop_dpll', GOP_DPLL):
                p.append('DPLL_B %#x at the end, expected the display driver\'s own %#x' % (r['dpll'] or 0, kw.get('gop_dpll', GOP_DPLL)))
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
            want_system = list(SYSTEM)                        # OpenFrame 2 when the boot block says so (model.inc)
            if kw.get('flash_model') == 'of2' and SYSTEM == ['OpenPeak', 'OpenFrame 1', 'OF1', 'OpenFrame']:
                want_system = ['OpenPeak', 'OpenFrame 2', 'OF2', 'OpenFrame']
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
                if ss[:4] != want_system or len(ss) != 5:
                    p.append('Type 1 strings %s' % ss)
                got = (ss[raw[7] - 1] if raw[7] else None, raw[8:24])
                if got != want_t1:
                    p.append('Type 1 serial/UUID %r %s, expected %r %s' % (got[0], got[1].hex(), want_t1[0], want_t1[1].hex()))
                if want_t1[0] is None and ss[4:5] != ['0' * 12]:
                    p.append('serial placeholder changed to %r' % ss[4:5])
            if img is dxe and r['entry_smbios']:
                e1 = [(raw[7], raw[8:24], ss) for t, _, raw, ss in r['entry_smbios']['structs'] if t == 1]
                if e1 != [(0, b'\xff' * 16, want_system + ['0' * 12])]:
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
            if timing and img is dxe and want_ev and want_count:
                want_count = 'BootCount: x00---0-00'        # usb_first.inc's summary (no file systems here)
            got11 = d[11][1] if 11 in d else None
            if kbdtime and got11:                              # the development build's third string
                kbd_got = got11[3] if len(got11) == 4 and got11[3].startswith(KBD_PREFIX) and len(got11[3]) == len(KBD_PREFIX) + 57 else None
                if kbd_got is None:
                    p.append('Type 11 %s: no KbdTime string' % got11)
                elif want_kbd is not None and kbd_got != KBD_PREFIX + want_kbd:
                    p.append('KbdTime %r, expected %r' % (kbd_got[len(KBD_PREFIX):], want_kbd))
                got11 = got11[:3]
            if got11:                                          # the panel and touch lines (panel_id.inc)
                pl, tl = (got11[1], got11[2]) if len(got11) == 3 else (None, None)
                ok_form = pl is not None and len(pl) == len(PANEL_PLACEHOLDER) and len(tl) == len(TOUCH_PLACEHOLDER) and (
                    (pl, tl) == (PANEL_PLACEHOLDER, TOUCH_PLACEHOLDER) or (
                        re.fullmatch(r'Panel: (Sharp|Samsung|unknown) *', pl) and re.fullmatch(r'Touch: ([!-~]{1,12}|(Samsung|Hyup) [0-9][0-9.]*) *', tl)))
                if not ok_form:
                    p.append('Type 11 panel lines %r %r malformed' % (pl, tl))
                elif want_panel is not None and (pl, tl) != want_panel:
                    p.append('panel lines %r, expected %r' % ((pl, tl), want_panel))
                got11 = got11[:1]
            if 11 in d and got11 != [want_count]:
                p.append('Type 11 %s, expected %r' % (got11, want_count))
            if img is dxe and r['entry_smbios'] and [ss[:3] for t, _, _, ss in r['entry_smbios']['structs'] if t == 11] != [[PLACEHOLDER, PANEL_PLACEHOLDER, TOUCH_PLACEHOLDER]]:
                p.append('boot count or panel line filled before ReadyToBoot')
            # acpi_rp2.inc: on an OF2 the SSDT, once, as the firmware's own modules add theirs; on
            # anything else ACPI Support isn't even looked for.
            if r['usb']['unfreed']:
                p.append('panel line: %d USB buffer(s) not freed' % len(r['usb']['unfreed']))
            sets = r['acpi']['sets']
            if want_ssdt is not None:
                if [(x['table'] == RP2_AML, x['checksum'], x['version'], x['handle']) for x in sets] != want_ssdt:
                    p.append('SetAcpiTable %s, expected %s' % ([(x['table'] == RP2_AML, x['checksum'], x['version'], x['handle']) for x in sets], want_ssdt))
            elif kw.get('flash_model') == 'of2':
                if [(x['table'] == RP2_AML, x['checksum'], x['version'], x['handle']) for x in sets] != [(True, 1, 6, 0)]:
                    p.append('OF2: SetAcpiTable %s, expected the SSDT once' % [(x['table'] == RP2_AML, x['checksum'], x['version'], x['handle']) for x in sets])
            elif sets or r['acpi']['locates']:
                p.append('not an OF2, yet ACPI Support looked for (%d) or used (%d)' % (r['acpi']['locates'], len(sets)))
            if want_esc is not None and (r['kt'].get('rks'), r['kt'].get('rks_t')) != want_esc:
                p.append('the shell\'s ReadKeyStroke calls %s at (ms, stalls) %s, expected %s' % (r['kt'].get('rks'), r['kt'].get('rks_t'), want_esc))
            want_cols = {'cols90': 90}.get(kw.get('core_variant'), 100 if kw.get('core_variant', 'stock') in ('stock', 'nopath', 'done') else 80)
            if kw.get('core_variant') != 'none' and r['core_cols'] != want_cols and img is dxe and want_ev and not kw.get('li_fails'):
                p.append('text console %s columns, expected %s' % (r['core_cols'], want_cols))
            if r['core_rest'] is False:
                p.append('text-mode table changed beyond its first columns')
            if want_blt is not None and (r['kt'].get('blt'), r['kt'].get('cursor', [])) != want_blt:
                p.append('Blt %s, cursor %s, expected %s' % (r['kt'].get('blt'), r['kt'].get('cursor', []), want_blt))
            if r['kt'].get('blt_errors'):
                p.append('Blt: %s' % '; '.join(r['kt']['blt_errors'][:4]))
            if want_timers is not None and r['kt'].get('timers') != want_timers:
                p.append('timers %s, expected %s' % (r['kt'].get('timers'), want_timers))
            if kbdtime and want_rks is not None and r['kt'].get('rks') != want_rks:
                p.append('the shell\'s ReadKeyStroke calls %s, expected %s' % (r['kt'].get('rks'), want_rks))
            if kbdtime and want_hooked is not None and (r['kt'].get('conin_rks') != CONIN_RKS) != want_hooked:
                p.append('ConIn->ReadKeyStroke %s' % ('left as it was' if want_hooked else 'replaced'))
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
        if p and first_fail:
            print('stopped at the first failure (--first-fail)')
            sys.exit(1)
    print('%d of %d scenarios passed' % (len(cases) - bad, len(cases)))
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
