#!/usr/bin/env python3
"""Plans the flash writes that put a release's files (our version marker and BlwPlatformDxe) on
a chip, in an order where a power cut at any moment leaves a chip that boots
(docs/flash-order.md), and proves it before anything is written.

    fwplan.py plan CHIP.bin RELEASE.bin OUTDIR   # write OUTDIR/steps.txt and one 64 KB file per step
    fwplan.py show CHIP.bin                       # the firmware's file list, spacer headers included
    fwplan.py canon CHIP.bin OUT.bin              # the chip with our spacer headers blanked (for md5s)

Pure Python 3, no third-party modules: it runs on the unit. Exit status: 0 planned (steps.txt may
be empty: nothing to do), 1 refused (nothing to write would be safe; the reason is printed).

The layout (from 26.11.31): after the stock files, our spacer (a RAW file of FF) runs to block 10;
block 10 holds spacer headers only, the open one reaching block 11; block 11 holds our files.
A spacer header is a signpost the firmware's walk follows. Clearing one bit of its State (F8 ->
D8) turns it into "skip 24 bytes", which lands in the spacer's own FF data: the walk ends there,
and whatever is beyond is out of the firmware's reach. A new header goes in the next 24 bytes,
State first (FE, the other 23 bytes, FA, F8), which every walker state allows.

- An update (our layout, block 11 to change): close block 10's open header, write block 11 (out
  of reach), open the next header in block 10. Block 9 never changes. About 2,700 updates.
- Block 10 to (re)build (stock firmware; 26.11.29/30's layout, our files in block 10; block 10
  full; anything cut off before block 10 was finished): the same one level up. Close block 9's
  open spacer header if there is one, write block 10 (its first header, complete) and block 11,
  then open the next header in block 9. Block 9 changes by 24 bytes each time this happens.
- A chip part way through either (a power cut): carried on from where it is.

Each step is a whole 64 KB block, 'fh' (erase and program) or 'fhp' (program only, bits only
cleared). The plan is then walked state by state, as emu_fvwalk.py does with the real code: after
each step, every value a byte the walk reads can hold part way through programming, and for an
erase, that nothing in the block is read. Any state that wouldn't boot, or that lists anything
but the chip's files as they were, the release's, or the stock files alone, refuses the plan.
"""
import os
import struct
import sys
import uuid

FV_MAIN, FV_END, BLOCK = 0x10000, 0xD0000, 0x10000
GATE, FILES = 0xA0000, 0xB0000          # block 10: spacer headers; block 11: our files
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


def spacer_header(at, end):
    """Our spacer's header at `at`, reaching `end`: RAW, no data checksum, State F8."""
    h = bytearray(SPACER + b'\0\0' + bytes([0x01, 0x00]) + (end - at).to_bytes(3, 'little') + b'\x00')
    h[16] = -sum(h) & 0xFF
    h[17] = FIXED_CHECKSUM
    h[23] = 0xF8
    return bytes(h)


def chain(img, lo, hi):
    """Spacer headers from lo, each reaching hi: closed ones (D8), then at most one open (F8/FA)
    or part way (State FF/FE/FA with the rest on its way), then FF to hi. Returns
    {'closed': n, 'cur': offset of the last header or None, 'open': bool, 'next': first free slot}
    or None if [lo, hi) holds anything else."""
    slot, closed, cur, opened = lo, 0, None, False
    while slot + 24 <= hi:
        h, t = bytes(img[slot:slot + 24]), spacer_header(slot, hi)
        if h == b'\xff' * 24:
            break
        if h[:23] == t[:23] and h[23] == 0xD8:
            closed += 1
            slot += 24
            continue
        if any(a & b != b for a, b in zip(h[:23], t[:23])) or h[23] not in (0xFF, 0xFE, 0xFA, 0xF8):
            return None
        cur, opened = slot, h[:23] == t[:23] and h[23] in (0xFA, 0xF8)
        slot += 24
        break
    if set(img[slot:hi]) - {0xFF}:
        return None
    return {'closed': closed, 'cur': cur, 'open': opened, 'next': slot}


def first_slot(img):
    """Where our spacer starts: the walk's first spacer header, skip or end."""
    ok, files, ev = walk(img)
    if not ok:
        raise Refused('the firmware would reject this chip\'s file list as it is')
    for o, k in ev:
        if k in ('end', 'skip') or (k == 'file' and bytes(img[o:o + 16]) == SPACER):
            if o >= GATE:
                raise Refused('the stock files run into block 10')
            return o
        if k != 'file':
            raise Refused('a deleted file at 0x%x' % o)
    raise Refused('no end to the file list')


def state(img):
    """Where the chip stands."""
    s0 = first_slot(img)
    c9 = chain(img, s0, GATE)
    if c9 is None:
        raise Refused('unexpected bytes in our spacer in block 9 (from 0x%x)' % s0)
    c10 = chain(img, GATE, FILES) if c9['open'] else None
    return {'s0': s0, 'c9': c9, 'c10': c10,
            'old_layout': c9['open'] and bytes(img[GATE:GATE + 16]) == MARKER,
            'files_reached': bool(c10 and c10['open'])}


def canon(img):
    """The chip with our spacer headers blanked: block 9 (and block 10, if it holds only spacer
    headers) as they would be without them, so their md5s can be looked up whatever headers
    updates have used."""
    st = state(img)
    c = bytearray(img)
    c[st['s0']:GATE] = b'\xff' * (GATE - st['s0'])
    if chain(img, GATE, FILES) is not None:
        c[GATE:FILES] = b'\xff' * BLOCK
    return bytes(c), st


def program(img, at, data):
    for i, v in enumerate(data):
        if v & ~img[at + i] & 0xFF:
            raise Refused('0x%x would need a bit set (%02x -> %02x)' % (at + i, img[at + i], v))
        img[at + i] = v


def release_files(release):
    """Block 11 of a release of our layout, checked."""
    ok, files, _ = walk(release)
    st = state(release)
    if not ok or not st['files_reached'] or st['c10']['closed'] or st['c10']['cur'] != GATE:
        raise Refused('the release is not of the block-11 layout')
    if bytes(release[FILES:FILES + 16]) != MARKER or not any(f > FILES for f in files):
        raise Refused('the release has no marker and driver in block 11')
    return bytes(release[FILES:FILES + BLOCK])


def plan(chip, release):
    """[(op, block, 64 KB bytes)] putting release's block 11 on the chip (see the top)."""
    new11 = release_files(release)
    st = state(chip)
    steps, cur = [], bytearray(chip)

    def step(op, blk):
        steps.append((op, blk, bytes(cur[blk * BLOCK:(blk + 1) * BLOCK])))

    def put(blk, data):
        base = blk * BLOCK
        if bytes(cur[base:base + BLOCK]) == data:
            return
        blank = set(cur[base:base + BLOCK]) == {0xFF}
        cur[base:base + BLOCK] = data
        step('fhp' if blank else 'fh', blk)

    def open_at(slot, end):
        h = spacer_header(slot, end)
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

    c9, c10 = st['c9'], st['c10']
    if c10 is not None and c10['open'] and bytes(chip[FILES:FILES + BLOCK]) == new11:
        return []
    if c10 is not None:
        slot10 = c10['next'] if c10['open'] or c10['cur'] is None else c10['cur']
    if c10 is not None and slot10 + 48 <= FILES:
        # Block 10 is ours and has room: step there; block 9 stays as it is.
        if c10['open']:
            program(cur, c10['cur'] + 23, b'\xd8')
            step('fhp', GATE // BLOCK)
        put(11, new11)
        open_at(slot10, FILES)
    else:
        # Block 10 to (re)build, out of the firmware's reach behind block 9's spacer.
        if c9['open']:
            program(cur, c9['cur'] + 23, b'\xd8')
            step('fhp', 9)
            slot = c9['next']
        else:
            slot = c9['cur'] if c9['cur'] is not None else c9['next']
        if slot + 48 > GATE:
            raise Refused('no room left for a spacer header in block 9')
        put(10, spacer_header(GATE, FILES) + b'\xff' * (BLOCK - 24))
        put(11, new11)
        open_at(slot, GATE)
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
    """What a boot may see along the way: the chip as it is, the result, and the stock files
    alone. Returns (goods, after)."""
    after = bytearray(chip)
    for op, blk, data in plan(chip, release):
        after[blk * BLOCK:(blk + 1) * BLOCK] = data
    s0 = first_slot(chip)
    stock = bytes(chip[:s0]) + b'\xff' * (FV_END - s0) + bytes(chip[FV_END:])
    return [bytes(chip), bytes(after), stock], bytes(after)


def check_after(after, release):
    st = state(after)
    if not st['files_reached'] or bytes(after[FILES:FILES + BLOCK]) != release_files(release):
        raise Refused('the plan does not end with the firmware running the release\'s files')


def full(chip, release):
    """plan + prove + check: (steps, states proven, after). Raises Refused."""
    steps = plan(chip, release)
    goods, after = goods_for(chip, release)
    n = prove(chip, steps, goods)
    check_after(after, release)
    return steps, n, after


def parts(steps):
    """The plan as the screen shows it: consecutive steps on one block are one part."""
    out = []
    for op, blk, data in steps:
        if not out or out[-1][0] != blk:
            out.append([blk, 0])
        out[-1][1] += 1
    return out


def main(argv):
    try:
        if len(argv) == 3 and argv[1] == 'show':
            img = open(argv[2], 'rb').read()
            ok, files, ev = walk(img)
            for o, k in ev:
                g = str(uuid.UUID(bytes_le=bytes(img[o:o + 16]))) if k != 'end' else ''
                print('0x%06x %-7s %s %s' % (o, k, g, 'state %02x size 0x%x' % (img[o + 23], size(img, o)) if k != 'end' else ''))
            print('file list %s' % ('ok' if ok else 'REJECTED by the DXE core'))
            if ok:
                print(state(img))
            return 0
        if len(argv) == 4 and argv[1] == 'canon':
            c, st = canon(open(argv[2], 'rb').read())
            open(argv[3], 'wb').write(c)
            reached = st['files_reached'] or st['old_layout']
            print('spacer=0x%x files=%s layout=%s' % (st['s0'], 'reached' if reached else 'unreached',
                                                     'old' if st['old_layout'] else 'block11'))
            return 0
        if len(argv) != 5 or argv[1] != 'plan':
            print(__doc__.split('\n\n')[1])
            return 1
        chip, release = open(argv[2], 'rb').read(), open(argv[3], 'rb').read()
        if len(chip) != 1 << 20 or len(release) != 1 << 20:
            raise Refused('chip and release must be 1 MB')
        steps, n, after = full(chip, release)
    except Refused as e:
        print('fwplan: refused: %s' % e)
        return 1
    os.makedirs(argv[4], exist_ok=True)
    with open(os.path.join(argv[4], 'steps.txt'), 'w') as f:
        for i, (op, blk, data) in enumerate(steps, 1):
            name = 'step%d.bin' % i
            open(os.path.join(argv[4], name), 'wb').write(data)
            f.write('%s %d %s\n' % (op, blk, name))
    print('fwplan: %d step%s (%s), %d states proven' % (len(steps), '' if len(steps) == 1 else 's',
                                                       ' '.join(str(b) for b, k in parts(steps)) or 'nothing to do', n))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
