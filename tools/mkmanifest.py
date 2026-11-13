#!/usr/bin/env python3
"""
mkmanifest.py: the list of firmware blocks of-firmware-update recognises.

    mkmanifest.py <out> <label>=<image.bin> [<label>=<image.bin> ...]

For every image given (each BirdsLikeWires release from 26.11.29 on, and each stock firmware we
know: O2, Verizon, Telstra), writes one line per block 1-15:

    <block> <md5 of the 64 KB block> <label>

of-firmware-update writes a block that differs from its target image only if that block's md5 is
listed here for that block number (or the chip shows an update of its own was under way). So a
chip whose firmware isn't listed is never written: its owner gets a dump instead. Block 0 (NVRAM)
is per unit and left out.
"""
import hashlib
import sys


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    out = sys.argv[1]
    lines, seen = [], {}
    for arg in sys.argv[2:]:
        label, _, path = arg.partition('=')
        if not path or not label or ' ' in label:
            sys.exit('expected <label>=<image.bin>, got %r' % arg)
        data = open(path, 'rb').read()
        if len(data) != 0x100000:
            sys.exit('%s is %d bytes, not 1 MB' % (path, len(data)))
        for n in range(1, 16):
            h = hashlib.md5(data[n * 0x10000:(n + 1) * 0x10000]).hexdigest()
            key = (n, h)
            if key in seen:
                seen[key].append(label)
                continue
            seen[key] = [label]
            lines.append(key)
    with open(out, 'w') as f:
        f.write('# of-firmware-update: known firmware blocks (tools/mkmanifest.py)\n')
        f.write('# <block> <md5> <images that have it>\n')
        for n, h in sorted(lines):
            f.write('%d %s %s\n' % (n, h, ','.join(seen[(n, h)])))
    print('%s: %d distinct blocks from %d images' % (out, len(lines), len(sys.argv) - 2))


if __name__ == '__main__':
    main()
