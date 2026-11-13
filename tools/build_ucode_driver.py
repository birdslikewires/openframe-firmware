#!/usr/bin/env python3
"""
Build the BlwPlatformDxe DXE driver (driver/ucode_late.asm; BlwUcodeLate until 26.11.16) and append
it to FV_MAIN.

  build_ucode_driver.py <base.bin> <out.bin> --microcode intel-ucode-06-1c-02 [--pe out.efi]

1. Picks the microcode entry for the base image's own CPU signature and platform flags
   (taken from the base's microcode file) that is newer than the base's entry.
2. Assembles the driver with nasm (-f bin), embedding that entry 16-byte aligned.
3. Wraps it in a minimal PE32 (IA32, EFI boot service driver, one section at RVA 0x200 marked
   code/read/execute (0x60000020): the driver also writes its own flags there, which this 2008
   firmware allows (it sets no page protection),
   image base 0x10000000, a one-entry no-op base relocation block).
4. Wraps that in an FFS file packaged like the stock DXE drivers: type DRIVER, a DXE_DEPEX section
   (PUSH MP Services GUID, END), then a Tiano COMPRESSION section holding PE32 and UI "BlwPlatformDxe",
   data checksum attribute. (--raw-sections gives the uncompressed packaging of v10/v11, which the
   firmware did not dispatch.) Appended after the last file in
   FV_MAIN, 8-byte aligned, all checksums set and re-checked.
The base's reset-vector loader and microcode file are left alone.
"""
import argparse, os, re, struct, subprocess, sys, tempfile, uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_patched as bp

DRIVER_GUID = 'a5d5b0c1-7d2f-4c3e-9b1a-26b1e0f17217'   # BlwPlatformDxe (was BlwUcodeLate)
MP_GUID = 'f33261e7-23cb-11d5-bd5c-0080c73c8881'
ASM = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'driver', 'ucode_late.asm')
UCODE_FILE_OFF = 0x10048
BOOTCOUNT_PREFIX = 'BootCount: '
BOOTCOUNT_PLACEHOLDER = BOOTCOUNT_PREFIX + '-' * 10      # 10 digits: any 32-bit count
MEMSIZE_UNKNOWN = 0xFFFF                                 # SMBIOS: size unknown
SERIAL_PLACEHOLDER = '0' * 12                            # the card MAC as 12 hex digits


def smbios_blobs(bios_version, bios_date, release, system):
    """SMBIOS 2.4: a 31-byte entry point (+1 pad) with the table address and both checksums left 0
    for the driver to fill in, and the structure table: Type 0 BIOS, Type 1 System, Type 3 Chassis,
    Type 16/17 memory, Type 11 OEM Strings (boot count placeholder), Type 127 End. Also returns the offsets of the
    placeholder's digits in the table."""
    def strings(*ss):
        ss = [x for x in ss if x]
        return (b''.join(x.encode('ascii') + b'\0' for x in ss) or b'\0') + b'\0'
    def idx(ss, x):
        return [y for y in ss if y].index(x) + 1 if x else 0
    vendor = 'BirdsLikeWires'
    s0 = [vendor, bios_version, bios_date]
    t0 = struct.pack('<BBHBBHBBQBBBBBB', 0, 0x18, 0x0000, 1, 2, 0x0000, 3, 0x0F,   # 1 MB ROM: 64K*(15+1)
                     (1 << 7) | (1 << 11),          # PCI supported, BIOS upgradeable (flash)
                     0x01,                           # ext 1: ACPI supported
                     0x08,                           # ext 2: UEFI supported
                     release[0], release[1], 0xFF, 0xFF) + strings(*s0)
    man, prod, ver, fam = system
    s1 = [man, prod, ver, fam]
    # Serial number and UUID come from the WLAN card's MAC, which the driver fills in at
    # ReadyToBoot (smbios_id.inc): until then the serial number field is 0 (none), the UUID all
    # FF (not present), and the 12 digits go over the placeholder that ends the string set.
    assert all(len(x) != len(SERIAL_PLACEHOLDER) for x in s1)
    t1 = struct.pack('<BBHBBBB16sBBB', 1, 0x1B, 0x0001, idx(s1, man), idx(s1, prod), idx(s1, ver), 0,
                     b'\xff' * 16,                   # UUID not present (yet)
                     0x08,                          # wake-up: AC power restored (no power switch; starts on DC)
                     0, idx(s1, fam)) + strings(*s1, SERIAL_PLACEHOLDER)
    # Type 3 Chassis (2.3 layout, no contained elements): type 0x22 Embedded PC (defined from SMBIOS
    # 3.0; readers only take the byte), states safe, security none. systemd-hostnamed maps it to
    # "embedded".
    t3 = struct.pack('<BBHBBBBBBBBBIBBBB', 3, 0x15, 0x0002, 1, 0x22, 0, 0, 0, 0x03, 0x03, 0x03, 0x03,
                     0, 0, 1, 0, 0) + strings(man)   # OEM 0, height unspecified, 1 power cord (DC jack)
    # Type 11 OEM Strings: the boot count, a fixed-width placeholder the driver fills in at
    # ReadyToBoot from the MonotonicCounter variable (so the table length never changes).
    # Type 16 Physical Memory Array and Type 17 Memory Device: the soldered DDR2. The SCH has one
    # 64-bit channel of x16 devices, one or two ranks, at most 2 GB (datasheet 319537 §8.2, §5.3);
    # no ECC. Type 17's size and speed are filled in by the driver at dispatch (mem_fill): size
    # from the top of RAM in the memory map, speed from the FSB (the DRAM runs at the FSB rate).
    # Until then: size 0xFFFF (unknown), speed 0 (unknown).
    t16 = struct.pack('<BBHBBBIHH', 16, 0x0F, 0x0003, 0x03, 0x03, 0x03,   # system board, system memory, no ECC
                      2 * 1024 * 1024,                # maximum capacity, KB: 2 GB
                      0xFFFE, 1) + b'\0\0'           # no error information; one device
    t17 = struct.pack('<BBHHHHHHBBBBBHHBBBB', 17, 0x1B, 0x0004, 0x0003, 0xFFFE,
                      64, 64,                         # total and data width: 64-bit, no ECC
                      MEMSIZE_UNKNOWN,                # size (MB): filled in
                      0x0B, 0,                        # form factor: row of chips (soldered); no device set
                      1, 0,                           # device locator "Onboard"; no bank locator
                      0x13, 0x0080,                   # DDR2; synchronous
                      0,                              # speed (MT/s): filled in
                      0, 0, 0, 0) + strings('Onboard')    # manufacturer, serial, asset tag, part: none
    t11 = struct.pack('<BBHB', 11, 5, 0x0005, 1) + strings(BOOTCOUNT_PLACEHOLDER)
    t127 = struct.pack('<BBH', 127, 4, 0x0006) + b'\0\0'
    structs = [t0, t1, t3, t16, t17, t11, t127]
    table = b''.join(structs)
    maxsz = max(len(t) for t in structs)
    eps = bytearray(b'_SM_' + bytes([0, 0x1F, 2, 4]) + struct.pack('<HB', maxsz, 0) + b'\0' * 5 +
                    b'_DMI_' + b'\0' + struct.pack('<HIHB', len(table), 0, len(structs), 0x24))
    assert len(eps) == 31
    digits = table.index(BOOTCOUNT_PLACEHOLDER.encode()) + len(BOOTCOUNT_PREFIX)
    t17_off = len(b''.join(structs[:structs.index(t17)]))
    assert table[t17_off] == 17 and table[t17_off + 1] == 0x1B
    assert table.count(BOOTCOUNT_PLACEHOLDER.encode()) == 1
    return bytes(eps) + b'\0', table, dict(count=digits, memsize=t17_off + 12, memspeed=t17_off + 21)


def pick_ucode(img, path):
    first = UCODE_FILE_OFF + 24
    hv, rev, _, sig, _, _, pf = struct.unpack_from('<7I', img, first)
    blobs = open(path, 'rb').read()
    o = 0
    while o + 48 <= len(blobs):
        hv2, rev2, _, sig2, _, _, pf2, dsz, tsz = struct.unpack_from('<9I', blobs, o)
        n = tsz if dsz else 2048
        if hv2 == 1 and sig2 == sig and pf2 & pf:
            e = blobs[o:o + n]
            if rev2 <= rev:
                bp.fail('microcode 0x%x is not newer than the base entry 0x%x' % (rev2, rev))
            if sum(struct.unpack('<%dI' % (n // 4), e)) & 0xffffffff:
                bp.fail('microcode checksum bad')
            return e, sig, pf, rev, rev2
        o += n
    bp.fail('no microcode for sig 0x%x pf 0x%x' % (sig, pf))


def pe32(code, entry=0, subsystem=11):
    SECT_RVA = 0x200
    reloc = struct.pack('<IIHH', 0, 12, 0, 0)          # one block, one ABSOLUTE (no-op) entry
    # Laid out like the AMI-built drivers in this firmware (CpuDxe, MicrocodeUpdate, the GOP):
    # FILE_DLL set, 0x20 section/file alignment, .text (RX) then a separate .reloc section.
    text = bytearray(code)
    text += b'\0' * ((-len(text)) % 0x20)
    text_vsize = len(code)
    reloc_rva = SECT_RVA + len(text)
    rel = bytearray(reloc) + b'\0' * ((-len(reloc)) % 0x20)
    size_img = reloc_rva + len(rel)
    h = bytearray(SECT_RVA)
    h[0:2] = b'MZ'
    struct.pack_into('<I', h, 0x3c, 0x40)
    h[0x40:0x44] = b'PE\0\0'
    chars = 0x2000 | 0x0100 | 0x0008 | 0x0004 | 0x0002    # DLL, 32BIT, syms/lines stripped, EXECUTABLE
    struct.pack_into('<HHIIIHH', h, 0x44, 0x14c, 2, 0, 0, 0, 0xE0, chars)
    o = 0x58
    opt = struct.pack('<HBBIIIIIIIIIHHHHHHIIIIHHIIIIII',
                      0x10b, 0, 0, len(text), len(rel), 0, SECT_RVA + entry, SECT_RVA, reloc_rva,
                      0x10000000, 0x20, 0x20, 0, 0, 0, 0, 0, 0, 0, size_img, SECT_RVA, 0,
                      subsystem, 0, 0, 0, 0, 0, 0, 16)
    h[o:o + len(opt)] = opt
    dirs = [(0, 0)] * 16
    dirs[5] = (reloc_rva, len(reloc))
    o2 = o + len(opt)
    for i, (r, s) in enumerate(dirs):
        struct.pack_into('<II', h, o2 + 8 * i, r, s)
    sh = o + 0xE0
    struct.pack_into('<8sIIIIIIHHI', h, sh, b'.text', text_vsize, SECT_RVA, len(text), SECT_RVA,
                     0, 0, 0, 0, 0x60000020)
    struct.pack_into('<8sIIIIIIHHI', h, sh + 40, b'.reloc', len(reloc), reloc_rva, len(rel), reloc_rva,
                     0, 0, 0, 0, 0x42000040)
    return bytes(h) + bytes(text) + bytes(rel)


def section(stype, data, pad=True):
    """An FFS section, padded to 4 bytes unless it is the last one in a file. The AMI core's
    section-stream check wants the file data to end exactly at the last section's end: with 2 bytes
    of trailing pad, FV ReadSection returned EFI_INVALID_PARAMETER and the dispatcher skipped the
    driver (v10-v12). Stock files here never have trailing pad."""
    s = (4 + len(data)).to_bytes(3, 'little') + bytes([stype]) + data
    return s + b'\0' * ((-len(s)) % 4) if pad else s


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('base')
    ap.add_argument('out')
    ap.add_argument('--microcode', required=True)
    ap.add_argument('--pe', help='also write the PE32 image here')
    ap.add_argument('--raw-sections', action='store_true', help='PE32 and UI uncompressed (v10/v11 packaging)')
    ap.add_argument('--check-with-core', metavar='CORE_DXE_PE', help='verify the compressed stream with the firmware decompressor')
    ap.add_argument('--previous', help='the previous release: also report (and with --expect-blocks, enforce) the 64K blocks that differ from it')
    ap.add_argument('--expect-blocks', help='fail unless exactly these 64K blocks change, e.g. 10 or 9,10 (against --previous if given, else the base)')
    ap.add_argument('--bios-version', required=True, help='SMBIOS BIOS version, e.g. 26.11.16; a development build 26.11.26-dev1')
    ap.add_argument('--bios-date', required=True, help='SMBIOS BIOS release date, MM/DD/YYYY')
    ap.add_argument('--smbios-out', help='also write the SMBIOS entry point + table here (address 0)')
    ap.add_argument('--app', help='also write an EFI application build (subsystem 10) here, for running from the shell')
    ap.add_argument('--define', action='append', default=[], metavar='NAME',
                    help='assemble with -DNAME as well (e.g. TIMING for the boot-timing development build)')
    a = ap.parse_args()

    orig = open(a.base, 'rb').read()
    img = bytearray(orig)
    files, fv_end = bp.fv_files(img)
    for o, g, s in files:
        bp.check_file(img, o)
    if any(g == DRIVER_GUID for _, g, _ in files):
        bp.fail('base already has the driver')
    entry, sig, pf, rev_old, rev_new = pick_ucode(img, a.microcode)
    m = re.fullmatch(r'(\d+)\.(\d+)\.\d+(-dev\d+)?', a.bios_version)     # (-devN: a development build)
    if not m or not re.fullmatch(r'\d\d/\d\d/\d{4}', a.bios_date):
        bp.fail('--bios-version must be YY.M.N (or YY.M.N-devN) and --bios-date MM/DD/YYYY')
    release = (int(m.group(1)), int(m.group(2)))      # SMBIOS BIOS major/minor release

    with tempfile.TemporaryDirectory() as td:
        uc = os.path.join(td, 'ucode.bin')
        open(uc, 'wb').write(entry)
        out = os.path.join(td, 'drv.bin')
        eps, table, offs = smbios_blobs(a.bios_version, a.bios_date, release,
                                  ('OpenPeak', 'OpenFrame 1', 'OF1', 'OpenFrame'))
        ef, tf = os.path.join(td, 'eps.bin'), os.path.join(td, 'smbios.bin')
        open(ef, 'wb').write(eps)
        open(tf, 'wb').write(table)
        if a.smbios_out:
            open(a.smbios_out, 'wb').write(eps + table)
        def assemble(*defs):
            r = subprocess.run(['nasm', '-f', 'bin', '-I', os.path.dirname(ASM) + '/', '-DUCODE_FILE="%s"' % uc,
                                '-DSMBIOS_EPS_FILE="%s"' % ef, '-DSMBIOS_FILE="%s"' % tf,
                                '-DSMBIOS_LEN=%d' % len(table), '-DSMBIOS_COUNT_OFF=%d' % offs['count'],
                                '-DSMBIOS_MEMSIZE_OFF=%d' % offs['memsize'], '-DSMBIOS_MEMSPEED_OFF=%d' % offs['memspeed']] + list(defs) + ['-o', out, ASM],
                               capture_output=True, text=True)
            if r.returncode:
                bp.fail('nasm: ' + r.stderr)
            c = open(out, 'rb').read()
            if c[-len(entry):] != entry or (len(c) - len(entry)) % 16:
                bp.fail('microcode not at the end of the code, 16-byte aligned')
            return c
        extra = ['-D' + d for d in a.define]
        code = assemble(*extra)
        # The app build applies the LAN MAC at once and registers no ReadyToBoot event: an
        # application's image is freed when it returns, so an event would call freed memory.
        app_code = assemble('-DAPP', *extra) if a.app else None
    pe = pe32(code)
    if a.pe:
        open(a.pe, 'wb').write(pe)
    if a.app:
        open(a.app, 'wb').write(pe32(app_code, subsystem=10))

    depex = b'\x02' + uuid.UUID(MP_GUID).bytes_le + b'\x08'
    ui = 'BlwPlatformDxe'.encode('utf-16le') + b'\0\0'
    inner = section(0x10, pe) + section(0x15, ui)
    if a.raw_sections:
        data = section(0x13, depex) + section(0x10, pe) + section(0x15, ui, pad=False)
    else:                                           # like every stock DXE driver here
        comp = bytes(bp.compression_section(inner))
        data = section(0x13, depex) + comp
        if a.check_with_core:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import emu_decompress as emu
            core = open(a.check_with_core, 'rb').read()
            out, st = emu.fw_decompress(core, comp[9:], len(inner))
            if st != 0 or out is None or bytes(out) != inner:
                bp.fail('firmware decompressor did not reproduce the driver sections (status %r)' % (st,))
            print('firmware CORE_DXE decompressor (Unicorn) reproduces the %d-byte section stream' % len(inner))
    i = 0
    while True:                                     # sections must tile the data exactly
        ln = int.from_bytes(data[i:i + 3], 'little')
        if ln < 4 or i + ln > len(data):
            bp.fail('bad section at %d' % i)
        if i + ln == len(data):
            break
        i = (i + ln + 3) & ~3
    last_off, _, last_size = files[-1]
    off = (last_off + last_size + 7) & ~7
    size = 24 + len(data)
    if off + size > fv_end or set(img[off:off + size]) != {0xff}:
        bp.fail('no free space for the driver')
    hdr = bytearray(uuid.UUID(DRIVER_GUID).bytes_le + b'\0\0' + bytes([0x07, 0x40]) +
                    size.to_bytes(3, 'little') + b'\xf8')
    img[off:off + 24] = hdr
    img[off + 24:off + size] = data
    bp.fix_checksums(img, off, size)

    new_files, _ = bp.fv_files(img)
    for o, g, s in new_files:
        bp.check_file(img, o)
    if len(new_files) != len(files) + 1 or new_files[-1][0] != off:
        bp.fail('driver file not found as the new last file')
    changed = [i for i in range(len(img)) if img[i] != orig[i]]
    if min(changed) < off or max(changed) >= off + size:
        bp.fail('bytes outside the new file changed')
    ref = open(a.previous, 'rb').read() if a.previous else orig
    vs = sorted({i // 0x10000 for i in range(len(img)) if img[i] != ref[i]})
    if a.expect_blocks is not None and vs != sorted(int(x) for x in a.expect_blocks.split(',')):
        bp.fail('64K blocks changed %s, expected %s: nothing written' % (vs, a.expect_blocks))
    open(a.out, 'wb').write(img)
    print('microcode: sig 0x%x pf 0x%x, firmware keeps 0x%x at reset, driver loads 0x%x late'
          % (sig, pf, rev_old, rev_new))
    print('driver: code %d bytes, PE32 %d bytes, FFS file %d bytes at 0x%x (GUID %s)'
          % (len(code) - len(entry), len(pe), size, off, DRIVER_GUID))
    print('wrote %s; 64K blocks changed: %s%s' % (a.out, sorted({i // 0x10000 for i in changed}),
          '; against the previous release: %s' % vs if a.previous else ''))


if __name__ == '__main__':
    main()
