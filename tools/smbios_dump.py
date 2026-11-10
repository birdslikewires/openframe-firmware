#!/usr/bin/env python3
"""
Turn build_ucode_driver.py --smbios-out into a file the real dmidecode can read, optionally filled
in the way BlwPlatformDxe fills it at boot, so the table can be checked before anything is flashed:

  smbios_dump.py <smbios-out.bin> <dump.bin> [--mac 00:0e:8e:12:34:56] [--mem 512,533] [--bootcount N]
  dmidecode --from-dump dump.bin            (on any Linux machine with dmidecode)

The table address is set to 32 (where --from-dump expects it) and both entry-point checksums are
computed, as the driver does. --mac fills Type 1 as smbios_id.inc does (a card's burned-in MAC:
serial number and UUID; a locally administered one: UUID only); --mem fills Type 17's size (MB)
and speed (MT/s) as mem_fill does; --bootcount fills Type 11.
"""
import argparse, struct


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('smbios')
    ap.add_argument('out')
    ap.add_argument('--mac', help='the MAC BlwMac.efi stored')
    ap.add_argument('--mem', help='SIZE_MB,SPEED_MTS for Type 17 (0xFFFF,0 = unknown)')
    ap.add_argument('--bootcount', type=int)
    a = ap.parse_args()
    d = bytearray(open(a.smbios, 'rb').read())
    if d[:4] != b'_SM_':
        raise SystemExit('not an SMBIOS entry point')
    tlen = struct.unpack_from('<H', d, 0x16)[0]
    struct.pack_into('<I', d, 0x18, 32)
    tbl = 32

    def structs():
        o = tbl
        while o < tbl + tlen:
            t, ln = d[o], d[o + 1]
            e = d.index(b'\0\0', o + ln)
            yield t, o, ln, e
            o = e + 2

    for t, o, ln, e in structs():
        if t == 1 and a.mac:
            mac = bytes.fromhex(a.mac.replace(':', '').replace('-', ''))
            strs = d[o + ln:e].split(b'\0')
            last = o + ln + len(b'\0'.join(strs[:-1])) + (1 if len(strs) > 1 else 0)
            d[o + 8:o + 24] = bytes.fromhex('00000000000080008000') + mac
            if mac[0] & 2:
                d[o + 7] = 0
            else:
                d[o + 7] = len(strs)
                d[last:last + 12] = mac.hex().upper().encode()
        if t == 17 and a.mem:
            size, speed = (int(x, 0) for x in a.mem.split(','))
            struct.pack_into('<H', d, o + 12, size)
            struct.pack_into('<H', d, o + 21, speed)
        if t == 11 and a.bootcount is not None:
            i = d.index(b'BootCount: ', o) + len('BootCount: ')
            d[i:i + 10] = b'%010d' % a.bootcount
    d[0x15] = 0
    d[0x15] = -sum(d[0x10:0x1f]) & 0xff
    d[4] = 0
    d[4] = -sum(d[0:0x1f]) & 0xff
    open(a.out, 'wb').write(d)


if __name__ == '__main__':
    main()
