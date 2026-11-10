# OpenFrame firmware

BirdsLikeWires firmware for the OpenPeak OpenFrame 1 (and the O2 Joggler): the stock O2 AMI Aptio
EFI firmware with a few careful additions, the tools that build and test it, and the record of how
it was done.

Releases are 1 MB images for the board's socketed SST 49LF008A firmware hub, written with `fh`
(in `fh/`), a kernel module that gives a running OpenFrame a `/dev/fh` for the chip. Only the
OpenFrame 1 is supported so far; nothing here has been tried on an OpenFrame 2.

This repository was `fh` until 2026-11; its history carries on here.

## What a release changes

Everything is in the main firmware volume (blocks 5–9 differ from stock); NVRAM, the NCB and the
boot block are the stock ones.

- **The shell's countdown is 0**, so `boot.nsh` runs straight away.
- **The boot logo** is the BirdsLikeWires pecking bird.
- **BlwPlatformDxe**, a small DXE driver (`driver/ucode_late.asm`):
  - loads CPU microcode 0x217 on both threads (loading it at reset, as the firmware normally would,
    hangs this board);
  - names the firmware vendor "BirdsLikeWires";
  - publishes SMBIOS 2.4, which the stock firmware lacks: BIOS, system (with a serial number and
    UUID made from the WLAN card's MAC), chassis (Embedded PC), memory array and device (size and
    speed found at boot), and a boot count;
  - gives the RTL8111 network chip, which has no address of its own, a stable MAC from NVRAM;
  - from 26.11.22, works around the stock MMC/SD driver, which reads the wrong sectors past the
    first megabyte of a transfer, so the shell can start a kernel from the internal memory
    (`driver/mmc_fix.inc`).
- **BlwMac.efi** (`driver/blwmac.asm`) runs from `boot.nsh` and keeps that MAC in NVRAM, writing
  only when it changes. openframe-linux ships it and feeds it the right MAC.
- **BlwMmcFix.efi** (`driver/blwmmcfix.asm`) is the same MMC workaround as a shell app, for
  firmware without it: run from `boot.nsh` before the kernel, it lets the shell start a kernel
  from the internal memory. **MmcTest.efi** (`driver/mmctest.asm`)
  shows whether large reads come back right.

The firmware never writes NVRAM itself, and nothing here adds a variable under a new vendor GUID:
AMI's NVRAM code corrupts the store when that happens ([docs/nvram.md](docs/nvram.md)).

## Using a release

Each version's image is a download on this repository's
[Releases](https://github.com/birdslikewires/openframe-firmware/releases) page (26.11.22 is the
latest); `releases/MD5SUMS` lists their md5s. The images are not kept in git. Releases from
26.11.16 on differ from the previous one in block 9 only, and the scripts in
`tools/flash/` write just that block, after checking the chip holds the previous release. Flashing
the firmware hub can leave a board that doesn't boot; recovery means swapping the chip, so keep a
dump of your own chip (`dd if=/dev/fh of=mine.bin bs=64k`) and read [docs/process.md](docs/process.md)
first.

## Repository

| Path | What |
|---|---|
| `fh/` | The firmware hub kernel module: reads and writes the chip through `/dev/fh` |
| `driver/` | BlwPlatformDxe (`ucode_late.asm` and includes), BlwMac.efi, BlwMmcFix.efi and MmcTest.efi, the one-off BlwLanMacDel.efi, FvDiag |
| `tools/` | Image builders, emulators for the driver and apps, NVRAM and firmware-volume decoders, unit scripts |
| `tools/flash/` | The block-9 flash script for each release |
| `releases/` | Where release images go when downloaded or built (see its README); their md5s |
| `docs/` | [process.md](docs/process.md) how releases are made and tested · [releases.md](docs/releases.md) every release · [nvram.md](docs/nvram.md) NVRAM and its rules · [history.md](docs/history.md) the original lab notebook |
| `logo/` | The bird, as SVG and the rendered splash |
| `mmtool/` | Module listings of the stock OpenFrame images surveyed |
| `third_party/` | Intel's microcode file for the Atom Z5xx |

Build the latest release from the one before it (needs `nasm`; see `tools/requirements.txt`):

```
python3 -m venv venv && venv/bin/pip install -r tools/requirements.txt
curl -fLo releases/of1-blw-26.11.20.bin https://github.com/birdslikewires/openframe-firmware/releases/download/26.11.20/of1-blw-26.11.20.bin
curl -fLo releases/of1-blw-26.11.21.bin https://github.com/birdslikewires/openframe-firmware/releases/download/26.11.21/of1-blw-26.11.21.bin
venv/bin/python -I tools/mkbase.py releases/of1-blw-26.11.20.bin 26.11.21 /tmp/base.bin --core-pe /tmp/core.pe
venv/bin/python -I tools/build_ucode_driver.py /tmp/base.bin /tmp/out.bin --microcode third_party/intel-ucode/06-1c-02 \
    --bios-version 26.11.21 --bios-date 11/09/2026 --check-with-core /tmp/core.pe
cmp /tmp/out.bin releases/of1-blw-26.11.21.bin
```
