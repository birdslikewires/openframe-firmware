# NVRAM on the OpenFrame 1

Block 0 of the 1 MB firmware hub (an SST 49LF008A, ID `BF 5A`) holds AMI's NVRAM store (NVAR).
Everything here was measured on the test unit's dumps.

## How it's written

- **Append-only.** A new or changed variable is a new `NVAR` record written into blank (FF) space;
  the old record is marked obsolete by clearing bits. Nothing is erased until the store fills up
  and the firmware reclaims it (erases block 0 and rewrites the live variables). We have never seen
  a reclaim on this unit.
- **Deleting** clears the record's valid bit in place: attribute byte `0x83` → `0x03`. No erase,
  no new space.
- **GUIDs** are kept in a table growing down from `0xEFF0`; each record stores a one-byte index
  into it.
- **Every boot** the firmware appends a `MonotonicCounter` record (14 bytes). That is the only
  change between boots unless something else writes.
- The test unit, at BootCount ~1,620, had ~35.6 KB blank: ~2,500 boots of counter records before
  the first reclaim. The chip is rated for about 100,000 erase cycles, so wear isn't the concern:
  a power cut during the reclaim's erase is.

## The AMI new-GUID bug

Adding a variable under a vendor GUID that isn't already in the table **half-writes the store**:
the new GUID lands at `0xEFF0` with index 0 and the record is left broken (2026-11-08; repaired
with `tools/repair-block0.sh` from a dump). So:

- Our variables use AMI's existing setup-vendor GUID `EC87D643-EBA4-4BB5-A1E5-3F3E36B20DA9`.
- Nothing that writes variables under its own GUID may run: systemd-boot (`Loader*`), shim,
  fwupd, efibootmgr with vendor variables, the kernel's efi-pstore, its bootloader-control and
  reset-attack-mitigation drivers.
- So OpenFrame kernels are built **without** `CONFIG_EFIVAR_FS` (systemd would mount it
  read-write), `CONFIG_EFI_VARS_PSTORE`, `CONFIG_EFI_BOOTLOADER_CONTROL`,
  `CONFIG_RESET_ATTACK_MITIGATION`, `CONFIG_EFI_TEST` and the capsule loader (openframe-kernel).
  The kernel's own boot-time delete of a dummy variable that doesn't exist writes nothing.
- For testing, `tools/blwmac-check.sh` loads a separately built efivarfs **read-only** and
  removes it again.

## Our variables

All under `EC87D643-…`:

| Name | Attributes | Size | Written by | Contents |
|---|---|---|---|---|
| `BlwMac` | NV+BS+RT | 6 | BlwMac.efi, only when it changes | The WLAN card's burned-in MAC, or with no card the generated LAN MAC (02 bit set) |
| `BlwMacStatus` | BS+RT (RAM only) | 24 | BlwMac.efi, every boot | flags (u32), SetVariable status (u32), stored MAC, LAN MAC applied, pad |
| `BlwLanMacDel` | BS+RT (RAM only) | 16 | BlwLanMacDel.efi (one-off) | GetVariable before, lock-down, SetVariable, GetVariable after |

`BlwLanMac` (6 bytes, the LAN MAC itself) was used up to 26.11.19 and deleted from the test unit.

`BlwMacStatus` flags: `0x1` MAC from LoadOptions, `0x2` from the shell's argv, `0x4` no usable MAC
given, `0x8` the stored MAC is a card's, `0x10` already stored (nothing written), `0x20` written,
`0x40` SetVariable failed, `0x80` blocks 1-15 locked down, `0x100` lock-down didn't stick (nothing
written), `0x200` read back and usable, `0x400` SMBIOS filled, `0x800` stored and read back: the unit
is reset at once (so the status is lost with it), `0x1000` no NIC, `0x2000` MAC0
written and verified, `0x4000` MAC0 read back wrong, `0x8000` not BirdsLikeWires firmware (the
system table's FirmwareVendor isn't exactly "BirdsLikeWires"): nothing locked, written or applied.
A normal boot reads `0x2619`; a boot that stored a new MAC reset before anything could read
`0x2ea9`; stock firmware `0x8000`.
Before that check (2026-11-13) a stick whose boot.nsh was written on a BirdsLikeWires unit, booted
on a stock unit, locked the stock unit's flash down and stored the first unit's MAC in its NVRAM.

BlwPlatformDxe only ever reads NVRAM: `BlwMac` (for the LAN MAC, serial number and UUID) and
`MonotonicCounter` (for the SMBIOS boot count).

## Before every write

BlwMac.efi (and BlwLanMacDel.efi) set write-lock + lock-down on blocks 1-15 through the firmware
hub's lock registers (`0xFFB00002 + block * 0x10000`) and read them back first; if they don't
stick, nothing is written. So the firmware itself can't be altered for the rest of that boot,
whatever AMI's code does. Lock-down can't be undone except by a reset, which is the point: a
write-lock alone, AMI's code could lift. So once BlwMac.efi has stored a MAC and read it back
exactly, it resets the unit at once (RT->ResetSystem, cold; since 2026-11-13): the boot that wrote
NVRAM never reaches an OS, and every OS session starts with the flash unlocked, ready for an update.
Proven on the test Joggler: after the reset the lock registers read 01. A write that fails or
reads back wrong doesn't reset, so a store that keeps failing can't loop. openframe-linux's
of-bootnsh only puts the BlwMac.efi line in boot.nsh while NVRAM doesn't hold the unit's MAC (it
reads it back from the SMBIOS UUID), and reboots for it during boot, so a stick moved between units
doesn't carry one unit's MAC to the other, and an ordinary boot writes nothing (`tools/lockregs.py`
shows the registers).
