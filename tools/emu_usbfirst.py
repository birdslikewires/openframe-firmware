#!/usr/bin/env python3
"""Runs driver/usb_first.inc under Unicorn against fake disks and the real shell, and checks the
shell's sort.

    venv/bin/python -I tools/emu_usbfirst.py
    venv/bin/python -I tools/emu_usbfirst.py --mutate tools/mutations/usb_first.py

The fake firmware has file systems on partitions of disks (the internal memory behind its SD
host, USB drives behind the USB controller), each a handle with a device path, and loaded images
(EFI_LOADED_IMAGE: FilePath a firmware-volume file node, ImageBase, ImageSize), one of them the
real built-in shell, decompressed from releases/of1-blw-26.11.29.bin, either already loaded at
ReadyToBoot or loading after it (then the driver's LoadedImage notify is run, after another image
has loaded first). After usb_first, the shell's own sort comparison is run on two mapping entries,
a USB drive's and the internal memory's (each with a file system and a block device): if a USB
file system has \\startup.nsh, the USB drive's must sort first; otherwise the shell must be
byte-for-byte as it was, and the internal memory's entry first, as stock.

Also checked: only the boot services usb_first needs; every pool freed once; every root and file
opened is closed; files opened only on USB file systems, read-only, and only \\startup.nsh; nothing
but the shell's two 7-byte sequences changed in any image, and nothing in an image that isn't the
exact stock shell (another GUID, too small, one byte different, no file path); the event closed if
it can't be registered; ecx and edx clobbered by every service, as cdecl allows; registers kept
and the stack balanced; once per boot. The boot-timing build (-DTIMING) runs the same scenarios,
and its records and SMBIOS summary are checked too. Exits non-zero on any failure.

--mutate runs each mutation in the given file (MUTATIONS = [(label, old, new), ...] or (label,
[(old, new), ...]), each `old` exactly once in usb_first.inc) on a copy of the driver directory, so
the source is never touched, and says whether the scenarios notice; it exits non-zero if any
mutation survives.
"""
import os
import re
import struct
import subprocess
import sys
import tempfile
import uuid

from unicorn import Uc, UC_ARCH_X86, UC_MODE_32, UC_HOOK_CODE, UcError
from unicorn.x86_const import (UC_X86_REG_ESP, UC_X86_REG_EAX, UC_X86_REG_EBX, UC_X86_REG_ECX, UC_X86_REG_EDX,
                               UC_X86_REG_ESI, UC_X86_REG_EDI, UC_X86_REG_EBP, UC_X86_REG_EIP)

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, '..')
DRIVER = os.path.join(ROOT, 'driver')
CODE, DATA, HEAP, HOOKS, STOP, STACK = 0x10000000, 0x30000000, 0x31000000, 0x3f000000, 0x3fff0000, 0x3ff00000
IMAGES = 0x20000000                           # loaded images, 0x40000 apart
TM_LOG = DATA + 0xf0000                       # the timing build's stand-in tm_rec: count, then tags
COUNT = DATA + 0xef000                        # its stand-in SMBIOS boot count (count_ptr)
G = lambda s: uuid.UUID(s).bytes_le
SFS, DP = G('964e5b22-6459-11d2-8e39-00a0c969723b'), G('09576e91-6d3f-11d2-8e39-00a0c969723b')
LI = G('5b1b31a1-9562-11d2-8e3f-00a0c969723b')
SHELL_FILE = G('c57ad6b7-0515-40a8-9d21-551652854e37')
OTHER_FILE = G('a5d5b0c1-7d2f-4c3e-9b1a-26b1e0f17217')
NOT_FOUND, INVALID, UNSUPPORTED = 0x8000000E, 0x80000002, 0x80000003
CMP, CMP_LEN, PA, PB = 0xb7c5, 120, 0x28, 0x4d

END = b'\x7f\xff\x04\x00'
ACPI = b'\x02\x01\x0c\x00\xd0\x41\x03\x0a\x00\x00\x00\x00'
pci = lambda dev, fn: bytes([1, 1, 6, 0, fn, dev])
usb_node = lambda port: bytes([3, 5, 6, 0, port, 0])
usb_class = b'\x03\x0f\x0b\x00' + bytes(7)
ctrl = b'\x01\x05\x08\x00\x00\x00\x00\x00'
hd = lambda n: bytes([4, 1, 0x2a, 0]) + struct.pack('<I', n) + bytes(34)

failures = []


def check(cond, what):
    print(('  ok   ' if cond else '  FAIL ') + what)
    if not cond:
        failures.append(what)


def real_shell():
    """The built-in shell's PE image (sections at their file offsets), from the 26.11.29 release."""
    from uefi_firmware import efi_compressor as ec
    img = open(os.path.join(ROOT, 'releases', 'of1-blw-26.11.29.bin'), 'rb').read()
    hlen, = struct.unpack_from('<H', img, 0x10030)
    fvlen, = struct.unpack_from('<Q', img, 0x10020)
    off = 0x10000 + hlen
    while off < 0x10000 + fvlen:
        off = (off + 7) & ~7
        if img[off:off + 16] == SHELL_FILE:
            break
        off += int.from_bytes(img[off + 20:off + 23], 'little')
    body = img[off + 24:off + int.from_bytes(img[off + 20:off + 23], 'little')]
    ssz = int.from_bytes(body[0:3], 'little')
    data = ec.TianoDecompress(body[9:ssz], ssz - 9)
    q = 0
    while q < len(data):
        isz, it = int.from_bytes(data[q:q + 3], 'little'), data[q + 3]
        if it == 0x10:
            return bytes(data[q + 4:q + isz])
        q = (q + isz + 3) & ~3
    raise ValueError('no PE section in the shell file')


SHELL = real_shell()


def assemble(driver=DRIVER, timing=False):
    """usb_first behind a jmp at offset 0. With timing, built -DTIMING with a tm_rec that appends eax
    to a log at TM_LOG (keeping every register but eax, as the real one does), and count_ptr."""
    with tempfile.TemporaryDirectory() as t:
        src, out = os.path.join(t, 'w.asm'), os.path.join(t, 'w.bin')
        tm = ''
        if timing:
            tm = ('tm_rec:\n        push    ecx\n        mov     ecx, [%#x]\n        mov     [%#x + 4 + ecx * 4], eax\n'
                  '        inc     dword [%#x]\n        pop     ecx\n        ret\n        align 4, db 0\n'
                  'count_ptr:      dd %#x\n' % (TM_LOG, TM_LOG, TM_LOG, COUNT))
        open(src, 'w').write('bits 32\n        jmp usb_first\n%include "usb_first.inc"\n' + tm)
        r = subprocess.run(['nasm', '-f', 'bin', '-I', driver + '/'] + (['-DTIMING'] if timing else []) +
                           ['-o', out, src], capture_output=True, text=True)
        if r.returncode:
            if driver != DRIVER:
                return None
            sys.exit('nasm: ' + r.stderr)
        return open(out, 'rb').read()


class World:
    """disks: list of dicts: name, usb (bool), path, parts (list of dicts: fs (default True),
    startup, open_fails, open_null, volume_fails). shell: 'before' (loaded at ReadyToBoot), 'after'
    (loads later, after another image), 'none'; shell_variant: 'stock', 'byte' (one byte of the
    function different), 'small' (ImageSize too small), 'guid' (another file), 'nopath', 'nodetype'
    (the shell's GUID in a file-path node of another type)."""

    def __init__(self, code, disks, no_dp=(), bad_path=(), shell='after', shell_variant='stock',
                 create_fails=False, register_fails=False):
        self.uc = uc = Uc(UC_ARCH_X86, UC_MODE_32)
        for base, size in ((CODE, 0x10000), (DATA, 0x100000), (HEAP, 0x100000), (HOOKS, 0x10000), (STOP, 0x1000),
                           (STACK - 0x10000, 0x10000), (IMAGES, 0x100000)):
            uc.mem_map(base, size)
        uc.mem_write(CODE, code)
        self.hooks, self.calls, self.errors = {}, [], []
        self.heap, self.pools, self.freed = HEAP, {}, set()
        self.data = DATA + 0x2000
        self.handles, self.order = {}, []
        self.opened, self.closed = [], []
        self.no_dp, self.bad_path = set(no_dp), set(bad_path)
        self.create_fails, self.register_fails = create_fails, register_fails
        self.events, self.closed_events = {}, []
        self.images = []                           # (handle, li address, base, length, kind, original bytes)
        uc.hook_add(UC_HOOK_CODE, self._hook, begin=HOOKS, end=HOOKS + 0x10000)
        bs = DATA
        bad = self.hook(lambda a: self.err('unexpected boot service call') or UNSUPPORTED)
        for i in range(0x100 // 4):
            uc.mem_write(bs + 4 * i, struct.pack('<I', bad))
        for off, fn in ((0x30, self.free_pool), (0x34, self.create_event), (0x44, self.close_event),
                        (0x58, self.handle_protocol), (0x60, self.register_notify), (0xa8, self.locate_handle_buffer)):
            uc.mem_write(bs + off, struct.pack('<I', self.hook(fn, off)))
        self.bs = bs
        self.f_open_volume = self.hook(self.open_volume)
        self.f_open = self.hook(self.file_open)
        self.f_close = self.hook(self.file_close)
        self.sfs_owner = {}
        for _ in range(2):
            self.new_handle(None, None, None)
        self.disks = []
        for d in disks:
            d = dict(d)
            d['handle'] = self.new_handle(d['path'], d, None)
            d['part_handles'] = [self.new_handle(d['path'] + hd(n + 1), d, p if p.get('fs', True) else None)
                                 for n, p in enumerate(d['parts'])]
            self.disks.append(d)
        self.shell_variant = shell_variant
        self.load_image('other')                   # some driver, loaded before
        if shell == 'before':
            self.load_image('shell')
        self.shell_when = shell

    # --- the fake core -------------------------------------------------------------------------
    def hook(self, fn, off=None):
        addr = HOOKS + 0x10 * len(self.hooks)
        self.uc.mem_write(addr, b'\xc3')
        self.hooks[addr] = (fn, off)
        return addr

    def _hook(self, uc, addr, size, user):
        h = self.hooks.get(addr)
        if h:
            fn, off = h
            esp = uc.reg_read(UC_X86_REG_ESP)
            args = lambda n: struct.unpack('<%dI' % n, uc.mem_read(esp + 4, 4 * n))
            if off is not None:
                self.calls.append(off)
            ret = fn(args)
            for r in (UC_X86_REG_ECX, UC_X86_REG_EDX):      # cdecl: the callee may clobber them
                uc.reg_write(r, 0xbad0bad0)
            uc.reg_write(UC_X86_REG_EAX, (ret or 0) & 0xffffffff)

    def err(self, what):
        self.errors.append(what)

    def rd(self, a):
        return struct.unpack('<I', self.uc.mem_read(a, 4))[0]

    def alloc(self, n):
        a = self.data
        self.data += (n + 15) & ~15
        return a

    def new_handle(self, path, disk, fs, li=None):
        hid = self.alloc(0x20)
        n = len(self.order)
        dpa = None
        if path is not None:
            dpa = self.alloc(len(path) + 4)
            self.uc.mem_write(dpa, b'\x00\x05\x00\x00' if n in self.bad_path else path + END)
        h = dict(id=hid, dp=dpa, path=path, disk=disk, part=fs, sfs=None, li=li)
        if fs is not None:
            sfs = self.alloc(16)
            self.uc.mem_write(sfs, struct.pack('<QI', 0x10000, self.f_open_volume))
            h['sfs'] = sfs
            self.sfs_owner[sfs] = hid
        self.handles[hid] = h
        self.order.append(hid)
        return hid

    def load_image(self, kind):
        """An image loaded (its LoadedImage installed). kind: 'shell' (as shell_variant says) or 'other'."""
        uc = self.uc
        base = IMAGES + 0x40000 * len(self.images)
        guid = SHELL_FILE if kind == 'shell' else OTHER_FILE
        content = SHELL if kind == 'shell' else bytes(range(256)) * 64
        size = len(content)
        if kind == 'shell':
            if self.shell_variant == 'byte':
                content = bytearray(content)
                content[CMP + 0x60] ^= 1
                content = bytes(content)
            if self.shell_variant == 'guid':
                guid = OTHER_FILE
            if self.shell_variant == 'small':
                size = CMP + CMP_LEN - 1
        uc.mem_write(base, content)
        fp = self.alloc(24)
        node = b'\x04\x04\x14\x00' if (kind == 'shell' and self.shell_variant == 'nodetype') else b'\x04\x06\x14\x00'
        uc.mem_write(fp, node + guid + END)
        li = self.alloc(0x40)
        nopath = kind == 'shell' and self.shell_variant == 'nopath'
        uc.mem_write(li, struct.pack('<IIIIIIIIIIQ', 0x1000, 0, 0, 0, 0 if nopath else fp, 0, 0, 0, base, 0, size))
        hid = self.new_handle(None, None, None, li=li)
        self.images.append((hid, li, base, len(content), kind, content))
        return hid

    def free_pool(self, a):
        p, = a(1)
        if p not in self.pools or p in self.freed:
            self.err('FreePool(%#x): not a live allocation' % p)
        self.freed.add(p)
        return 0

    def give_pool(self, items, pbuf, pn):
        p = self.heap
        self.heap += 4 * len(items) + 16
        self.pools[p] = 4 * len(items)
        self.uc.mem_write(p, b''.join(struct.pack('<I', h) for h in items))
        self.uc.mem_write(pn, struct.pack('<I', len(items)))
        self.uc.mem_write(pbuf, struct.pack('<I', p))

    def locate_handle_buffer(self, a):
        typ, guid, key, pn, pbuf = a(5)
        g = bytes(self.uc.mem_read(guid, 16)) if guid else None
        if typ != 2 or g not in (SFS, LI):
            self.err('LocateHandleBuffer other than ByProtocol SimpleFileSystem or LoadedImage')
            return INVALID
        hs = [h for h in self.order if (self.handles[h]['sfs'] if g == SFS else self.handles[h]['li'])]
        if not hs:
            return NOT_FOUND
        self.give_pool(hs, pbuf, pn)
        return 0

    def handle_protocol(self, a):
        hid, guid, out = a(3)
        h = self.handles.get(hid)
        if not h:
            self.err('HandleProtocol on %#x, not a handle' % hid)
            return INVALID
        g = bytes(self.uc.mem_read(guid, 16))
        if g == DP and h['dp'] and self.order.index(hid) not in self.no_dp:
            self.uc.mem_write(out, struct.pack('<I', h['dp']))
            return 0
        if g == SFS and h['sfs']:
            self.uc.mem_write(out, struct.pack('<I', h['sfs']))
            return 0
        if g == LI and h['li']:
            self.uc.mem_write(out, struct.pack('<I', h['li']))
            return 0
        if g not in (DP, SFS, LI):
            self.err('HandleProtocol for another protocol')
        return UNSUPPORTED

    def create_event(self, a):
        typ, tpl, fn, ctx, out = a(5)
        if (typ, tpl) != (0x200, 8) or ctx != self.bs:
            self.err('CreateEvent(%#x, %d, ..., %#x): not a TPL_CALLBACK signal event with BS' % (typ, tpl, ctx))
        if self.create_fails:
            return 0x80000009
        ev = 0xe7e70010 + len(self.events)
        self.events[ev] = dict(fn=fn, ctx=ctx, guid=None)
        self.uc.mem_write(out, struct.pack('<I', ev))
        return 0

    def register_notify(self, a):
        guid, ev, out = a(3)
        if bytes(self.uc.mem_read(guid, 16)) != LI or ev not in self.events:
            self.err('RegisterProtocolNotify for something other than LoadedImage on our event')
            return INVALID
        if self.register_fails:
            return 0x80000009
        self.events[ev]['guid'] = LI
        self.uc.mem_write(out, struct.pack('<I', ev + 0x100))
        return 0

    def close_event(self, a):
        ev, = a(1)
        if ev not in self.events:
            self.err('CloseEvent of %#x' % ev)
        self.closed_events.append(ev)
        return 0

    def open_volume(self, a):
        this, out = a(2)
        hid = self.sfs_owner.get(this)
        if not hid:
            self.err('OpenVolume on something that is not a file system')
            return INVALID
        h = self.handles[hid]
        if not h['disk']['usb']:
            self.err('OpenVolume on a file system not on a USB disk')
        if h['part'].get('volume_fails'):
            return 0x80000007
        root = self.alloc(32)
        self.uc.mem_write(root, struct.pack('<QIII', 0x10000, self.f_open, self.f_close, 0))
        self.opened.append(('root', root, hid))
        self.uc.mem_write(out, struct.pack('<I', root))
        return 0

    def file_open(self, a):
        this, out, name, mlo, mhi, alo, ahi = a(7)
        rec = [o for o in self.opened if o[1] == this and o[0] == 'root']
        if not rec:
            self.err('Open on something that is not an open root')
            return INVALID
        hid = rec[0][2]
        s = b''
        while True:
            c = bytes(self.uc.mem_read(name + len(s), 2))
            if c == b'\0\0' or len(s) > 200:
                break
            s += c
        if s.decode('utf-16-le') != '\\startup.nsh':
            self.err('opened %r' % s.decode('utf-16-le'))
        if (mlo, mhi, alo, ahi) != (1, 0, 0, 0):
            self.err('opened with mode %#x:%#x attributes %#x:%#x (must be read-only)' % (mhi, mlo, ahi, alo))
        part = self.handles[hid]['part']
        if part.get('open_fails'):                 # (and leaves something in NewHandle)
            self.uc.mem_write(out, struct.pack('<I', 0xdeadbee0))
            return 0x80000007
        if part.get('open_null'):                  # success, but no file
            return 0
        if not part.get('startup'):
            return NOT_FOUND
        f = self.alloc(32)
        self.uc.mem_write(f, struct.pack('<QIII', 0x10000, self.f_open, self.f_close, 0))
        self.opened.append(('file', f, hid))
        self.uc.mem_write(out, struct.pack('<I', f))
        return 0

    def file_close(self, a):
        this, = a(1)
        if not [o for o in self.opened if o[1] == this] or this in self.closed:
            self.err('Close of %#x: not open' % this)
        self.closed.append(this)
        return 0

    def tm_tags(self):
        n = self.rd(TM_LOG)
        return [bytes(self.uc.mem_read(TM_LOG + 4 + 4 * i, 4)) for i in range(n)]

    # --- running it ----------------------------------------------------------------------------
    def run(self, fn, args, what):
        uc = self.uc
        esp = STACK - 0x100
        uc.mem_write(esp, struct.pack('<I', STOP) + b''.join(struct.pack('<I', x) for x in args))
        uc.reg_write(UC_X86_REG_ESP, esp)
        saved = {UC_X86_REG_EBX: 0x11111111, UC_X86_REG_ESI: 0x22222222, UC_X86_REG_EDI: 0x33333333,
                 UC_X86_REG_EBP: 0x44444444}
        for r, v in saved.items():
            uc.reg_write(r, v)
        try:
            uc.emu_start(fn, STOP, count=2_000_000)
        except UcError as e:
            self.err('%s: emulation stopped: %s at %#x' % (what, e, uc.reg_read(UC_X86_REG_EIP)))
            return None
        if uc.reg_read(UC_X86_REG_EIP) != STOP:
            self.err('%s: still running after 2,000,000 instructions' % what)
            return None
        if uc.reg_read(UC_X86_REG_ESP) != esp + 4:
            self.err('%s: stack unbalanced' % what)
        if any(uc.reg_read(r) != v for r, v in saved.items()):
            self.err('%s: callee-saved registers not kept' % what)
        return uc.reg_read(UC_X86_REG_EAX)

    def call(self):
        return self.run(CODE, [self.bs], 'usb_first')

    def boot_on(self):
        """After ReadyToBoot: the boot manager loads another image and then the shell (unless it
        was loaded already, or never is), each load signalling the LoadedImage notifies."""
        loads = ['other'] + (['shell'] if self.shell_when == 'after' else [])
        for kind in loads:
            self.load_image(kind)
            for e in list(self.events.values()):
                if e['guid'] == LI:
                    self.run(e['fn'], [0xe7e70000, e['ctx']], 'LoadedImage notify')

    def shell_sorts_usb_first(self):
        """Runs the loaded shell's comparison on (USB entry, MMC entry): True if the USB one sorts
        first. An entry: +0x14 its type (2: a hard-disk partition), +0x18 1 if USB or SCSI, +0x19 1
        if a block device, +0x1A 1 if a file system."""
        sh = [x for x in self.images if x[4] == 'shell']
        base = sh[0][2]
        e_usb, e_mmc, p = DATA + 0xe0000, DATA + 0xe0100, DATA + 0xe0200
        self.uc.mem_write(e_usb, bytes(0x14) + struct.pack('<I', 2) + bytes([1, 1, 1]) + bytes(5))
        self.uc.mem_write(e_mmc, bytes(0x14) + struct.pack('<I', 2) + bytes([0, 1, 1]) + bytes(5))
        self.uc.mem_write(p, struct.pack('<II', e_usb, e_mmc))
        r = self.run(base + CMP, [p, p + 4], 'the shell\'s comparison')
        r2 = self.run(base + CMP, [p + 4, p], 'the shell\'s comparison, the other way round')
        if r is None or r2 is None:
            return None
        r, r2 = (struct.unpack('<i', struct.pack('<I', x))[0] for x in (r, r2))
        if (r < 0) == (r2 < 0):
            self.err('the comparison is not antisymmetric (%d, %d)' % (r, r2))
        return r < 0


MMC = dict(name='mmc', usb=False, path=ACPI + pci(0x1e, 0) + ctrl + b'\x01\x02\x06\x00\x00\x00')
USB = lambda port, **k: dict(name='usb%d' % port, usb=True, path=ACPI + pci(0x1d, 7) + usb_node(port), **k)


def scenario(title, code, disks, expect_ret, expect_usb_first, timing=False, **kw):
    print(title)
    w = World(code, disks, **kw)
    ret = w.call()
    w.boot_on()
    check(ret == expect_ret, 'returns %s (expected %s)' % (ret, expect_ret))
    check(set(w.pools) == w.freed, 'every allocation freed (%d)' % len(w.pools))
    opened = [o[1] for o in w.opened]
    check(sorted(opened) == sorted(w.closed), 'every root and file closed (%d)' % len(opened))
    allowed = set(range(CMP + PA, CMP + PA + 7)) | set(range(CMP + PB, CMP + PB + 7))
    for hid, li, base, n, kind, orig in w.images:
        now = bytes(w.uc.mem_read(base, n))
        diff = [j for j in range(n) if orig[j] != now[j]]
        if kind == 'shell' and expect_usb_first:
            check(diff and set(diff) <= allowed, 'the shell changed in its two sequences only (%d bytes)' % len(diff))
        else:
            check(not diff, '%s image unchanged' % kind)
    if any(x[4] == 'shell' for x in w.images) and w.shell_variant == 'stock':
        check(w.shell_sorts_usb_first() == expect_usb_first,
              'the shell sorts the %s first' % ('USB drive' if expect_usb_first else 'internal memory'))
    if kw.get('register_fails'):
        check(len(w.closed_events) == 1, 'the unregistered event closed')
    check(not w.errors, 'no errors%s' % ('' if not w.errors else ': ' + '; '.join(w.errors[:4])))
    if timing:
        tags = w.tm_tags()
        check(all(re.fullmatch(rb'U[EHKVOP][0-9A-F]{2}|N[0-9][0-9A-F]{2}', t) for t in tags),
              'records well formed: %s' % b' '.join(tags[:12]).decode('latin1'))
        summ = bytes(w.uc.mem_read(COUNT, 10)).decode('latin1')
        nfs = sum(1 for h in w.order if w.handles[h]['sfs'])
        check(summ[0] == (('%X' % (nfs & 15)) if nfs else 'x') and summ[6] == str(ret) and
              (summ[7] == '1') == bool(expect_usb_first), 'summary %r' % summ)
    n = len(w.calls)
    ret2 = w.call()
    check(ret2 == 0 and len(w.calls) == n, 'a second ReadyToBoot does nothing')
    return w


def run_all(code, timing=False):
    boot, plain = dict(startup=True), dict(startup=False)
    S = lambda *a, **k: scenario(*a, timing=timing, **k)
    S('No file systems at all', code, [], 0, False)
    S('Internal memory only', code, [dict(MMC, parts=[plain, boot])], 0, False)
    S('Internal memory, USB drive without startup.nsh', code, [dict(MMC, parts=[boot]), USB(1, parts=[plain])], 0, False)
    S('Internal memory, USB drive with startup.nsh, shell loads after: USB first', code,
      [dict(MMC, parts=[boot]), USB(1, parts=[boot])], 2, True)
    S('... shell already loaded at ReadyToBoot: USB first at once', code,
      [dict(MMC, parts=[boot]), USB(1, parts=[boot])], 1, True, shell='before')
    S('... and with no shell ever loading: nothing else changed', code,
      [dict(MMC, parts=[boot]), USB(1, parts=[boot])], 2, False, shell='none')
    S('USB drive with startup.nsh on its second partition', code,
      [dict(MMC, parts=[boot]), USB(1, parts=[plain, boot])], 2, True)
    S('Two USB drives, only the second with startup.nsh', code,
      [dict(MMC, parts=[boot]), USB(1, parts=[plain]), USB(2, parts=[boot])], 2, True)
    S('USB drive found by USB class node', code,
      [dict(MMC, parts=[boot]), dict(name='usbc', usb=True, path=ACPI + pci(0x1d, 7) + usb_class, parts=[boot])], 2, True)
    S('USB drive whose volume will not open: nothing changes', code,
      [dict(MMC, parts=[boot]), USB(1, parts=[dict(startup=True, volume_fails=True)])], 0, False)
    S('USB drive whose startup.nsh will not open: nothing changes', code,
      [dict(MMC, parts=[boot]), USB(1, parts=[dict(startup=True, open_fails=True)])], 0, False)
    S('USB drive whose Open says success but gives no file: nothing changes', code,
      [dict(MMC, parts=[boot]), USB(1, parts=[dict(startup=True, open_null=True)])], 0, False)
    S('A USB file system with no device path is passed over', code,
      [dict(MMC, parts=[boot]), USB(1, parts=[boot])], 0, False, no_dp={5})
    S('A malformed device path (zero-length node) is passed over', code,
      [dict(MMC, parts=[boot]), USB(1, parts=[boot])], 0, False, bad_path={5})
    for v, label in (('byte', 'one byte of its comparison different'), ('small', 'ImageSize too small'),
                     ('guid', 'another file GUID'), ('nopath', 'no FilePath'),
                     ('nodetype', 'its GUID in a FilePath node of another type')):
        for when in ('before', 'after'):
            S('Shell with %s (loaded %s ReadyToBoot): left alone' % (label, when), code,
              [dict(MMC, parts=[boot]), USB(1, parts=[boot])], 2, False, shell=when, shell_variant=v)
    S('CreateEvent fails: nothing changes', code,
      [dict(MMC, parts=[boot]), USB(1, parts=[boot])], 0, False, create_fails=True)
    S('RegisterProtocolNotify fails: event closed, nothing changes', code,
      [dict(MMC, parts=[boot]), USB(1, parts=[boot])], 0, False, register_fails=True)


def mutate(path):
    import contextlib, io, runpy, shutil
    muts = runpy.run_path(path)['MUTATIONS']
    survived = []
    for m in muts:
        label, edits = m[0], (m[1] if len(m) == 2 else [(m[1], m[2])])
        with tempfile.TemporaryDirectory() as t:
            d = os.path.join(t, 'driver')
            shutil.copytree(DRIVER, d)
            f = os.path.join(d, 'usb_first.inc')
            src = open(f).read()
            bad = [old for old, _ in edits if src.count(old) != 1]
            if bad:
                print('PATTERN (%d matches)  %s' % (src.count(bad[0]), label))
                survived.append(label)
                continue
            for old, new in edits:
                src = src.replace(old, new)
            open(f, 'w').write(src)
            code, tcode = assemble(d), assemble(d, timing=True)
        if code is None or tcode is None:
            print('BUILD FAILED  %s' % label)
            survived.append(label)
            continue
        del failures[:]
        with contextlib.redirect_stdout(io.StringIO()):
            try:
                run_all(code)
                run_all(tcode, timing=True)
            except Exception:                      # the harness itself fell over: noticed
                failures.append('exception')
        print('%-9s %s' % ('CAUGHT' if failures else 'SURVIVED', label))
        if not failures:
            survived.append(label)
    print('%d of %d caught' % (len(muts) - len(survived), len(muts)))
    sys.exit(1 if survived else 0)


def main():
    if len(sys.argv) > 2 and sys.argv[1] == '--mutate':
        return mutate(sys.argv[2])
    code = assemble()
    print('usb_first: %d bytes assembled with its test entry; the shell from 26.11.29 (%d bytes)' % (len(code), len(SHELL)))
    run_all(code)
    tcode = assemble(timing=True)
    print()
    print('The boot-timing build (-DTIMING, %d bytes): the same scenarios, its records and summary' % len(tcode))
    run_all(tcode, timing=True)
    print()
    if failures:
        print('%d FAILED' % len(failures))
        sys.exit(1)
    print('all passed')


if __name__ == '__main__':
    main()
