"""Runs the firmware's own MMC/SD media driver (MmcMediaDevice) under Unicorn against a fake SD host
and card, first as it is, to show the bug, then wrapped by BlwMmcFix.efi, which must make every
read and write land where it should. Also runs MmcTest.efi against the same fake disk.

    venv/bin/python -I tools/emu_mmcfix.py <firmware.bin> <BlwMmcFix.efi> <MmcTest.efi>

The fake host checks what the real one needs: each data command's buffer is the driver's aligned
bounce buffer and the transfer doesn't cross a 512 KB DMA boundary (SendCommand never handles a
DMA interrupt, so on hardware that would hang). Exits non-zero on any failure.
"""
import struct
import sys
import zlib

import pefile
from unicorn import Uc, UC_ARCH_X86, UC_MODE_32, UC_HOOK_CODE, UcError
from unicorn.x86_const import UC_X86_REG_ESP, UC_X86_REG_EAX, UC_X86_REG_EIP

sys.path.insert(0, __file__.rsplit('/', 1)[0])
from mmc_sig import find_pe  # noqa: E402
import uefi_firmware  # noqa: E402

MMC_BASE = 0x10000000
FIX_BASE = 0x20000000
TEST_BASE = 0x20100000
DATA = 0x30000000          # CardData, SdHostIo, ST, BS, ConOut
BOUNCE = 0x30400000        # the driver's 512 KB-aligned bounce buffer (2 x 512 KB)
HEAP = 0x31000000
HOOKS = 0x3f000000
STOP = 0x3fff0000
STACK = 0x3ff00000
CARD_SIZE = 32 << 20
GBS_GLOBAL = MMC_BASE + 0x333c   # MmcMediaDevice's gBS

failures = []


def check(cond, what):
    print(('  ok   ' if cond else '  FAIL ') + what)
    if not cond:
        failures.append(what)


class Emu:
    def __init__(self, mmc_pe, fix_pe, test_pe):
        self.uc = uc = Uc(UC_ARCH_X86, UC_MODE_32)
        for base, size in ((MMC_BASE, 0x10000), (FIX_BASE, 0x10000), (TEST_BASE, 0x10000), (DATA, 0x1000000),
                           (HEAP, 0x4000000), (HOOKS, 0x10000), (STOP, 0x1000), (STACK - 0x100000, 0x100000)):
            uc.mem_map(base, size)
        self.images = {}
        for base, pe in ((MMC_BASE, mmc_pe), (FIX_BASE, fix_pe), (TEST_BASE, test_pe)):
            self.images[base] = bytes(pe.get_memory_mapped_image(ImageBase=base))
            uc.mem_write(base, self.images[base])
        self.heap = HEAP
        self.pools = []
        self.freed = []
        self.out = []
        self.cmds = []
        self.problems = []
        self.card = bytearray(CARD_SIZE)
        for i in range(0, CARD_SIZE, 4):            # every dword says where it is
            struct.pack_into('<I', self.card, i, i)
        self.hooks = {}
        uc.hook_add(UC_HOOK_CODE, self._hook, begin=HOOKS, end=HOOKS + 0x10000)
        uc.hook_add(UC_HOOK_CODE, lambda *a: a[0].emu_stop(), begin=STOP, end=STOP)

        # Boot services: only what the code under test calls.
        bs = DATA + 0x1000
        for idx, fn in ((5, self.allocate_pool), (6, self.free_pool), (16, self.handle_protocol),
                        (36, self.locate_handle_buffer), (40, self.crc32), (41, self.copy_mem)):
            uc.mem_write(bs + 0x18 + 4 * idx, struct.pack('<I', self.hook(fn)))
        self.bs = bs
        conout = DATA + 0x1400
        uc.mem_write(conout + 4, struct.pack('<I', self.hook(self.output_string)))
        st = DATA + 0x1800
        uc.mem_write(st + 0x2c, struct.pack('<I', conout))
        uc.mem_write(st + 0x3c, struct.pack('<I', bs))
        self.st = st
        uc.mem_write(GBS_GLOBAL, struct.pack('<I', bs))

        # The SD host: Revision, capabilities, BoundarySize, then SendCommand ... ResetSdHost.
        host = DATA + 0x2000
        uc.mem_write(host, struct.pack('<III', 1, 0, 0x80000))
        uc.mem_write(host + 0x0c, struct.pack('<I', self.hook(self.send_command)))
        uc.mem_write(host + 0x1c, struct.pack('<I', self.hook(lambda a: 0)))
        self.host = host

        # CardData at DATA + 0x3000, BlockIo at CardData + 8 (the driver's layout).
        self.bio = DATA + 0x3008
        self.set_card(card_type=1)
        self.handles = [self.bio]

    def set_card(self, card_type):
        bio, uc = self.bio, self.uc
        uc.mem_write(bio, struct.pack('<QIIIII', 0x10000, bio + 0x20, 0, MMC_BASE + 0xf38, MMC_BASE + 0x109c, 0))
        media = struct.pack('<IBBBBB3xIIIQ', 1, 0, 1, 0, 0, 0, 512, 0, 0, CARD_SIZE // 512 - 1)
        uc.mem_write(bio + 0x20, media)
        uc.mem_write(bio + 0x40, struct.pack('<I', self.host))
        uc.mem_write(bio + 0x48, struct.pack('<I', card_type))
        uc.mem_write(bio + 0x294, struct.pack('<I', BOUNCE))
        self.card_type = card_type

    def hook(self, fn):
        addr = HOOKS + 0x10 * len(self.hooks)
        self.uc.mem_write(addr, b'\xc3')            # ret, after the hook has set eax
        self.hooks[addr] = fn
        return addr

    def _hook(self, uc, addr, size, user):
        fn = self.hooks.get(addr)
        if fn:
            esp = uc.reg_read(UC_X86_REG_ESP)
            args = lambda n: struct.unpack('<%dI' % n, uc.mem_read(esp + 4, 4 * n))
            uc.reg_write(UC_X86_REG_EAX, fn(args) & 0xffffffff)

    def rd32(self, a):
        return struct.unpack('<I', self.uc.mem_read(a, 4))[0]

    def call(self, fn, *args):
        esp = STACK - 0x100
        self.uc.mem_write(esp, struct.pack('<I', STOP) + b''.join(struct.pack('<I', a & 0xffffffff) for a in args))
        self.uc.reg_write(UC_X86_REG_ESP, esp)
        try:
            self.uc.emu_start(fn, STOP, count=50_000_000)
        except UcError as e:
            raise SystemExit('emulation failed at 0x%x: %s' % (self.uc.reg_read(UC_X86_REG_EIP), e))
        if self.uc.reg_read(UC_X86_REG_EIP) != STOP:
            raise SystemExit('did not return (eip 0x%x)' % self.uc.reg_read(UC_X86_REG_EIP))
        if self.uc.reg_read(UC_X86_REG_ESP) != esp + 4:
            raise SystemExit('stack unbalanced')
        return self.uc.reg_read(UC_X86_REG_EAX)

    # --- fakes ---
    def allocate_pool(self, a):
        ptype, size, out = a(3)
        p = self.heap
        self.pools.append((ptype, p))
        self.heap = (self.heap + size + 0xfff) & ~0xfff
        self.uc.mem_write(out, struct.pack('<I', p))
        return 0

    def free_pool(self, a):
        self.freed.append(a(1)[0])
        return 0

    def locate_handle_buffer(self, a):
        stype, guid, key, nout, bout = a(5)
        g = bytes(self.uc.mem_read(guid, 16))
        assert stype == 2 and g == bytes.fromhex('215b4e9659 64d211 8e3900a0c969723b'.replace(' ', '')), 'unexpected LocateHandleBuffer'
        buf = self.heap
        self.heap += 0x1000
        self.uc.mem_write(buf, b''.join(struct.pack('<I', 0x5000 + i) for i in range(len(self.handles))))
        self.uc.mem_write(nout, struct.pack('<I', len(self.handles)))
        self.uc.mem_write(bout, struct.pack('<I', buf))
        return 0

    def handle_protocol(self, a):
        h, guid, out = a(3)
        self.uc.mem_write(out, struct.pack('<I', self.handles[h - 0x5000]))
        return 0

    def crc32(self, a):
        data, size, out = a(3)
        self.uc.mem_write(out, struct.pack('<I', zlib.crc32(bytes(self.uc.mem_read(data, size)))))
        return 0

    def copy_mem(self, a):
        dst, src, n = a(3)
        self.uc.mem_write(dst, bytes(self.uc.mem_read(src, n)))
        return 0

    def output_string(self, a):
        _, s = a(2)
        chars = []
        while (c := struct.unpack('<H', self.uc.mem_read(s + 2 * len(chars), 2))[0]):
            chars.append(chr(c))
        self.out.append(''.join(chars))
        return 0

    def send_command(self, a):
        host, cmd, arg, dtype, buf, size, rtype, timeout, resp = a(9)
        cmd &= 0xffff
        self.cmds.append((cmd, arg, dtype, size))
        if resp:
            self.uc.mem_write(resp, struct.pack('<I', 0x920 if cmd == 55 else 0x900))
        if dtype in (1, 2):
            addr = arg * 512 if self.card_type == 5 else arg
            if buf & 0x7ffff or (buf & 0x7ffff) + size > 0x80000:
                self.problems.append('CMD%d buffer 0x%x size 0x%x crosses a DMA boundary' % (cmd, buf, size))
            if addr + size > CARD_SIZE:
                self.problems.append('CMD%d beyond the card' % cmd)
                return 0x80000007
            if dtype == 1:
                self.uc.mem_write(buf, bytes(self.card[addr:addr + size]))
            else:
                self.card[addr:addr + size] = self.uc.mem_read(buf, size)
        return 0

    # --- helpers ---
    def blockio(self, which, lba, size, buf, media_id=1):
        fn = self.rd32(self.bio + (0x10 if which == 'read' else 0x14))
        return self.call(fn, self.bio, media_id, lba & 0xffffffff, lba >> 32, size, buf)


def read_ok(e, lba, size, label, expect_bad=False):
    buf = 0x32000000
    e.uc.mem_write(buf, b'\xee' * size)
    e.cmds.clear()
    st = e.blockio('read', lba, size, buf)
    got = bytes(e.uc.mem_read(buf, size))
    want = bytes(e.card[lba * 512:lba * 512 + size])
    bad = next((i for i in range(0, size, 512) if got[i:i + 512] != want[i:i + 512]), None)
    check(st == 0 and (bad is not None if expect_bad else bad is None),
          '%s: read %d bytes at LBA %d -> status 0x%x, %s' % (label, size, lba, st,
              'all correct' if bad is None else 'wrong from byte 0x%x (%d KB)' % (bad, bad // 1024)))
    return bad


def main():
    fw_path, fix_path, test_path = sys.argv[1:4]
    mmc_pe = pefile.PE(data=find_pe(uefi_firmware.AutoParser(open(fw_path, 'rb').read()).parse()))
    e = Emu(mmc_pe, pefile.PE(fix_path), pefile.PE(test_path))

    print('The driver as it is:')
    bad = read_ok(e, 2048, 1 << 20, 'unwrapped 1 MB')
    bad = read_ok(e, 2048, 3 << 20, 'unwrapped 3 MB (the bug)', expect_bad=True)
    if bad is not None:
        check(bad == 1 << 20, 'unwrapped 3 MB goes wrong exactly at 1 MB (the bug as diagnosed)')
        addrs = [c[1] for c in e.cmds if c[0] == 18]
        print('       CMD18 addresses: ' + ' '.join('0x%x' % x for x in addrs))
    else:
        check(False, 'unwrapped 3 MB should have shown the bug')

    print('BlwMmcFix:')
    in_pool = lambda a: HEAP <= a < HEAP + 0x4000000

    def run_fix():
        """Runs the app as the shell would, then frees its image as the core does when an app
        returns: anything left pointing into it hits int3."""
        e.uc.mem_write(FIX_BASE, e.images[FIX_BASE])
        e.pools.clear()
        e.freed.clear()
        e.out.clear()
        st = e.call(FIX_BASE + 0x200, 0x1234, e.st)
        e.uc.mem_write(FIX_BASE, b'\xcc' * len(e.images[FIX_BASE]))
        return st

    nonmmc = DATA + 0x5008                          # another driver's BlockIo, found first
    e.uc.mem_write(nonmmc, bytes(e.uc.mem_read(e.bio, 0x300)))
    e.uc.mem_write(nonmmc + 0x8, struct.pack('<I', nonmmc + 0x20))
    e.uc.mem_write(nonmmc + 0x10, struct.pack('<II', TEST_BASE + 0x200, TEST_BASE + 0x200))
    e.handles = [nonmmc, e.bio]
    st = run_fix()
    check(st == 0, 'returns EFI_SUCCESS (0x%x)' % st)
    check(''.join(e.out).strip().endswith('00000001'), 'prints one instance wrapped: %r' % ''.join(e.out).strip())
    check([t for t, _ in e.pools] == [3] and e.pools[0][1] not in e.freed, 'one boot-services code pool, kept')
    rd, wr = e.rd32(e.bio + 0x10), e.rd32(e.bio + 0x14)
    check(in_pool(rd) and in_pool(wr), 'ReadBlocks/WriteBlocks now in the pool copy (0x%x, 0x%x)' % (rd, wr))
    check(e.rd32(nonmmc + 0x10) == TEST_BASE + 0x200 and e.rd32(nonmmc + 0x14) == TEST_BASE + 0x200,
          'the other driver\'s BlockIo, seen first, is left alone')
    e.handles = [e.bio]

    for card_type, name in ((1, 'byte-addressed card'), (5, 'sector-addressed (SDHC) card')):
        e.set_card(card_type)
        e.uc.mem_write(e.bio + 0x10, struct.pack('<II', rd, wr))
        print('Wrapped, %s (the app\'s image gone):' % name)
        for lba, size in ((2048, 3 << 20), (0, 512), (1, 1024), (77, (1 << 20) + 512), (2048, 0x80000),
                          (2048, 0x80000 + 512), (5000, (5 << 20) + 3 * 512), (0, 9 << 20), (12345, 7 << 20)):
            read_ok(e, lba, size, 'wrapped')
        e.problems and check(False, 'host problems: %s' % e.problems[:3])
        e.problems.clear()

        # Writes: put a fresh pattern down through WriteBlocks, read the card back.
        lba, size = 3000, (4 << 20) + 1536
        src = 0x33000000
        pattern = bytes((i * 7 + 3) & 0xff for i in range(size))
        e.uc.mem_write(src, pattern)
        before = bytes(e.card)
        st = e.blockio('write', lba, size, src)
        ok = bytes(e.card[lba * 512:lba * 512 + size]) == pattern
        rest = before[:lba * 512] == bytes(e.card[:lba * 512]) and before[lba * 512 + size:] == bytes(e.card[lba * 512 + size:])
        check(st == 0 and ok and rest, 'wrapped write of %d bytes at LBA %d lands exactly (status 0x%x)' % (size, lba, st))
        e.card[:] = before

    print('Requests the original refuses before any transfer:')
    e.set_card(1)
    e.uc.mem_write(e.bio + 0x10, struct.pack('<II', rd, wr))
    e.cmds.clear()
    st = e.blockio('read', 0, (2 << 20) + 100, 0x32000000)
    check(st == 0x80000004 and not e.cmds, 'size not whole blocks -> EFI_BAD_BUFFER_SIZE, nothing sent (0x%x)' % st)
    st = e.blockio('read', 0, 2 << 20, 0)
    check(st == 0x80000002 and not e.cmds, 'NULL buffer -> EFI_INVALID_PARAMETER, nothing sent (0x%x)' % st)
    st = e.blockio('read', 0, 0, 0x32000000)
    check(st == 0 and not e.cmds, 'zero bytes -> EFI_SUCCESS, nothing sent (0x%x)' % st)

    print('An error part-way stops the split and is returned:')
    orig_send = e.hooks[e.rd32(e.host + 0x0c)]
    n = {'c': 0}

    def failing(a):
        cmd = a(2)[1] & 0xffff
        if cmd == 18:
            n['c'] += 1
            if n['c'] == 3:
                return 0x80000007
        return orig_send(a)
    e.hooks[e.rd32(e.host + 0x0c)] = failing
    e.cmds.clear()
    st = e.blockio('read', 2048, 4 << 20, 0x32000000)
    reads = sum(1 for c in e.cmds if c[0] == 18)
    check(st == 0x80000007 and reads == 2, 'third piece fails -> EFI_DEVICE_ERROR after two good pieces (0x%x, %d reads)' % (st, reads))
    e.hooks[e.rd32(e.host + 0x0c)] = orig_send

    print('Run again, with a BlockIo that isn\'t the MMC driver\'s:')
    other = DATA + 0x4008
    e.uc.mem_write(other, bytes(e.uc.mem_read(e.bio, 0x300)))
    e.uc.mem_write(other + 0x8, struct.pack('<I', other + 0x20))
    e.uc.mem_write(other + 0x10, struct.pack('<II', TEST_BASE + 0x200, TEST_BASE + 0x200))   # some other code
    e.handles = [e.bio, other]
    st = run_fix()
    check(st == 0x8000000e, 'nothing left to wrap -> EFI_NOT_FOUND (0x%x)' % st)
    check(len(e.pools) == 1 and e.pools[0][1] in e.freed, 'and the unused copy is freed')
    check(e.rd32(e.bio + 0x10) == rd and e.rd32(other + 0x10) == TEST_BASE + 0x200, 'neither instance changed')
    read_ok(e, 2048, 3 << 20, 'the first copy still works')

    print('A fresh instance of the MMC driver alongside the wrapped one:')
    e.uc.mem_write(other + 0x10, struct.pack('<II', MMC_BASE + 0xf38, MMC_BASE + 0x109c))
    st = run_fix()
    check(st == 0 and in_pool(e.rd32(other + 0x10)) and e.rd32(other + 0x10) != rd and e.rd32(e.bio + 0x10) == rd,
          'wrapped by the new copy, the old one left as it was (0x%x)' % st)

    # Within one run, the originals recorded from the first instance must match later ones.
    third = DATA + 0x6008
    e.uc.mem_write(third, bytes(e.uc.mem_read(e.bio, 0x300)))
    e.uc.mem_write(third + 0x8, struct.pack('<I', third + 0x20))

    print('One run: the driver\'s instance, then one whose WriteBlocks is a second copy, elsewhere:')
    copy = MMC_BASE + 0x8000
    e.uc.mem_write(copy, e.images[MMC_BASE][0x109c:0x1211])
    e.uc.mem_write(e.bio + 0x10, struct.pack('<II', MMC_BASE + 0xf38, MMC_BASE + 0x109c))
    e.uc.mem_write(third + 0x10, struct.pack('<II', MMC_BASE + 0xf38, copy))
    e.handles = [e.bio, third]
    st = run_fix()
    rd2 = e.rd32(e.bio + 0x10)
    check(st == 0 and in_pool(rd2) and e.rd32(third + 0x10) == rd2 and e.rd32(third + 0x14) == copy,
          'its reads are wrapped, its writes left to the copy, not sent to the recorded original (0x%x)' % st)

    print('One run: the driver\'s instance, then one whose ReadBlocks is a second copy:')
    rcopy = MMC_BASE + 0x9000
    e.uc.mem_write(rcopy, e.images[MMC_BASE][0xf38:0x109c])
    e.uc.mem_write(e.bio + 0x10, struct.pack('<II', MMC_BASE + 0xf38, MMC_BASE + 0x109c))
    e.uc.mem_write(third + 0x10, struct.pack('<II', rcopy, MMC_BASE + 0x109c))
    st = run_fix()
    check(st == 0 and e.rd32(third + 0x10) == rcopy and e.rd32(third + 0x14) == MMC_BASE + 0x109c,
          'that instance is left alone (0x%x)' % st)

    print('One run: the driver\'s reads with another driver\'s writes:')
    e.uc.mem_write(third + 0x10, struct.pack('<II', MMC_BASE + 0xf38, TEST_BASE + 0x200))
    e.handles = [third]
    st = run_fix()
    check(st == 0 and in_pool(e.rd32(third + 0x10)) and e.rd32(third + 0x14) == TEST_BASE + 0x200,
          'reads wrapped, the other WriteBlocks left alone (0x%x)' % st)

    # Not tested: the carry into the LBA's high dword. The driver only ever uses the low dword
    # (imul with [ebp+0x10]), so nothing it does can show it.

    print('MmcTest.efi:')
    e.handles = [e.bio]
    e.set_card(1)
    e.out.clear()
    st = e.call(TEST_BASE + 0x200, 0x1234, e.st)
    text = ''.join(e.out)
    print('    ' + text.replace('\r\n', '\n    ').rstrip())
    crcs = [zlib.crc32(bytes(e.card[2048 * 512 + i * 0x80000:2048 * 512 + (i + 1) * 0x80000])) for i in range(6)]
    want = ' '.join('%08x' % c for c in crcs)
    one = [l for l in text.split('\r\n') if l.strip().startswith('one')]
    pcs = [l for l in text.split('\r\n') if l.strip().startswith('pcs')]
    check(st == 0 and one and pcs, 'runs and reports (0x%x)' % st)
    check(pcs and pcs[0].endswith(want), 'piecewise CRCs match the card')
    check(one and not one[0].endswith(want), 'one-call CRCs show the bug on the unwrapped driver')

    print()
    if failures:
        print('%d FAILED' % len(failures))
        sys.exit(1)
    print('all passed')


if __name__ == '__main__':
    main()
