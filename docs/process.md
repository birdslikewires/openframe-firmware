# Making a firmware release

The cycle every release since 26.11.17 has been through. Each step catches something the others
can't, so none of them is optional. Hardware steps assume the OpenFrame 1 test unit booted from an
openframe-linux stick, reachable over SSH (`tools/of.sh`), with a smart plug for power.

## The rules

- **Never write NVRAM (block 0) or the boot block (blocks 14-15) with a flash.** Releases change
  block 10 only (block 9 up to 26.11.28; 26.11.29 was the one move, blocks 9 and 10), and only
  in the order step 6 describes: a power cut at any point must leave a chip that boots. `tools/repair-block0.sh` exists for one emergency and needs a known-good image.
- **Never create an NVRAM variable under a vendor GUID the store doesn't already have.** AMI's
  NVRAM code half-writes the store when it adds a GUID (see [nvram.md](nvram.md)). Our variables
  use AMI's own `EC87D643-…` GUID.
- **One change per release**, and one release per flash, unless each change has first been tested
  on its own as a development build (26.11.27's five USB fixes, 26.11.28's two MMC changes).
  26.11.1 changed three things at once, didn't boot, and took a chip swap to find which
  ([history.md](history.md)).
- **After any boot in which our apps wrote NVRAM, reboot before flashing.** `BlwMac.efi` locks
  flash blocks 1-15 down before it writes, and lock-down only clears at reset; a flash then fails
  with `fwhub_unlock error … lock=0x03` (harmlessly: nothing is written).
- **Shut down cleanly before cutting power** (ext4 root, FAT `/boot`): `sync`, `systemctl
  poweroff`, wait until the unit stops answering ping and then ten seconds more, then switch off.

## 1. Set up

```
python3 -m venv venv && venv/bin/pip install -r tools/requirements.txt
```
Plus `nasm`. Run tools as `venv/bin/python -I tools/<tool>.py`.

## 2. Make the change

- Firmware behaviour: `driver/ucode_late.asm` (BlwPlatformDxe) and its includes.
- SMBIOS tables: `smbios_blobs()` in `tools/build_ucode_driver.py`. Anything per-unit is a
  fixed-size placeholder there that the driver fills in at boot, so the table length never changes.
- The NVRAM app: `driver/blwmac.asm` (BlwMac.efi, shipped by openframe-linux).

Use only documented hardware interfaces, and say in a comment where each one is documented.

## 3. Emulate, then try to break it

```
venv/bin/python -I tools/build_ucode_driver.py <base> /tmp/t.bin --microcode third_party/intel-ucode/06-1c-02 \
    --bios-version X --bios-date MM/DD/YYYY --pe /tmp/dxe.efi --app /tmp/app.efi
venv/bin/python -I tools/emu_platform.py /tmp/dxe.efi /tmp/app.efi       # BlwPlatformDxe
venv/bin/python -I tools/build_blwmac_app.py /tmp/BlwMac.efi
venv/bin/python -I tools/emu_blwmac.py /tmp/BlwMac.efi                   # BlwMac.efi
venv/bin/python -I tools/emu_mmcinit.py releases/of1-blw-<prev>.bin driver/mmcinit_sig.inc  # SdHost's wait
venv/bin/python -I tools/emu_mmcfix.py <firmware.bin> <BlwMmcFix.efi> <MmcTest.efi>     # BlwMmcFix.efi
```

The emulators run the real code under Unicorn against a fake firmware and fail on anything the
code mustn't do (any SetVariable from the driver, port I/O before PCI is up, unrestored registers,
leaked pool, an MSR read on the wrong CPU, writes to the flash...). Add a scenario for every new
behaviour and every way its input can be odd.

Then **mutation-test**: break each new check on purpose (flip a comparison, drop a call), rebuild,
and confirm some scenario fails. A check no scenario notices isn't tested. Keep the mutations as a
file in `tools/mutations/` and run them with `tools/mutate.py` (each in its own copy of the tree, so
the source is never touched), so they can be run again after later changes.

## 4. Check the SMBIOS table with the real dmidecode

```
venv/bin/python -I tools/build_ucode_driver.py ... --smbios-out /tmp/smbios.bin
venv/bin/python -I tools/smbios_dump.py /tmp/smbios.bin /tmp/dump.bin --mac 00:0e:8e:12:34:56 --mem 512,533
dmidecode --from-dump /tmp/dump.bin        # on the unit, or any Linux box
```
Check both the filled and the unfilled (unknown) forms. For an unchanged table, the filled dump
matches `dmidecode --dump-bin` from a unit running the release byte for byte.

## 5. Build the release

The previous release comes from GitHub Releases (`gh release download <prev> -D releases/`, or the
download link); check it against `releases/MD5SUMS`.

```
venv/bin/python -I tools/mkbase.py releases/of1-blw-<prev>.bin <new> /tmp/base.bin --core-pe /tmp/core.pe
venv/bin/python -I tools/build_ucode_driver.py /tmp/base.bin releases/of1-blw-<new>.bin \
    --microcode third_party/intel-ucode/06-1c-02 --bios-version <new> --bios-date MM/DD/YYYY \
    --check-with-core /tmp/core.pe --previous releases/of1-blw-<prev>.bin --expect-blocks 10
```
`mkbase.py` swaps the version marker and removes the old driver; `build_ucode_driver.py` appends
the new driver, checks the firmware's own decompressor accepts it, and refuses to write the image
unless the blocks that differ from the previous release are exactly `--expect-blocks`. From 26.11.29
our files are alone in block 10, so a release changes block 10 only. (26.11.29 itself was the one
move: `mkbase.py --move-to-block10` and `--expect-blocks 9,10`; up to 26.11.28 it was block 9.)
Run `tools/emu_platform.py` on the driver (`--pe`, `--app`). Then build the same release from
the stock O2 image (`tools/build_patched.py --block10`, as in the README) and check it comes out
byte for byte the same: the chain of releases must never depend on a binary only we hold. Record the md5 of the image, of blocks
1-15 and of block 10, check blocks 1-9 and 11-15 are still `d11b766a…`, and add the image to
`releases/MD5SUMS`.

## 6. Stage the flash script

Copy `tools/flash/flash-block10-<prev>.sh` to `flash-block10-<new>.sh` and change the version labels
(`NEW`, `FROM`, the header) and the hashes: the image's md5; `FROM10` and `NEW10`, block 10 of each.
`OUT10` (blocks 1-9 and 11-15) stays `d11b766a…`. Diff the two: nothing else may differ. Test it on a
fake chip (`FH=/tmp/fake.bin`, a copy of the previous release, as root) including
`FLASH_TEST_FAULT=1` (repaired on a retry), `=3` (exit 2, then a re-run completes) and
`FLASH_TEST_STOP=1` and `=2` (a power cut after each step: a re-run completes).

The script refuses unless blocks 1-9 and 11-15 are the block-10 layout's, checks the lock registers,
the kernel's panel fix and that fh has `/dev/fhp`, then writes block 10 in three steps, each read
back twice: erase and write both files' bodies with both headers blank (the chip then boots like
stock firmware: the firmware's file list stops at a blank header); the driver's header; the
marker's header, State byte last (the last two through `/dev/fhp`, programming without an erase).
Exit status: 0 verified (or already there); 1 refused, nothing written (rebooting is safe); 2 not
verified (**don't reboot**: run it again, it starts again from step 1). `FH_KO=` points it at an fh.ko
with `/dev/fhp` if the installed one has none.

Releases reach users through `tools/of-firmware-update` (from stock firmware or any earlier
release; it writes block 10 the same way). After a release, regenerate its manifest with
`tools/mkmanifest.py` (every release from 26.11.29 on, plus the stock images) and run
`tools/test_of_firmware_update.sh` against it. The flash scripts for releases before 26.11.29
were removed with those releases on 2026-11-13.

## 7. Flash (only with the owner's OK)

The unit needs the `fh` module (openframe-kernel's `fh/`). OpenFrame kernels
built after 2026-11-10 include it, with `/dev/fhp` (needed by block-10 updates) from the first build
after 2026-11-13; for another kernel build it out of tree against that kernel (its README), or point
the scripts at a built copy with `FH_KO`. `fh` only reads unless it's loaded with `writable=1`, and even then refuses to change
NVRAM or the boot block without `unprotect=1`; the flash script loads it with `writable=1` only.

```
scp releases/of1-blw-<new>.bin tools/flash/flash-block10-<new>.sh tools/lockregs.py of@unit:/tmp/
sudo python3 /tmp/lockregs.py          # all 01, or reboot first
sudo bash /tmp/flash-block10-<new>.sh  # exit 0 and "FLASH VERIFIED"; prints where it kept its dumps
```
Keep the post-flash dump (`post1.bin` in the directory it names) and a copy of its block 0
(`head -c 65536 …/post1.bin`) for the NVRAM diff after the boot.

## 8. Cold boot

Clean shutdown, power off, ten seconds, power on (`tools/timeboot.sh` does this and times it).
The unit should answer ping about 25 s after power-on (more with a USB drive that has to spin up).

## 9. Check

- `dmesg`: `efi: EFI v2.0 by BirdsLikeWires`, `DMI: OpenPeak OpenFrame 1, BIOS <new> <date>`,
  `DMI: Memory slots populated: 1/1`, `microcode: Current revision: 0x00000217` (both threads).
- `sudo dmidecode`: the types the release changed, plus Type 1 serial/UUID and Type 11 BootCount.
- `tools/blwmac-check.sh`: eth0's MAC at probe, `BlwMacStatus` (no write: flag 0x10 set), lock
  registers 01, and block 0 read twice. Diff it against the pre-boot copy: only the
  `MonotonicCounter` record (14 bytes) may change, unless the boot stored a new MAC.
- The boot log has no fsck or dirty-bit messages.

## 10. Record and publish

Add the release to [releases.md](releases.md) (what changed, how it was tested, the hashes, the
hardware results) and its md5 to `releases/MD5SUMS`, and commit. Then publish the image as a
GitHub Release, tagged with the version (the images are kept out of git so that the source and its
history don't share their fate):

```
gh release create <new> releases/of1-blw-<new>.bin --title "<new> (OF1)" --notes "<the releases.md entry>"
```
Only then tell anyone it exists.

## Development builds: boot timing

`--define TIMING` builds BlwPlatformDxe with `driver/timing.inc`: it records (with the CPU's
timestamp counter) its own entry, the images already loaded by then (in order), each driver
loaded after it, protocols appearing (graphics, BlockIo, file systems, USB, console, PCI), the
boot manager's stage signals and ReadyToBoot, and leaves them in the volatile variable
`BlwBootTimes` (BS+RT, no NV bit: nothing reaches the flash). Give a development build the version
of the release it is working towards plus `-devN`, counting up: after 26.11.28 is released, the
builds towards the next one are `26.11.29-dev1`, `-dev2`, ... Pass that full version to both
`mkbase.py` (it goes into the version marker) and `--bios-version`. A `-devN` is never mistaken for a
release; never publish one. If the work ends up released under a different number, say so in
releases.md.

Until 2026-11-13 a development build carried the version of the release it was built ON instead
(26.11.27-dev1 to -dev6 were the builds that became 26.11.28; the table below keeps those names),
and until 2026-11-12 development builds were numbered 26.11.90-99 and then counted down from .89;
some pushed commits, test-unit dumps and scripts carry those numbers. They are, in order:

| then | now | what |
|---|---|---|
| 26.11.90 | 26.11.24-dev1 | boot timing (`TIMING`): found the MMC's CE-ATA probe (26.11.25) |
| 26.11.93 | 26.11.25-dev1 | + `PROFILE` |
| 26.11.94 | 26.11.25-dev2 | + `USBTRACE` |
| 26.11.95 | 26.11.25-dev3 | USBRT's waits changed in memory (became 26.11.26) |
| 26.11.97 | 26.11.26-dev1 | + `SCREEN` (accepted a framebuffer base of 0: drew into low memory; don't boot it) |
| 26.11.98 | 26.11.26-dev2 | screen trace, framebuffer re-read before every draw |
| 26.11.99 | 26.11.26-dev3 | + the last 16 SCSI opcodes, USBRT's sense codes |
| 26.11.91 | 26.11.26-dev4 | START UNIT experiment (disproved; removed) |
| 26.11.92 | 26.11.26-dev5 | `usbrt.inc` D (a real Bulk-Only reset), E (20 s transfers) |
| 26.11.96 | 26.11.26-dev6 | + control transfers, USBRT's error log on the screen |
| 26.11.89 | 26.11.26-dev7 | every transfer drawn as it starts, with its port |
| 26.11.88 | 26.11.26-dev8 | `usbrt.inc` F (READ CAPACITY(16)), G (CErr = 3) |
| 26.11.87 | 26.11.26-dev9 | F's count on the screen: F fired, but its command lost its opcode |
| - | 26.11.26-dev10 | `usbrt.inc` H (the command-block builder's clear) |
| - | 26.11.27-dev1 | `mmcinit.inc`: SdHost's command wait polls every 4 us; MmcMediaDevice waits 1 ms, not 50, after CMD0 (its GRUB fallbacks were of-bootnsh's, fixed in openframe-linux of-bootnsh v26.11.8) |
| - | 26.11.27-dev2 | `-DMMI_CMD0_ONLY`: just the CMD0 waits (no measurable gain on its one direct boot) |
| - | 26.11.27-dev3 | `-DMMI_INPLACE`, 64 us polls (built, not flashed) |
| - | 26.11.27-dev4 | the CMD0 waits left alone: SendCommand's wait replaced by the driver's `sdh_wait` (10 us polls, then waits for Command Inhibit (CMD/DAT) to clear, capped): kernel start 5.24 s vs 5.35 s |
| - | 26.11.27-dev5 | dev4 + the CMD0 waits (`-DMMI_CMD0` since): 5.24 s, no gain, so not in releases |
| - | 26.11.27-dev6 | dev4 + `mmc_pin.inc` (MMC connected at the first BDS stage: always fs0); a TIMING build showed the MMC's file system at 2.34 s, the stick's at 4.08 s; kernel start 5.24-5.25 s |
| - | 26.11.30-dev1 | `usb_first.inc`, first try: the internal memory's partitions disconnected and connected again after a USB drive with `startup.nsh` (no change on hardware) |
| - | 26.11.30-dev2 | dev1, TIMING with `usb_first`'s records (unreadable: this kernel has no efivarfs) |
| - | 26.11.30-dev3 | dev2 + its summary over the SMBIOS boot count: everything ran and returned success, nothing changed (DisconnectController leaves the partitions) |
| - | 26.11.30-dev4 | the drive moved in the core's handle list (read back first; the shell's numbering unchanged) |
| - | 26.11.30-dev5 | the drive moved in SimpleFileSystem's list of instances (read back first; unchanged: the shell sorts its mapping) |
| - | 26.11.30-dev6 | the shell's mapping sort turned round in memory: the stick is fs0 (became 26.11.30) |

```
venv/bin/python -I tools/build_ucode_driver.py /tmp/base.bin /tmp/of1-blw-26.11.26-dev1-timing.bin \
    --microcode third_party/intel-ucode/06-1c-02 --bios-version 26.11.26-dev1 --bios-date MM/DD/YYYY \
    --check-with-core /tmp/core.pe --define TIMING --pe /tmp/tdxe.efi --app /tmp/tapp.efi
venv/bin/python -I tools/emu_platform.py /tmp/tdxe.efi /tmp/tapp.efi --timing
# on the unit, after a cold boot (efivarfs mounted):
#   cp /sys/firmware/efi/efivars/BlwBootTimes-ec87d643-eba4-4bb5-a1e5-3f3e36b20da9 /tmp/bt
venv/bin/python -I tools/boot_times.py /tmp/bt /tmp/of1-blw-26.11.26-dev1-timing.bin
```
A release build without `--define` is unchanged by any of this (byte for byte). OpenFrame kernels are
built without efivarfs, so BlwBootTimes can't be read on them; the TIMING build also writes a 10-character
summary of `usb_first.inc` over the SMBIOS Type 11 boot count, which can (`sudo cat
/sys/firmware/dmi/tables/DMI | tr -c '[:print:]' '\n' | grep BootCount`; usb_first.inc says what each
character means).
