#!/usr/bin/env python3
"""
Run the firmware's own decompressor (AMI CORE_DXE, EDK1 Decompress at
0x10016273) under Unicorn on the shell and graphics-driver streams of one or
more images, and compare its output with the EDK reference Tiano decompressor.

Usage: emu_decompress.py <core_dxe.pe> <image.bin> [<image.bin> ...]
"""
import sys, struct
from unicorn import Uc, UC_ARCH_X86, UC_MODE_32, UcError
from unicorn.x86_const import UC_X86_REG_ESP, UC_X86_REG_EAX
from uefi_firmware import efi_compressor as ec

BASE, DECOMP, SCRATCH_SIZE = 0x10000000, 0x10016273, 0x3438
FILES = {'shell': 'c57ad6b7-0515-40a8-9d21-551652854e37', 'gop': '2b13e5f0-e82e-4209-898a-8a53e8570db9'}


def find_file(img, guid):
    import uuid
    hlen, = struct.unpack_from('<H', img, 0x10030)
    fvlen, = struct.unpack_from('<Q', img, 0x10020)
    off = 0x10000 + hlen
    while off < 0x10000 + fvlen:
        off = (off + 7) & ~7
        if img[off:off + 24] == b'\xff' * 24:
            break
        if str(uuid.UUID(bytes_le=bytes(img[off:off + 16]))) == guid:
            return off
        off += int.from_bytes(img[off + 20:off + 23], 'little')
    raise ValueError('file %s not found' % guid)


def fw_decompress(pe, src, dst_size):
    mu = Uc(UC_ARCH_X86, UC_MODE_32)
    mu.mem_map(BASE, (len(pe) + 0x10000) & ~0xfff)
    mu.mem_write(BASE, pe)
    SRC, DST, SCR, STK, RET = 0x20000000, 0x30000000, 0x40000000, 0x50000000, 0x60000000
    for a, n in ((SRC, len(src)), (DST, dst_size), (SCR, SCRATCH_SIZE), (STK, 0x10000), (RET, 0x1000)):
        mu.mem_map(a, (n + 0xfff) & ~0xfff)
    mu.mem_write(SRC, src)
    mu.mem_write(RET, b'\xf4')
    esp = STK + 0x8000
    args = [RET, SRC, len(src), DST, dst_size, SCR, SCRATCH_SIZE]
    mu.mem_write(esp, struct.pack('<7I', *args))
    mu.reg_write(UC_X86_REG_ESP, esp)
    try:
        mu.emu_start(DECOMP, RET, timeout=120 * 1000 * 1000)
    except UcError as e:
        return None, 'emulation fault: %s' % e
    status = mu.reg_read(UC_X86_REG_EAX)
    return bytes(mu.mem_read(DST, dst_size)), status


def streams(img):
    for name, guid in FILES.items():
        off = find_file(img, guid)
        body = img[off + 24:]
        ssize = int.from_bytes(body[0:3], 'little')
        ulen = int.from_bytes(body[4:8], 'little')
        yield name, bytes(body[9:ssize]), ulen


# Byte ranges (PE offsets) a patched image is allowed to change
ALLOWED = {
    'shell': [(0x1857, 0x1858), (0x187F, 0x1880), (0x235E2, 0x2404C)],
    'gop': [(0x1442, 0x1460), (0x1696, 0xDFD7)],   # $sspc01 values, splash reserve
}


def pe_start(stream):
    p = 0
    while p < len(stream):
        p = (p + 3) & ~3
        if stream[p + 3] == 0x10:
            return p + 4
        p += int.from_bytes(stream[p:p + 3], 'little')
    raise ValueError('no PE32 section')


def run(pe, path):
    img = open(path, 'rb').read()
    res = {}
    for name, src, ulen in streams(img):
        out, status = fw_decompress(pe, src, ulen)
        ref = ec.TianoDecompress(src, len(src))
        res[name] = (out, status, out == ref)
    return res


def main(core, original, images):
    pe = open(core, 'rb').read()
    base = run(pe, original)
    ok = True
    for name, (out, status, same) in base.items():
        good = status == 0 and same
        ok &= good
        print('%-32s %-5s firmware status 0x%x, matches EDK reference: %s' % (original.split('/')[-1], name, status, same))
    for path in images:
        for name, (out, status, same) in run(pe, path).items():
            if isinstance(status, str) or status != 0:
                print('%-32s %-5s firmware decompressor FAILED: %s' % (path.split('/')[-1], name, status))
                ok = False
                continue
            o = base[name][0]
            ps = pe_start(o)
            diffs = [i - ps for i in range(min(len(o), len(out))) if o[i] != out[i]]
            outside = [d for d in diffs if not any(a <= d < b for a, b in ALLOWED[name])]
            verdict = 'OK' if len(o) == len(out) and diffs and not outside else 'UNEXPECTED'
            ok &= verdict == 'OK'
            print('%-32s %-5s firmware status 0x0, %d bytes differ from original, %d outside intended ranges%s -> %s' % (
                path.split('/')[-1], name, len(diffs), len(outside),
                ' (first at PE 0x%x)' % outside[0] if outside else '', verdict))
    return ok


if __name__ == '__main__':
    if len(sys.argv) < 4:
        sys.exit('Usage: emu_decompress.py <core_dxe.pe> <original.bin> <patched.bin> [...]')
    sys.exit(0 if main(sys.argv[1], sys.argv[2], sys.argv[3:]) else 1)
