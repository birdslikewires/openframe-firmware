#!/usr/bin/env python3
"""Runs driver/splash.inc under Unicorn on the real display drivers, then their own splash handlers
on the result.

    venv/bin/python -I tools/emu_splash.py of1_efi_o2.img of2_efi_cisco.img [IMAGE ...]
    venv/bin/python -I tools/emu_splash.py of1_efi_o2.img of2_efi_cisco.img of2_openpeak.img \
        of1_efi_openpeak_verizon.img of1_efi_telstra.img of2_efi_telio.img of2_efi_avaya.img
    venv/bin/python -I tools/emu_splash.py --mutate tools/mutations/splash.py of1_efi_o2.img of2_efi_cisco.img

The first image must have the Sharp panel's display driver (the stock O2 one), the second the
Samsung's (the Cisco OF2's); any more are other firmware to try (each must take our splash: from
26.11.32 the older builds too, of2_openpeak.img, of1_efi_openpeak_verizon.img and
of1_efi_telstra.img). Each display driver (IEGD GOP) is loaded as the firmware would: decompressed,
its sections at their RVAs, relocated to a base that isn't its own. gsp_patch_image is called with
a LoadedImage for it, then the driver's own `ssp` and `ssd` handlers (RVA 0x1960c and 0x18786 in
the current build, 0x50 earlier in the older ones, with their globals read from them) run on the result, as its
configuration parser calls them when the console starts the screen: `ssp` with the parse pointer
at the `c` of `$sspc01`, `ssd` with it at the first pixel. (The whole parser needs the buffers its
Start sets up first, so only these two are run.)

Checked: the patch reports success and changes nothing but the 11 digits of the splash's position
and size and the reserve from the first pixel to its end; it keeps ebx, esi, edi and ebp and
balances the stack; both handlers accept the result (status 1, as stock), `ssp` storing our
position and size where it stored stock's, `ssd` copying exactly our pixels (and, as it does stock,
the 8 bytes after them), and an old splash bigger than ours is all replaced; for the Sharp panel the patched stock O2 driver is byte for byte the display
driver in releases/of1-blw-26.11.30.bin, whose splash the hardware shows, and the handlers give the
same from both. Also, each of these is left exactly as it was (the patch reporting failure): one byte
of the panel settings different, one byte of either handler different, the reserve's end marker
different, another file GUID, no FilePath, an ImageSize too small. (Whatever the 11 digits held
isn't checked: they're replaced, with the old splash they described.)

--mutate runs each mutation in the given file (MUTATIONS = [(label, old, new), ...] or (label,
[(old, new), ...]), each `old` exactly once in splash.inc) on a copy of the driver directory, and
says whether the checks notice; it exits non-zero if any mutation survives.
"""
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import uuid

import pefile
from unicorn import Uc, UC_ARCH_X86, UC_MODE_32, UC_HOOK_MEM_INVALID, UcError
from unicorn.x86_const import (UC_X86_REG_ESP, UC_X86_REG_EAX, UC_X86_REG_EBX, UC_X86_REG_ESI, UC_X86_REG_EDI,
                               UC_X86_REG_EBP)

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, '..')
DRIVER = os.path.join(ROOT, 'driver')
sys.path.insert(0, HERE)
import build_patched as bp  # noqa: E402
import build_ucode_driver as bud  # noqa: E402

CODE, DATA, STACK, STOP, CFGBUF = 0x10000000, 0x11000000, 0x12000000, 0x13000000, 0x14000000
BASE = 0x20000000                       # where the display driver is loaded (its own base is 0)
GOP_GUID = '2b13e5f0-e82e-4209-898a-8a53e8570db9'
SSP, SSD = 0x1960c, 0x18786             # the display driver's splash handlers (the current build)
# Where the ssd handler names its globals (PTR, CONSUMED, FOUND, CONFIG): the current build's are
# 0x2adb0, 0x2a5bc, 0x2a5b8 and 0x2adac; an older build has the handlers `shift` bytes earlier and
# its globals elsewhere, so they're read from the handler itself.
GLOBAL_REFS = (0x187a2, 0x18796, 0x1878b, 0x187c7)
STRINGS, STRINGS_TEXT = 0x1524c, b'$\0\0\0^%\0\0ENDSSDATA\0\0\0,\0\0'   # the handlers' strings
CFGBUF_SIZE = 0x20000
SAVED = ((UC_X86_REG_EBX, 0x11111111), (UC_X86_REG_ESI, 0x22222222), (UC_X86_REG_EDI, 0x33333333),
         (UC_X86_REG_EBP, 0x44444444))
SPLASH = bud.splash_data(os.path.join(ROOT, 'logo', 'logo-blw-pecking-bird-204x82.png'), '274,199')

failures = []


def check(cond, what):
    print(('  ok   ' if cond else '  FAIL ') + what)
    if not cond:
        failures.append(what)


def gop(path):
    """The display driver's image relocated to BASE, and its ImageSize."""
    img = bytearray(open(path, 'rb').read())
    files, _ = bp.fv_files(img)
    o, s = next((o, s) for o, g, s in files if g == GOP_GUID)
    stream, _ = bp.decompress_file(img, o, s)
    pe = pefile.PE(data=bytes(stream[bp.pe_offset(stream):]))
    pe.relocate_image(BASE)
    return bytes(pe.get_memory_mapped_image()), pe.OPTIONAL_HEADER.SizeOfImage


def assemble(driver=DRIVER):
    """gsp_patch_image behind a jmp at offset 0, with mmc_sig_match from mmc_fix.inc."""
    fix = open(os.path.join(driver, 'mmc_fix.inc')).read()
    m = re.search(r'^mmc_sig_match:.*?^        ret\n', fix, re.S | re.M)
    with tempfile.TemporaryDirectory() as t:
        src, out, sp = os.path.join(t, 'w.asm'), os.path.join(t, 'w.bin'), os.path.join(t, 'splash.bin')
        open(sp, 'wb').write(SPLASH)
        # panel_id.inc (from dev2): splash.inc notes the panel configuration for it. The wrapper
        # says where its note is: a dword after the jmp, 0 for a driver without it.
        pid = os.path.exists(os.path.join(driver, 'panel_id.inc'))
        open(src, 'w').write('bits 32\n        jmp near gsp_patch_image\n' +
                             ('        dd pid_display\n%include "panel_id.inc"\n' if pid else '        dd 0\n') +
                             '%include "splash.inc"\n' + m.group(0))
        r = subprocess.run(['nasm', '-f', 'bin', '-I', driver + '/', '-DSPLASH_FILE="%s"' % sp, '-o', out, src],
                           capture_output=True, text=True)
        if r.returncode:
            if driver != DRIVER:
                return None
            sys.exit('nasm: ' + r.stderr)
        return open(out, 'rb').read()


def machine(code, image, size):
    uc = Uc(UC_ARCH_X86, UC_MODE_32)
    for base, n in ((CODE, 0x100000), (DATA, 0x10000), (STACK - 0x10000, 0x20000), (STOP, 0x1000),
                    (CFGBUF, CFGBUF_SIZE), (BASE, (len(image) + 0xfff) & ~0xfff)):
        uc.mem_map(base, n)
    uc.mem_write(CODE, code)
    uc.mem_write(BASE, image)
    bad = []
    uc.hook_add(UC_HOOK_MEM_INVALID, lambda uc_, acc, addr, sz, v, _: bad.append(addr) or False)
    return uc, bad


def call(uc, fn, args):
    sp = STACK - 0x100
    uc.mem_write(sp, struct.pack('<%dI' % (len(args) + 1), STOP, *args))
    uc.reg_write(UC_X86_REG_ESP, sp)
    for r, v in SAVED:
        uc.reg_write(r, v)
    uc.emu_start(fn, STOP, count=20_000_000)
    return uc.reg_read(UC_X86_REG_EAX)


def patch(code, image, size, guid=GOP_GUID, path=True, li_size=None):
    """gsp_patch_image on this image: (status, image after, registers kept, stack balanced, bad accesses)."""
    uc, bad = machine(code, image, size)
    li, node = DATA, DATA + 0x100
    uc.mem_write(node, b'\x04\x06\x14\x00' + uuid.UUID(guid).bytes_le + b'\x7f\xff\x04\x00')
    uc.mem_write(li, struct.pack('<IIIIII', 0x1000, 0, 0, 0, node if path else 0, 0))
    uc.mem_write(li + 0x20, struct.pack('<IIQ', BASE, 0, li_size or size))
    try:
        st = call(uc, CODE, [li])
    except UcError as e:
        return None, image, False, False, ['%s' % e] + bad
    kept = all(uc.reg_read(r) == v for r, v in SAVED)
    balanced = uc.reg_read(UC_X86_REG_ESP) == STACK - 0x100 + 4
    return st, bytes(uc.mem_read(BASE, len(image))), kept, balanced, bad


def build(image):
    """(ssp, ssd, PTR, CONSUMED, FOUND, CONFIG) for this display driver's build."""
    shift = STRINGS - image.find(STRINGS_TEXT)
    g = [struct.unpack_from('<I', image, r - shift)[0] - BASE for r in GLOBAL_REFS]
    return (SSP - shift, SSD - shift, *g)


def handlers(image, size):
    """The display driver's ssp and ssd handlers on this image's configuration, as its parser calls
    them: (ssp status, the five numbers it stored, ssd status, the bytes it copied, bad accesses)."""
    SSP, SSD, PTR, CONSUMED, FOUND, CONFIG = build(image)
    v, px, end = layout(image)
    uc, bad = machine(b'\xc3', image, size)
    put = lambda rva, x: uc.mem_write(BASE + rva, struct.pack('<I', x))
    try:
        put(CONFIG, CFGBUF)
        put(PTR, BASE + v - 3)                          # at the 'c' of $sspc01
        s1 = call(uc, BASE + SSP, [])
        nums = struct.unpack('<5I', uc.mem_read(CFGBUF + 0x33c, 20))
        put(PTR, BASE + px)                             # at the first pixel, after "$ssd"
        put(CONSUMED, px - 0x13d7)
        put(FOUND, 0)
        s2 = call(uc, BASE + SSD, [])
        n = struct.unpack('<I', uc.mem_read(BASE + FOUND, 4))[0]
        copied = bytes(uc.mem_read(CFGBUF + 0x1a5c, n)) if 0 < n < 0x10000 else b''
    except UcError as e:
        return None, None, None, b'', ['%s' % e] + bad
    return s1, nums, s2, copied, bad


def layout(image):
    """(numbers offset, first pixel, reserve end) found the plain way, for the checks."""
    v = image.find(b'$sspc01') + 7
    px = image.find(b'$ssd', v) + 4
    end = [m.start() for m in re.finditer(b'CEDCED', image[:0x20000])][1]
    return v, px, end


def panel_note(code, image, size):
    """What gsp_patch_image notes about the panel configuration (panel_id.inc): 1 Sharp, 2 Samsung,
    0 neither; None for a driver without the note."""
    at = struct.unpack_from('<I', code, 5)[0]
    if not at:
        return None
    uc, bad = machine(code, image, size)
    li, node = DATA, DATA + 0x100
    uc.mem_write(node, b'\x04\x06\x14\x00' + uuid.UUID(GOP_GUID).bytes_le + b'\x7f\xff\x04\x00')
    uc.mem_write(li, struct.pack('<IIIIII', 0x1000, 0, 0, 0, node, 0))
    uc.mem_write(li + 0x20, struct.pack('<IIQ', BASE, 0, size))
    try:
        call(uc, CODE, [li])
    except UcError:
        return 'crashed'
    return uc.mem_read(CODE + at, 1)[0]


def run_all(code, images):
    rel_path = os.path.join(ROOT, 'releases', 'of1-blw-26.11.30.bin')
    if build(gop(images[0])[0])[:2] != (SSP, SSD):
        sys.exit('the first image must have the current build of the display driver (of1_efi_o2.img)')
    release = gop(rel_path)[0] if os.path.exists(rel_path) else None
    parsed = {}
    for n, path in enumerate(images):
        name = os.path.basename(path)
        image, size = gop(path)
        print('%s:' % name)
        h0 = handlers(image, size)
        want_pn = {b'OpenFrame-Sh': 1, b'OpenFrame-Sa': 2}.get(bytes(image[0x13fb:0x13fb + 12]), 0)
        pn = panel_note(code, image, size)
        if pn is not None:
            check(pn == want_pn, 'panel noted as %r (its label says %s)' % (pn, {1: 'Sharp', 2: 'Samsung', 0: 'neither'}[want_pn]))
        st, after, kept, balanced, bad = patch(code, image, size)
        check(st == 1 and not bad, 'patched (status %r%s)' % (st, ', bad accesses %s' % [hex(b) for b in bad[:3]] if bad else ''))
        check(kept and balanced, 'ebx, esi, edi, ebp kept and the stack balanced')
        v, px, end = layout(image)
        want = bytearray(image)
        want[v:v + 24] = SPLASH[:24]
        want[px:end] = SPLASH[24:] + bytes(end - px - (len(SPLASH) - 24))
        diff = [i for i in range(len(image)) if after[i] != want[i]]
        check(not diff, 'only the numbers and the reserve changed, to our splash' + (' (%d bytes differ, first %#x)' % (len(diff), diff[0]) if diff else ''))
        h1 = handlers(after, size)
        check(h0[0] == h0[2] == 1 and h1[0] == h1[2] == 1 and not h1[4],
              'its own handlers accept it, as they did stock (ssp %r, ssd %r; stock %r, %r)' % (h1[0], h1[2], h0[0], h0[2]))
        check(h1[1] == (0, 0x112, 0xc7, 0xcc, 0x52), 'ssp stores our position and size: %s (stock %s)' % (
            h1[1] and ','.join('%#x' % x for x in h1[1]), h0[1] and ','.join('%#x' % x for x in h0[1])))
        pix = SPLASH[24:-9]
        check(h1[3] == pix + b'ENDSSDAT' and len(h0[3]) == h0[1][3] * h0[1][4] * 3 + 8,
              'ssd copies exactly our %d pixel bytes (and the 8 after them, as it does stock)' % len(pix))
        parsed[name] = h1
        if n == 0 and release is not None:
            check(after == release, 'the same as 26.11.30\'s display driver, byte for byte')
            check(handlers(release, size)[:4] == h1[:4], '26.11.30\'s display driver gives the handlers the same')

    print('an old splash bigger than ours (as the Verizon OF1\'s 300x56):')
    image, size = gop(images[0])
    v, px, end = layout(image)
    m = bytearray(image)
    m[px:end - 0x100] = bytes((i * 13 + 5) & 0xff for i in range(end - 0x100 - px))
    m[end - 0x100:end - 0xf7] = b'ENDSSDATA'
    st, after, kept, balanced, bad = patch(code, bytes(m), size)
    want = bytearray(m)
    want[v:v + 24] = SPLASH[:24]
    want[px:end] = SPLASH[24:] + bytes(end - px - (len(SPLASH) - 24))
    check(st == 1 and after == bytes(want), 'all of it replaced: ours, then zeros to the reserve\'s end')

    print('left alone:')
    image, size = gop(images[0])
    v, px, end = layout(image)
    cfg = image.find(b'^%0x700$01$')
    dtd = image.find(b'$dtdc01', cfg)
    variants = [
        ('one byte of the panel settings different', lambda m: m.__setitem__(dtd + 12, m[dtd + 12] ^ 1), {}),
        ('one byte of the ssd handler different', lambda m: m.__setitem__(0x187a6 + 6, m[0x187a6 + 6] ^ 1), {}),
        ('one byte of the ssp handler different', lambda m: m.__setitem__(0x19700, m[0x19700] ^ 1), {}),
        ('the reserve\'s end marker different', lambda m: m.__setitem__(end, m[end] ^ 1), {}),
        ('another file GUID', None, dict(guid='11111111-2222-3333-4444-555555555555')),
        ('no FilePath', None, dict(path=False)),
        ('ImageSize too small', None, dict(li_size=0x19000)),
    ]
    for label, f, kw in variants:
        m = bytearray(image)
        if f:
            f(m)
        st, after, kept, balanced, bad = patch(code, bytes(m), size, **kw)
        check(st == 0 and after == bytes(m) and kept and balanced and not bad, '%s: status %r, %s' % (
            label, st, 'unchanged' if after == bytes(m) else 'CHANGED'))


def mutate(path, images):
    import contextlib, io, runpy
    muts = runpy.run_path(path)['MUTATIONS']
    survived = []
    for m in muts:
        label, edits = m[0], (m[1] if len(m) == 2 else [(m[1], m[2])])
        with tempfile.TemporaryDirectory() as t:
            d = os.path.join(t, 'driver')
            shutil.copytree(DRIVER, d)
            f = os.path.join(d, 'splash.inc')
            src = open(f).read()
            bad = [old for old, _ in edits if src.count(old) != 1]
            if bad:
                print('PATTERN (%d matches)  %s' % (src.count(bad[0]), label))
                survived.append(label)
                continue
            for old, new in edits:
                src = src.replace(old, new)
            open(f, 'w').write(src)
            code = assemble(d)
        if code is None:
            print('BUILD FAILED  %s' % label)
            survived.append(label)
            continue
        del failures[:]
        with contextlib.redirect_stdout(io.StringIO()):
            try:
                run_all(code, images)
            except Exception:
                failures.append('exception')
        print('%-9s %s' % ('CAUGHT' if failures else 'SURVIVED', label))
        if not failures:
            survived.append(label)
    print('%d of %d caught' % (len(muts) - len(survived), len(muts)))
    sys.exit(1 if survived else 0)


def main():
    args = sys.argv[1:]
    if args and args[0] == '--mutate':
        return mutate(args[1], args[2:])
    if len(args) < 2:
        sys.exit(__doc__)
    run_all(assemble(), args)
    print('%d failures' % len(failures) if failures else 'all ok')
    sys.exit(1 if failures else 0)


if __name__ == '__main__':
    main()
