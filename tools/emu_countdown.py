#!/usr/bin/env python3
"""Runs driver/countdown.inc under Unicorn on the real built-in shells.

    venv/bin/python -I tools/emu_countdown.py of1_efi_o2.img of2_efi_cisco.img [IMAGE ...]
    venv/bin/python -I tools/emu_countdown.py --mutate tools/mutations/countdown.py of1_efi_o2.img of2_efi_cisco.img

The first image must have the OF1's shell build, the second the OF2's; more images (our OF1
releases, other OF2 firmware) are each tried too, and releases/of1-blw-26.11.30.bin if it is
there. Each shell is loaded as the firmware would (decompressed, sections at their RVAs, relocated
to a base that isn't its own), and cd_patch_image is called with a LoadedImage for it.

Checked: the patch reports success; exactly the countdown's two `push 0x32` operands change, to 0
(or nothing, where they are 0 already); for the OF1 shell the result has the same countdown
function as our releases' (whose countdown is proven 0 on hardware); ebx, esi and edi kept and the
stack balanced. Left exactly as they were (the patch reporting failure): one byte of the function
different, another file GUID, no FilePath, ImageSize too small, the OF1 function at the OF2's place.

--mutate runs each mutation in the given file (MUTATIONS = [(label, old, new), ...], each `old`
exactly once in countdown.inc) on a copy of the driver directory; it exits non-zero if any survives.
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
from unicorn.x86_const import UC_X86_REG_ESP, UC_X86_REG_EAX, UC_X86_REG_EBX, UC_X86_REG_ESI, UC_X86_REG_EDI

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, '..')
DRIVER = os.path.join(ROOT, 'driver')
sys.path.insert(0, HERE)
import build_patched as bp  # noqa: E402

CODE, DATA, STACK, STOP, BASE = 0x10000000, 0x11000000, 0x12000000, 0x13000000, 0x20000000
SHELL_GUID = 'c57ad6b7-0515-40a8-9d21-551652854e37'
SITES = (0x0e, 0x36)
SAVED = ((UC_X86_REG_EBX, 0x11111111), (UC_X86_REG_ESI, 0x22222222), (UC_X86_REG_EDI, 0x33333333))
failures = []


def check(cond, what):
    print(('  ok   ' if cond else '  FAIL ') + what)
    if not cond:
        failures.append(what)


def shell(path):
    img = bytearray(open(path, 'rb').read())
    o, s = next((o, s) for o, g, s in bp.fv_files(img)[0] if g == SHELL_GUID)
    stream, _ = bp.decompress_file(img, o, s)
    pe = pefile.PE(data=bytes(stream[bp.pe_offset(stream):]))
    pe.relocate_image(BASE)
    return bytes(pe.get_memory_mapped_image()), pe.OPTIONAL_HEADER.SizeOfImage


def countdown_at(image):
    m = re.search(rb'\x55\x8b\xec\x83\xec\x14\xa1.{4}\x53\x56\x6a[\x32\x00]\x5e\x68', image, re.S)
    return m.start() if m else None


def assemble(driver=DRIVER):
    fix = open(os.path.join(driver, 'mmc_fix.inc')).read()
    m = re.search(r'^mmc_sig_match:.*?^        ret\n', fix, re.S | re.M)
    with tempfile.TemporaryDirectory() as t:
        src, out = os.path.join(t, 'w.asm'), os.path.join(t, 'w.bin')
        open(src, 'w').write('bits 32\n        jmp cd_patch_image\n%include "countdown.inc"\n'
                             'usbf_shell_guid:\n        dd      0xc57ad6b7\n        dw      0x0515, 0x40a8\n'
                             '        db      0x9d, 0x21, 0x55, 0x16, 0x52, 0x85, 0x4e, 0x37\n' + m.group(0))
        r = subprocess.run(['nasm', '-f', 'bin', '-I', driver + '/', '-o', out, src], capture_output=True, text=True)
        if r.returncode:
            if driver != DRIVER:
                return None
            sys.exit('nasm: ' + r.stderr)
        return open(out, 'rb').read()


def patch(code, image, size, guid=SHELL_GUID, path=True, li_size=None):
    uc = Uc(UC_ARCH_X86, UC_MODE_32)
    for b, n in ((CODE, 0x10000), (DATA, 0x1000), (STACK - 0x10000, 0x20000), (STOP, 0x1000),
                 (BASE, (len(image) + 0xfff) & ~0xfff)):
        uc.mem_map(b, n)
    uc.mem_write(CODE, code)
    uc.mem_write(BASE, image)
    bad = []
    uc.hook_add(UC_HOOK_MEM_INVALID, lambda u, a, ad, sz, v, _: bad.append(ad) or False)
    node = DATA + 0x100
    uc.mem_write(node, b'\x04\x06\x14\x00' + uuid.UUID(guid).bytes_le + b'\x7f\xff\x04\x00')
    uc.mem_write(DATA, struct.pack('<IIIIII', 0x1000, 0, 0, 0, node if path else 0, 0))
    uc.mem_write(DATA + 0x20, struct.pack('<IIQ', BASE, 0, li_size or size))
    sp = STACK - 0x100
    uc.mem_write(sp, struct.pack('<II', STOP, DATA))
    uc.reg_write(UC_X86_REG_ESP, sp)
    for r, v in SAVED:
        uc.reg_write(r, v)
    try:
        uc.emu_start(CODE, STOP, count=2_000_000)
    except UcError as e:
        return None, image, False, ['%s' % e] + bad
    ok = all(uc.reg_read(r) == v for r, v in SAVED) and uc.reg_read(UC_X86_REG_ESP) == sp + 4
    return uc.reg_read(UC_X86_REG_EAX), bytes(uc.mem_read(BASE, len(image))), ok, bad


def run_all(code, images):
    rel = os.path.join(ROOT, 'releases', 'of1-blw-26.11.30.bin')
    paths = list(images) + ([rel] if os.path.exists(rel) and rel not in images else [])
    ours = shell(rel)[0] if os.path.exists(rel) else None
    for n, path in enumerate(paths):
        image, size = shell(path)
        cd = countdown_at(image)
        print('%s (shell ImageSize %#x, countdown at %#x):' % (os.path.basename(path), size, cd or 0))
        st, after, kept, bad = patch(code, image, size)
        want = bytearray(image)
        for s in SITES:
            want[cd + s] = 0
        check(st == 1 and not bad, 'patched (status %r)' % st)
        check(kept, 'ebx, esi, edi kept and the stack balanced')
        d = [i for i in range(len(image)) if after[i] != image[i]]
        check(after == bytes(want), 'only the two operands changed, to 0: %s' % [hex(i - cd) for i in d])
        if n == 0 and ours is not None:
            oc = countdown_at(ours)
            check(after[cd:cd + 0x5e] == ours[oc:oc + 0x5e], 'the countdown function is then as in 26.11.30 (whose countdown is 0)')
    print('left alone:')
    image, size = shell(paths[0])
    cd = countdown_at(image)
    two, size2 = shell(paths[1])
    moved = bytearray(two)                          # the OF1 function where the OF2's is
    cd2 = countdown_at(two)
    moved[cd2:cd2 + 0x5e] = image[cd:cd + 0x5e]
    variants = [
        ('one byte of the function different', bytearray(image), lambda m: m.__setitem__(cd + 0x20, m[cd + 0x20] ^ 1), size, {}),
        ('the OF1 shell\'s function at the OF2\'s place', moved, None, size2, {}),
        ('another file GUID', bytearray(image), None, size, dict(guid='11111111-2222-3333-4444-555555555555')),
        ('no FilePath', bytearray(image), None, size, dict(path=False)),
        ('ImageSize too small', bytearray(image), None, size, dict(li_size=cd + 0x5d)),
    ]
    for label, m, f, sz, kw in variants:
        if f:
            f(m)
        st, after, kept, bad = patch(code, bytes(m), sz, **kw)
        check(st == 0 and after == bytes(m) and kept and not bad, '%s: status %r, %s' % (label, st, 'unchanged' if after == bytes(m) else 'CHANGED'))


def mutate(path, images):
    import contextlib, io, runpy
    muts = runpy.run_path(path)['MUTATIONS']
    survived = []
    for label, old, new in muts:
        with tempfile.TemporaryDirectory() as t:
            d = os.path.join(t, 'driver')
            shutil.copytree(DRIVER, d)
            f = os.path.join(d, 'countdown.inc')
            src = open(f).read()
            if src.count(old) != 1:
                print('PATTERN (%d matches)  %s' % (src.count(old), label))
                survived.append(label)
                continue
            open(f, 'w').write(src.replace(old, new))
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
