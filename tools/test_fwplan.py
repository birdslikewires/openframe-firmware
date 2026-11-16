#!/usr/bin/env python3
"""Tests fwplan.py: every plan it makes is cut off by a power cut at many points, and a re-run from
the chip as it was left must plan, prove and finish the job.

    venv/bin/python -I tools/test_fwplan.py of1_efi_o2.img of2_efi_cisco.img [MORE STOCK IMAGES]

Chains, each from the last one's result: stock -> a release -> the next -> back again (block 10
steps); 26.11.30 (files in block 10) -> a release (block 9 steps once) -> the next; and a block 10
with no room left (block 9 steps, block 10 rebuilt). Cuts: after each step; part way through each
step (first byte, a quarter, half, the last byte, and each byte of a spacer header, that byte's
bits part cleared); during an erase (the block left as garbage, as it was, or blank). Releases are
made here from 26.11.30's files (releases/of1-blw-26.11.30.bin) in the block-11 layout.
emu_fvwalk.py runs the same plans on the firmware's own walkers.
"""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_patched as bp  # noqa: E402
import fwplan as fp  # noqa: E402

B = fp.BLOCK
fails = 0
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def releases():
    """(26.11.30, a block-11 release from its files, the next one: its driver one byte changed)."""
    r30 = open(os.path.join(HERE, 'releases', 'of1-blw-26.11.30.bin'), 'rb').read()
    rel = bytearray(r30)
    rel[fp.GATE:fp.FILES] = fp.spacer_header(fp.GATE, fp.FILES) + b'\xff' * (B - 24)
    rel[fp.FILES:fp.FILES + B] = r30[fp.GATE:fp.GATE + B]
    nxt = bytearray(rel)
    o = fp.FILES + ((fp.size(rel, fp.FILES) + 7) & ~7)
    sz = fp.size(nxt, o)
    nxt[o + sz // 2] ^= 0x55
    bp.fix_checksums(nxt, o, sz)
    return r30, bytes(rel), bytes(nxt)


def on_stock(stock, rel):
    """rel's layout on another stock image: its spacer after that image's files."""
    img = bytearray(stock)
    files, fv_end = bp.fv_files(img)
    bp.add_spacer(img, files[-1][0] + files[-1][2], fp.GATE)
    img[fp.GATE:fp.FILES + B] = rel[fp.GATE:fp.FILES + B]
    return bytes(img)


def apply(chip, step, upto=None, partial=None):
    """Chip after `step`, or cut off after byte `upto` of it (the next byte left `partial`)."""
    op, blk, data = step
    c = bytearray(chip)
    base = blk * B
    if op == 'fh':
        c[base:base + B] = b'\xff' * B
    for i in range(B):
        if upto is not None and i > upto:
            if partial is not None and i == upto + 1 and c[base + i] != data[i]:
                c[base + i] = partial(c[base + i], data[i])
            break
        c[base + i] = data[i]
    return c


def half(was, v):
    """A byte cut off part way through programming: some of its bits cleared, not all."""
    clear = was & ~v
    return was & ~(clear & 0x55 or clear & -clear)


def finish(chip, rel, what):
    """Re-run from chip: plan, prove, apply; must end with rel's files running, and nothing more
    to do."""
    global fails
    try:
        steps, n, after = fp.full(chip, rel)
    except fp.Refused as e:
        print('  FAIL %s: refused: %s' % (what, e))
        fails += 1
        return None
    c = bytearray(chip)
    for s in steps:
        c = apply(c, s)
    if bytes(c) != after or fp.plan(c, rel):
        print('  FAIL %s: did not finish' % what)
        fails += 1
        return None
    return c


def cuts(chip, rel, name, expect_parts=None):
    steps = fp.plan(chip, rel)
    parts = ' '.join(str(b) for b, k in fp.parts(steps))
    if expect_parts is not None and parts != expect_parts:
        print('  FAIL %s: writes blocks %s, not %s' % (name, parts, expect_parts))
        globals()['fails'] += 1
    n, c, rng = 0, bytearray(chip), random.Random(1)
    for k, s in enumerate(steps):
        op, blk, data = s
        changed = [i for i in range(B) if op == 'fh' or c[blk * B + i] != data[i]]
        pts = set()
        if changed:
            pts.update([changed[0], changed[len(changed) // 4], changed[len(changed) // 2], changed[-1]])
            if blk in (9, 10) and len(changed) <= 24:
                pts.update(changed)
        for i in sorted(pts):
            for part in (None, half):
                finish(apply(c, s, i - 1, part), rel, '%s step %d cut at byte 0x%x' % (name, k + 1, i))
                n += 1
        if op == 'fh':
            base = blk * B
            for fill in ('garbage', 'as it was', 'blank'):
                g = bytearray(c)
                if fill == 'garbage':
                    g[base:base + B] = bytes(rng.getrandbits(8) for _ in range(B))
                elif fill == 'blank':
                    g[base:base + B] = b'\xff' * B
                finish(g, rel, '%s step %d erase cut (%s)' % (name, k + 1, fill))
                n += 1
        c = apply(c, s)
        finish(c, rel, '%s after step %d' % (name, k + 1))
        n += 1
    print('  %-46s blocks %-9s %d cut points re-run' % (name, parts or '-', n))
    return bytes(c)


def refuse(what, img, rel):
    global fails
    try:
        fp.full(img, rel)
        print('  FAIL: planned on %s' % what)
        fails += 1
    except fp.Refused as e:
        print('  refuses %s: %s' % (what, e))


def main():
    r30, rel, nxt = releases()
    print('fwplan:')
    for path in sys.argv[1:]:
        stock = open(path, 'rb').read()
        name = os.path.basename(path)
        mine, mine2 = on_stock(stock, rel), on_stock(stock, nxt)
        a = cuts(stock, mine, name + ' -> release', '10 11 9')
        if a != mine:
            print('  FAIL %s: the result is not the release built on it' % name)
            globals()['fails'] += 1
        b = cuts(a, mine2, name + ' -> release -> next', '10 11 10')
        cuts(b, mine, name + ' -> ... -> back again', '10 11 10')
    a = cuts(r30, rel, '26.11.30 (files in block 10) -> release', '9 10 11 9')
    cuts(a, nxt, '26.11.30 -> release -> next', '10 11 10')
    # Block 10 with no room left for another header: block 9 steps and block 10 is rebuilt.
    full10 = bytearray(rel)
    for slot in range(fp.GATE, fp.FILES - 48, 24):
        full10[slot:slot + 24] = fp.spacer_header(slot, fp.FILES)[:23] + b'\xd8'
    last = fp.FILES - 48 - (fp.FILES - 48 - fp.GATE) % 24
    full10[last:last + 24] = fp.spacer_header(last, fp.FILES)
    if not fp.state(bytes(full10))['files_reached']:
        print('  FAIL: the full block 10 does not reach the files')
        globals()['fails'] += 1
    cuts(bytes(full10), nxt, 'block 10 full -> next', '9 10 11 9')
    # Things it must refuse.
    bad = bytearray(open(sys.argv[1], 'rb').read())
    s0 = fp.first_slot(bad)
    bad[s0:s0 + 12] = fp.spacer_header(s0, fp.GATE)[:12]
    refuse('a part-written header, State FF', bytes(bad), rel)
    bad = bytearray(rel)
    bad[fp.GATE + 100] = 0x00
    cuts(bytes(bad), nxt, 'junk in block 10 behind its header -> next', '9 10 11 9')
    refuse('a release of the old layout', bytes(rel), r30)
    print('FAIL: %d' % fails if fails else 'all pass')
    sys.exit(1 if fails else 0)


if __name__ == '__main__':
    main()
