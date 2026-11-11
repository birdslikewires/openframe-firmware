"""Decodes the USB trace the development build (--define TIMING --define PROFILE --define USBTRACE)
leaves in BlwUsbTr00, 01, ... (volatile variables, GUID EC87D643), with callers mapped through the
profile's image table (BlwProfImg).

    venv/bin/python -I tools/boot_usb.py <dir with the efivarfs files> <firmware image> [--min-ms 1]

Records (20 bytes: start, end, caller, info, extra; times are TSC >> 10, 1330 MHz on the OF1):
  T  SmmControl->Trigger: info = SMI value | request function << 8 | subfunction << 16 (USB's SMI
     0x31 only), extra = status. Its time is time in SMM.
  S  BS->Stall: info = microseconds, extra = how many in a row (merged).
  C  a USBRT copy checked: end = its base; bit 0 FixedDelay and bit 1 the 1 s wait read as stock
     (so the copy is readable: not closed SMRAM), bit 7 = at ReadyToBoot.
"""
import argparse
import collections
import os
import struct
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from boot_times import driver_names  # noqa: E402

GUID = 'ec87d643-eba4-4bb5-a1e5-3f3e36b20da9'


def var(path):
    d = open(path, 'rb').read()
    return d[4:]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('dir')
    ap.add_argument('firmware')
    ap.add_argument('--mhz', type=float, default=1330.0)
    ap.add_argument('--min-ms', type=float, default=0.0, help='hide T/S records shorter than this')
    a = ap.parse_args()
    names = driver_names(a.firmware)
    img = var(os.path.join(a.dir, 'BlwProfImg-' + GUID))
    imgs = [struct.unpack_from('<III', img, i) for i in range(0, len(img) - 11, 12)]
    raw = b''
    for i in range(256):
        p = os.path.join(a.dir, 'BlwUsbTr%02X-%s' % (i, GUID))
        if not os.path.exists(p):
            break
        raw += var(p)
    recs = [struct.unpack_from('<IIIII', raw, i) for i in range(0, len(raw) - 19, 20)]
    sec = lambda t: t * 1024 / (a.mhz * 1e6)

    def where(x):
        if not x:
            return '-'
        for g, base, size in imgs:
            if base <= x < base + size:
                return '%s+%x' % (names.get(g, '%08X' % g), x - base)
        return '%08x' % x

    smi_total = collections.Counter()
    smi_count = collections.Counter()
    stall_total = collections.Counter()
    print('%d records' % len(recs))
    for st, en, caller, info, extra in recs:
        kind = chr(info >> 24)
        if kind == 'C':
            print('%8.3f s  C  USBRT copy at %08x: %s%s' % (sec(st), en,
                  'readable (stock code)' if info & 3 == 3 else 'NOT readable as stock: %x' % (info & 3),
                  ', at ReadyToBoot' if info & 0x80 else ', as it loaded'))
            continue
        ms = (sec(en) - sec(st)) * 1000
        if kind == 'T':
            smi, fn, sub = info & 0xff, info >> 8 & 0xff, info >> 16 & 0xff
            key = ('SMI %02x fn %02x/%02x' % (smi, fn, sub), where(caller))
            smi_total[key] += ms
            smi_count[key] += 1
            if ms >= a.min_ms:
                print('%8.3f s  T  %9.1f ms  SMI %02x  fn %02x/%02x  status %08x  from %s'
                      % (sec(st), ms, smi, fn, sub, extra, where(caller)))
        elif kind == 'S':
            us = info & 0xffffff
            stall_total[(us, where(caller))] += ms
            if ms >= a.min_ms:
                print('%8.3f s  S  %9.1f ms  %d x Stall(%d us)  from %s' % (sec(st), ms, extra, us, where(caller)))
    print('\nSMM time by request and caller:')
    for k, v in smi_total.most_common(20):
        print('  %9.1f ms  %4d x  %s  from %s' % (v, smi_count[k], k[0], k[1]))
    print('\nStall time by length and caller (first..last of each merged run):')
    for k, v in stall_total.most_common(20):
        print('  %9.1f ms  Stall(%d us)  from %s' % (v, k[0], k[1]))


if __name__ == '__main__':
    main()
