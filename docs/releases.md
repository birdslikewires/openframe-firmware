# Releases

BirdsLikeWires releases for the OpenFrame 1, newest last. Each is built on the one before
(`tools/mkbase.py`), and from 26.11.16 on differs from it in block 9 only. The images are on the
repository's GitHub Releases page, tagged by version (md5s in `releases/MD5SUMS`); how they were
made and tested is in [process.md](process.md).

These notes were kept as the work went along, in the original working tree, so paths in them are
from there: `firmware/efi-builds/X` is the GitHub Release of that version for release images
(other builds were test images, not kept), `firmware/efi-dumps/` were the test unit's dumps (kept privately: they
hold unit-specific data), `tools/flash-block9-*.sh` is `tools/flash/`, `lanmac.asm` is now
`blwmac.asm`.

| Version | Change |
|---|---|
| 26.11.15 | Clean O2 base; shell countdown 0; the pecking bird; version marker; BlwUcodeLate (microcode 0x217 loaded late) |
| 26.11.16 | BlwUcodeLate becomes BlwPlatformDxe and adds SMBIOS 2.4 (Types 0, 1) |
| 26.11.17 | Applies the stored LAN MAC to the RTL8111 at ReadyToBoot |
| 26.11.18 | Firmware vendor "BirdsLikeWires"; SMBIOS Type 3 (Embedded PC) and Type 11 (BootCount) |
| 26.11.19 | SMBIOS corrections: wake-up type AC Power Restored, one power cord |
| 26.11.20 | Serial number and UUID from the WLAN card's MAC; BlwLanMac.efi becomes BlwMac.efi |
| 26.11.21 | SMBIOS Types 16/17: the memory array and device, size and speed found at boot |
| 26.11.22 | Works around the MMC/SD driver's large-transfer bug: the shell can start a kernel from the internal memory |
| 26.11.23 | Keeps the panel at 60 Hz: undoes the stock north-bridge driver's fixed 156 Hz panel clock |
| 26.11.24 | No screen twitch at boot: the north-bridge driver is changed in memory to write the 60 Hz clock itself |
| 26.11.25 | Boots 2.8 s faster: the MMC/SD driver no longer probes the internal memory for a CE-ATA disk |
| 26.11.26 | Boots up to 1 s faster with a USB drive attached: shorter fixed waits in the firmware's USB code |

## Releases (2026-11-08)

- **26.11.15 (OF1)**, `firmware/efi-builds/of1-blw-26.11.15.bin` (md5 `cfc604dd…`): clean stock O2 base + shell countdown 0 +
  Andy's pecking bird (white on black, 204x82, at 274,199, optically centred: the mass centroid is 24 px right of the bbox
  centre) + marker + BlwUcodeLate. Only blocks 5–9 differ from stock; NVRAM, NCB and boot block are stock.
- **26.11.16 (OF1)**, `firmware/efi-builds/of1-blw-26.11.16.bin` (md5 `fb95521e…`): the driver becomes **BlwPlatformDxe**
  and also installs **SMBIOS 2.4**: Type 0 (BirdsLikeWires, 26.11.16, 11/08/2026, 1 MB, PCI/upgradeable/ACPI/UEFI,
  revision 26.11), Type 1 (OpenPeak, OpenFrame 1, OF1, family OpenFrame) and Type 127. The table goes in
  EfiRuntimeServicesData via InstallConfigurationTable, and is skipped if SMBIOS already exists. Checked by emulation
  (`emu_driver.py`, including the skip path), by the real `dmidecode --from-dump`, by an app run from `boot.nsh`, and then
  flashed: the kernel reports `DMI: OpenPeak OpenFrame 1, BIOS 26.11.16 11/08/2026` and `/sys/class/dmi/id` is populated.
  Both threads are on 0x217. Flashed on the OF1 test unit, which now runs it.
- Splash limit: 16,800 px is proven on this IEGD build (the Verizon OF1 shows 300x56). The old 16,000 figure came from
  wrongly blaming 26.11.1's splash.
- Still to do: a flasher (identifies chips by the marker, refuses unknown images, never writes block 0 or the boot block),
  carrier variants, the Plymouth theme (a wire under the bird's feet at panel y ~276–278, growing from x 400), and tidying the stick.
- **26.11.17 (OF1)**, `firmware/efi-builds/of1-blw-26.11.17.bin` (md5 `e779ed3b…`): BlwPlatformDxe also **applies the LAN MAC**.
  At dispatch it only registers a ReadyToBoot event (CreateEventEx, group `7CE88FB3…`; this core's boot services are
  UEFI 2.0, header 0xC8). The notify reads `BlwLanMac` (GUID `EC87D643…`, stored by `BlwLanMac.efi` from `boot.nsh`) and,
  if it is exactly 6 bytes, unicast and non-zero, writes it into the RTL8111C's MAC0 (`driver/nic_mac0.inc`, shared with
  BlwLanMac.efi). It never writes a variable. Once the event exists the entry returns EFI_SUCCESS, because an error status
  would make the core free the image under the event. `--app` builds with `-DAPP`: it applies the MAC at once and registers
  no event. `tools/emu_platform.py <dxe.efi> <app.efi>` (17 scenarios: any SetVariable, port I/O at dispatch, unrestored
  command registers or an error return with the event registered are failures; four deliberate mutations were all caught).
  Hardware, 2026-11-09, cold boots: a control with no MAC writer gave a junk MAC0; the app build from boot.nsh gave
  02:0e:8e:12:34:56; then 26.11.17 flashed (block 9 only vs 26.11.16, fh verified) with **no MAC line in boot.nsh**
  gave 02:0e:8e:12:34:56, DMI 26.11.17, both threads 0x217, NVRAM changed by MonotonicCounter only.
- **26.11.18 (OF1)**, `firmware/efi-builds/of1-blw-26.11.18.bin` (md5 `87ed0cb2…`), block 9 only vs 26.11.17. BlwPlatformDxe also:
  sets `ST->FirmwareVendor` to "BirdsLikeWires" (EfiRuntimeServicesData copy, system table CRC32 recomputed with
  BS->CalculateCrc32, old pointer and CRC restored if that fails), so Linux prints `efi: EFI v2.0 by BirdsLikeWires`;
  adds SMBIOS Type 3 (OpenPeak, chassis 0x22 Embedded PC; dmidecode 3.6 labels it even in a 2.4 table; systemd-hostnamed
  reports `embedded` without /etc/machine-info) and Type 11 `BootCount: NNNNNNNNNN`, a fixed-width placeholder filled at
  ReadyToBoot from `MonotonicCounter` (EFI global GUID, 4 bytes; GetVariable only, never GetNextHighMonotonicCount,
  which writes). Read it with `sudo /usr/sbin/dmidecode -t 11`. The app build replaces an existing SMBIOS table so a
  boot.nsh test shows the new one. `tools/emu_platform.py`: 24 scenarios; six new mutations caught. Flashed 2026-11-09
  (`tools/flash-block9-26.11.18.sh`); cold boot with no MAC line in boot.nsh: vendor line, DMI 26.11.18, chassis 34,
  BootCount 1615 = NVRAM, MAC 02:0e:8e:12:34:56, both threads 0x217, NVRAM changed by MonotonicCounter only.
- **26.11.19 (OF1)**, `firmware/efi-builds/of1-blw-26.11.19.bin` (md5 `130c2f14…`), block 9 only vs 26.11.18. SMBIOS
  corrections only: Type 1 Wake-up Type 0x06 Power Switch → 0x08 AC Power Restored (OpenFrames have no switch; they
  start when DC is applied), Type 3 Number of Power Cords 0 → 1 (one DC jack; Height stays Unspecified). The driver PE
  differs from 26.11.18 in exactly three bytes (version digit, wake-up, cords); its code is unchanged. Build recipe:
  take the previous release, erase its last FV file (the driver), change the marker text and fix that file's checksums,
  then `build_ucode_driver.py <base> <out> --microcode firmware/efi-builds/intel-ucode-06-1c-02 --bios-version 26.11.19
  --bios-date 11/09/2026 --check-with-core <CORE_DXE PE>` (the DXE core PE is file type 5, GUID 5ae3f37e…, decompressed
  from any image). The recipe rebuilt 26.11.18 byte-identically (87ed0cb2…) before 26.11.19 was made. emu_platform 24/24.
  Flash script `tools/flash-block9-26.11.19.sh` (pre-check: chip blocks 1-15 = 26.11.18 `dc0e83cf…`; after:
  `652675f9…`).
  **Flashed 2026-11-09** (fh programmed + verified 0x90000, two read-backs `ef191ded…`, block 0 unchanged; dump
  `firmware/efi-dumps/of1-testunit-20261109-26.11.19-postflash.bin`). Clean shutdown, then cold boot: DMI 26.11.19,
  `Wake-up Type: AC Power Restored`, `Number Of Power Cords: 1`, Embedded PC, BootCount 1618, both threads 0x217,
  MAC 02:0e:8e:12:34:56.
- **26.11.20 (OF1)**, `firmware/efi-builds/of1-blw-26.11.20.bin` (md5 `ea973fd5…`), block 9 only vs 26.11.19. **Serial
  number and UUID from the WLAN card's MAC**; the MAC app renamed and given the card's MAC. `BlwLanMac.efi <lan>`
  became **`BlwMac.efi <mac>`** (`driver/blwmac.asm`, `tools/build_blwmac_app.py`, `tools/emu_blwmac.py`,
  `tools/blwmac-check.sh`). It stores one MAC in NVRAM variable **`BlwMac`** (6 bytes, same AMI GUID `EC87D643…`,
  so not the new-GUID bug); status in volatile `BlwMacStatus`, 24 bytes (flags, SetVariable status, stored MAC, LAN
  MAC applied, pad; new flags 0x8 = a card's burned-in MAC, 0x400 = SMBIOS set). The stored MAC is either
  - a WLAN card's own address (universally administered, bit 1 of the first octet clear), any maker's: the LAN MAC is
    made from it (`lan_mac` in `driver/nic_mac0.inc`: 02 then its last five octets, as of-netplan does), and it
    becomes the Type 1 serial number `000E8E123456` (as on the Joggler's sticker) and UUID; or
  - with no card, the LAN MAC of-netplan generated (locally administered): it goes on the chip as it is and becomes
    the UUID, but not a serial number, since it isn't the hardware's. A card whose only address is locally
    administered (random, perhaps new every boot) counts as no card in of-netplan, which then generates a LAN MAC once
    and keeps it; a card's permanent address (`ip -d link` permaddr) is used if the kernel shows one.

  UUID `00000000-0000-8000-8000-<mac>` (RFC 9562 version 8, stored in written order, which is how a 2.4 table is
  read), from `driver/smbios_id.inc` (shared). A LAN MAC typed into network.yaml is never passed to the firmware
  (Linux sets it). The old 6-byte `BlwLanMac` is ignored by both and left in NVRAM. BlwPlatformDxe reads `BlwMac`
  at ReadyToBoot (exactly 6 bytes, unicast, non-zero, or nothing is done). Type 1 now ends with an unreferenced
  placeholder string `000000000000`, with serial index 0 and UUID all-FF until filled; Type 3's serial stays
  unspecified. The table is found by walking the structures and only filled if Type 1's last string is 12 hex digits,
  so 26.11.16-19 tables are never touched. `boot.nsh` runs after ReadyToBoot, so BlwMac.efi also fills SMBIOS from
  what NVRAM holds once it has stored (and for a generated MAC takes a serial filled earlier that boot away again): a
  change shows from the boot that stores it. Emulation: `emu_platform.py` 31/31, `emu_blwmac.py` 23/23; four
  deliberate mutations caught. The unit's real `dmidecode --from-dump` reads the tables as: card, serial
  `000E8E123456` + UUID `…-000e8e123456`; generated, Not Specified + UUID `…-02a1b2c3d4e5`; unfilled, Not Specified
  / Not Present. Flash script `tools/flash-block9-26.11.20.sh` (pre-check: chip blocks 1-15 = 26.11.19 `652675f9…`;
  after: `5c0d45a5…`; block 9 `bc55a496…`). Linux side: openframe-linux 26e75cd.
  **Flashed 2026-11-09 ~23:18** (fh programmed + verified 0x90000, two read-backs `12af159e…`, block 0 unchanged; dump
  `firmware/efi-dumps/of1-testunit-20261109-26.11.20-postflash.bin`). Clean shutdown, cold boot 1: DMI 26.11.20,
  `Serial Number: 000E8E123456`, `UUID: 00000000-0000-8000-8000-000e8e123456` (also /sys/class/dmi/id), eth0
  02:0e:8e:12:34:56 at probe, BlwMac created (`00 0e 8e 12 34 56`), BlwMacStatus 0x26a9 (loadopt, card MAC, lock-down,
  written, read back, SMBIOS, NIC), NVRAM diff 35 bytes = MonotonicCounter + the new BlwMac record (GUID index 2, no
  GUID-table write). Cold boot 2: BlwMacStatus 0x2619 (NV_SAME), lock registers 01, NVRAM diff 14 bytes =
  MonotonicCounter only, serial/UUID present, BootCount 1622. Dumps `of1-testunit-20261109-nvram-{pre-26.11.20-boot,
  after-26.11.20-boot,after-26.11.20-2nd-boot-pre}.bin`. "x86/CPU: Running old microcode" still prints, as it has
  since 0x217 was first loaded (not new).
- **Old `BlwLanMac` removed from the test unit (2026-11-09 ~23:30).** One-off app `BlwLanMacDel.efi`
  (`driver/blwlanmac_del.asm`, `build_blwmac_app.py --del`), run once from boot.nsh: GetVariable, lock down blocks 1-15,
  SetVariable(BlwLanMac, EC87D643, NV+BS+RT, size 0), GetVariable again; results in volatile `BlwLanMacDel`. Result
  0 / 0 / 0 / 0x8000000E. AMI deleted it in place: the record's attribute byte 0x83 → 0x03 (valid bit cleared), no erase,
  no new space; the rest of the NVRAM diff is the MonotonicCounter record. Dumps
  `of1-testunit-20261109-nvram-{pre,after}-lanmacdel.bin`. The boot.nsh lines and the app were then removed.
- **26.11.21 (OF1)**, `firmware/efi-builds/of1-blw-26.11.21.bin` (md5 `b38bda05…`), block 9 only vs 26.11.20. **SMBIOS memory:
  Type 16** (Physical Memory Array: system board, system memory, no ECC, maximum 2 GB, one device) and **Type 17** (Memory
  Device: 64/64-bit, row of chips, locator "Onboard", DDR2, synchronous; manufacturer/serial/part not specified, since
  soldered chips have no SPD). Handles renumbered: 0 BIOS, 1 System, 2 Chassis, 3 Type 16, 4 Type 17, 5 OEM strings,
  6 End. Type 17's **size and speed are filled in at dispatch** (`mem_fill` in `driver/ucode_late.asm`):
  - size: the end of the highest RAM region below 4 GB in the UEFI memory map (types 1-7, 9, 10; reserved, unusable and
    MMIO excluded, because this firmware marks everything from the graphics stolen memory up to 2 GB reserved), rounded up
    to a power of two, 256 MB to 2 GB, else 0xFFFF (unknown). The SCH has one 64-bit channel of x16 DDR2, one or two ranks
    of 256 MB to 1 GB, at most 2 GB (datasheet 319537 §5.3, §8.2), so totals are powers of two. OF1: top 0x1FAF0000 → 512 MB.
  - speed: MSR_FSB_FREQ (0xCD) bits 2:0, read only on family 6 model 0x1C: 101b → 400, 001b → 533 MT/s, else 0
    (unknown). The SCH runs DDR2 at the FSB rate (datasheet §7.2.8.1, Port 05h MSR bit 3). OF1 Z520: 0x111 → 533.
  The memory controller's own registers (rank/density) are not in the public datasheet and are not touched.
  `emu_platform.py` 43/43 (new: GetMemoryMap/FreePool/MSR 0xCD fakes, the OF1's real map, 256 MB-2 GB, past 2 GB, above
  4 GB, tiny map, descriptor sizes 32/40/48, GetMemoryMap failing, FSB 400/other, a non-Atom CPU, leak check); five
  mutations caught. The unit's `dmidecode --from-dump` reads it as 512 MB DDR2 533 MT/s, or Unknown/Unknown when unfilled;
  7 structures, 254 bytes. Flash script `tools/flash-block9-26.11.21.sh` (pre-check: chip blocks 1-15 = 26.11.20
  `5c0d45a5…`; after: `6e537024…`; block 9 `5d353a84…`).
  **Flashed 2026-11-10 ~00:00.** The first attempt (2026-11-09 ~23:55) wrote nothing: fh reported
  `fwhub_unlock error baddr=0x00090000 lock=0x03`, because BlwLanMacDel.efi had locked blocks 1-15 down that boot and
  lock-down only clears at reset; the read-backs matched the pre-check. **After any boot in which BlwMac.efi (or another
  of our apps) wrote NVRAM, reboot before flashing.** After a clean cold boot (lock registers 01) the flash went through:
  fh programmed + verified 0x90000, read-backs `0b9b3e47…`, block 0 unchanged; dump
  `firmware/efi-dumps/of1-testunit-20261110-26.11.21-postflash.bin`. Clean shutdown, cold boot: DMI 26.11.21,
  `DMI: Memory slots populated: 1/1` (was 0/0), `dmidecode -t memory`: 2 GB maximum, 512 MB, Row Of Chips, Onboard, DDR2,
  Synchronous, 533 MT/s; serial/UUID unchanged, BootCount 1625, MAC ok, 0x217, lock registers 01, NVRAM diff 14 bytes =
  MonotonicCounter only.
- **26.11.22 (OF1)**, `of1-blw-26.11.22.bin` (md5 `3f74a745…`), block 9 only vs 26.11.21. BlwPlatformDxe also wraps the
  **MMC/SD media driver's BlockIo** (`driver/mmc_fix.inc`). That driver (MmcMediaDevice `DD223EF8…`, stock in every OF1
  build) splits a transfer into 512 KB chunks but works out each chunk's card address as the caller's LBA plus the
  previous chunk's length, so from the third chunk on it re-reads (or re-writes) the second chunk's place. The FAT driver
  reads a file in one call, so a kernel loaded from the internal memory came in corrupted past its first megabyte and
  hung with no output (GRUB reads cluster by cluster, which is why it worked). The wrappers, matched to the driver by
  its code (`mmc_sig.inc`), hand it one chunk at a time. A BlockIo protocol notify (CreateEvent + RegisterProtocolNotify,
  registered only once the ReadyToBoot event exists, so the image is sure to stay loaded) wraps instances as the driver
  installs them; ReadyToBoot wraps any already there. The app build leaves this to **BlwMmcFix.efi**, the same fix as a
  shell app for firmware without it. `tools/emu_mmcfix.py` runs the firmware's own driver code (the bug: CMD18 at
  0x100000, 0x180000, 0x180000…) and the wrapped one; `tools/emu_platform.py`: 52 scenarios (nine new), 26.11.21's
  driver fails them, nine mutations caught. Before flashing, on the test unit: **MmcTest.efi** showed the repeated
  chunks (Linux's reads of the same sectors matched only the piecewise ones), and after BlwMmcFix.efi the shell started
  the internal memory's kernel through its EFI stub and the system came up from it. Flash script
  `tools/flash/flash-block9-26.11.22.sh` (pre-check: chip blocks 1-15 = 26.11.21 `6e537024…`; after: `61ca1897…`;
  block 9 `d1991708…`).
  **Flashed 2026-11-10 ~21:20** from the internal memory's system (fh.ko built earlier for this kernel; lock registers
  01): fh programmed + verified 0x90000, read-backs `28258f76…`, block 0 unchanged; dump
  `firmware/efi-dumps/of1-testunit-20261110-26.11.22-postflash.bin`. Clean shutdown, cold boot from the stick: vendor
  line, DMI 26.11.22, memory slots 1/1, 0x217, MAC ok (no BlwMac write), lock registers 01, NVRAM diff 14 bytes =
  MonotonicCounter only; MmcTest.efi with no BlwMmcFix.efi: the internal memory's BlockIo already wrapped, one-call
  reads right. Then with the stick removed and `fs0:vmlinuz … initrd=\initrd.img-…` in the internal memory's boot.nsh:
  stub boot from the internal memory, 28.3 s plug-on to SSH (GRUB from there: 30.2-30.5 s).
- **26.11.23 (OF1)**, `of1-blw-26.11.23.bin` (md5 `42150d55…`), block 9 only vs 26.11.22. BlwPlatformDxe also keeps the
  **panel at 60 Hz** (`driver/panel_clock.inc`). The display driver (Intel's IEGD GOP, its table `OpenFrame-Sharp`:
  800x480, 33.264 MHz) programs pipe B's PLL correctly, FPB0 `0x00021005` and DPLL_B `0xd8100003` (96 MHz reference,
  P1 5, P2 14), 60 Hz. Then the stock north-bridge driver NBDXE (`E4ECD0B2…`, RVA 0x3ca), on a notify for the boot
  manager's stage signal `EF9A3971-C1A0-4A93-BD40-5AA165F2DC3A`, unlocks the panel registers and writes a fixed DPLL_B
  `0xd8027203`: the spread-spectrum reference (~100 MHz) with P1 2, 86.5 MHz, about 155 Hz. Some panels then show white
  or blue with blotches (a tired OF1 panel from a stock O2 unit); and Linux's gma500 reads the clock back assuming a
  66 MHz reference and ran every OF1 panel at 103 Hz (fixed separately by openframe-kernel patch 07, 6.18.63-4). Found
  by measuring the refresh from the display pipe's frame counter, reading the GOP's PLL code, a shell probe
  (GOP SetMode(0) gave 60 frames a second; **SetMode to another mode hangs this GOP**), then reading NBDXE. The driver
  registers its own notify on the same protocol (after NBDXE's, at the same TPL, so it runs straight after) and checks
  again at ReadyToBoot; the app build does it at once. It acts only on exactly NBDXE's value with the GOP's dividers on
  a Poulsbo GMA, and writes the PLL with the panel registers unlocked, as the GOP and gma500 do. The notify registration
  is shared with the MMC fix (`notify_on`). `tools/emu_platform.py`: 63 scenarios (11 new: graphics device, fake display
  registers, NBDXE's write), 26.11.22's driver passes 5, 15 mutations caught. Before flashing, `panel_clock.inc` run once
  from the shell on a stock O2 unit put `0xd8100003` back (refresh 59.6 Hz from 156). Flash script
  `tools/flash/flash-block9-26.11.23.sh` (pre-check: blocks 1-15 = 26.11.22 `61ca1897…`; after: `3c467c65…`; block 9
  `e2537c42…`) also **refuses a kernel whose gma500 lacks patch 07**: with this firmware an unpatched gma500 reads the
  96 MHz reference as 48 MHz and drives the panel at 30 Hz.
  **Flashed 2026-11-11 ~02:35** on the test unit, from the stick (6.18.63-4, fh in the kernel; lock registers 01):
  before, with gma500 held back (`module_blacklist=gma500_gfx`), the firmware left `0xd8027203`, 152.4 Hz. fh programmed +
  verified 0x90000, read-backs `2790bf3e…`, block 0 unchanged; dump `firmware/efi-dumps/of1-testunit-20261111-26.11.23-postflash.bin`.
  Reboot: DMI 26.11.23, vendor line, slots 1/1, 0x217; with gma500 held back the firmware leaves `0xd8100003`, **60.00 Hz**;
  MAC ok (no BlwMac write), lock registers 01, NVRAM diff 14 bytes = MonotonicCounter only. Normal boot: gma500 logs
  `pre-programmed mode is 30 Hz (16628 kHz); using 60 Hz (33264 kHz)`, panel at 60 Hz. Then a clean shutdown and a cold
  boot by the smart plug: 25.5 s plug-on to SSH, DMI 26.11.23, 0x217, gma500 again found 30 Hz (the firmware's 60 Hz
  clock), panel at 60 Hz, lock registers 01, no failed units.
- **26.11.24 (OF1)**, `of1-blw-26.11.24.bin` (md5 `3d71d9e4…`), block 9 only vs 26.11.23. With 26.11.23 the screen
  **twitched** once at the boot manager's hand-over (10.55 s after power-on on the test unit, by Andy's stopwatch, with
  Linux's graphics driver held back): NBDXE changed the PLL to its fixed value and our notify changed it back, and the
  panel re-locked twice. Tried live on the test unit with on-screen markers: NBDXE's value then 60 Hz 150 µs later
  twitches; rewriting the value the PLL already holds shows nothing. So BlwPlatformDxe now also changes NBDXE's loaded
  image (`nb_patch` in `driver/panel_clock.inc`): an EFI_LOADED_IMAGE whose FilePath is one MEDIA_FW_VOL_FILEPATH node
  with NBDXE's file GUID, big enough, and whose clock-writing function (RVA 0x3ca) matches `driver/nbdxe_sig.inc`
  exactly (from `tools/nbdxe_sig.py`; the value included, the one relocated dword masked) gets that write's 4-byte value
  (RVA 0x402) changed to `0xd8100003`. It runs at the driver's entry and from a LoadedImage protocol notify, so NBDXE is
  patched as it loads, before its code runs. The 26.11.23 notify and ReadyToBoot fix stay as the safety net. A read-only
  shell probe on 26.11.23 first confirmed the layout on hardware: all FV drivers' FilePath is that node, NBDXE's image
  matches the signature, and this driver is listed before NBDXE. `tools/emu_platform.py`: 72 scenarios (9 new: loaded
  before/after, another value, one code byte, another GUID, too small, no path, another node, lookup failing), 26.11.23's
  driver passes 27, 12 mutations caught. Flash script `tools/flash/flash-block9-26.11.24.sh` (pre-check: blocks 1-15 =
  26.11.23 `3c467c65…`; after: `70555f26…`; block 9 `55c380c3…`; the kernel check as 26.11.23).
  **Flashed 2026-11-11 ~03:10** on the test unit from the stick (lock registers 01): fh programmed + verified 0x90000,
  read-backs `897d0001…`, block 0 unchanged; dump `firmware/efi-dumps/of1-testunit-20261111-26.11.24-postflash.bin`.
  Cold boot by the plug with the probe and gma500 held back: NBDXE's value in memory `d8100003` (patched by the driver),
  firmware refresh 60.00 Hz, DMI 26.11.24, 0x217, lock registers 01, NVRAM diff 14 bytes = MonotonicCounter only;
  **Andy: "Absolutely stable, no twitching!"** Normal cold boot: 25.3 s plug-on to SSH, gma500 found the 60 Hz clock and
  kept it, BlwMac ok (no write), no failed units.
- **26.11.25 (OF1)**, `of1-blw-26.11.25.bin` (md5 `7ad10462…`), block 9 only vs 26.11.24. **2.8 s off every boot.**
  Found with a timing build (26.11.24-dev1, then numbered 26.11.90; `--define TIMING`, test unit only: TSC-stamped protocol installs, image loads and
  boot-manager stages in a volatile variable): from reset, BlwPlatformDxe's entry 1.06 s, every driver loaded by 1.13 s,
  GOP 1.34 s, console 1.96 s, first USB device 3.49 s, the USB stick's BlockIo 4.66 s, then **the internal MMC's BlockIo
  only at 8.09 s**, ReadyToBoot 8.17 s, shell ~8.5 s (which disk was which: the shell's handle numbers). The new shell
  app MmcProbe.efi (`driver/mmcprobe.asm`, `tools/mmcprobe.py`) reconnects each SD controller and times every SdHostIo
  command and Stall: the two empty controllers took ~11 ms each, the MMC 2993 ms, of which **one CMD60 took 2797 ms** and
  timed out. MmcMediaDevice's Start asks every MMC card whether it is a CE-ATA disk (ATA over the MMC bus; RVA 0x72b:
  CMD60 RW_MULTIPLE_REGISTER for 16 registers, signature CE AA, 5000-poll timeout); no MMC answers. Linux identifies the
  same card in 81 ms. With the check patched to return FALSE in RAM (`MmcProbe -DSKIPCEATA`) the MMC's connect took
  194 ms, the same commands otherwise, partition reads fine. (The shell's `map -r` hangs after MmcProbe's reconnect, with
  or without the patch: a plug cycle recovers it.) So BlwPlatformDxe now changes that check's first three bytes to
  `xor al, al; ret` (`driver/ceata.inc`): only in an EFI_LOADED_IMAGE with MmcMediaDevice's file GUID, big enough, and
  whose check matches `driver/ceata_sig.inc` exactly (from `tools/ceata_sig.py`, which also checks the CMD60 helper and
  that Start calls the check only for MMC cards; the function is the same in every BLW release and in stock O2). The
  loaded-image loop moved from `panel_clock.inc` to `driver/img_patch.inc` and tries both patches on each image: NBDXE
  loads before this driver (patched at the entry), MmcMediaDevice just after it (1.105 s; patched from the LoadedImage
  notify, long before the boot manager connects the MMC). The internal IDE port is not affected: this firmware has no
  IDE/ATA driver. `tools/emu_platform.py`: 84 scenarios (12 new: MMC driver loaded before/after, both after, absent, one
  byte different, another GUID, too small, no path, already patched, NBDXE changed, notify failing, app build), 10
  mutations caught; the timing build with it passes `--timing`. Flash script `tools/flash/flash-block9-26.11.25.sh`
  (pre-check: blocks 1-15 = 26.11.24 `70555f26…`; after: `ce47813a…`; block 9 `ca2fe519…`; the kernel check as before).
  **Flashed 2026-11-11 ~08:05** on the test unit, from 26.11.24-dev1 with a one-off copy of that script (lock
  registers 01): fh programmed + verified 0x90000, read-backs `db5feef0…`, block 0 unchanged; dump
  `firmware/efi-dumps/of1-testunit-20261111-26.11.25-postflash.bin`. Cold boots by the plug: **shell 5.63/5.62/5.62 s
  from reset** (26.11.24: 8.46-9.22 s), kernel 7.24-7.25 s, **plug-on to SSH 23.1/23.4/23.1 s** (26.11.24: 25.3-26.8 s);
  DMI 26.11.25, vendor line, slots 1/1, 0x217, panel 800x480 at 60 Hz, lock registers 01, NVRAM diff 14 bytes =
  MonotonicCounter only, fsck clean. Then a one-shot `boot.nsh` entry started the internal MMC's own system
  (`fs0:vmlinuz`, 4.8 MB through the patched driver and the 26.11.22 transfer fix): it booted (6.18.62, root mmcblk2p2,
  no I/O errors), 24.8 s plug-on to SSH; the next cold boot came back to the stick.
- **26.11.26 (OF1)**, `of1-blw-26.11.26.bin` (md5 `b3a16e92…`), block 9 only vs 26.11.25. **Up to 1 s off every boot with a
  USB drive attached.** After 26.11.25 the firmware's USB start-up was the biggest cost: about 2.7 s, from the console
  (1.96 s from reset) to the USB stick's BlockIo (4.64 s). AMI's USB stack runs in SMM (USBRT), where nothing in DXE can
  sample it, so three development builds measured it from outside (none released):
  26.11.25-dev1 (then 26.11.93; `--define TIMING --define PROFILE`, a 10 ms timer event sampling the interrupted code and return addresses;
  the firmware's timer actually ticks every 55 ms) showed the USB start-up as one 2.7 s stretch with no samples at all;
  26.11.25-dev2 (then 26.11.94; `--define USBTRACE`: SmmControl->Trigger and BS->Stall wrapped, put back at ReadyToBoot with the BS CRC
  restored) split it into three SMIs of UHCD's USB requests (SW SMI 0x31, request block at gUsbData+0xdd9): 0x20/00
  1528 ms (USB start: controllers, ports, class drivers), 0x2e/01 635 ms, 0x27/0b 506 ms (mass storage), and showed
  USBRT's SMM copy (0x1fb24000) readable as stock code as it loads, not at ReadyToBoot; 26.11.25-dev3 (then 26.11.95; an in-memory change of
  two USBRT delays as it loads) showed the SMM copy writable then too, and that USBRT's 500 ms wait before a
  mass-storage device's first command (FixedDelay at RVA 0x56cb, in the identify step 0x567f; FixedDelay counts 15 us
  port-0x61 refresh ticks) ran **twice per drive**: 0x2e/01 went to 128 ms, 0x27/0b to 13 ms, the stick ready at 3.64 s
  instead of 4.64 s. So BlwPlatformDxe now changes both USBRT copies as they load (`driver/usbrt.inc`, from the
  LoadedImage notify in `driver/img_patch.inc`, which now tries it on every image until two copies are done): that wait
  to 10 ms; and in the ready loop (0x52a8), which waits 1 s before asking again after sense 6/29/00 (power-on reset),
  2/04/01 or 2/04/02 (becoming ready, a disk spinning up), the wait to 200 ms with five times the tries (the tries are
  (setting + 1) x 10, set at RVA 0x3f95 when USB starts, inside request 0x20, long after the patch: now x 50), so a
  drive is used within 200 ms of being ready but still gets the same 10-40 s. Each site only where the whole instruction
  sequence is stock (none has a relocated byte), each value read back; the wait and the tries together or not at all.
  `tools/emu_platform.py`: USBRT's two copies in every scenario, 96 scenarios (10 new: before/after, none, each site not
  stock, SMM copy unreadable, other GUID, too small, no path), notifies run highest TPL first as in the core; release,
  timing, profile and USB-trace modes all 96/96; nine mutations caught. Flash script `tools/flash/flash-block9-26.11.26.sh`
  (pre-check: blocks 1-15 = 26.11.25 `ce47813a…`; after: `654dfeef…`; block 9 `b6e53203…`).
  **Flashed 2026-11-11** on the test unit (first from 26.11.25-dev3 with a one-off script, read-backs `0b75b2b2…`; later from
  26.11.25 with the release script itself, read-backs `48335074…`; block 0 unchanged both times; dump
  `firmware/efi-dumps/of1-testunit-20261111-26.11.26-postflash.bin`). Cold boots by the plug from the USB stick:
  **shell 4.66/4.66/4.66 s from reset** (26.11.25: 5.62 s), kernel 6.27-6.28 s, **plug-on to SSH 21.8/22.2/22.5 s**
  (26.11.25: 23.1-23.4 s); DMI 26.11.26, vendor line, 0x217, panel 800x480, lock registers 01, NVRAM diff = MonotonicCounter
  records only, fsck clean; the internal MMC's own system started through it (one-shot `boot.nsh` entry, 23.7 s, no
  errors). With the USB port empty the unit boots the MMC (startup.nsh tries `fs1:boot`, the USB drive, then `fs0:boot`):
  kernel at 5.31 s. With a 150 GB WD Raptor in an ASMedia (174c:55aa) caddy powered at the plug's click, the firmware
  finds no FAT file system on it and boots the MMC: kernel at **6.97 s** (26.11.25: 7.42 s); the drive spinning up takes
  most of the time the shorter wait would save. **Not fixed, and in every release so far:** some USB drives attached
  at power-on keep the unit on the boot logo for many minutes or indefinitely, the drive's light flashing. Corrected
  2026-11-12 (this note first blamed the size of a 12 TB drive): that 12 TB drive turned out unreadable over USB 2
  anywhere (Linux and macOS too: every read a medium error; fine over USB 3), and the cause for a good drive is the
  firmware's USB code: with a 4 TB drive in an ASMedia ASM1053 caddy (174c:55aa), the caddy hangs on READ(10) until the
  host has sent READ CAPACITY(16), which AMI's code never does, and AMI's recovery from that then makes it worse (a
  malformed Bulk-Only reset, a retry that sends a corrupt command). Linux sends READ CAPACITY(16) first and is fine. Being
  fixed in development builds (26.11.26-dev5 onwards).
- **26.11.27 (OF1)**, `of1-blw-26.11.27.bin` (md5 `1de33e3f…`), block 9 only vs 26.11.26. **A USB drive attached at power-on
  no longer stops the boot.** With a 4 TB drive asleep in an ASMedia ASM1053 caddy (174c:55aa, self-powered), every
  release so far sat on the boot logo for many minutes or for good. Traced with development builds 26.11.26-dev1 to -dev10
  (the screen trace, `--define SCREEN`: USBRT's requests, delays, SCSI commands and USB transfers drawn live from SMM) and
  by replaying USBRT's exact command sequence against the caddy from Linux through usbfs. Five changes to USBRT as it
  loads (`driver/usbrt.inc`, each only where the code is stock, each read back; A-C are 26.11.26's):
  **D** its "Bulk-Only Mass Storage Reset" put the request in the wrong field (21 00 FF 00 ..., bRequest 0); the caddy
  rejects that and then refuses every command until a port reset, which USBRT never does. The 11 bytes that push its
  arguments are re-encoded to send 21 FF 00 00 (after it the caddy answers again at once, even after a timed-out read).
  **E** every transfer phase got 5 s, and the data of READ, WRITE, READ CAPACITY and START STOP UNIT 10 s; the caddy holds
  a reply for up to 7 s, and the first read for 12 s, while its drive spins up (Linux waits 30 s): now 20 s for all.
  **F** READ CAPACITY(10) of a drive over 2 TiB answers FFFFFFFF ("ask READ CAPACITY(16)"); USBRT never asks and goes
  on to READ(10), and the caddy hangs on READ(10) (no data, then no answer to anything) until the host has sent READ
  CAPACITY(16). READ CAPACITY's call into USBRT's dispatcher now goes to a stub at the end of USBRT's relocation section
  that, after an FFFFFFFF answer, also sends READ CAPACITY(16), then puts the first answer back for USBRT. **H** that
  needs USBRT's command-block builder fixed: it cleared as many bytes at the start of the block as the command is long,
  which for a 16-byte command wipes its opcode (the caddy got 00 10 ...); the header is written straight after anyway, so
  its two `rep stos` become nops (no change for USBRT's own 12-byte commands); F is made only if H is. **G** the error
  counter (CErr) in control transfers' descriptors was 0, so a device answering with garbage was retried for the whole
  timeout: now 3, as Linux has it. The Joggler's internal Wi-Fi (RT2770, port 5) can't work at high speed while a powered
  ASM1053 is attached (electrical: fine with the caddy unpowered, in any power-on order; Linux falls back to full speed):
  that cost 5 s on every boot, 20 s with E; now under a second.
  `tools/emu_platform.py`: F run against a stand-in dispatcher that applies the builder's move and clear as the real
  (patched or not) bytes say, so the version without H fails as the hardware did; 108 scenarios (D-H: each site not
  stock, F without H), all builds 108/108; mutations: usbrt_de 12/12, usbrt_fg 23/23. Flash script
  `tools/flash/flash-block9-26.11.27.sh` (pre-check: blocks 1-15 = 26.11.26 `654dfeef…`; after: `6e687d87…`; block 9
  `acafc0c5…`). **Flashed 2026-11-12** on the test unit (from 26.11.26-dev10 back to 26.11.26 with that build's script,
  then with the release script itself; lock registers 01): fh programmed + verified 0x90000, read-backs `e89b3dfb…`,
  block 0 unchanged; dump `firmware/efi-dumps/of1-testunit-20261112-26.11.27-postflash.bin`. Cold boots by the plug from
  the USB stick: **shell 4.72/4.72/4.72 s from reset** (26.11.26: 4.66 s), kernel 6.36 s, plug-on to SSH 22.2/22.2/22.3
  s; DMI 26.11.27, vendor line, slots 1/1, serial/UUID, BootCount, 0x217 (both threads), lock registers 01, NVRAM diff =
  three MonotonicCounter records (one per boot), fsck clean, no failed units. With the 4 TB asleep in the caddy and no
  stick (the internal MMC's system): the drive spins up as the firmware sets it up, the kernel starts at 17.7-20.8 s
  (an empty port: 5.3 s; most of the difference is the drive's 12.5 s spin-up, which Linux would wait for too), plug-on
  to SSH 34-44 s; Linux finds the drive spinning and readable. **Known behaviour:** with such a drive attached, the
  shell's direct start of the kernel (the EFI stub line in `boot.nsh`) sometimes fails (one boot of two) and the
  `grub` line after it starts the kernel instead (about 3 s later); the unit boots either way. Not yet traced (perhaps
  the kernel's ExitBootServices racing the firmware's USB driver); the shell's direct start works with the caddy
  unpowered. **Still not fixed:** a drive that can't be read over USB 2 at all (the 12 TB above) still keeps the unit
  waiting a long time at the boot logo.
