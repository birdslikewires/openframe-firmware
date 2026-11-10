#!/usr/bin/env python3
"""
Print the firmware hub's block-lock registers (run as root on the OpenFrame).

  sudo python3 lockregs.py

Each 64 KB block of the SST 49LF008A has a lock register at 0xFFB00002 + block * 0x10000.
  01  write-locked, the power-on state: fh clears it to flash. Normal.
  03  write-locked and locked down: nothing can change the block until the next reset.
BlwMac.efi (and BlwLanMacDel.efi) lock blocks 1-15 down before writing NVRAM, so after a boot in
which one of them wrote, blocks 1-15 read 03 and a flash fails with "fwhub_unlock error".
Reboot first. Exits 1 if any of blocks 1-15 is locked down.
"""
import mmap, os, sys

fd = os.open('/dev/mem', os.O_RDONLY | os.O_SYNC)
m = mmap.mmap(fd, 0x100000, mmap.MAP_SHARED, mmap.PROT_READ, offset=0xFFB00000)
regs = [m[b * 0x10000 + 2] for b in range(16)]
print('lock regs:', ' '.join('%d:%02x' % (b, r) for b, r in enumerate(regs)))
if any(r & 2 for r in regs[1:]):
    print('locked down: reboot before flashing')
    sys.exit(1)
