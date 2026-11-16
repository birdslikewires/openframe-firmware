# Write orders a power cut can't break

Worked out in emulation on 2026-11-16 (`tools/emu_fvwalk.py`), after a test unit stopped booting
on a part-written file header. Nothing here has been written to a chip yet.

## What the firmware does with the file list

Two pieces of the firmware's own code walk the main volume's file list (FV_MAIN, 0x10000-0xCFFFF),
and `emu_fvwalk.py` runs both under Unicorn: the PEI core's `FfsFindNextFile` (boot block, run in
place) and the DXE core's `FvCheck` (EDK1 `FwVol.c`), which builds the list the DXE dispatcher loads
drivers from. The code is the same, at the same addresses, in the OF1's O2 firmware and all four
OF2 builds we have (Cisco, Telio, Avaya, OpenPeak).

Each file has a 24-byte header; its last byte, State, counts down from FF as bits are cleared
("state" below is the highest bit set in the inverted byte):

| Header | PEI | DXE |
|---|---|---|
| 24 bytes of FF | end of the list | end of the list (success) |
| DATA_VALID (State F8, FA, FB...), header checksum right | a file | a file (data checksum not checked) |
| DELETED (E8...), header checksum right | skipped by its size | skipped by its size |
| HEADER_INVALID (D8...) | skipped by 24 bytes | skipped by 24 bytes |
| HEADER_CONSTRUCTION (FE) | end of the list | skipped by 24 bytes |
| anything else, including any header with State still FF but other bytes written | end of the list | **the whole volume is corrupt: nothing loads, the unit doesn't boot** |

The emulation agrees with every state seen on hardware: stock O2 and 26.11.30 boot; block 10
erased, or written with its headers blank, boots like stock firmware; stock + block 10 with the
spacer's header blank boots like stock (.131, step 1); the spacer header's first 12 bytes written,
State FF, doesn't boot (.131, step 2).

## The two holes in today's block-10 update

`emu_fvwalk.py` finds both in `of-firmware-update`'s order for a release after 26.11.30 (erase block
10, both bodies, the driver's header, the marker's header with State last):

1. **The erase.** On the block-10 layout the spacer in block 9 leads straight to 0xA0000, so the
   walkers read block 10 at every moment. A block cut off part way through its erase (tens of
   milliseconds) can hold anything.
2. **The marker's header.** While its GUID bytes are written and its State is still FF, the DXE
   core rejects the volume (~0.5 ms per header on a real chip). Writing State first doesn't
   help here: a header under construction is skipped by 24 bytes only, into the marker's text.

Installing on stock firmware has the same problem with the spacer's header if it's written State
last (the .131 brick).

## Orders that pass every state

`emu_fvwalk.py` checks a plan step by step, and for every byte the walkers read, every value it can
hold part-way through programming (a power cut mid-byte leaves any mix of the bits being cleared).
An erase passes only if nothing in the block is read.

**A header goes in State first**: State FE (construction: DXE skips 24 bytes, PEI stops), the other
23 bytes, then State FA (DATA_VALID: one bit), then F8 (one bit). This only works where the 24 bytes
after the header are still FF while the header is under construction. The spacer's body is all FF,
so its header can always go in this way; the marker's and the driver's can't.

**Installing on stock firmware** (OF2, or an OF1 the same way): block 10 first, whole (the walk ends
at the blank spacer header before it), then the spacer's header State first. No erase at all: the
stock chip's free space is already FF. Passes on the O2 image and all four OF2 images.

**Updating: a gate.** A spacer header can be turned into a gate. Clear one bit of its State
(F8 -> D8, HEADER_INVALID) and the walk skips 24 bytes, into the spacer's own FF data, and stops:
whatever lies beyond is out of the firmware's reach and can be erased and rewritten at any moment
without risk. A new header in the next 24 bytes, State first, opens the way again.

**The layout from 26.11.31** (Andy's idea, 2026-11-16), so that block 9 stays as it is:

| Block | Holds | Changes |
|---|---|---|
| 9 | the end of the stock files, then our spacer (FF) to 0xA0000 | 24 bytes at install; never again (bar below) |
| 10 | spacer headers only: closed ones, then the open one reaching 0xB0000 | 24 bytes per update, never erased |
| 11 | our files: the version marker and BlwPlatformDxe | erased and rewritten each update, only while out of reach |

- **An update** closes block 10's open header, writes block 11, and opens the next header in block
  10. Block 9 never changes; block 11 is the release's byte for byte. About 2,700 updates.
- **An install, or block 10 to rebuild** (stock firmware; the 26.11.29/30 layout with our files in
  block 10; block 10 full; junk in it): the same one level up. Close block 9's open spacer header
  if there is one, write block 10 (its first header, complete) and block 11 out of reach, then open
  the next header in block 9. On stock firmware nothing is erased at all; the chip's free space is
  already FF. A 26.11.30 unit does this once, using 24 bytes of block 9.

Every state of every such plan passes, on the OF1 and all four OF2 images, chained (stock ->
release -> next -> back; 26.11.30 -> release -> next). Block 11's files are the same for both
boards but for the marker's text. Nothing the firmware reads is ever erased.

Between closing a gate and opening the next, the chip boots like stock firmware, so an OS that
relies on our driver (direct boot from the internal memory needs our MMC fix) must be in its
fallback mode first. `of-firmware-update` (v26.11.6 on) does that: before its first write it puts
down `/var/lib/openframe-firmware/writing`, and `of-bootnsh` (openframe-scripts v26.11.15 on)
then writes a `boot.nsh` that starts the internal memory on either firmware (BlwMmcFix.efi and the
kernel on stock-like firmware, GRUB on ours: proven on the unknown Joggler, 2026-11-16, a reboot
through GRUB in 24 s). The file outlives a power cut; once the chip is verified it goes and
`boot.nsh` is written for the firmware again. On a system on the internal memory with an older
`of-bootnsh`, or without BlwMmcFix.efi, the updater refuses to start.

## The planner

`tools/fwplan.py` (pure Python, runs on the unit) reads the chip as the walkers do, finds where it
stands (stock, the old layout, ours, or an earlier run cut off anywhere along the way), plans
the order above as whole-block `fh`/`fhp` writes, and proves every state of the
plan with the walkers' rules before anything is written; it refuses rather than write anything it
can't prove. `tools/test_fwplan.py` cuts its plans off at hundreds of points (between and inside steps,
part-programmed bytes, part-erased blocks as garbage) and checks each re-run finishes the job;
`emu_fvwalk.py` runs its plans on the real PEI and DXE code. Removing the State-first step or
the gate's close makes its own proof refuse. `of-firmware-update` (from v26.11.5) runs it on every
unit and executes its plan step by step, each block read back twice; a re-run after a cut or a
failed write plans again from what is on the chip. Its fake-chip tests
(`tools/test_of_firmware_update.sh`) cut it off after every step from stock, the old layout and
the new, and all re-runs finish.

## Confirmed on hardware

The one physical assumption the emulation can't cover: that the SST 49LF008A takes a second
program of a byte already programmed, clearing just the new bits. Tested 2026-11-16 on the unknown
Joggler (26.11.30) in block 12, which its walk never reaches (it ends at 0xA2B78, checked with the
real walkers on that chip's dump): the gate's own sequence FE -> FA -> F8 -> D8, and one byte
cleared a bit at a time over eight programs, each read back right with every other block
unchanged. Block 12 was then erased; the chip read back byte for byte as before (a5261ecb...).
