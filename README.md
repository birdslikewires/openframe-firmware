# OpenFrame firmware

BirdsLikeWires firmware for the OpenPeak OpenFrame 1 (and the O2 Joggler) and OpenFrame 2: the
stock AMI Aptio EFI firmware (O2's on the OpenFrame 1, Telio's on the OpenFrame 2) with a few
careful additions, the tools that build and test it, and the record of how it was done.

Releases are 1 MB images for the board's socketed SST 49LF008A firmware hub, written with `fh`, a
kernel module that gives a running OpenFrame a `/dev/fh` for the chip (in
[openframe-kernel](https://github.com/birdslikewires/openframe-kernel/tree/main/fh), whose kernel
packages ship it). A chip that doesn't boot can be written in another OpenFrame 1: see
[docs/chip-swap.md](docs/chip-swap.md).

From 26.11.32 each release has an image for each model, `of1-blw-<version>.bin` and
`of2-blw-<version>.bin`. There is one OpenFrame 2 image for every OpenFrame 2: an update writes
only our blocks 10 and 11, so each unit keeps its own stock blocks 1-9, its display driver
included. The image's own blocks 1-9 are Telio's, whose Sharp display driver drives both the
Sharp and the Samsung panel.

This repository was `fh` until 2026-11; its history carries on here.

## What a release changes

Everything is in the main firmware volume; NVRAM, the NCB and the boot block are the stock ones.
From 26.11.31 (the block-11 layout, [docs/flash-order.md](docs/flash-order.md)) our version marker
and BlwPlatformDxe sit alone in block 11, block 10 holds only the signposts (spacer headers) the
firmware's file walk follows to them, and an update writes those two blocks and nothing else, in
an order where a power cut at any moment leaves a unit that starts. Up to 26.11.30 our files were
in block 10.

- **The shell's countdown is 0**, so `boot.nsh` runs straight away.
- **The boot logo** is the BirdsLikeWires pecking bird: in the OpenFrame 1 image's display driver,
  and put into a unit's own display driver in memory as it loads, since an update keeps that
  driver (O2 OpenFrame 1s and Cisco, Telio and Avaya OpenFrame 2s, and from 26.11.32 the OpenPeak
  OpenFrame 2 and the Verizon and Telstra OpenFrame 1s too).
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
    changes until the update is run again);
  - from 26.11.30, a USB drive with boot files is the source of truth: if a FAT volume on a USB
    drive has `\startup.nsh`, the shell makes it fs0, ahead of the internal memory, so its own
    `startup.nsh` runs and its files are found first, whatever is on the internal memory. The
    shell's device-naming sort always put USB drives after the internal memory; for that boot it
    is turned round, in memory as the shell loads (14 bytes of its code, only where they are the
    stock ones). A USB drive without `startup.nsh` changes nothing (`driver/usb_first.inc`).
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

Each version's images are downloads on this repository's
[Releases](https://github.com/birdslikewires/openframe-firmware/releases) page (only the latest is
offered: 26.11.32, the first with an OpenFrame 2 image); `releases/MD5SUMS` lists their md5s. The images are not kept in git.
`tools/of-firmware-update` brings a unit to a release of the block-11 layout (26.11.31 on) from
stock O2, Verizon or Telstra firmware or an earlier release (26.11.29 on) on an OpenFrame 1, or from
stock Cisco, Telio, Avaya or OpenPeak firmware on an OpenFrame 2, writing only blocks 9-11
(the plan `tools/fwplan.py` makes and proves first), and refuses anything else. `tools/flash/` holds
the scripts 26.11.29 and 26.11.30 were tested with (the old layout). (Releases 26.11.15 to 26.11.28 were withdrawn on 2026-11-13: they were only
ever on test units. Their record stays in [docs/releases.md](docs/releases.md).) Flashing
the firmware hub can leave a board that doesn't boot; recovery means swapping the chip, so keep a
dump of your own chip (`dd if=/dev/fh of=mine.bin bs=64k`) and read [docs/process.md](docs/process.md)
first.

## Repository

| Path | What |
|---|---|
| `driver/` | BlwPlatformDxe (`ucode_late.asm` and includes), BlwMac.efi, BlwMmcFix.efi and MmcTest.efi, the one-off BlwLanMacDel.efi, FvDiag |
| `tools/` | Image builders, emulators for the driver and apps, NVRAM and firmware-volume decoders, unit scripts |
| `tools/of-firmware-update` | The updater: brings a unit from any firmware in `tools/firmware-manifest.txt` (releases from 26.11.29 on; stock O2, Verizon, Telstra on an OpenFrame 1; stock Cisco, Telio, Avaya, OpenPeak on an OpenFrame 2) to a release of the block-11 layout, writing only blocks 9-11; refuses firmware it doesn't know and saves a dump, but leaves alone a newer release of ours than it knows (an older stick or package on a chip a later one updated). `tools/mkmanifest.py` makes the manifest, `tools/test_of_firmware_update.sh` tests it on fake chips |
| `tools/fwplan.py` | Plans the updater's writes from what is on the chip (stock, either layout, or a run cut off anywhere) and proves every state they pass through starts, with the firmware walkers' own rules, before anything is written. `tools/test_fwplan.py` cuts its plans off at hundreds of points; `tools/emu_fvwalk.py` runs them on the firmware's real PEI and DXE code |
| `tools/flash/` | The block-10 flash scripts 26.11.29 and 26.11.30 were tested with (the old layout; not for later releases) |
| `releases/` | Where release images go when downloaded or built (see its README); their md5s |
| `docs/` | [process.md](docs/process.md) how releases are made and tested · [releases.md](docs/releases.md) every release · [nvram.md](docs/nvram.md) NVRAM and its rules · [history.md](docs/history.md) the original lab notebook |
| `logo/` | The bird, as SVG and the rendered splash |
| `mmtool/` | Module listings of the stock OpenFrame images surveyed |
| `third_party/` | Intel's microcode file for the Atom Z5xx |

Build a release from the stock O2 firmware (needs `nasm`; see `tools/requirements.txt`; the stock
image is [of1_efi_o2.img](https://openbeak.net/openframe/firmware/efi/of1_efi_o2.img), md5
`eeb32d85e8b8c63ccc00429e083a88b8`). From 26.11.31, with `<new>` its version (or `<new>-devN` for a
development build):

```
python3 -m venv venv && venv/bin/pip install -r tools/requirements.txt
venv/bin/python -I tools/build_patched.py of1_efi_o2.img /tmp/base.bin \
    --logo logo/logo-blw-pecking-bird-204x82.png --pos 274,199 --block11 \
    --version 'BirdsLikeWires OpenFrame EFI <new> (OF1)' --core-pe /tmp/core.pe
venv/bin/python -I tools/build_ucode_driver.py /tmp/base.bin /tmp/out.bin --microcode third_party/intel-ucode/06-1c-02 \
    --bios-version <new> --bios-date MM/DD/YYYY --check-with-core /tmp/core.pe
```
Blocks 1-9 and 12-15 come out as 26.11.30's (block 9 but for its signpost), so a unit on 26.11.30
is updated in blocks 9-11 only, and a unit on stock firmware keeps its own blocks 1-9.

The OpenFrame 2 image is built from the stock Telio firmware
([of2_efi_telio.img](https://openbeak.net/openframe/firmware/efi/of2_efi_telio.img), md5
`188c50384cb4ec378cdd3f82812d6276`) with nothing of it changed: no shell patch (the OpenFrame 2's
shell is another build, changed in memory by BlwPlatformDxe) and no logo (the bird is drawn in
memory too). Only what's added after its last file differs.

```
venv/bin/python -I tools/build_patched.py of2_efi_telio.img /tmp/base2.bin --no-shell --block11 \
    --version 'BirdsLikeWires OpenFrame EFI <new> (OF2)' --core-pe /tmp/core2.pe
venv/bin/python -I tools/build_ucode_driver.py /tmp/base2.bin /tmp/out2.bin --microcode third_party/intel-ucode/06-1c-02 \
    --bios-version <new> --bios-date MM/DD/YYYY --check-with-core /tmp/core2.pe
```

26.11.30 itself was built the same way with `--block10` in place of `--block11` (and `--version
'BirdsLikeWires OpenFrame EFI 26.11.30 (OF1)' --bios-version 26.11.30 --bios-date 11/13/2026`),
which reproduces it byte for byte.

## Licence

Our own work here is under the MIT licence (see [LICENSE](LICENSE)): the driver and apps in
`driver/`, the tools, the package scripts, the docs and the bird in `logo/`.

The firmware itself is not ours. Each release image is the stock AMI Aptio firmware of an
OpenFrame (O2's for the OpenFrame 1, Telio's for the OpenFrame 2) with our driver and changes added,
so most of every image is the work of American Megatrends and the OpenFrame's makers and keeps their
terms; only what we add is under the MIT licence. Also not ours:

- `third_party/intel-ucode/`: Intel's microcode for the Atom Z5xx, under Intel's licence, which is
  beside it.
- `mmtool/`: listings of the stock images, made with AMI's MMTool. The module names and GUIDs in
  them are AMI's.
- `tools/fhprobe.c` is a Linux kernel module, so it's under the GPL version 2, like the kernel.
