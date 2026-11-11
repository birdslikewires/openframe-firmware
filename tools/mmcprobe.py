"""Decodes MmcProbe.efi's output (driver/mmcprobe.asm): the firmware's SD/MMC init, timed.

    venv/bin/python -I tools/mmcprobe.py probe.txt [--mhz 1330]

probe.txt is the shell's redirect (UTF-16) or the same converted to UTF-8.
"""
import argparse

NAMES = {0: 'GO_IDLE', 1: 'SEND_OP_COND', 2: 'ALL_SEND_CID', 3: 'SET_REL_ADDR', 6: 'SWITCH', 7: 'SELECT',
         8: 'SEND_IF_COND/EXT_CSD', 9: 'SEND_CSD', 13: 'SEND_STATUS', 16: 'SET_BLOCKLEN', 17: 'READ_SINGLE',
         18: 'READ_MULTIPLE', 41: 'SD_SEND_OP_COND', 55: 'APP_CMD'}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('log')
    ap.add_argument('--mhz', type=float, default=1330.0)
    a = ap.parse_args()
    raw = open(a.log, 'rb').read()
    text = raw.decode('utf-16') if raw[:2] in (b'\xff\xfe', b'\xfe\xff') or raw[1:2] == b'\0' else raw.decode()
    ms = lambda t: t * 1024 / (a.mhz * 1e3)
    t0 = None
    for line in text.splitlines():
        if line.startswith('MmcProbe:'):
            v = line.split()[-3:]
            print('%s\n  (stalls inside SendCommand: %d, %.1f ms in all)' % (line, int(v[0], 16), int(v[1], 16) / 1000))
            continue
        if not line.startswith('R '):
            continue
        tag, arg, start, end, n = (int(x, 16) for x in line.split()[1:6])
        t0 = start if t0 is None else t0
        kind = chr(tag >> 24)
        if kind == 'C':
            cmd, st = tag & 0xffff, tag >> 16 & 0xff
            what = 'CMD%-2d %-20s arg %08x %s' % (cmd, NAMES.get(cmd, ''), arg,
                                                   'ok' if not st else 'ERR %02x' % (st & 0x7f))
        elif kind == 'S':
            what = 'Stall %d us' % arg
        else:
            what = {'P': 'MmcMediaDevice CE-ATA check skipped: %d', 'H': '== controller %d', 'D': 'disconnected (status %08x)', 'E': 'connected (status %08x)'}[kind] % arg
        print('%9.1f ms  %9.1f ms  x%-5d %s' % (ms(start - t0), ms(end - start), n, what))


if __name__ == '__main__':
    main()
