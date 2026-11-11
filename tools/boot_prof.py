"""Decodes the sampling profile the profiling development build (--define TIMING --define PROFILE)
leaves in BlwProfImg and BlwProf00, BlwProf01, ... (volatile variables, GUID EC87D643).

    venv/bin/python -I tools/boot_prof.py <dir with the efivarfs files> <firmware image> [--from 1.9 --to 5.0]

Each sample is the CPU's TSC >> 10 (1330 MHz on the OF1) and the interrupted EIP (0 if no
interrupt frame was found: the timer event ran deferred), plus up to two return addresses. Each is
shown as driver+RVA, using the image table (file GUID's first dword, base, size) and the driver
names in the firmware image. A gap between samples well over the 10 ms period is time the timer
couldn't interrupt: SMM (System Management Mode), or code running with interrupts off.
"""
import argparse
import collections
import glob
import os
import struct
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from boot_times import driver_names  # noqa: E402

GUID = 'ec87d643-eba4-4bb5-a1e5-3f3e36b20da9'


def var(path):
    d = open(path, 'rb').read()
    return d[4:] if len(d) % 4 == 0 and len(d) >= 4 else d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('dir')
    ap.add_argument('firmware')
    ap.add_argument('--mhz', type=float, default=1330.0)
    ap.add_argument('--from', dest='t0', type=float, default=0.0, help='seconds from reset')
    ap.add_argument('--to', dest='t1', type=float, default=1e9)
    ap.add_argument('--bucket', type=float, default=0.1, help='seconds per summary line')
    a = ap.parse_args()
    names = driver_names(a.firmware)
    img = var(os.path.join(a.dir, 'BlwProfImg-' + GUID))
    imgs = [struct.unpack_from('<III', img, i) for i in range(0, len(img) - 11, 12)]
    raw = b''
    for i in range(256):
        p = os.path.join(a.dir, 'BlwProf%02X-%s' % (i, GUID))
        if not os.path.exists(p):
            break
        raw += var(p)
    samples = [struct.unpack_from('<IIII', raw, i) for i in range(0, len(raw) - 15, 16)]
    sec = lambda t: t * 1024 / (a.mhz * 1e6)

    def where(x):
        if not x:
            return '-'
        for g, base, size in imgs:
            if base <= x < base + size:
                return '%s+%x' % (names.get(g, '%08X' % g), x - base)
        return '%08x' % x

    print('%d images, %d samples' % (len(imgs), len(samples)))
    prev = None
    buckets = collections.OrderedDict()
    for t, eip, r1, r2 in samples:
        s = sec(t)
        gap = s - prev if prev is not None else 0
        prev = s
        if not a.t0 <= s <= a.t1:
            continue
        mark = '  <-- %.0f ms gap' % (gap * 1000) if gap > 0.03 else ''
        print('%7.3f s  %-28s %-28s %-28s%s' % (s, where(eip), where(r1), where(r2), mark))
        b = int(s / a.bucket)
        buckets.setdefault(b, collections.Counter())[(where(eip).split('+')[0])] += 1
    print('\nper %.0f ms: drivers interrupted (samples)' % (a.bucket * 1000))
    for b, c in buckets.items():
        print('%5.1f-%5.1f s  %s' % (b * a.bucket, (b + 1) * a.bucket, ', '.join('%s %d' % kv for kv in c.most_common(4))))


if __name__ == '__main__':
    main()
