#!/usr/bin/env python3
"""Writes driver/splash_sig.inc: how splash.inc recognises, in memory, the display drivers whose
boot splash it can replace, and where it writes.

    venv/bin/python -I tools/splash_sig.py of1_efi_o2.img of2_efi_cisco.img driver/splash_sig.inc \\
        [--check IMAGE ...]

of1_efi_o2.img (md5 eeb32d85e8b8c63ccc00429e083a88b8) and of2_efi_cisco.img (md5
07a8fb52aedbf967cbcb1f2acc837d87) are the stock images from https://openbeak.net/openframe/firmware/efi/.

The splash is a raw RGB bitmap inside Intel's IEGD display driver (GOP, FFS file 2B13E5F0-E82E-
4209-898A-8A53E8570DB9), in its configuration text: `$sspc01` gives the background, x, y, width
and height as five hex numbers, and `$ssd` is followed by the pixels and `ENDSSDATA`, all inside
the CED area that ends with the second `CEDCED`. The driver's entry only installs its driver
binding; the text is parsed in its Start (the `ssp` and `ssd` token handlers, through the token
table), when the console connects the screen, after every DXE driver has run. So BlwPlatformDxe
can rewrite the numbers and the pixels in the driver's loaded image beforehand, and an OpenFrame
whose display driver is in blocks no update writes (an OpenFrame 2's) still shows our splash.

Every OpenFrame display driver we know is the same IEGD build with one of two panel
configurations: `OpenFrame-Sharp` (33.264 MHz; every OF1, and the Telio, Avaya and Swisscom Da
Vinci OF2s) or `OpenFrame-Samsung` (24.5 MHz; the Cisco OF2), whose label is two characters
longer, so its text after the label sits two bytes later. For each, the signature is all of the
configuration text from its start (RVA 0x13d7) to the first pixel except the 11 hex digits of
x, y, width and height, which are what splash.inc writes, plus the reserve's closing `CEDCED` and
the token table's `ssp` and `ssd` names. The code signature is the two handlers and their
strings, less relocated dwords (the same in both). Checked here: the code is the same in both
inputs; nothing in the configuration or the reserve is relocated; the old splash ends exactly at
`ENDSSDATA` with only zeros after it to the reserve's end; the digits are three, two, two and two.
"""
import argparse
import re
import struct
import sys

import pefile

sys.path.insert(0, __file__.rsplit('/', 1)[0])
import build_patched as bp  # noqa: E402

GOP_GUID = '2b13e5f0-e82e-4209-898a-8a53e8570db9'
CFG = 0x13d7                    # the configuration text, as the handlers reference it
CODE = 0x15240                  # the code signature's base: the handlers' strings at 0x1524c-0x15262
SSD_FN, SSD_END = 0x18786, 0x18800      # the `ssd` handler (copies the pixels up to ENDSSDATA)
SSP_FN, SSP_END = 0x1960c, 0x197f2      # the `ssp` handler (parses the five numbers)
STRINGS = (0x1524c, 0x15263)            # '$', 'ENDSSDATA', ','
TOKENS = (0xe008, 0xe018)               # the token table's `ssp` and `ssd` entries
VALUE = re.compile(rb'\$sspc01(0x0,0x)([0-9A-F]{3})(,0x)([0-9A-F]{2})(,0x)([0-9A-F]{2})(,0x)([0-9A-F]{2})\$halc01')
LABELS = (b'OpenFrame-Sharp', b'OpenFrame-Samsung')


def gop_image(path):
    """The display driver's PE image as loaded (at its preferred base, 0) and its base relocations."""
    img = bytearray(open(path, 'rb').read())
    files, _ = bp.fv_files(img)
    found = [(o, s) for o, g, s in files if g == GOP_GUID]
    if len(found) != 1:
        bp.fail('%s: %d display drivers' % (path, len(found)))
    stream, _ = bp.decompress_file(img, *found[0])
    pe = pefile.PE(data=bytes(stream[bp.pe_offset(stream):]))
    relocs = sorted(e.rva for b in pe.DIRECTORY_ENTRY_BASERELOC for e in b.entries if e.type == 3)
    return pe.get_memory_mapped_image(), relocs, pe.OPTIONAL_HEADER.SizeOfImage


def runs_of(mem, base, ranges, relocs):
    """(offset from base, bytes) runs covering ranges, less every relocated dword."""
    masked = {r + k for r in relocs for k in range(4)}
    out = []
    for a, b in ranges:
        i = a
        while i < b:
            if i in masked:
                i += 1
                continue
            j = i
            while j < b and j not in masked:
                j += 1
            out.append((i - base, mem[i:j]))
            i = j
    return out


def analyse(path):
    mem, relocs, size = gop_image(path)
    label = next((x for x in LABELS if mem.find(b'$ccnc01,' + x + b'$', CFG, CFG + 0x80) >= 0), None)
    if label is None:
        bp.fail('%s: not a configuration we know' % path)
    if mem[CFG:CFG + 17] != b'^%0x700$01$chpc01':
        bp.fail('%s: configuration not at 0x%x' % (path, CFG))
    m = VALUE.search(mem, CFG, CFG + 0x100)
    if not m:
        bp.fail('%s: $sspc01 is not 0x0 and 3, 2, 2, 2 hex digits' % path)
    value = m.start(1)
    ssd = mem.find(b'$ssd', value)
    px = ssd + 4
    ced = [x.start() for x in re.finditer(b'CEDCED', mem[:0x20000])]
    if len(ced) < 2:
        bp.fail('%s: no reserve end' % path)
    end = ced[1]
    if end - CFG != 0xcc00:
        bp.fail('%s: reserve end 0x%x is not 0xcc00 after the configuration (the handlers\' limit)' % (path, end))
    x, y, w, h = (int(m.group(i), 16) for i in (2, 4, 6, 8))
    old_end = px + w * h * 3
    if mem[old_end:old_end + 9] != b'ENDSSDATA' or set(mem[old_end + 9:end]) - {0}:
        bp.fail('%s: old splash not followed by ENDSSDATA and zeros' % path)
    bad = [r for r in relocs if CFG - 3 <= r < end + 6]
    if bad:
        bp.fail('%s: relocated dwords in the configuration: %s' % (path, [hex(r) for r in bad]))
    if mem[0x15254:0x1525d] != b'ENDSSDATA':
        bp.fail('%s: ENDSSDATA string not at 0x15254' % path)
    cfg_runs = runs_of(mem, CFG, [(CFG, value + 6), (value + 9, value + 12), (value + 14, value + 17),
                                  (value + 19, value + 22), (value + 24, px), (end, end + 6), TOKENS], relocs)
    code_runs = runs_of(mem, CODE, [STRINGS, (SSD_FN, SSD_END), (SSP_FN, SSP_END)], relocs)
    for o, d in cfg_runs + code_runs:
        if not 0 <= o < 0x10000 or len(d) >= 0x10000:
            bp.fail('run out of 16-bit range')
    return dict(path=path, label=label.decode(), value=value, px=px, end=end, size=size, cfg=cfg_runs,
                code=code_runs, old=(x, y, w, h), mem=mem)


def emit_runs(f, runs):
    for o, d in runs:
        f.write('        dw      0x%04x, 0x%04x\n' % (o, len(d)))
        for i in range(0, len(d), 16):
            f.write('        db      ' + ', '.join('0x%02x' % b for b in d[i:i + 16]) + '\n')
    f.write('        dw      0, 0\n')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('sharp', help='stock image with the OpenFrame-Sharp display driver (of1_efi_o2.img)')
    ap.add_argument('samsung', help='stock image with the OpenFrame-Samsung display driver (of2_efi_cisco.img)')
    ap.add_argument('out')
    ap.add_argument('--check', nargs='*', default=[], help='more images: each must match one of the two')
    a = ap.parse_args()
    v = [analyse(a.sharp), analyse(a.samsung)]
    if [x['label'] for x in v] != ['OpenFrame-Sharp', 'OpenFrame-Samsung']:
        bp.fail('inputs are %s, not Sharp then Samsung' % [x['label'] for x in v])
    if v[0]['code'] != v[1]['code'] or v[0]['end'] != v[1]['end']:
        bp.fail('the two display drivers differ in their code or reserve')
    for path in a.check:
        c = analyse(path)
        match = [x['label'] for x in v if x['cfg'] == c['cfg'] and x['code'] == c['code'] and x['value'] == c['value']]
        print('%s: %s, splash %dx%d at %d,%d' % (path.split('/')[-1], match[0] if match else 'NO MATCH', c['old'][2], c['old'][3], *c['old'][:2]))
        if not match:
            sys.exit(1)
    with open(a.out, 'w') as f:
        f.write('; Generated by tools/splash_sig.py from %s and %s: do not edit.\n'
                % (a.sharp.split('/')[-1], a.samsung.split('/')[-1]))
        f.write('; The IEGD display driver (2B13E5F0-E82E-4209-898A-8A53E8570DB9): its splash configuration and\n'
                '; the code that parses it, in mmc_sig_match runs. Offsets are from GSP_CFG_RVA or GSP_CODE_RVA.\n')
        f.write('GSP_CFG_RVA     equ     0x%x\n' % CFG)
        f.write('GSP_END_RVA     equ     0x%x                  ; the reserve ends here (its second CEDCED)\n' % v[0]['end'])
        f.write('GSP_CODE_RVA    equ     0x%x\n' % CODE)
        f.write('GSP_MIN_SIZE    equ     0x%x                 ; ImageSize at least this\n' % max(SSP_END, v[0]['end'] + 6))
        f.write('GSP_PX_MAX      equ     0x%x                   ; the later first pixel, from GSP_CFG_RVA\n'
                % max(x['px'] - CFG for x in v))
        f.write('gsp_code_sig:\n')
        emit_runs(f, v[0]['code'])
        f.write('; Each configuration: dw its length in bytes (0 ends the list), the five numbers\' offset, the\n'
                '; first pixel\'s offset, 0; then its runs.\n')
        f.write('gsp_cfgs:\n')
        for x in v:
            n = 8 + sum(4 + len(d) for _, d in x['cfg']) + 4
            f.write('; %s: numbers at 0x%x, pixels from 0x%x (stock splash %dx%d at %d,%d)\n'
                    % (x['label'], x['value'], x['px'], x['old'][2], x['old'][3], *x['old'][:2]))
            f.write('        dw      0x%04x, 0x%04x, 0x%04x, 0\n' % (n, x['value'] - CFG, x['px'] - CFG))
            emit_runs(f, x['cfg'])
        f.write('        dw      0\n')
    for x in v:
        print('%s: numbers at 0x%x, pixels 0x%x-0x%x (%d bytes), %d configuration bytes checked'
              % (x['label'], x['value'], x['px'], x['end'], x['end'] - x['px'], sum(len(d) for _, d in x['cfg'])))
    print('code: %d bytes checked' % sum(len(d) for _, d in v[0]['code']))


if __name__ == '__main__':
    main()
