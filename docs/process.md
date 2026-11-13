# Making a firmware release

The cycle every release since 26.11.17 has been through. Each step catches something the others
can't, so none of them is optional. Hardware steps assume the OpenFrame 1 test unit booted from an
openframe-linux stick, reachable over SSH (`tools/of.sh`), with a smart plug for power.

## The rules

- **Never write NVRAM (block 0) or the boot block (blocks 14-15) with a flash.** Releases change
  block 9 only. `tools/repair-block0.sh` exists for one emergency and needs a known-good image.
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
    --check-with-core /tmp/core.pe
```
`mkbase.py` swaps the version marker and removes the old driver; `build_ucode_driver.py` appends
the new driver and checks the firmware's own decompressor accepts it. Both must report
"64K blocks changed: [9]". Record the md5 of the image, of blocks 1-15 and of block 9, and add the
image to `releases/MD5SUMS`.

## 6. Stage the flash script

Copy `tools/flash/flash-block9-<prev>.sh` to `flash-block9-<new>.sh` and change the image name, the
four hashes (image; blocks 1-15 before = the previous release; after = the new one; block 9) and
the version labels. Diff the two: nothing else may differ. The script refuses to flash unless the
chip holds exactly the previous release, writes block 9 only, then reads the whole chip back twice.

## 7. Flash (only with the owner's OK)

The unit needs the `fh` module. OpenFrame kernels built after 2026-11-10 include it; for an older
kernel build it with `make -C fh` against that kernel, or point the scripts at a built copy with
`FH_KO`. `fh` only reads unless it's loaded with `writable=1`, and even then refuses to change
NVRAM or the boot block without `unprotect=1`; the flash script loads it with `writable=1` only.

```
scp releases/of1-blw-<new>.bin tools/flash/flash-block9-<new>.sh tools/lockregs.py of@unit:/tmp/
sudo python3 /tmp/lockregs.py          # all 01, or reboot first
sudo bash /tmp/flash-block9-<new>.sh   # must end "FLASH VERIFIED"; keeps /tmp/post1.bin
```
Keep the post-flash dump and a copy of block 0 (`head -c 65536 /tmp/post1.bin`) for the NVRAM
diff after the boot.

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
of the release it is built on plus `-devN`, counting up (`26.11.26-dev1`, `-dev2`, ...), so it is
never mistaken for a release, and never publish it.

Until 2026-11-12 development builds were numbered 26.11.90-99 and then counted down from .89; some
pushed commits, test-unit dumps and scripts carry those numbers. They are, in order:

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

```
venv/bin/python -I tools/build_ucode_driver.py /tmp/base.bin /tmp/of1-blw-26.11.26-dev1-timing.bin \
    --microcode third_party/intel-ucode/06-1c-02 --bios-version 26.11.26-dev1 --bios-date MM/DD/YYYY \
    --check-with-core /tmp/core.pe --define TIMING --pe /tmp/tdxe.efi --app /tmp/tapp.efi
venv/bin/python -I tools/emu_platform.py /tmp/tdxe.efi /tmp/tapp.efi --timing
# on the unit, after a cold boot (efivarfs mounted):
#   cp /sys/firmware/efi/efivars/BlwBootTimes-ec87d643-eba4-4bb5-a1e5-3f3e36b20da9 /tmp/bt
venv/bin/python -I tools/boot_times.py /tmp/bt /tmp/of1-blw-26.11.26-dev1-timing.bin
```
A release build without `--define` is unchanged by any of this (byte for byte).
