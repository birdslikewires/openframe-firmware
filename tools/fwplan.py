#!/usr/bin/env python3
"""Plans the flash writes that bring a chip's block 10 (our version marker and BlwPlatformDxe) to a
release's, in an order where a power cut at any moment leaves a chip that boots
(docs/flash-order.md), and proves it before anything is written.

    fwplan.py plan CHIP.bin RELEASE.bin OUTDIR   # write OUTDIR/steps.txt and one 64 KB file per step
    fwplan.py show CHIP.bin                       # what the firmware's file list holds, gates included

Pure Python 3, no third-party modules: it runs on the unit. Exit status: 0 planned (steps.txt may
be empty: nothing to do), 1 refused (nothing to write would be safe; the reason is printed).

The chip's main volume is read with the rules of the firmware's own walkers (`walk`, checked
against the real PEI and DXE cores under Unicorn by emu_fvwalk.py). Then:
- If the list reaches block 10 through an open spacer (one of our releases) and block 10 is the
  release's already: nothing to do. Otherwise the spacer is closed first (State F8 -> D8: the walk
  then stops in its own FF data, short of block 10).
- With block 10 out of reach (stock firmware; a closed spacer; a cut-off earlier run), block 10 is
  written whole: programmed if blank, else erased and programmed.
- Then a spacer header is opened at the first free 24-byte slot, reaching 0xA0000: State FE, the
  other 23 bytes, State FA, State F8. A header an earlier run left part way is carried on.

Each step is a whole 64 KB block, 'fh' (erase and program) or 'fhp' (program only, bits only
cleared). The plan is then walked state by state, as emu_fvwalk.py does with the real code: after
each step, every value a byte the walk reads can hold part way through programming, and for an
erase, that nothing in the block is read. Any state that wouldn't boot, or that lists anything
but the chip's files as they were, the release's, or the files before our spacer alone (stock),
refuses the whole plan.
"""
import os
import struct
import sys
import uuid

FV_MAIN, FV_END, BLOCK10, BLOCK = 0x10000, 0xD0000, 0xA0000, 0x10000
SPACER = uuid.UUID('8e9d1954-ef62-4aa4-b8e5-88602b186161').bytes_le
MARKER = uuid.UUID('d1728e0b-d49f-480e-a48d-d0b4ec5768f4').bytes_le
FIXED_CHECKSUM = 0x5A


class Refused(Exception):
    pass


def size(img, o):
    return int.from_bytes(img[o + 20:o + 23], 'little')


def walk(img, reads=None):
    """The firmware's walk of FV_MAIN: (ok, listed files, events). ok False = the DXE core would
    call the volume corrupt. events: (offset, 'file' | 'deleted' | 'skip' | 'end'). reads, a set,
    gets every offset the walk looks at."""
    hlen = struct.unpack_from('<H', img, FV_MAIN + 0x30)[0]
    o, files, ev = FV_MAIN + hlen, [], []
    while o < FV_END:
        n = min(24, FV_END - o)
        h = bytes(img[o:o + n])
        if reads is not None:
            reads.update(range(o, o + n))
        if h == b'\xff' * n:
            ev.append((o, 'end'))
            return True, files, ev
        st = ~h[23] & 0xFF
        top = 0x80
        while top and not st & top:
            top >>= 1
        if not (top in (2, 4, 8, 0x10) and (sum(h) - h[17] - h[23]) & 0xFF == 0):
            if top in (0x20, 0x01):
                ev.append((o, 'skip'))
                o += 24
                continue
            return False, files, ev
        if top == 2:
            return False, files, ev
        ev.append((o, 'deleted' if top == 0x10 else 'file'))
        if top != 0x10:
            files.append(o)
        o = (o + size(img, o) + 7) & ~7
    ev.append((FV_END, 'end'))
    return True, files, ev


def config(img, offs):
    """The files as the firmware sees them (State aside), our spacers left out."""
    out = []
    for o in offs:
        if bytes(img[o:o + 16]) == SPACER:
            continue
        b = bytearray(img[o:o + size(img, o)])
        b[23] = 0
        out.append(bytes(b))
    return tuple(out)


def spacer_header(at):
    """Our spacer's header at `at`, reaching BLOCK10: RAW, no data checksum, State F8."""
    n = BLOCK10 - at
    h = bytearray(SPACER + b'\0\0' + bytes([0x01, 0x00]) + n.to_bytes(3, 'little') + b'\x00')
    h[16] = -sum(h) & 0xFF
    h[17] = FIXED_CHECKSUM
    h[23] = 0xF8
    return bytes(h)


def state(img):
    """Where the chip stands: dict with 'open' (offset of the open spacer, or None), 'slot' (where
    the next spacer header goes, if block 10 is out of reach), 'b10_reached'."""
    ok, files, ev = walk(img)
    if not ok:
        raise Refused('the firmware would reject this chip\'s file list as it is')
    opened = [o for o in files if bytes(img[o:o + 16]) == SPACER]
    if opened:
        o = opened[-1]
        if o + size(img, o) != BLOCK10 or opened[:-1]:
            raise Refused('a spacer that does not end at 0x%x' % BLOCK10)
        if not any(f >= BLOCK10 for f in files):
            raise Refused('the spacer leads to an empty block 10')
        return {'open': o, 'slot': None, 'b10_reached': True}
    end = ev[-1][0]
    if any(f >= BLOCK10 for f in files) or end >= BLOCK10:
        raise Refused('the file list reaches block 10 without our spacer')
    # A header an earlier run left under construction: skipped (State FE), its slot is 24 back.
    slot = end
    if len(ev) >= 2 and ev[-2][1] == 'skip' and ev[-2][0] == end - 24 and img[end - 1] in (0xFE, 0xFA):
        slot = end - 24
    elif img[slot + 23] != 0xFF:
        raise Refused('unexpected bytes at 0x%x' % slot)
    if slot & 7 or slot + 48 > BLOCK10:
        raise Refused('no room for a spacer header at 0x%x' % slot)
    return {'open': None, 'slot': slot, 'b10_reached': False}


def program(img, at, data):
    for i, v in enumerate(data):
        if v & ~img[at + i] & 0xFF:
            raise Refused('0x%x would need a bit set (%02x -> %02x)' % (at + i, img[at + i], v))
        img[at + i] = v


def plan(chip, release):
    """[(op, block, 64 KB bytes)] taking chip's block 10 to release's, with the spacer handling."""
    chip, new10 = bytearray(chip), bytes(release[BLOCK10:BLOCK10 + BLOCK])
    if not any(o >= BLOCK10 for o in walk(release)[1]) or bytes(release[BLOCK10:BLOCK10 + 16]) != MARKER:
        raise Refused('the release has no block-10 files')
    st = state(chip)
    steps, cur = [], bytearray(chip)

    def step(op, blk):
        steps.append((op, blk, bytes(cur[blk * BLOCK:(blk + 1) * BLOCK])))

    if st['open'] is not None:
        if bytes(chip[BLOCK10:BLOCK10 + BLOCK]) == new10:
            return []
        g = st['open']
        program(cur, g + 23, b'\xd8')
        step('fhp', g // BLOCK)
        slot = g + 24
        if set(cur[slot:slot + 48]) != {0xFF}:
            raise Refused('no free room after the spacer at 0x%x' % g)
    else:
        slot = st['slot']
    if bytes(cur[BLOCK10:BLOCK10 + BLOCK]) != new10:
        blank = set(cur[BLOCK10:BLOCK10 + BLOCK]) == {0xFF}
        cur[BLOCK10:BLOCK10 + BLOCK] = new10
        step('fhp' if blank else 'fh', 10)
    h = spacer_header(slot)
    if cur[slot + 23] == 0xFF:
        program(cur, slot + 23, b'\xfe')
        step('fhp', slot // BLOCK)
    if bytes(cur[slot:slot + 23]) != h[:23]:
        program(cur, slot, h[:23])
        step('fhp', slot // BLOCK)
    for s in (0xFA, 0xF8):
        if cur[slot + 23] != s:
            program(cur, slot + 23, bytes([s]))
            step('fhp', slot // BLOCK)
    return steps


def prove(chip, steps, goods):
    """Walk every state of the steps (see the top). Returns the number of states walked."""
    allowed = set(config(g, walk(g)[1]) for g in goods)
    cur, states = bytearray(chip), 0

    def judge(where):
        nonlocal states
        states += 1
        ok, files, _ = walk(cur)
        if not ok:
            raise Refused('%s: the firmware would reject the file list' % where)
        if config(cur, files) not in allowed:
            raise Refused('%s: the firmware would see a mix of files' % where)

    def relevant():
        r = set()
        ok, files, _ = walk(cur, r)
        for o in files:
            r.update(range(o, o + max(24, size(cur, o))))
        return r

    judge('before')
    for n, (op, blk, data) in enumerate(steps):
        base = blk * BLOCK
        rel = relevant()
        if op == 'fh':
            if any(o in rel for o in range(base, base + BLOCK)):
                raise Refused('step %d erases block %d, which the firmware reads' % (n + 1, blk))
            cur[base:base + BLOCK] = b'\xff' * BLOCK
        for i in range(BLOCK):
            o, v = base + i, data[i]
            if cur[o] == v:
                continue
            if v & ~cur[o] & 0xFF:
                raise Refused('step %d: 0x%x needs a bit set' % (n + 1, o))
            if o in rel:
                was, clear = cur[o], cur[o] & ~v
                sub = clear
                while True:
                    sub = (sub - 1) & clear
                    if not sub:
                        break
                    cur[o] = was & ~sub
                    judge('step %d, 0x%x part way to %02x' % (n + 1, o, v))
                cur[o] = v
                judge('step %d, 0x%x = %02x' % (n + 1, o, v))
                rel = relevant()
            else:
                cur[o] = v
        judge('after step %d' % (n + 1))
    return states


def goods_for(chip, release):
    """What a boot may see along the way: the chip as it is, the result, and its files before our
    spacer (stock firmware's)."""
    after = bytearray(chip)
    for op, blk, data in plan(chip, release):
        after[blk * BLOCK:(blk + 1) * BLOCK] = data
    ok, files, ev = walk(chip)
    first = next((o for o, k in ev if bytes(chip[o:o + 16]) == SPACER), ev[-1][0])
    stock = bytes(chip[:first]) + b'\xff' * (FV_END - first) + bytes(chip[FV_END:])
    return [bytes(chip), bytes(after), stock], bytes(after)


def main(argv):
    if len(argv) == 3 and argv[1] == 'show':
        img = open(argv[2], 'rb').read()
        ok, files, ev = walk(img)
        for o, k in ev:
            g = str(uuid.UUID(bytes_le=bytes(img[o:o + 16]))) if k != 'end' else ''
            print('0x%06x %-7s %s %s' % (o, k, g, 'state %02x size 0x%x' % (img[o + 23], size(img, o)) if k != 'end' else ''))
        print('file list %s; %s' % ('ok' if ok else 'REJECTED by the DXE core', state(img) if ok else ''))
        return 0
    if len(argv) != 5 or argv[1] != 'plan':
        print(__doc__.split('\n\n')[1])
        return 1
    chip, release = open(argv[2], 'rb').read(), open(argv[3], 'rb').read()
    try:
        if len(chip) != 1 << 20 or len(release) != 1 << 20:
            raise Refused('chip and release must be 1 MB')
        steps = plan(chip, release)
        goods, after = goods_for(chip, release)
        n = prove(chip, steps, goods)
        ok, files, _ = walk(after)
        if bytes(after[BLOCK10:BLOCK10 + BLOCK]) != release[BLOCK10:BLOCK10 + BLOCK] or \
                [f for f in files if f >= BLOCK10] != [f for f in walk(release)[1] if f >= BLOCK10]:
            raise Refused('the plan does not end with the firmware running the release\'s block 10')
    except Refused as e:
        print('fwplan: refused: %s' % e)
        return 1
    os.makedirs(argv[4], exist_ok=True)
    with open(os.path.join(argv[4], 'steps.txt'), 'w') as f:
        for i, (op, blk, data) in enumerate(steps, 1):
            name = 'step%d.bin' % i
            open(os.path.join(argv[4], name), 'wb').write(data)
            f.write('%s %d %s\n' % (op, blk, name))
    print('fwplan: %d step%s, %d states proven' % (len(steps), '' if len(steps) == 1 else 's', n))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
