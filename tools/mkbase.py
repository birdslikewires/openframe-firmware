#!/usr/bin/env python3
"""
Make the base image for the next release from the previous release.

  mkbase.py <previous-release.bin> <new-version> <base-out.bin> [--core-pe core_dxe.pe]

Each release is built on the one before: everything carries over except the last two files in
FV_MAIN, the version marker and BlwPlatformDxe, both in block 9. This erases those two, appends
a new marker "BirdsLikeWires OpenFrame EFI <new-version> (OF1)" where they were (any length), and
checks every FV file. build_ucode_driver.py then appends the new driver after the marker.

--core-pe also writes the DXE core's PE32 (the FFS file of type 5, decompressed), for
build_ucode_driver.py --check-with-core, which runs the firmware's own decompressor on the new
driver to prove the firmware will accept it.

Refuses anything that doesn't look like a BirdsLikeWires release: the last file must be
BlwPlatformDxe and the one before it the marker. Only bytes from the old marker onwards change.
"""
import argparse, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_patched as bp
from build_ucode_driver import DRIVER_GUID

MARKER_RE = re.compile(r'BirdsLikeWires OpenFrame EFI (\d+\.\d+\.\d+) \((OF\d)\)')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('previous', help='the previous release image')
    ap.add_argument('version', help='the new version, e.g. 26.11.22')
    ap.add_argument('out', help='the base image to write')
    ap.add_argument('--core-pe', help='also write the DXE core PE32 here')
    a = ap.parse_args()
    if not re.fullmatch(r'\d+\.\d+\.\d+', a.version):
        bp.fail('version must be YY.M.N')

    orig = open(a.previous, 'rb').read()
    if len(orig) != 0x100000:
        bp.fail('not a 1 MB image')
    img = bytearray(orig)
    files, fv_end = bp.fv_files(img)
    for o, g, s in files:
        bp.check_file(img, o)
    (mo, mg, ms), (lo, lg, ls) = files[-2], files[-1]
    if lg != DRIVER_GUID or mg != bp.MARKER_GUID:
        bp.fail('the last two files are not the marker and BlwPlatformDxe: not a BirdsLikeWires release')
    text = bytes(img[mo + 24:mo + ms]).split(b'\0')[0].decode('ascii', 'replace')
    m = MARKER_RE.fullmatch(text)
    if not m:
        bp.fail('unexpected marker text %r' % text)
    old_version, model = m.groups()

    if a.core_pe:
        core = [(o, g, s) for o, g, s in files if img[o + 18] == 0x05]
        if len(core) != 1:
            bp.fail('expected one DXE core file, found %d' % len(core))
        o, g, s = core[0]
        stream, _ = bp.decompress_file(img, o, s)
        open(a.core_pe, 'wb').write(bytes(stream[bp.pe_offset(stream):]))

    img[mo:lo + ls] = b'\xff' * (lo + ls - mo)          # the marker, any padding, the driver
    po, pg, ps = files[-3]
    new_text = 'BirdsLikeWires OpenFrame EFI %s (%s)' % (a.version, model)
    print(bp.add_marker(img, po + ps, fv_end, new_text))

    new_files, _ = bp.fv_files(img)
    for o, g, s in new_files:
        bp.check_file(img, o)
    if len(new_files) != len(files) - 1 or new_files[-1][1] != bp.MARKER_GUID:
        bp.fail('file list not as expected after the change')
    changed = [i for i in range(len(img)) if img[i] != orig[i]]
    if changed and (min(changed) < mo or max(changed) >= lo + ls):
        bp.fail('bytes outside the marker and driver changed')
    open(a.out, 'wb').write(img)
    print('%s -> %s: base for %s written to %s; 64K blocks changed: %s'
          % (old_version, a.version, a.version, a.out, sorted({i // 0x10000 for i in changed})))


if __name__ == '__main__':
    main()
