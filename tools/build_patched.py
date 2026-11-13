#!/usr/bin/env python3
"""
Build a BirdsLikeWires OpenFrame 1 EFI image from a stock (or dumped) AMI image.

Changes, each optional except the shell fix:
  1. EFI Shell (C57AD6B7...): the startup.nsh countdown default 0x32 (50 x 100 ms)
     becomes 0, at both `push 0x32` sites (PE 0x1856 and 0x187E). Just enough of
     the ALIAS help text is blanked that the Tiano-recompressed shell is exactly
     its original length, so the file keeps its size and place.
  2. IEGD graphics driver (2B13E5F0...): the splash (raw RGB after `$ssd`) is
     replaced. `$sspc01` (background, x, y, width, height) is rewritten in place
     (same string length) and `ENDSSDATA` moved to follow the new pixels. The
     rest of the splash reserve (up to the second `CEDCED`) is zeroed, as in
     Intel's own carrier images.
  3. CPU microcode (17088572..., raw file): an entry with the same signature and
     platform flags is replaced in place by a newer one of the same size.
  4. A version marker: a RAW FFS file (GUID MARKER_GUID) holding an ASCII string,
     appended after the last file in FV_MAIN. The firmware ignores RAW files.

Only FV_MAIN (0x10000-0xCFFFF) changes. NVRAM, NCB and boot block are copied from
the base image untouched. Every FFS header/data checksum is recomputed and checked.

Needs uefi_firmware (Tiano compressor) and Pillow.
"""
import argparse, struct, sys, uuid, re
from uefi_firmware import efi_compressor as ec
from PIL import Image

FV_MAIN = 0x10000
SHELL_GUID = 'c57ad6b7-0515-40a8-9d21-551652854e37'
GOP_GUID = '2b13e5f0-e82e-4209-898a-8a53e8570db9'
UCODE_GUID = '17088572-377f-44ef-8f4e-b09fff46a070'
MARKER_GUID = 'd1728e0b-d49f-480e-a48d-d0b4ec5768f4'   # BirdsLikeWires version marker
SPACER_GUID = '8e9d1954-ef62-4aa4-b8e5-88602b186161'   # BirdsLikeWires spacer: fills block 9 after the GOP
BLOCK10 = 0xA0000                 # from 26.11.29 the marker and BlwPlatformDxe start here
SHELL_PATCHES = [(0x1856, b'\x6a\x32', b'\x6a\x00'), (0x187E, b'\x6a\x32', b'\x6a\x00')]
ALIAS_HELP = (0x235E2, 0x2404C)   # ALIAS help text, UTF-16, inside the shell PE
SPLASH_OFF = 0x1696               # first pixel byte, right after `$ssd`
FFS_FIXED_CHECKSUM = 0x5A         # this firmware's value for files without a data checksum


def fail(msg):
    sys.exit('ERROR: ' + msg)


def fv_files(img):
    hlen, = struct.unpack_from('<H', img, FV_MAIN + 0x30)
    fvlen, = struct.unpack_from('<Q', img, FV_MAIN + 0x20)
    off, files = FV_MAIN + hlen, []
    while off < FV_MAIN + fvlen:
        off = (off + 7) & ~7
        if img[off:off + 24] == b'\xff' * 24:
            break
        size = int.from_bytes(img[off + 20:off + 23], 'little')
        files.append((off, str(uuid.UUID(bytes_le=bytes(img[off:off + 16]))), size))
        off += size
    return files, FV_MAIN + fvlen


def fix_checksums(img, off, size):
    """FFS1: the header sums to 0 with IntegrityCheck.File and State taken as 0.
    With FFS_ATTRIB_CHECKSUM (0x40) the data plus IntegrityCheck.File sums to 0;
    otherwise IntegrityCheck.File is the fixed value."""
    state = img[off + 23]
    img[off + 16] = img[off + 17] = img[off + 23] = 0
    img[off + 16] = (-sum(img[off:off + 24])) & 0xff
    img[off + 23] = state
    img[off + 17] = (-sum(img[off + 24:off + size])) & 0xff if img[off + 19] & 0x40 else FFS_FIXED_CHECKSUM


def check_file(img, off):
    size = int.from_bytes(img[off + 20:off + 23], 'little')
    if (sum(img[off:off + 24]) - img[off + 17] - img[off + 23]) & 0xff:
        fail('header checksum wrong at 0x%x' % off)
    if img[off + 19] & 0x40:
        if (sum(img[off + 24:off + size]) + img[off + 17]) & 0xff:
            fail('data checksum wrong at 0x%x' % off)
    elif img[off + 17] != FFS_FIXED_CHECKSUM:
        fail('fixed checksum wrong at 0x%x' % off)


def decompress_file(img, off, size):
    body = img[off + 24:off + size]
    ssize = int.from_bytes(body[0:3], 'little')
    if body[3] != 0x01 or body[8] != 1 or (ssize + 3) & ~3 < len(body) - 3:
        fail('file at 0x%x is not a single standard-compression section' % off)
    data = bytes(body[9:ssize])
    out = ec.TianoDecompress(data, len(data))
    if len(out) != int.from_bytes(body[4:8], 'little'):
        fail('decompressed length mismatch at 0x%x' % off)
    return bytearray(out), ssize


def pe_offset(stream):
    p = 0
    while p < len(stream):
        p = (p + 3) & ~3
        if stream[p + 3] == 0x10:
            return p + 4
        p += int.from_bytes(stream[p:p + 3], 'little')
    fail('no PE32 section')


def compression_section(stream):
    comp = ec.TianoCompress(bytes(stream), len(stream))
    if ec.TianoDecompress(comp, len(comp)) != bytes(stream):
        fail('Tiano round-trip failed')
    return bytearray((9 + len(comp)).to_bytes(3, 'little') + b'\x01' +
                     len(stream).to_bytes(4, 'little') + b'\x01' + comp)


def patch_shell(img, off, size):
    stream, sec_len = decompress_file(img, off, size)
    pe = pe_offset(stream)
    for va, old, new in SHELL_PATCHES:
        if stream[pe + va:pe + va + len(old)] != old:
            fail('shell bytes at 0x%x are not %s' % (va, old.hex()))
        stream[pe + va:pe + va + len(new)] = new
    a, b = ALIAS_HELP
    if not stream[pe + a:pe + a + 12].decode('utf-16le').startswith('ALIAS'):
        fail('ALIAS help text not where expected')

    def blanked(end):
        s = bytearray(stream)
        for i in range(pe + a, pe + end, 2):
            ch = s[i] | s[i + 1] << 8
            if 0x21 <= ch <= 0x7e and ch != 0x25:   # keep spaces, newlines, NULs and '%'
                s[i:i + 2] = b' \x00'
        return s
    for end in range(a + 2, b + 1, 2):
        if 9 + len(ec.TianoCompress(bytes(blanked(end)), len(stream))) == sec_len:
            break
    else:
        fail('no blanking length gives an exact-size shell')
    sec = compression_section(blanked(end))
    if len(sec) != sec_len:
        fail('shell section length changed')
    img[off + 24:off + 24 + sec_len] = sec
    fix_checksums(img, off, size)
    check_file(img, off)
    return 'shell: countdown 0, ALIAS help blanked PE 0x%x-0x%x, size unchanged' % (a, end)


def patch_gop(img, off, size, logo, pos, fv_end, next_files, allow_large=False):
    stream, _ = decompress_file(img, off, size)
    pe = pe_offset(stream)
    if stream[pe + SPLASH_OFF - 4:pe + SPLASH_OFF] != b'$ssd':
        fail('$ssd marker not found')
    ced = [m.start() for m in re.finditer(b'CEDCED', stream[pe:pe + 0x20000])]
    if len(ced) < 2:
        fail('splash reserve end (CEDCED) not found')
    reserve_end = pe + ced[1]
    m = re.search(rb'\$sspc01(0x[0-9A-F]+),(0x[0-9A-F]+),(0x[0-9A-F]+),(0x[0-9A-F]+),(0x[0-9A-F]+)', stream[pe:pe + SPLASH_OFF])
    if not m:
        fail('$sspc01 not found')
    im = Image.open(logo).convert('RGB')
    w, h = im.size
    # 26.11.1 used 140x122 (17,080 px, 68,320 bytes at 4 bpp) and hung before the
    # splash. Every splash proven on this IEGD code is <= 16,000 px (Telio 250x64).
    # Cause unconfirmed (splash size or microcode); stay inside the proven envelope.
    # 16,800 px is proven on this exact IEGD build: the Verizon/OpenPeak OF1 firmware shows 300x56.
    # (The old 16,000 limit came from blaming 26.11.1's splash; that brick was microcode 0x217 at reset.)
    if w * h > 16800 and not allow_large:
        fail('splash %dx%d = %d px exceeds the proven 16,800 px (use --allow-large-splash to override)' % (w, h, w * h))
    x, y = pos if pos else ((800 - w) // 2, (480 - h) // 2)
    old = [m.group(i).decode() for i in range(1, 6)]
    new = [old[0]] + ['0x%0*X' % (len(o) - 2, v) for o, v in zip(old[1:], (x, y, w, h))]
    if any(len(n) != len(o) for n, o in zip(new, old)):
        fail('new $sspc values %s need different digit counts from %s' % (new, old))
    stream[pe + m.start(1):pe + m.end(5)] = ','.join(new).encode()
    old_end = stream.find(b'ENDSSDATA', pe + SPLASH_OFF)
    old_px = int(old[3], 16) * int(old[4], 16) * 3
    if old_end != pe + SPLASH_OFF + old_px:
        fail('ENDSSDATA is not right after the old splash')
    rgb = im.tobytes()                              # stored R, G, B
    if pe + SPLASH_OFF + len(rgb) + 9 > reserve_end:
        fail('logo %dx%d (%d bytes) does not fit the %d-byte splash reserve' % (w, h, len(rgb), reserve_end - pe - SPLASH_OFF - 9))
    region = bytearray(reserve_end - (pe + SPLASH_OFF))
    region[0:len(rgb)] = rgb
    region[len(rgb):len(rgb) + 9] = b'ENDSSDATA'
    stream[pe + SPLASH_OFF:reserve_end] = region
    sec = compression_section(stream)
    new_size = 24 + len(sec)
    if next_files:
        fail('graphics driver must be the last file in FV_MAIN')
    hdr = img[off:off + 24]
    img[off:fv_end] = b'\xff' * (fv_end - off)
    img[off:off + 24] = hdr
    img[off + 20:off + 23] = new_size.to_bytes(3, 'little')
    img[off + 24:off + new_size] = sec
    fix_checksums(img, off, new_size)
    check_file(img, off)
    return 'graphics: splash %dx%d at %d,%d ($sspc01 %s -> %s), file 0x%x -> 0x%x' % (
        w, h, x, y, ','.join(old), ','.join(new), size, new_size)


def patch_ucode(img, off, size, ucode_path):
    body = img[off + 24:off + size]
    blobs = open(ucode_path, 'rb').read()
    p = 0
    hv, rev, date, sig, ck, ldr, pf, dsz, tsz = struct.unpack_from('<9I', body, 0)
    if hv != 1:
        fail('microcode file does not start with a microcode header')
    tsz = tsz or 2048
    while p + 48 <= len(blobs):
        h = struct.unpack_from('<9I', blobs, p)
        n = h[8] or 2048
        if h[0] == 1 and h[3] == sig and h[6] == pf:
            new = blobs[p:p + n]
            if n != tsz:
                fail('replacement microcode is %d bytes, slot is %d' % (n, tsz))
            if sum(struct.unpack('<%dI' % (n // 4), new)) & 0xffffffff:
                fail('replacement microcode checksum is bad')
            if h[1] <= rev:
                fail('replacement microcode rev 0x%x is not newer than 0x%x' % (h[1], rev))
            img[off + 24:off + 24 + n] = new
            fix_checksums(img, off, size)
            check_file(img, off)
            return 'microcode: sig 0x%x pf 0x%x rev 0x%x (%08x) -> 0x%x (%08x)' % (sig, pf, rev, date, h[1], h[2])
        p += n
    fail('no microcode for sig 0x%x pf 0x%x in %s' % (sig, pf, ucode_path))


def add_marker(img, after, fv_end, text):
    off = (after + 7) & ~7
    data = text.encode('ascii') + b'\0'
    size = 24 + len(data)
    if off + size > fv_end:
        fail('no room for the version marker')
    if set(img[off:off + size]) != {0xff}:
        fail('marker space is not free')
    hdr = bytearray(uuid.UUID(MARKER_GUID).bytes_le + b'\0\0' + bytes([0x01, 0x00]) +
                    size.to_bytes(3, 'little') + b'\xf8')   # RAW file, state = valid (erase polarity 1)
    img[off:off + 24] = hdr
    img[off + 24:off + size] = data
    fix_checksums(img, off, size)
    check_file(img, off)
    return 'marker: %r at 0x%x' % (text, off)


def add_spacer(img, after, end):
    """A RAW file (the same kind as the version marker, which the firmware is proven to accept)
    whose data is all FF, from the first 8-byte boundary after `after` up to `end`, so that the
    next file starts exactly at `end`. From 26.11.29 it fills block 9 after the GOP, putting the
    marker and BlwPlatformDxe in block 10, which is then the only block an update writes."""
    off = (after + 7) & ~7
    size = end - off
    if size < 24 or size >= 1 << 24 or end & 7:
        fail('no room for a spacer from 0x%x to 0x%x' % (off, end))
    if set(img[off:end]) != {0xff}:
        fail('spacer space 0x%x-0x%x is not free' % (off, end))
    hdr = bytearray(uuid.UUID(SPACER_GUID).bytes_le + b'\0\0' + bytes([0x01, 0x00]) +
                    size.to_bytes(3, 'little') + b'\xf8')   # RAW file, state = valid (erase polarity 1)
    img[off:off + 24] = hdr
    fix_checksums(img, off, size)
    check_file(img, off)
    return 'spacer: 0x%x-0x%x (%d bytes, RAW, FF data)' % (off, end, size)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('base', help='1 MB stock image or chip dump')
    ap.add_argument('out')
    ap.add_argument('--logo', help='splash image (PNG, any size that fits the reserve)')
    ap.add_argument('--pos', help='splash x,y (default: centred on 800x480)')
    ap.add_argument('--microcode', help='Intel microcode file containing a newer entry for this CPU')
    ap.add_argument('--version', help='version marker text')
    ap.add_argument('--allow-large-splash', action='store_true', help='allow a splash over 16,800 px, the largest proven on this IEGD build (Verizon OF1, 300x56)')
    a = ap.parse_args()

    orig = open(a.base, 'rb').read()
    img = bytearray(orig)
    if len(img) != 0x100000:
        fail('base is not 1 MB')
    files, fv_end = fv_files(img)
    by_guid = {g: (o, s) for o, g, s in files}
    for g in (SHELL_GUID, GOP_GUID, UCODE_GUID):
        if g not in by_guid:
            fail('base has no file %s' % g)
    if MARKER_GUID in by_guid:
        fail('base already has a version marker')
    for o, g, s in files:
        check_file(img, o)

    notes = [patch_shell(img, *by_guid[SHELL_GUID])]
    if a.microcode:
        notes.append(patch_ucode(img, *by_guid[UCODE_GUID], a.microcode))
    g_off, g_size = by_guid[GOP_GUID]
    if a.logo:
        pos = tuple(int(v) for v in a.pos.split(',')) if a.pos else None
        later = [f for f in files if f[0] > g_off]
        notes.append(patch_gop(img, g_off, g_size, a.logo, pos, fv_end, later, a.allow_large_splash))
    if a.version:
        new_files, _ = fv_files(img)
        last_off, _, last_size = new_files[-1]
        notes.append(add_marker(img, last_off + last_size, fv_end, a.version))

    # Re-walk the volume and check everything
    new_files, _ = fv_files(img)
    for o, g, s in new_files:
        check_file(img, o)
    last_off, _, last_size = new_files[-1]
    if set(img[last_off + last_size:fv_end]) - {0xff}:
        fail('free space after the last file is not erased')
    changed_guids = {SHELL_GUID, GOP_GUID, UCODE_GUID}
    for o, g, s in files:
        if g not in changed_guids and img[o:o + s] != orig[o:o + s]:
            fail('file %s changed unexpectedly' % g)
    if img[:FV_MAIN + 0x48] != orig[:FV_MAIN + 0x48] or img[fv_end:] != orig[fv_end:]:
        fail('bytes outside FV_MAIN files changed')

    open(a.out, 'wb').write(img)
    for n in notes:
        print(n)
    changed = sorted({i // 0x10000 for i in range(len(img)) if img[i] != orig[i]})
    print('wrote %s; %d files; 64K blocks changed vs base: %s' % (a.out, len(new_files), ' '.join(map(str, changed))))


if __name__ == '__main__':
    main()
