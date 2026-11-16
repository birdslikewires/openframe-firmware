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

**Updating: the spacer as a gate.** The spacer's body is ~59 KB of FF, so it can be closed and
opened again many times:

1. Close: the open spacer header's State F8 -> D8 (one bit: HEADER_INVALID). The walk now skips 24
   bytes, into the spacer's own FF data, and stops: block 10 is out of reach, the chip boots like
   stock firmware.
2. Erase block 10 and write the release's block 10 whole (out of reach, so safe at any moment).
3. Open: a new spacer header 24 bytes after the closed one, reaching 0xA0000, State first.

Passes on the OF1 from 26.11.30 and on every OF2 image, twice in a row (the second from the first's
result). Each update uses 24 bytes of the spacer: about 2,400 updates. Block 9 is only ever
programmed (bits cleared), never erased; nothing the firmware reads is ever erased.

What it changes: block 9 differs by 24 bytes per update, so a chip is no longer identified by
block 9's hash alone (the closed headers have to be read past); and between steps 1 and 3 the chip
boots like stock firmware, so an OS that relies on our driver (direct boot from the internal memory
needs our MMC fix) must be in its fallback mode first. That also applies to today's order.

## Still to confirm on hardware

One physical assumption isn't in the emulation: that the SST 49LF008A takes a second program of a
byte already programmed (FE -> FA -> F8, F8 -> D8), clearing just the new bits. It can be checked
with no risk in a part of block 11 or 12 that the walk never reaches (it stops at the first blank
header after the driver, 0xA2B78 on 26.11.30).
