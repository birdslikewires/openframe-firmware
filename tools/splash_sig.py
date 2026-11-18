#!/usr/bin/env python3
"""Writes driver/splash_sig.inc: how splash.inc recognises, in memory, the display drivers whose
boot splash it can replace, and where it writes.

    venv/bin/python -I tools/splash_sig.py of1_efi_o2.img of2_efi_cisco.img driver/splash_sig.inc \\
        --more of2_openpeak.img of1_efi_openpeak_verizon.img of1_efi_telstra.img [--check IMAGE ...]

of1_efi_o2.img (md5 eeb32d85e8b8c63ccc00429e083a88b8), of2_efi_cisco.img (md5
07a8fb52aedbf967cbcb1f2acc837d87), of2_openpeak.img (md5 c35f7e01052a3d2323610d7688244e9b),
of1_efi_openpeak_verizon.img (md5 dbfeeb9e7e943ee0c46986995d4b383c) and of1_efi_telstra.img (md5
8903c18f882cbde3b98c793f7e827a40) are stock images from https://openbeak.net/openframe/firmware/efi/.

The splash is a raw RGB bitmap inside Intel's IEGD display driver (GOP, FFS file 2B13E5F0-E82E-
4209-898A-8A53E8570DB9), in its configuration text: `$sspc01` gives the background, x, y, width
and height as five hex numbers, and `$ssd` is followed by the pixels and `ENDSSDATA`, all inside
the CED area that ends with the second `CEDCED`. The driver's entry only installs its driver
binding; the text is parsed in its Start (the `ssp` and `ssd` token handlers, through the token
table), when the console connects the screen, after every DXE driver has run. So BlwPlatformDxe
can rewrite the numbers and the pixels in the driver's loaded image beforehand, and an OpenFrame
whose display driver is in blocks no update writes (an OpenFrame 2's) still shows our splash.

Most OpenFrame display drivers are one IEGD build with one of two panel configurations:
`OpenFrame-Sharp` (33.264 MHz; the O2 OF1s, and the Telio, Avaya and Swisscom Da Vinci OF2s) or
`OpenFrame-Samsung` (24.5 MHz; the Cisco OF2), whose label is two characters longer, so its text
after the label sits two bytes later. Two older builds (--more: the OpenPeak OF2's, and the
Verizon and Telstra OF1s') have the same handlers, byte for byte but for relocated dwords, 0x50
bytes earlier, and Sharp configurations of their own (fewer attributes, their own splash). For
each configuration, the signature is all of its text from its start (RVA 0x13d7) to the first
pixel except the hex digits of x, y, width and height, which are what splash.inc writes, plus the
reserve's closing `CEDCED` and the token table's `ssp` and `ssd` names. The code signature is the
two handlers and their strings, less relocated dwords, from each place they can be (gsp_code_rvas).
Checked here: the code is the same in every input, relative to its place; nothing in the
configuration or the reserve is relocated; the old splash ends exactly at `ENDSSDATA` with only
zeros after it to the reserve's end; the numbers field is 24 characters, as ours is.
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
# Where they are in the current build (O2, Cisco, Telio...); an older build has them all `shift`
# bytes earlier, found from where its handlers' strings are.
CODE = 0x15240                  # the code signature's base: the handlers' strings at 0x1524c-0x15262
SSD_FN, SSD_END = 0x18786, 0x18800      # the `ssd` handler (copies the pixels up to ENDSSDATA)
SSP_FN, SSP_END = 0x1960c, 0x197f2      # the `ssp` handler (parses the five numbers)
STRINGS = (0x1524c, 0x15263)            # '$', 'ENDSSDATA', ','
STRINGS_TEXT = b'$\0\0\0^%\0\0ENDSSDATA\0\0\0,\0\0'
TOKENS = (0xe008, 0xe018)               # the token table's `ssp` and `ssd` entries
VALUE = re.compile(rb'\$sspc01(0x0,0x)([0-9A-F]{1,3})(,0x)([0-9A-F]{1,3})(,0x)([0-9A-F]{1,3})(,0x)([0-9A-F]{1,3})\$halc01')
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
    at = [m.start() for m in re.finditer(re.escape(STRINGS_TEXT), mem)]
    if len(at) != 1:
        bp.fail('%s: the handlers\' strings found %d times' % (path, len(at)))
    shift = STRINGS[0] - at[0]
    if shift not in (0, 0x50):
        bp.fail('%s: the handlers\' strings at 0x%x, not a place we know' % (path, at[0]))
    code = CODE - shift
    label = next((x for x in LABELS if mem.find(b'$ccnc01,' + x + b'$', CFG, CFG + 0x80) >= 0), None)
    if label is None:
        bp.fail('%s: not a configuration we know' % path)
    if mem[CFG:CFG + 17] != b'^%0x700$01$chpc01':
        bp.fail('%s: configuration not at 0x%x' % (path, CFG))
    m = VALUE.search(mem, CFG, CFG + 0x100)
    if not m or m.end(8) - m.start(1) != 24:
        bp.fail('%s: $sspc01 is not 0x0 and four hex numbers in 24 characters' % path)
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
    digits = [(m.start(i), m.end(i)) for i in (2, 4, 6, 8)]
    keep = [(CFG, digits[0][0])] + [(digits[i][1], digits[i + 1][0]) for i in range(3)] + [(digits[3][1], px)]
    cfg_runs = runs_of(mem, CFG, keep + [(end, end + 6), TOKENS], relocs)
    code_runs = runs_of(mem, code, [(a - shift, b - shift) for a, b in (STRINGS, (SSD_FN, SSD_END), (SSP_FN, SSP_END))],
                        relocs)
    for o, d in cfg_runs + code_runs:
        if not 0 <= o < 0x10000 or len(d) >= 0x10000:
            bp.fail('run out of 16-bit range')
    return dict(path=path, label=label.decode(), value=value, px=px, end=end, size=size, cfg=cfg_runs,
                code=code_runs, code_rva=code, ssp_end=SSP_END - shift, old=(x, y, w, h), mem=mem)


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
    ap.add_argument('--more', nargs='*', default=[], help='other builds\' display drivers to recognise too '
                    '(the OpenPeak OF2\'s, the Verizon and Telstra OF1s\')')
    ap.add_argument('--check', nargs='*', default=[], help='more images: each must match one we recognise')
    a = ap.parse_args()
    v = [analyse(a.sharp), analyse(a.samsung)]
    if [x['label'] for x in v] != ['OpenFrame-Sharp', 'OpenFrame-Samsung']:
        bp.fail('inputs are %s, not Sharp then Samsung' % [x['label'] for x in v])
    if v[0]['code_rva'] != CODE or v[1]['code_rva'] != CODE:
        bp.fail('the Sharp and Samsung display drivers are not the current build')
    for path in a.more:
        c = analyse(path)
        if not any(x['cfg'] == c['cfg'] and x['value'] == c['value'] for x in v):
            v.append(c)
    for x in v:
        if x['code'] != v[0]['code'] or x['end'] != v[0]['end']:
            bp.fail('%s: its handlers or reserve differ from %s\'s' % (x['path'], v[0]['path']))
    rvas = sorted({x['code_rva'] for x in v}, reverse=True)
    for path in a.check:
        c = analyse(path)
        match = [x for x in v if x['cfg'] == c['cfg'] and x['code'] == c['code'] and x['value'] == c['value']
                 and c['code_rva'] in rvas]
        print('%s: %s, splash %dx%d at %d,%d' % (path.split('/')[-1], 'as %s (%s)' % (match[0]['path'].split('/')[-1], match[0]['label'])
                                                if match else 'NO MATCH', c['old'][2], c['old'][3], *c['old'][:2]))
        if not match:
            sys.exit(1)
    with open(a.out, 'w') as f:
        f.write('; Generated by tools/splash_sig.py from %s: do not edit.\n'
                % ', '.join(x['path'].split('/')[-1] for x in v))
        f.write('; The IEGD display driver (2B13E5F0-E82E-4209-898A-8A53E8570DB9): its splash configuration and\n'
                '; the code that parses it, in mmc_sig_match runs. Offsets are from GSP_CFG_RVA or GSP_CODE_RVA.\n')
        f.write('GSP_CFG_RVA     equ     0x%x\n' % CFG)
        f.write('GSP_END_RVA     equ     0x%x                  ; the reserve ends here (its second CEDCED)\n' % v[0]['end'])
        f.write('GSP_CODE_RVA    equ     0x%x\n' % CODE)
        f.write('GSP_MIN_SIZE    equ     0x%x                 ; ImageSize at least this\n'
                % max([x['ssp_end'] for x in v] + [v[0]['end'] + 6]))
        f.write('GSP_PX_MAX      equ     0x%x                   ; the later first pixel, from GSP_CFG_RVA\n'
                % max(x['px'] - CFG for x in v))
        f.write('; Each place the handlers can be (the current build\'s first, then older builds\'), 0 ends the list.\n')
        f.write('gsp_code_rvas:\n')
        f.write('        dd      %s, 0\n' % ', '.join('0x%x' % r for r in rvas))
        f.write('gsp_code_sig:\n')
        emit_runs(f, v[0]['code'])
        f.write('; Each configuration: dw its length in bytes (0 ends the list), the five numbers\' offset, the\n'
                '; first pixel\'s offset, 0; then its runs.\n')
        f.write('gsp_cfgs:\n')
        for x in v:
            n = 8 + sum(4 + len(d) for _, d in x['cfg']) + 4
            f.write('; %s (%s): numbers at 0x%x, pixels from 0x%x (stock splash %dx%d at %d,%d)\n'
                    % (x['label'], x['path'].split('/')[-1], x['value'], x['px'], x['old'][2], x['old'][3], *x['old'][:2]))
            f.write('        dw      0x%04x, 0x%04x, 0x%04x, 0\n' % (n, x['value'] - CFG, x['px'] - CFG))
            emit_runs(f, x['cfg'])
        f.write('        dw      0\n')
    for x in v:
        print('%s (%s, handlers from 0x%x): numbers at 0x%x, pixels 0x%x-0x%x (%d bytes), %d configuration bytes checked'
              % (x['label'], x['path'].split('/')[-1], x['code_rva'], x['value'], x['px'], x['end'], x['end'] - x['px'],
                 sum(len(d) for _, d in x['cfg'])))
    print('code: %d bytes checked' % sum(len(d) for _, d in v[0]['code']))


if __name__ == '__main__':
    main()
