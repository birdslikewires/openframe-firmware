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
