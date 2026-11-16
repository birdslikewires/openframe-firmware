#!/usr/bin/env python3
"""Writes driver/shell_sig.inc: where the two shell changes BlwPlatformDxe makes in memory are, in
each build of the built-in EFI shell (FFS file C57AD6B7-0515-40A8-9D21-551652854E37) we know.

    venv/bin/python -I tools/shell_sig.py of1_efi_o2.img of2_efi_cisco.img driver/shell_sig.inc \\
        [--check IMAGE ...]

of1_efi_o2.img (md5 eeb32d85e8b8c63ccc00429e083a88b8) and of2_efi_cisco.img (md5
07a8fb52aedbf967cbcb1f2acc837d87) are the stock images from https://openbeak.net/openframe/firmware/efi/.
The OpenFrame 1 has one shell build and the OpenFrame 2 another (24 KB bigger, built in February
2009), with the same code for these two functions at other addresses:

- The startup.nsh countdown (countdown.inc): the function that waits before running startup.nsh
  starts with a delay of 50 tenths of a second (`push 0x32; pop esi`), takes a StartupDelay-style
  setting instead if there is one, and falls back to 50 again if that won't parse (a second
  `push 0x32`). Its signature is the function from its start to the countdown loop, less the
  relocated dwords and the two 0x32 operands (so a shell whose operands are already 0, as in our
  OF1 releases, matches too); countdown.inc writes 0 to both.
- The mapping sort comparison (usb_first.inc): 120 bytes, exactly, including a call's rel32 (the
  only bytes that differ between the two builds); usb_first.inc's two 7-byte changes are at +0x28
  and +0x4d in both.

Checked here: each shell has exactly one of each, at the expected structure; the sort comparisons
differ only in the rel32; nothing in either is relocated but the dwords the signature leaves out.
"""
import argparse
import re
import sys

import pefile

sys.path.insert(0, __file__.rsplit('/', 1)[0])
import build_patched as bp  # noqa: E402

SHELL_GUID = 'c57ad6b7-0515-40a8-9d21-551652854e37'
CD_LEN = 0x5e                   # the countdown function, from its start to the loop (inclusive of both sites)
CD_SITES = (0x0e, 0x36)         # the two 0x32 operands, from the function's start
CD_PAT = re.compile(rb'\x55\x8b\xec\x83\xec\x14\xa1.{4}\x53\x56\x6a[\x32\x00]\x5e\x68.{4}\xff\x50\x04\x33\xdb\x3b\xc3\x59\x74',
                    re.S)
SORT_LEN, SORT_A, SORT_B = 120, 0x28, 0x4d
SEQ_A, SEQ_B = bytes.fromhex('f6da1bd283e202'), bytes.fromhex('f6d81bc083e002')   # "USB or SCSI: later"


def shell(path):
    img = bytearray(open(path, 'rb').read())
    files, _ = bp.fv_files(img)
    o, s = next((o, s) for o, g, s in files if g == SHELL_GUID)
    stream, _ = bp.decompress_file(img, o, s)
    pe = pefile.PE(data=bytes(stream[bp.pe_offset(stream):]))
    relocs = {e.rva + k for b in pe.DIRECTORY_ENTRY_BASERELOC for e in b.entries if e.type == 3 for k in range(4)}
    return pe.get_memory_mapped_image(), relocs, pe.OPTIONAL_HEADER.SizeOfImage


def analyse(path, sort_ref=None):
    mem, relocs, size = shell(path)
    cds = [m.start() for m in CD_PAT.finditer(mem)]
    if len(cds) != 1:
        bp.fail('%s: %d countdown functions' % (path, len(cds)))
    cd = cds[0]
    for site in CD_SITES:
        if mem[cd + site - 1] != 0x6a or mem[cd + site] not in (0x32, 0x00) or mem[cd + site + 1] != 0x5e:
            bp.fail('%s: no push/pop esi at +0x%x' % (path, site))
    skip = {cd + s for s in CD_SITES} | relocs
    runs, i = [], cd
    while i < cd + CD_LEN:
        if i in skip:
            i += 1
            continue
        j = i
        while j < cd + CD_LEN and j not in skip:
            j += 1
        runs.append((i - cd, mem[i:j]))
        i = j
    if sort_ref is None:                                # the OF1 shell: the comparison at its known place
        sort = 0xb7c5
        if mem[sort + SORT_A:sort + SORT_A + 7] != SEQ_A or mem[sort + SORT_B:sort + SORT_B + 7] != SEQ_B:
            bp.fail('%s: sort comparison not at 0x%x' % (path, sort))
    else:                                               # others: the same bytes but the call's rel32
        e8 = sort_ref.rfind(b'\xe8')
        pat = re.compile(re.escape(sort_ref[:e8 + 1]) + b'.{4}' + re.escape(sort_ref[e8 + 5:]), re.S)
        hits = [m.start() for m in pat.finditer(mem)]
        if len(hits) != 1:
            bp.fail('%s: %d sort comparisons' % (path, len(hits)))
        sort = hits[0]
    if any(r in relocs for r in range(sort, sort + SORT_LEN)):
        bp.fail('%s: the sort comparison is relocated' % path)
    return dict(path=path, size=size, cd=cd, cd_runs=runs, sort=sort, sort_bytes=mem[sort:sort + SORT_LEN],
                countdown=[mem[cd + s] for s in CD_SITES])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('of1', help="stock image with the OpenFrame 1's shell (of1_efi_o2.img)")
    ap.add_argument('of2', help="stock image with the OpenFrame 2's shell (of2_efi_cisco.img)")
    ap.add_argument('out')
    ap.add_argument('--check', nargs='*', default=[], help='more images: each must have one of the two')
    a = ap.parse_args()
    s1 = analyse(a.of1)
    s2 = analyse(a.of2, s1['sort_bytes'])
    v = [s1, s2]
    if s1['size'] == s2['size']:
        bp.fail('the two shells are the same build')
    for path in a.check:
        c = analyse(path, s1['sort_bytes'])
        match = [n for n, x in zip(('OF1', 'OF2'), v) if x['size'] == c['size'] and x['cd'] == c['cd'] and
                 x['cd_runs'] == c['cd_runs'] and x['sort'] == c['sort'] and x['sort_bytes'] == c['sort_bytes']]
        print('%s: %s shell, countdown operands %s' % (path.split('/')[-1], match[0] if match else 'NO MATCH', c['countdown']))
        if not match:
            sys.exit(1)
    with open(a.out, 'w') as f:
        f.write('; Generated by tools/shell_sig.py from %s and %s: do not edit.\n' % (a.of1.split('/')[-1], a.of2.split('/')[-1]))
        f.write('; The built-in EFI shell (C57AD6B7-0515-40A8-9D21-551652854E37): the OF1 build, then the OF2 build.\n')
        f.write('SHL_CD_A        equ     0x%x                    ; the countdown\'s two 0x32 operands, from its start\n' % CD_SITES[0])
        f.write('SHL_CD_B        equ     0x%x\n' % CD_SITES[1])
        f.write('; The countdown: dd its RVA, the ImageSize needed; then its mmc_sig_match runs. dd 0 ends the list.\n')
        f.write('shl_cds:\n')
        for n, x in zip(('OF1', 'OF2'), v):
            f.write('; %s shell: countdown at RVA 0x%x (ImageSize 0x%x)\n' % (n, x['cd'], x['size']))
            f.write('        dd      0x%x, 0x%x\n' % (x['cd'], x['cd'] + CD_LEN))
            for o, d in x['cd_runs']:
                f.write('        dw      0x%04x, 0x%04x\n' % (o, len(d)))
                for i in range(0, len(d), 16):
                    f.write('        db      ' + ', '.join('0x%02x' % b for b in d[i:i + 16]) + '\n')
            f.write('        dw      0, 0\n')
        f.write('        dd      0\n')
        f.write('; The sort comparison: dd its RVA, then its %d bytes as stock. dd 0 ends the list.\n' % SORT_LEN)
        f.write('shl_sorts:\n')
        for n, x in zip(('OF1', 'OF2'), v):
            f.write('        dd      0x%x                          ; %s shell\n' % (x['sort'], n))
            for i in range(0, SORT_LEN, 16):
                f.write('        db      ' + ', '.join('0x%02x' % b for b in x['sort_bytes'][i:i + 16]) + '\n')
        f.write('        dd      0\n')
    for n, x in zip(('OF1', 'OF2'), v):
        print('%s shell (ImageSize 0x%x): countdown at 0x%x (operands %s), %d bytes checked; sort comparison at 0x%x'
              % (n, x['size'], x['cd'], x['countdown'], sum(len(d) for _, d in x['cd_runs']), x['sort']))


if __name__ == '__main__':
    main()
