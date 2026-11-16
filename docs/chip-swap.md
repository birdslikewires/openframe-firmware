# Swapping the firmware chip

The OpenFrame 1's firmware is an SST 49LF008A (or Intel 82802AC) firmware hub in a PLCC32 socket,
so a chip that doesn't boot can be taken out and written in another OpenFrame 1 that does. On the
OpenFrame 2 the chip is soldered: none of this applies unless a socket has been fitted.

Two ways:

- **Cold swap**: power off, change the chip, power on. For booting a chip that's known to work, or
  going back to one.
- **Hot swap**: with a working OpenFrame 1 running Linux from a stick, take its good chip out and
  press in the chip to be written, then write it. For a chip that doesn't boot, or a spare chip
  being prepared for a test. Done many times in November 2026 to recover test builds that didn't
  boot (26.11.1, and test builds v4, v5a, v7 and v9; see [history.md](history.md)).

## What you need

- An OpenFrame 1 that boots with a good chip, opened so the socket can be reached, and an
  openframe-linux stick. Its kernel needs `fh`: OpenFrame kernels built after 2026-11-10 have it
  (`modprobe fh`); for any other, build it from openframe-kernel's `fh/`.
- A PLCC extractor.
- The image to write, and its md5.

## Hot swap

1. **Boot the working OpenFrame 1 from the stick**, on its good chip, and log in over SSH.

2. **Load `fh` now, before the swap, with the options the write will need, and leave it loaded
   until the end.** A chip pressed in while `fh` wasn't loaded has read all FF, and the options
   can only be given at load:

   ```
   sudo modprobe -r fh
   sudo modprobe fh writable=1                # add unprotect=1 to change block 0 or 14-15
   dmesg | tail -2                            # "SST4900LF008 Firmware Hub detected" (or Intel 82802AC)
   ```

   `fh` chooses the chip's erase and program commands when it loads, so the chip you'll write must
   be the same type as the one it detected. `writable=1` writes nothing by itself: only a write to
   `/dev/fh` does.

3. **Optionally, back up the good chip** while it's in: `sudo cat /dev/fh > good.bin`, twice,
   and `cmp` the two.

4. **Swap.** Pull the good chip straight up with the extractor. Look at the other chip's pins:
   straight, clean, and nothing caught on them. One fine clothing fibre on one pin once made a
   chip read all FF on every insertion. Line the chamfered corner up with the socket's, and press
   the chip in squarely in one go. Don't rock it in at an angle: the socket is powered.

5. **Read it before writing anything.** Twice, and compare:

   ```
   sudo cat /dev/fh > before1.bin; sudo cat /dev/fh > before2.bin
   cmp before1.bin before2.bin && md5sum before1.bin
   ```

   All FF (md5 `2fdd6851…`, a whole chip of FF) or `cmp` failing means the chip isn't making
   contact: pull it, check the pins, and press it in again. Keep `before1.bin`: it's what the chip
   held, NVRAM included.

6. **Write.** Normally keep the chip's own NVRAM (block 0): it holds the unit's settings, such
   as its stored MAC. To write blocks 1-15 of an image:

   ```
   sudo dd if=image.bin of=/dev/fh bs=64k skip=1 seek=1 count=15 iflag=fullblock
   dmesg | grep 'fh:' | tail -16
   ```

   Each block says `programmed` or `unchanged, skipped`. A block that would change NVRAM or the
   boot block is refused unless `fh` was loaded with `unprotect=1` (step 2). `verify fails` means
   stop and read the chip again (step 5). Writing block 0 is for a broken NVRAM only, from a known
   good dump: see `tools/repair-block0.sh`.

7. **Verify** blocks 1-15 against the image:

   ```
   sudo dd if=/dev/fh bs=64k skip=1 2>/dev/null | cmp - <(dd if=image.bin bs=64k skip=1 2>/dev/null) && echo VERIFIED
   ```

8. **Shut down cleanly** (`sync`, `sudo poweroff`, wait for it to stop answering ping, then ten
   seconds more) and switch off. The written chip is now in the socket: switch on to boot it. If it
   doesn't boot, cold-swap the good chip back.

## If a chip won't read

Live-inserted chips that read all FF have so far always been a contact problem. If cleaning and
reseating doesn't help, use a programmer: the XGecu TL866II Plus lists `SST49LF008A @PLCC32` and
`N82802AC @PLCC32` (parallel programming mode, with its PLCC32 adapter).

## Testing on a spare chip

To try a build that might not boot without risking a unit's own chip: hot-swap the spare in, write
the test image to it, and boot that. To go back, cold-swap the unit's own chip in again. It's never
been written, so it boots as before. An image that changes the boot block (an OpenFrame 2's
complete firmware, say) needs `unprotect=1` in step 2 and all 16 blocks or blocks 1-15 written.
