# How it got here: the lab notebook

The working notes from the first days (2026-11-07 and 08), kept as written: a survey of every
known OpenFrame firmware image, the first patched images, a release that didn't boot (26.11.1) and
how the cause was found (microcode 0x217 loaded at reset hangs this firmware), and the late-loading
driver that became BlwPlatformDxe. Later releases are in [releases.md](releases.md).

Paths are from the original working tree: `firmware/efi-builds/` held every build (only releases
are kept, in `releases/`), `firmware/efi-dumps/` the test unit's dumps (kept privately),
`firmware/original/` the stock images surveyed. "Andy" is the project's owner; the "test unit" and
"spare" are his two OpenFrame 1s.

# OpenFrame EFI firmware (started 2026-11-07)

## WHERE WE LEFT OFF (2026-11-07, end of session)

**RESOLVED late 2026-11-07: the test unit boots again on v2** ("Woo!" logo). The 26.11.1 chip was hot-swapped into the running spare, its blocks 1–12 written from `pre-v3.bin`, and the whole chip verified (md5 `f339886a…`). Everything below is history; the next step is the "one change per flash" list.

**Microcode 0x217 tested from Linux (early load), 2026-11-07:** the exact blob from 26.11.1 (image offset 0x10060), loaded as an early-microcode cpio via a second `initrd=` in boot.nsh. The CPU took it on both threads (0x20a → 0x217), the kernel's Atom PSE erratum warning went away, and there was no MCE under load. So the microcode is very likely innocent and the splash size is the prime suspect. The cpio stays at `/boot/ucode-0x217.cpio` on the stick; boot.nsh is back to normal.

**UPDATE 2026-11-07 evening: hot-swap failed 3/3 on the spare's board** (but the jogglerwiki forum reports success with the same fh+dd method, loading fh BEFORE the swap). Tested on the spare OF1 (open, booted from the stick):
any chip pressed into the socket while the board is running reads all `FF`, ID requests included. That held for 3 of 3
insertions, among them the spare's own known-good chip, which had dumped cleanly before the swap (md5 `1649649d…`,
pure stock O2 apart from NVRAM, `firmware/efi-dumps/of1-spare-20261107.bin`). After a reboot with that chip left in
place it reads perfectly: `BF 5A`, and blocks 1–15 match the dump. So the socket and chip are fine and a live-inserted
chip just never answers. The likely cause is that it misses power-on/reset initialisation; the datasheet says the IC
(interface mode) pin is sampled at power-up. The mystery loose 49LF008A also read `FF` live, so it is **untested, not
dead**. Read-only probe module: `tools/fhprobe.c`. **Then it WORKED the wiki's way:** fh loaded (SST detected) on the good chip and LEFT LOADED, mystery chip pressed in squarely in one go, and it read `BF 5A` with two matching dumps (`firmware/efi-dumps/of1-mystery-20261107.bin`, md5 `bfe7ab00…`). The mystery chip is stock `of1_efi_openpeak_verizon` apart from NVRAM. So: load fh BEFORE the swap and seat the chip in one press. Which of the two mattered isn't known. Fallback if it ever fails: an external programmer. The XGecu TL866II
Plus device list (V12.63) has `SST49LF008A @PLCC32` and `N82802AC @PLCC32` (PP mode, needs the PLCC32 adapter).
The steps below are kept for the record.

**The OF1 test unit doesn't boot.** Its chip holds `of1-blw-26.11.1-DOES-NOT-BOOT.bin`. Flash and read-back verify
were perfect. At power-on: the panel's usual init flash, then black, no splash, no USB stick activity, no network.
Two power cycles gave the same result. Nothing else on the chip changed: NVRAM (block 0), NCB and boot block are as before.

**Suspects (unconfirmed), all new in 26.11.1 relative to v2, which booted:**
1. **Splash size.** 140x122 = 17,080 px = 68,320 bytes at 4 bpp, over 64 KB. Every splash proven on this exact IEGD
   driver code is ≤ 16,000 px / 64,000 bytes (Telio 250x64). op1.rom's 300x56 is in the site's *untested* folder.
   The splash *parser* is fine (0xCC00-byte limit from config start 0x13D7; our data fits). The drawing code wasn't found.
2. **Microcode 0x217**, loaded at reset in PEI. A failure there would look identical.
3. Less likely: the RAW version-marker file, or the stock O2 layout in blocks 3–4 (from a working Joggler's dump).

**Recovery (one hot-swap):**
1. Power off. Put a working chip from a spare Joggler in the test unit. Keep the 26.11.1 chip aside.
2. Boot with the USB stick in: stock firmware → shell → `startup.nsh` → stick, as usual. It comes up at link-local
   `fe80::e:8eff:fe12:3456%en0` (the unit's NIC, so the same address), user `of`/`joggler`.
3. Check `lsmod | grep fh` shows **nothing**. fh detects the chip type at load, and the borrowed chip may be Intel 82802
   rather than SST. Optionally back up the borrowed chip after loading, then `rmmod fh`.
4. Hot-swap: pull the borrowed chip (PLCC extractor) and press in the 26.11.1 chip, notch matching the socket.
5. `sudo insmod ~/fh/new/fh.ko` (built for 6.18.62, which the stick now runs). Then:
   `sudo dd if=~/pre-v3.bin of=/dev/fh bs=64k skip=1 seek=1 count=12 iflag=fullblock` and verify
   `sudo cat /dev/fh | cmp - ~/pre-v3.bin` (block 0 may differ only if the firmware wrote NVRAM; blocks 1–15 must match).
   `pre-v3.bin` is the v2 state, which boots. `~/of1-efi-a.bin` (= `firmware/efi-dumps/of1-testunit-20261107.bin`) is
   the original from the morning. Copies of both are in `firmware/efi-dumps/`.
6. Power off, then return the borrowed chip to its Joggler.

**Next, one change per flash** (the lesson from tonight):
- Test microcode 0x217 from Linux (late load) if the kernel has a microcode loader. If the CPU takes it and stays up,
  the microcode is very likely innocent.
- Flash v2 + microcode + version marker, keeping the 80x81 splash (fix its colours to RGB). If it boots, the splash size
  is the culprit.
- Then the sparrow within the proven envelope: ≤ 16,000 px, e.g. 128x125. `build_patched.py` now refuses > 16,000 px
  unless `--allow-large-splash` is given.
- Finding the IEGD drawing code would settle the real limit. The token table is at PE 0xDFE0
  (`ssp`→0x1960C, `ssd`→0x18786); the config struct's splash data is at +0x1A5C after 5 ports x 0x544.

**Environment:** `python3 -m venv venv && venv/bin/pip install uefi_firmware pillow unicorn` (plus `rsvg-convert`
from MacPorts for the logo SVG). Run the tools with `venv/bin/python -I tools/<script>`. `tools/of.sh` and
`tools/timeboot.sh` need an SSH_ASKPASS helper (see `of.sh`).

**Files:** builds and logos are in `firmware/efi-builds/`, chip dumps in `firmware/efi-dumps/`, the sparrow source
(Andy's birdslikewires.net favicon) in `logo/` and `firmware/efi-builds/logo-blw-sparrow-wire.svg`.

---

# Survey

Source: https://openbeak.net/openframe/firmware/efi/ (plus `untested/` and `do_not_use/`).
All 10 images are 1 MB AMI Aptio 4 (2008-era, EDK1) dumps for Poulsbo, from an LPC Firmware Hub (not SPI). Every MD5 on the site matches.
Images are not copied here; re-fetch them. `mmtool/` holds the site's MMTool listings and readmes.

## Layout (all images)

| Offset | Size | What |
|---|---|---|
| 0x00000 | 64 KB | NVRAM (AMI NVAR store) |
| 0x10000 | 768 KB | Main DXE volume (~50 drivers, EFI Shell, IEGD GOP) |
| 0xD0000 | 64 KB | NCB |
| 0xE0000 | 128 KB | Boot block (PEI) |

## Only four distinct builds

Comparing decompressed module bodies, not file sizes:

| Build | Images | Notes |
|---|---|---|
| OF1 | `of1_efi_o2`, `of1_efi_openpeak_verizon`, `of1_efi_telstra` | identical code; has DigitalThermalSensor, no S3Resume |
| OF2 good | `of2_efi_avaya`, `of2_efi_cisco`, `of2_efi_telio`, `of2_openpeak`, `untested/op1.rom` | identical code |
| OF2 Swisscom | `do_not_use/of2_efi_swisscom` | different build (broken ACPI per readme) |
| OF2 Telio bad | `do_not_use/of2_efi_telio_bad` | different, older build; every module differs from the good one |

Within a build, images differ only in:
1. **The IEGD GOP driver** (`2B13E5F0-E82E-4209-898A-8A53E8570DB9`, Intel `efi_fb_plb`). It embeds the
   panel config and the boot splash. Strings like `OpenFrame-Sharp` vs `OpenFrame-Samsung` (Cisco/Swisscom),
   the 800x480 DTD (Sharp 33.264 MHz, Samsung 24.5 MHz) and `sspc` splash position/size
   (O2: 80x81 at 360,191; Verizon/Telstra: 300x56 at 248,145). The splash bitmap follows `$ssd`.
   `of2_efi_telio` and `telio_bad` share the same GOP, so the lockdown is not in the GOP.
2. **The NVRAM**, which is a dump from a used unit. `MonotonicCounter` gives boot counts: O2 498,
   OF2 OpenPeak 171, Avaya 110, Telstra 48, Cisco 42, Verizon 23, Telio 21, Swisscom 2, op1 1;
   telio_bad has pristine NVRAM. Standard variables: BootOrder = `Boot0000` "Built-in EFI Shell",
   then `Boot000A` "MMC/SD Media Device". StdDefaults: `Timeout` = 1.
   OF1 vs OF2 `Setup` defaults differ in one byte (offset 0x66: 00 vs 01), not yet identified.

## Why the bad Telio EFI can't boot from USB

It's a build without AMI's USB mass-storage driver. Compared against Swisscom, its nearest relative (same old build
family, but it does boot from USB): the USB core module USBRT (`04EAAAA1…`) has 0x560 less code. The good build
registers six USB device drivers (types 1, 1, 6, 3, 4, 2 at the `mov byte [eax], N` sites). The bad build registers
the same set minus **type 4**, whose probe function (good Telio 0x10003fae) accepts interface class **0x08 Mass
Storage** with protocol 0x50 (Bulk-Only), 0x00 or 0x01. The bad USBRT has no class-0x08 test anywhere, and the
mass-storage LUN setup functions that follow it are gone too. So USB keyboards still work, but no USB stick can
appear as a disk. Internal storage is unaffected (SdHost/MmcMediaDevice). The missing EFI command line hasn't been
found yet; it's not in USBRT.

## Boot-time test result (2026-11-07): StartupDelay 0 saves nothing

Timed from smart-plug-on to sshd, minus sshd's kernel-relative start time (4 Nov GitHub image, GRUB path):
plug-on to kernel was 29.9, 29.9, 30.7 s with no `StartupDelay`, and 32.1, 31.6 s with `StartupDelay` = "0" confirmed in NVRAM
(set once via `boot.nsh`, then the line removed). No 5 s saving, so the analysis below doesn't describe what
happens on a real boot. The function runs only for the root shell (flag set when the shell environment protocol isn't
installed yet), and the loop ends as soon as ReadKeyStroke returns any key. The likeliest explanation is that
input is already waiting, so the countdown ends at once. Not proven. The +1.7 s after the change is from two samples
and is unexplained, possibly noise.

Follow-up the same day: `set StartupDelay 30` gave 33.6 s plug-on to kernel (no +30 s). But NVRAM then held **two
unlinked StartupDelay entries** ("0" and "30"), both written with GUID index 0 (`4599D26F…`). The shell uses
`47C7B224-C42A-11D2-8E57-00A0C969723B` for its env vars (found in the shell binary), and that GUID was never added to
the store's GUID list. So the firmware likely stores shell env vars under the wrong GUID, and the shell can't read
them back. That would also explain the duplicate. Conclusion: setting StartupDelay via the shell does nothing on this
firmware. Whether the 5 s countdown actually happens is still **unknown**. To settle it, patch the shell's default
(`push 0x32` at VA 0x10001856 in the OF1 shell) in an image and flash it, or film a boot with the splash disabled.

## Shell countdown (analysis from the binary; see the result above)

The built-in EFI Shell (EDK1, built from `C:\AMI\Poulsbo\OpenPeak\backup\004_12_15_2008\...`) waits before
running `startup.nsh`. Disassembly of the OF1 shell (VA 0x10001849): reads shell env var `StartupDelay`,
default 50 ticks; if set, ticks = value*10; each tick is `Stall(100000)` (100 ms); any key other than ESC
ends the wait early; 0 skips the loop entirely. So **every boot spends 5 s here, hidden behind the O2 splash**.

`SET` without `-v` stores the variable in NVRAM, so `set StartupDelay 0` once should remove it. Untested.
Options: run it once at the shell, or guard it in the script so NVRAM is written only once:
`if not "%StartupDelay%" == "0" then` / `set StartupDelay 0` / `endif` (shell-script syntax unverified on this build).

## The OF1 test unit's own chip (2026-11-07)

- Chip: **SST 49LF008A LPC Firmware Hub** (not SPI), 1 MB at 0xFFF00000, read with `fh` (`research/fh`).
  LPC bridge 00:1f.0: BIOS write enable on (0xD8 = 0x01), prefetch off (0xD9 = 0x00).
- Backup: `firmware/efi-dumps/of1-testunit-20261107.bin`, md5 `aa925667c63a40fa9cad6eed1dca9e53` (two reads matched).
- Contents: the O2 OF1 build, except that both AMI logo bitmaps (`7BB28B99…`, 568x151, and `294B1CEF…`, 400x100)
  have "Hi!" / "Woo!" drawn on them, and OEMPEI differs by 4 bytes (looks like MMTool rebasing). So this chip was reflashed
  with an MMTool-edited image before. NVRAM: boot count 1532, no `StartupDelay`.
- No SMBIOS table (dmidecode is empty). Kernel 6.18.60-openframe has `CONFIG_EFIVAR_FS` unset, so Linux can't read or
  write NVRAM variables.

## Where the visible boot logo lives

The splash on screen is **not** AMI's logo. It's an 80x81 raw 24-bit RGB bitmap inside the IEGD graphics driver
(`2B13E5F0…`), right after the `$ssd` marker (PE offset 0x1696 to `ENDSSDATA` at 0x6286 in the OF1 build). Its position
and size are in the driver's `sspc` config string (`0x0,0x168,0xBF,0x50,0x51` = x 360, y 191, 80 x 81). AMI's own logos
(`7BB28B99…` 568x151 and `294B1CEF…` 400x100 BMPs) are never shown. That's why Andy's old "Hi!"/"Woo!" edit to them
flashed fine but changed nothing on screen.

## Patched image v1 (built 2026-11-07, not yet flashed)

`tools/build_patched.py <dump> <logo80x81.png> <out>` (needs `uefi_firmware` for Tiano and Pillow):
- Shell: both `push 0x32` countdown defaults become `push 0x00`. The ALIAS help text is blanked so the Tiano-recompressed
  shell fits its original slot. The stream is padded with zeros after the real CompSize, so the file keeps its exact size.
- Graphics driver: splash pixels replaced. It's the last file in FV_MAIN, so it can change size freely.
- Compression is Tiano (EFI-flavour decompress fails). Our TianoCompress comes out ~30 bytes bigger than AMI's for the
  same input, hence the help-text trick.

`firmware/efi-builds/of1-patched-v1.bin` (md5 `6f6217d325a0041cbe374c36cf612817`), from
`firmware/efi-dumps/of1-testunit-20261107.bin` plus `test-logo-woo-80x81.png`. Checks:
- uefi-firmware-parser unpacks it with the same number of parts as the original.
- Decompressed diffs are confined to the patch sites, the ALIAS help and the splash pixels.
- All 51 FFS checksums are valid; the FV header, NVRAM and boot block are identical; free space is all FF.
- Only 64K blocks 5–9 change. Flash with `dd if=… of=/dev/fh bs=64k skip=5 seek=5 count=5`.

## Patched image v2 and how it was verified (2026-11-07)

v2 (`firmware/efi-builds/of1-patched-v2.bin`, md5 `4bb1d1d288bb41f09233c7c406085324`) replaces v1. Instead of padding,
the build blanks just enough ALIAS help (PE 0x235E2–0x23850) that the recompressed shell is exactly the original length.
The stream is then structurally identical to AMI's (CompSize + 8 = section data length).

Evidence it will load:
1. **The firmware's own decompressor accepts it.** CORE_DXE installs EFI_DECOMPRESS (and a Tiano-style GUID) with
   GetInfo 0x1000adf9 and Decompress 0x1000ae12, which call EDK1's Decompress at 0x10016273. That code tries 5-bit
   (Tiano) first and needs SrcSize >= CompSize + 8 and DstSize == OrigSize. `tools/emu_decompress.py` runs it under
   Unicorn. For v2, its output differs from the original modules only at the intended bytes: shell 206 (patch sites
   plus help), graphics 7963 (splash pixels). A one-byte-corrupted control image is flagged.
2. **FFS structure:** two parsers agree; all 51 file checksums are valid; the volume header, NVRAM and boot block are
   identical; free space is all FF.
3. **No hidden checks in the graphics driver.** Across carriers (O2, Avaya, Telio, op1.rom, Telio-bad) the IEGD
   driver is byte-identical outside Intel's CED area (`BCED_ARRAY` 0x21F to `CEDCED` 0xDFD7). Inside it, only `$sspc`
   and the splash bytes differ (Cisco/Swisscom also change panel tokens). The PE checksum field is 0. Splash data runs
   from 0x1696 into a reserve that ends at 0xDFD7 (51,525 bytes), so larger logos fit if `$sspc` is updated.
4. The `fh` erase/program sequences are the original driver's, which the community used to flash coreboot. Our
   changes only add bounds, timeouts and skipping.

Remaining unknowns: the shell's behaviour with the delay at 0 is from reading the code (same path as an ESC-less
countdown that ends at once). There's no recovery mode in this firmware that we know of.

## Flashed on the OF1 test unit (2026-11-07)

- Rehearsal: block 0xA (FV_MAIN free space) got a test pattern, then FF again. Both verified, and the whole chip
  then matched the pre-flash dump.
- Flash: `dd if=of1-patched-v2.bin of=/dev/fh bs=64k skip=5 seek=5 count=5 iflag=fullblock`, 6.3 s. Every block
  programmed and verified. The full read-back matches v2 (block 0 = pre-flash NVRAM).
  Dumps: `firmware/efi-dumps/of1-testunit-20261107-preflash.bin` and `…-v2-postflash.bin`.
- It boots. efifb comes up, so the patched IEGD driver loads. Blocks 1–15 still match v2 after two boots.
- Timing, plug-on to kernel (4 Nov image, GRUB path): **25.5 and 27.3 s** against 29.9/29.9/30.7 s stock.
  Plug-on to sshd: 44.5/44.5 against 47.4/47.6/47.9 s. So about **3–4 s saved**: the countdown was real, but
  doesn't account for the full 5 s.
- To undo: write blocks 5–9 from the pre-flash dump.

## BirdsLikeWires 26.11.1 (v3, built 2026-11-07) — FLASHED, DOES NOT BOOT (see top)

`firmware/efi-builds/of1-blw-26.11.1-DOES-NOT-BOOT.bin` (md5 `f5212ad34aa2309463b9c47678629228`), built from the **stock** O2 image
(`stock-of1_efi_o2.img`, md5 `eeb32d85…`), so Andy's old AMI-logo edits are gone:

    build_patched.py stock-of1_efi_o2.img of1-blw-26.11.1.bin --logo logo-blw-sparrow-140x122.png \
        --microcode intel-ucode-06-1c-02 --version "BirdsLikeWires OpenFrame EFI 26.11.1 (OF1, O2 base)"

- Shell countdown 0 (as in v2).
- Microcode 0x20a (2008-06-04) -> **0x217** (2009-04-10), Intel's final for sig 0x106C2 pf 0x1, same 5120-byte slot.
  It fixes the Atom PSE erratum, so Linux should stop disabling large pages ("Atom PSE erratum detected").
- Splash: Andy's birdslikewires.net favicon sparrow, white on a wire, 140x122 at 330,179 (centred).
  `$sspc01 0x0,0x168,0xBF,0x50,0x51 -> 0x0,0x14A,0xB3,0x8C,0x7A`, RGB, `ENDSSDATA` moved, rest of reserve zeroed.
  Splash limit: the reserve runs from PE 0x1696 to the second `CEDCED` (0xDFD7), 51,521 bytes = ~17,170 px.
  OpenPeak's 300x56 used 98%.
- Version marker: RAW FFS file `D1728E0B-D49F-480E-A48D-D0B4EC5768F4`, appended after the graphics driver.
- Files without a data checksum use IntegrityCheck.File = **0x5A** in this firmware (Framework FFS), not 0xAA.

Verified: the firmware's own decompressor (emulated) gives stock modules except the intended ranges; all 52 file
checksums are good; the parser sees the marker; NVRAM, NCB, boot block and FV header are identical to stock; the splash,
decoded through the firmware's decompressor, renders correctly. Blocks changed vs stock: 1, 5–9. On Andy's unit it
writes blocks 1 and 3–9 (3–4 also restore the stock layout).

## Tools

`tools/` (Python 3, stdlib; run with `-I`). Extraction used `uefi_firmware` (pip) in a scratch venv:
`uefi-firmware-parser -b -e -o <dir> <image>`.
- `nvar.py` / `nvsum.py`: decode AMI NVAR stores (follows update chains) and summarise per image.
- `modhash.py`: per-module hash of decompressed payloads across images.
- `mmtable.py`: diff MMTool listings.
- `u16strings.py`: UTF-16 strings.

## Test build v4: v2 + microcode 0x217 + version marker (built 2026-11-07, not yet flashed)

`firmware/efi-builds/of1-test-v4-ucode-marker.bin` (md5 `977305a0f750235c54a2c8ee02c767c8`). Its purpose is to clear the
microcode and the marker inside the firmware, keeping v2's splash, so a hang would mean one of those two.

    build_patched.py of1-testunit-20261107.bin of1-test-v4-ucode-marker.bin --logo test-logo-woo-80x81.png \
        --pos 360,191 --microcode intel-ucode-06-1c-02 \
        --version "BirdsLikeWires OpenFrame EFI test v4 (OF1, v2 + microcode 0x217 + marker)"

- Same base as v2 (the test unit's morning dump). `--pos 360,191` keeps v2's splash position (the default centring
  would move it to y 199).
- Against v2: one new file (the marker, at 0x914D0) and two changed files. The microcode file has the same size, and
  the 0x217 blob sits at 0x10060, byte-identical to the blob early-loaded from Linux. The graphics driver is the same
  file and offset, 3 bytes smaller compressed. Decompressed, the graphics driver differs from v2 only inside the splash pixels.
  These are now the PNG in RGB order (v2's were BGR), so "Woo!" should show yellow, not blue.
- The firmware's own decompressor (Unicorn, `emu_decompress.py`) accepts the shell and graphics streams, with no bytes
  outside the intended ranges. The build re-checks all 52 FFS checksums.
- Block 1 is byte-identical to 26.11.1's block 1, so if v4 boots, 26.11.1's microcode block is cleared too.
- Against the chip as it is now (v2), blocks **1, 7, 8, 9** differ (block 0 is NVRAM and isn't written).

## The culprit: microcode 0x217 loaded by the firmware (2026-11-07)

- **v4** (v2 + microcode + marker) didn't boot: flash and fh/flashrom verify were fine, and a cold boot was dead.
- **v5a** (`of1-test-v5a-v2-plus-ucode.bin`, md5 `1be7afb6…`) is v2's own bytes plus microcode 0x217, so only block 1
  differs. It shows the panel init flash, then no logo, the same as 26.11.1. So the microcode is the cause of both
  bricks, and the splash size (the old suspect 1) is very likely innocent.
- The same blob works when Linux loads it early from the initrd. So the update itself is fine for this CPU, and loading
  it at reset, before cache-as-RAM is set up, is what this firmware can't survive. Why isn't known; a POST-code reader
  would show where it stops.
- **The reset-vector loader** is boot block 0xFFFFFAA0–0xFFFFFC96. It walks FV_MAIN at 0xFFF10000 for file
  `17088572…` (GUID at 0xFFFFFA80) and steps entries (TotalSize, or 2048, rounded up to 512). It matches CPUID sig and
  the MSR 0x17 platform bit, then does `WRMSR 0x79` with header+0x30. It outputs POST 0x0E if no entry is found and 0x0F
  if MSR 0x8B reads 0. There are no other checks. Before it, it enables EIST (MSR 0x1A0 bit 16) and sets MSR 0x199 from
  0x198.
- DXE has its own `MicrocodeUpdate` module (`F3331DE6…`) that maps the same file.
- **Decision:** ship 0x217 from Linux, as an early-microcode cpio as a second `initrd=` (tested), and keep the
  firmware's 0x20a.
- Still to clear, one at a time: the version marker (`of1-test-v5b-v2-plus-marker.bin`, block 9 only) and a larger
  splash (140x122 with `--allow-large-splash`).

## Clean base and the P-state test (2026-11-08)

- **v6** `of1-test-v6-stock-clean.bin` (md5 `cd274df8…`): stock `of1_efi_o2.img` + shell countdown 0 + yellow 80x81
  "Woo!" at the stock position. Blocks 5–9 differ from stock. **Flashed and boots**, so this is the new clean baseline.
  None of the old test-unit scribbles or the rebased OEMPEI remain.
- **v7** `of1-test-v7-ucode-skip-perfctl.bin` (md5 `fb85edd1…`): v6 + microcode 0x217 + `tools/patch_reset.py
  --skip-perfctl`, which NOPs `WRMSR IA32_PERF_CTL` at 0xFFFFFBF4 and fixes the VTF data checksum. It wrote blocks 1 and
  15 (the first boot-block write) and verified with fh and flashrom. **Does not boot.** So a P-state change in flight
  during the microcode load is not the cause.
- **Live-swap note:** the original chip read all FF on two live insertions with fh already loaded, while the mystery chip
  read fine live in between; after Andy worked on the original chip's leads it read fine. The cause was a very fine clothing fibre stuck to
  one pin. Failures are contact, not procedure. Always read and identify the chip before writing.
- **v8** `of1-test-v8-marker.bin` (md5 `3f6177c6…`) = v6 + RAW version marker (block 9 only). **Boots**, and the marker
  reads back. Appended FFS files are safe.
- **v9** `of1-test-v9-two-stage-ucode.bin` (md5 `36505a8a…`) = v8 + `patch_reset.py --two-stage-ucode`. 0x20a stays at
  0x10060 and 0x217 is added at 0x11460. The reset load jumps to an 88-byte stub at 0xFFFFF800 (boot FV pad file) that
  loads both in turn. `tools/emu_reset.py` (Unicorn, the real reset path) confirms 0x20a then 0x217. **Does not boot.**
  So 0x217 active on the BSP from reset hangs the firmware, however it gets there.
- Second-thread microcode is loaded by CpuPei (`2BB5AFA9…`, push 0x79 at 0xFFFEB495). It reuses the BSP's entry
  address when the sig and platform match, otherwise it calls a finder callback.

## Step 3: BlwUcodeLate, a DXE driver that loads 0x217 late (built 2026-11-08)

The reset code keeps loading 0x20a, so early init is untouched. A small DXE driver appended to FV_MAIN loads 0x217 once
DXE is running, on the BSP and then on every AP through Framework MP Services, the same conditions under which Linux's
early loader succeeds.

- Source: `driver/ucode_late.asm` (NASM, position-independent). Build and append: `tools/build_ucode_driver.py <base>
  <out> --microcode intel-ucode-06-1c-02 [--pe x.efi]`. It wraps the code in a PE32 (IA32, subsystem 11) and an FFS
  DRIVER file `A5D5B0C1-7D2F-4C3E-9B1A-26B1E0F17217` with a DXE_DEPEX on MP Services (`F33261E7-23CB-11D5-BD5C-0080C73C8881`,
  installed by CpuDxe `E03ABADF…`; its table matches the Framework layout) and a UI name.
- The driver does nothing unless the embedded update matches CPUID sig and the MSR 0x17 platform bit, and is newer than
  the running revision (MSR 0x8B). (Since 26.11.17 it returns EFI_SUCCESS whenever its ReadyToBoot event exists; see below.)
- `tools/emu_driver.py` runs it under Unicorn at a non-base address with fake boot services and MP Services. On a 0x20a
  CPU, BSP and AP both load 0x217 from a 16-byte-aligned copy. On ≥0x217 it does nothing. Stack and registers are balanced.
- **v10** `of1-test-v10-dxe-ucode.bin` (md5 `84a374f9…`) = v8 + driver. Only block 9 differs from v8.

## Step 3 result: microcode 0x217 from the firmware (2026-11-08)

- **v10–v12 were never dispatched.** The FV protocol listed the file, but `ReadSection` returned `EFI_INVALID_PARAMETER`
  for every section (found with `driver/fvdiag.asm`, an app run from `boot.nsh` with its output redirected to the stick).
  Every stock file's data ends exactly at its last section, and ours had 2 bytes of trailing pad. The PE layout and
  compression changes were harmless red herrings. `build_ucode_driver.py` now leaves the last section unpadded and
  checks that the sections tile the file exactly.
- **AMI's built-in shell has no `load` command.** To run test code from `boot.nsh`, build it as an application
  (subsystem 10). Run that way, BlwUcodeLate put both threads on 0x217 at shell time, so a late load is fine.
- **v13** `of1-test-v13-dxe-ucode.bin` (md5 `49b37310…`) = clean O2 base + countdown 0 + test splash + marker +
  BlwUcodeLate (DEPEX + compressed {PE32, UI}, no trailing pad). **Boots, both threads on 0x217, the kernel's PSE
  erratum warning gone, no MCE under load.** The reset code is stock (0x20a), and the 0x217 entry lives only in the driver.
- Bricks along the way and why: 26.11.1, v4, v5a, v7 and v9 all had 0x217 active from reset. Each was recovered by live
  swap with fh loaded first (a fibre on one pin caused the all-FF reads).
- FvDiag's first run used the FV **Block** protocol GUID (DE28BC59) instead of FV (389F751F) and called its Write() with
  junk. The firmware refused, the chip was verified unchanged, and the app now refuses any interface whose +0x18 isn't a small KeySize.

