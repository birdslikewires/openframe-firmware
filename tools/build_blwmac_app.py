#!/usr/bin/env python3
"""
Build BlwMac.efi (driver/blwmac.asm): an IA32 EFI application, run from boot.nsh, that keeps
the OpenFrame's WLAN card MAC and LAN MAC in NVRAM, writes the LAN MAC into the RTL8111's MAC0
registers and the card MAC into SMBIOS (serial number, UUID).

  build_blwmac_app.py <out.efi>

Assembles with nasm (-f bin) and wraps the code in the same minimal PE32 as BlwPlatformDxe
(build_ucode_driver.pe32), with subsystem 10 (EFI application).
"""
import os, subprocess, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_ucode_driver import pe32

DRIVER = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'driver')
ASM = os.path.join(DRIVER, 'blwmac.asm')
DEL_ASM = os.path.join(DRIVER, 'blwlanmac_del.asm')   # --del: the one-off BlwLanMacDel.efi


def build(ASM=ASM):
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, 'app.bin')
        r = subprocess.run(['nasm', '-f', 'bin', '-I', os.path.dirname(ASM) + '/', '-o', out, ASM], capture_output=True, text=True)
        if r.returncode:
            sys.exit('nasm: ' + r.stderr)
        code = open(out, 'rb').read()
    return code, pe32(code, subsystem=10)


if __name__ == '__main__':
    dele = '--del' in sys.argv[1:]
    out = [a for a in sys.argv[1:] if a != '--del'][0]
    code, pe = build(DEL_ASM if dele else ASM)
    open(out, 'wb').write(pe)
    print('%s: code %d bytes, PE32 %d bytes -> %s' % ('BlwLanMacDel' if dele else 'BlwMac', len(code), len(pe), out))
