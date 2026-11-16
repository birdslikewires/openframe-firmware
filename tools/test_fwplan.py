#!/usr/bin/env python3
"""Tests fwplan.py: every plan it makes is cut off by a power cut at many points, and a re-run from
the chip as it was left must plan, prove and finish the job.

    venv/bin/python -I tools/test_fwplan.py of1_efi_o2.img of2_efi_cisco.img [MORE STOCK IMAGES]

Cuts: after each step; part way through each step (first byte, a quarter, half, the last byte,
and each byte of a spacer header, with that byte's bits part cleared); during an erase (the block
left as garbage, the old contents or blank). Then a second update from the first's result, and
releases going back and forth. Needs releases/of1-blw-26.11.30.bin. emu_fvwalk.py --fwplan runs
the same plans on the firmware's own walkers.
"""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_patched as bp  # noqa: E402
import fwplan as fp  # noqa: E402

B = fp.BLOCK
fails = 0


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
    """Re-run from chip: plan, prove, apply; must end with rel's block 10 running."""
    global fails
    try:
        steps = fp.plan(chip, rel)
        goods, after = fp.goods_for(chip, rel)
        fp.prove(chip, steps, goods)
    except fp.Refused as e:
        print('  FAIL %s: refused: %s' % (what, e))
        fails += 1
        return None
    c = bytearray(chip)
    for s in steps:
        c = apply(c, s)
    st = fp.state(c)
    if bytes(c[fp.BLOCK10:fp.BLOCK10 + B]) != rel[fp.BLOCK10:fp.BLOCK10 + B] or st['open'] is None:
        print('  FAIL %s: did not finish' % what)
        fails += 1
        return None
    if fp.plan(c, rel):
        print('  FAIL %s: a second run wants to write again' % what)
        fails += 1
    return c


def cuts(chip, rel, name):
    steps = fp.plan(chip, rel)
    n = 0
    c = bytearray(chip)
    rng = random.Random(1)
    for k, s in enumerate(steps):
        op, blk, data = s
        changed = [i for i in range(B) if (op == 'fh' or c[blk * B + i] != data[i])]
        pts = set()
        if changed:
            pts.update([changed[0], changed[len(changed) // 4], changed[len(changed) // 2], changed[-1]])
            pts.update(i for i in changed if blk * B + i < fp.BLOCK10 and i >= changed[0])   # header bytes
        for i in sorted(pts):
            prev = i - 1
            for part in (None, half):
                finish(apply(c, s, prev, part), rel, '%s step %d cut at byte 0x%x' % (name, k + 1, i))
                n += 1
        if op == 'fh':
            base = blk * B
            for fill in ('garbage', 'old', 'blank'):
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
    print('  %-44s %d steps, %d cut points re-run' % (name, len(steps), n))
    return c


def main():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    r30 = open(os.path.join(here, 'releases', 'of1-blw-26.11.30.bin'), 'rb').read()
    # A next release: 26.11.30's block 10, one byte of the driver changed (checksum kept right).
    nxt = bytearray(r30)
    o = 0xA0048
    sz = fp.size(nxt, o)
    nxt[o + sz // 2] ^= 0x55
    bp.fix_checksums(nxt, o, sz)
    nxt = bytes(nxt)
    print('fwplan:')
    for path in sys.argv[1:]:
        stock = open(path, 'rb').read()
        name = os.path.basename(path)
        a = cuts(stock, r30, name + ' -> 26.11.30')
        b = cuts(bytes(a), nxt, name + ' -> 26.11.30 -> next')
        cuts(bytes(b), r30, name + ' -> ... -> back to 26.11.30')
    a = cuts(r30, nxt, '26.11.30 release chip -> next')
    cuts(bytes(a), r30, '26.11.30 -> next -> 26.11.30')
    # Things it must refuse.
    for what, img in (('a part-written header, State FF', bytearray(open(sys.argv[1], 'rb').read())),):
        img[0x91708:0x91708 + 12] = fp.spacer_header(0x91708)[:12]
        try:
            fp.plan(bytes(img), r30)
            print('  FAIL: planned on %s' % what)
            globals()['fails'] += 1
        except fp.Refused as e:
            print('  refuses %s: %s' % (what, e))
    print('FAIL: %d' % fails if fails else 'all pass')
    sys.exit(1 if fails else 0)


if __name__ == '__main__':
    main()
