"""Builds BlwMmcFix.efi (driver/blwmmcfix.asm), MmcTest.efi (driver/mmctest.asm) and MmcProbe.efi
(driver/mmcprobe.asm), shell apps,
wrapped like the other PE32 images here.

    venv/bin/python -I tools/build_mmcfix.py <out-dir>

Regenerate driver/mmc_sig.inc first if the firmware's MMC driver could differ (tools/mmc_sig.py).
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_ucode_driver import pe32  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DRV = os.path.join(ROOT, 'driver')


def nasm(src, out):
    subprocess.run(['nasm', '-f', 'bin', '-I', DRV + '/', '-o', out, os.path.join(DRV, src)], check=True)
    return open(out, 'rb').read()


def main():
    outdir = sys.argv[1]
    os.makedirs(outdir, exist_ok=True)
    for src, name, subsystem in (('blwmmcfix.asm', 'BlwMmcFix.efi', 10), ('mmctest.asm', 'MmcTest.efi', 10),
                                   ('mmcprobe.asm', 'MmcProbe.efi', 10),
                                   ('screentest.asm', 'ScreenTest.efi', 10)):
        code = nasm(src, os.path.join(outdir, name + '.bin'))
        with open(os.path.join(outdir, name), 'wb') as f:
            f.write(pe32(code, subsystem=subsystem))
        os.remove(os.path.join(outdir, name + '.bin'))
        print('%s: %d bytes of code' % (name, len(code)))


if __name__ == '__main__':
    main()
