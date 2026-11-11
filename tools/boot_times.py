"""Prints the boot timeline the timing development build (-DTIMING) leaves in BlwBootTimes.

    scp of@<unit>:/sys/firmware/efi/efivars/BlwBootTimes-ec87d643-eba4-4bb5-a1e5-3f3e36b20da9 /tmp/bt
    venv/bin/python -I tools/boot_times.py /tmp/bt releases/of1-blw-26.11.24.bin [--mhz 1330]

The variable (efivarfs: a 4-byte attributes word first) holds 8-byte records: a tag and the
CPU timestamp counter shifted right by 10. Tags are four characters (ENTR, GOP , RTB ...) or the
first dword of a loaded driver's FV file GUID, which the firmware image (optional) turns into the
driver's name. Times are from CPU reset, at the OF1's constant 1330 MHz TSC.
"""
import argparse
import struct
import uefi_firmware

TAGS = {
    'ENTR': 'BlwPlatformDxe entry',
    'ENTE': 'BlwPlatformDxe entry done (watchers set)',
    'GOP ': 'Graphics Output installed',
    'BLK ': 'BlockIo installed',
    'SFS ': 'SimpleFileSystem installed',
    'USB ': 'UsbIo installed',
    'OUT ': 'SimpleTextOut installed',
    'IN  ': 'SimpleTextIn installed',
    'PCI ': 'PciIo installed',
    'STG1': 'BDS stage 2DF1E051',
    'STGN': 'BDS stage EF9A3971 (NBDXE panel clock)',
    'STG3': 'BDS stage DBC9FD21',
    'STG4': 'BDS stage 8FF925F1',
    'RTB ': 'ReadyToBoot',
    'LI??': 'image loaded (no FV path)',
    'PRE ': 'images loaded before BlwPlatformDxe, in load order (times: the entry\'s):',
    'PREE': 'end of that list',
}


def driver_names(path):
    """{first dword of the file GUID: name} for every FFS file with a UI section."""
    names = {}
    stack = [uefi_firmware.AutoParser(open(path, 'rb').read()).parse()]
    while stack:
        o = stack.pop()
        if type(o).__name__ == 'FirmwareFile':
            ui = None
            todo = [o]
            while todo:
                x = todo.pop()
                if type(x).__name__ == 'FirmwareFileSystemSection' and x.type == 0x15:
                    ui = x.data.decode('utf-16-le', 'replace').rstrip('\0')
                todo.extend(getattr(x, 'objects', []) or [])
            d = int(o.guid_label.split('-')[0], 16)
            names.setdefault(d, ui or o.guid_label.upper())
        stack.extend(getattr(o, 'objects', []) or [])
    return names


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('var')
    ap.add_argument('firmware', nargs='?')
    ap.add_argument('--mhz', type=float, default=1330.0)
    a = ap.parse_args()
    raw = open(a.var, 'rb').read()
    if len(raw) % 8 == 4:                                        # efivarfs: attributes first
        raw = raw[4:]
    names = driver_names(a.firmware) if a.firmware else {}
    unit = 1024 / (a.mhz * 1e3)                                  # ms per count
    prev, pre = None, False
    for i in range(0, len(raw) - 7, 8):
        tag, t = raw[i:i + 4], struct.unpack_from('<I', raw, i + 4)[0]
        ms = t * unit
        s = tag.decode('latin-1')
        if s in TAGS:
            what = TAGS[s]
        else:
            d = struct.unpack('<I', tag)[0]
            what = 'loaded ' + names.get(d, '%08X-...' % d)
            if pre:
                print('                      ' + what[7:])
                continue
        pre = (s == 'PRE ')
        print('%9.1f ms  %+8.1f  %s' % (ms, ms - prev if prev is not None else 0, what))
        prev = ms


if __name__ == '__main__':
    main()
