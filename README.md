# OpenFrame firmware

BirdsLikeWires firmware for the OpenPeak OpenFrame 1 (and the O2 Joggler): the stock O2 AMI Aptio
EFI firmware with a few careful additions, the tools that build and test it, and the record of how
it was done.

Releases are 1 MB images for the board's socketed SST 49LF008A firmware hub, written with `fh`, a
kernel module that gives a running OpenFrame a `/dev/fh` for the chip (it lived in `fh/` here and
is now in [openframe-kernel](https://github.com/birdslikewires/openframe-kernel/tree/main/fh), whose
kernel packages ship it). Only the
OpenFrame 1 is supported so far; nothing here has been tried on an OpenFrame 2.

This repository was `fh` until 2026-11; its history carries on here.

## What a release changes

Everything is in the main firmware volume (blocks 5–10 differ from stock); NVRAM, the NCB and the
boot block are the stock ones. From 26.11.29 our version marker and BlwPlatformDxe sit alone in
block 10, so updates write that block only and never erase stock code.

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
    (`driver/mmc_fix.inc`);
  - from 26.11.23, keeps the panel at 60 Hz: the stock north-bridge driver overwrites the display
    driver's panel clock with a fixed value that runs it at 156 Hz (`driver/panel_clock.inc`);
    from 26.11.24 that driver is changed in memory to write the right value itself, so the screen
    doesn't twitch;
  - from 26.11.25, boots 2.8 s faster: the stock MMC/SD driver asked the internal memory whether
    it was a CE-ATA disk on every boot and waited for an answer no MMC gives; it is changed in
    memory to skip that question (`driver/ceata.inc`);
  - from 26.11.26, boots up to 1 s faster with a USB drive attached: the firmware's USB code (run
    in SMM) waited 500 ms before a drive's first command, twice per drive, and 1 s between "not
    ready yet" retries; it is changed in memory as it loads to 10 ms, and to 200 ms with five
    times the tries, so a drive spinning up gets the same patience (`driver/usbrt.inc`);
  - from 26.11.27, boots with a USB drive attached that the stock firmware hung on (a 4 TB disk in
    an ASMedia caddy): the same USB code now sends a real Bulk-Only reset, gives transfers 20 s,
    asks READ CAPACITY(16) for drives over 2 TiB (and no longer wipes the opcode of a 16-byte
    command), and stops retrying a device that answers with garbage after three tries
    (`driver/usbrt.inc`);
  - from 26.11.28, starts the kernel about 0.1 s sooner from the internal memory: the stock SD
    host driver stalled 1 ms after nearly every command; its wait now polls every 10 us and then
    waits for the controller to be free, as Linux does (`driver/mmcinit.inc`, `driver/sdh_wait.inc`);
    and the internal memory is always the shell's fs0, connected before USB has any drive
    (`driver/mmc_pin.inc`);
  - from 26.11.29, lives alone in block 10 with the version marker: an update erases and rewrites
    only that block, headers last, so a power cut during one leaves a unit that boots (without our
    changes until the update is run again).
- **BlwMac.efi** (`driver/blwmac.asm`) runs from `boot.nsh` and keeps that MAC in NVRAM, writing
  only when it changes. openframe-linux ships it and feeds it the right MAC.
- **BlwMmcFix.efi** (`driver/blwmmcfix.asm`) is the same MMC workaround as a shell app, for
  firmware without it: run from `boot.nsh` before the kernel, it lets the shell start a kernel
  from the internal memory. **MmcTest.efi** (`driver/mmctest.asm`)
  shows whether large reads come back right, and **MmcProbe.efi** (`driver/mmcprobe.asm`) times
  the firmware's SD/MMC initialisation command by command.

The firmware never writes NVRAM itself, and nothing here adds a variable under a new vendor GUID:
AMI's NVRAM code corrupts the store when that happens ([docs/nvram.md](docs/nvram.md)).

## Using a release

Each version's image is a download on this repository's
[Releases](https://github.com/birdslikewires/openframe-firmware/releases) page (26.11.29 is the
latest); `releases/MD5SUMS` lists their md5s. The images are not kept in git. Releases from
26.11.16 to 26.11.28 differ from the previous one in block 9 only; 26.11.29 moves our files to
block 10 (blocks 9 and 10, once), and later releases differ in block 10 only. The scripts in
`tools/flash/` write just those blocks, after checking the chip holds the previous release. Flashing
the firmware hub can leave a board that doesn't boot; recovery means swapping the chip, so keep a
dump of your own chip (`dd if=/dev/fh of=mine.bin bs=64k`) and read [docs/process.md](docs/process.md)
first.

## Repository

| Path | What |
|---|---|
| `fh/` | A pointer: the firmware hub kernel module (`/dev/fh`, `/dev/fhp`) moved to openframe-kernel on 2026-11-13 |
| `driver/` | BlwPlatformDxe (`ucode_late.asm` and includes), BlwMac.efi, BlwMmcFix.efi and MmcTest.efi, the one-off BlwLanMacDel.efi, FvDiag |
| `tools/` | Image builders, emulators for the driver and apps, NVRAM and firmware-volume decoders, unit scripts |
| `tools/flash/` | The flash script for each release (block 9 up to 26.11.28; the move to block 10; block 10 from then on) |
| `releases/` | Where release images go when downloaded or built (see its README); their md5s |
| `docs/` | [process.md](docs/process.md) how releases are made and tested · [releases.md](docs/releases.md) every release · [nvram.md](docs/nvram.md) NVRAM and its rules · [history.md](docs/history.md) the original lab notebook |
| `logo/` | The bird, as SVG and the rendered splash |
| `mmtool/` | Module listings of the stock OpenFrame images surveyed |
| `third_party/` | Intel's microcode file for the Atom Z5xx |

Build the latest release from the one before it (needs `nasm`; see `tools/requirements.txt`):

```
python3 -m venv venv && venv/bin/pip install -r tools/requirements.txt
curl -fLo releases/of1-blw-26.11.28.bin https://github.com/birdslikewires/openframe-firmware/releases/download/26.11.28/of1-blw-26.11.28.bin
curl -fLo releases/of1-blw-26.11.29.bin https://github.com/birdslikewires/openframe-firmware/releases/download/26.11.29/of1-blw-26.11.29.bin
venv/bin/python -I tools/mkbase.py releases/of1-blw-26.11.28.bin 26.11.29 /tmp/base.bin --core-pe /tmp/core.pe --move-to-block10
venv/bin/python -I tools/build_ucode_driver.py /tmp/base.bin /tmp/out.bin --microcode third_party/intel-ucode/06-1c-02 \
    --bios-version 26.11.29 --bios-date 11/13/2026 --check-with-core /tmp/core.pe \
    --previous releases/of1-blw-26.11.28.bin --expect-blocks 9,10
cmp /tmp/out.bin releases/of1-blw-26.11.29.bin
```
