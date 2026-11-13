#!/bin/bash
# flash-block9-26.11.28.sh: write ONLY block 9 of of1-blw-26.11.28.bin to an OpenFrame 1 holding 26.11.27, through
# fh. Never touches block 0 (NVRAM) or the boot block. Run as root on the unit, with the image beside it in /tmp:
#     sudo bash flash-block9-26.11.28.sh
# Exit status:
#   0  26.11.28 is on the chip, verified by two full read-backs (or it already was)
#   1  refused before writing anything: the chip is as it was, and rebooting is safe
#   2  block 9 was written but could not be verified after 3 tries: DO NOT REBOOT OR POWER OFF.
#      Run this script again (it repairs a torn block 9); if it still fails, keep the unit on and get help.
# Blocks 1-15 other than 9 must be exactly 26.11.27's (= 26.11.28's); block 9 may be 26.11.27's (the normal case),
# 26.11.28's (nothing to do), or anything else (an earlier run was interrupted: it is rewritten).
#
# Test mode, for checking this script without a chip: FH=/path/to/a/1MB/file makes it work on that file instead of
# /dev/fh (no module, no lock registers), and FLASH_TEST_FAULT=N corrupts the file's block 9 after each of the first
# N writes. Neither has any effect on a real chip: both need FH to be a regular file.
set -u
NEW=26.11.28; OLD=26.11.27
IMG=${IMG:-/tmp/of1-blw-$NEW.bin}; IMG_MD5=d0453018a998daa1713c292fa911730b
OUT9=43ddc7571f01c6d3d7446ba366114a9d      # md5 of blocks 1-8 and 10-15 (the same in 26.11.27 and 26.11.28)
OLD9=acafc0c5dcf16ad73e18b1c11fbe705a; NEW9=bd74f39ecc672a48f1f879d0ae828704
TRIES=3
FH=${FH:-/dev/fh}; TEST=0; [ -f "$FH" ] && TEST=1

refuse() { [ -n "${W:-}" ] && rm -rf "$W"; echo "STOP: $*. Nothing was written; the chip is as it was."; exit 1; }
m() { md5sum "$1" | cut -d' ' -f1; }
blk() { dd if="$1" bs=64k skip="$2" count="$3" iflag=fullblock status=none; }
out9() { { blk "$1" 1 8; blk "$1" 10 6; } | md5sum | cut -d' ' -f1; }
b9() { blk "$1" 9 1 | md5sum | cut -d' ' -f1; }
readchip() { dd if="$FH" bs=64k iflag=fullblock status=none of="$1"; }

[ "$(id -u)" = 0 ] || refuse "run it as root (sudo)"
W=$(mktemp -d /tmp/flash-$NEW.XXXXXX) || refuse "could not make a work directory"
[ -f "$IMG" ] && [ "$(m "$IMG")" = $IMG_MD5 ] || refuse "$IMG is missing or its md5 is wrong"
blk "$IMG" 9 1 > "$W/blk9.bin"
[ "$(m "$W/blk9.bin")" = $NEW9 ] && [ "$(stat -c %s "$W/blk9.bin")" = 65536 ] || refuse "block 9 extract wrong"

if [ $TEST = 0 ]; then
	# 26.11.23 on leaves the panel clock as the display driver set it (96 MHz reference). Linux's gma500 without
	# openframe-kernel patch 07 (6.18.63-4 on) misreads that and runs the panel at 30 Hz.
	KO=$(find /lib/modules/$(uname -r) -name 'gma500_gfx.ko*' | head -1)
	case "$KO" in *.xz) KCAT=xzcat ;; *.zst) KCAT=zstdcat ;; *.gz) KCAT=zcat ;; *) KCAT=cat ;; esac
	[ -n "$KO" ] && $KCAT "$KO" | grep -aq "pre-programmed mode is" || refuse "this kernel's gma500 lacks the 60 Hz panel fix: install 6.18.63-4 or later first"
	# Lock registers (block n at 0xFFB00002 + n * 0x10000): bit 1 set = locked down until reset (BlwMac.efi does
	# this when it writes NVRAM). fh can't unlock that, so say so before trying.
	LOCKED=$(python3 -c '
import mmap, os
f = os.open("/dev/mem", os.O_RDONLY | os.O_SYNC)
m = mmap.mmap(f, 0x100000, mmap.MAP_SHARED, mmap.PROT_READ, offset=0xFFB00000)
print(" ".join(str(b) for b in range(1, 16) if m[b * 0x10000 + 2] & 2))') || refuse "could not read the lock registers"
	[ -z "$LOCKED" ] || refuse "flash blocks $LOCKED are locked down until the next reset: reboot, then run this again"
	# fh writes only when loaded with writable=1 (an fh from before those options has none, and writes anyway):
	# reload it if it is loaded without them.
	for p in writable; do [ -e /sys/module/fh/parameters/$p ] && [ "$(cat /sys/module/fh/parameters/$p)" != Y ] && rmmod fh; done
	lsmod | grep -qw fh || modprobe fh writable=1 2>/dev/null || insmod "${FH_KO:?fh is not installed: set FH_KO to the path of fh.ko}" writable=1 2>/dev/null || insmod "$FH_KO"
	[ ! -e /sys/module/fh/parameters/writable ] || [ "$(cat /sys/module/fh/parameters/writable)" = Y ] || refuse "fh is not writable"
else
	echo "TEST MODE: working on the file $FH, not the chip"
fi

for i in 1 2; do readchip "$W/pre$i.bin" || refuse "could not read the chip"; done
[ "$(m "$W/pre1.bin")" = "$(m "$W/pre2.bin")" ] || refuse "two reads of the chip differ"
[ "$(out9 "$W/pre1.bin")" = $OUT9 ] || refuse "blocks 1-8 and 10-15 are not $OLD/$NEW's (this script is only for a chip holding $OLD)"
case "$(b9 "$W/pre1.bin")" in
	$NEW9) echo "The chip already holds $NEW: nothing to do."; exit 0 ;;
	$OLD9) echo "pre-check ok: chip = $OLD, full read $(m "$W/pre1.bin")" ;;
	*)     echo "pre-check: blocks outside 9 = $OLD, but block 9 is neither $OLD's nor $NEW's (an interrupted flash?): rewriting it" ;;
esac

for try in $(seq 1 $TRIES); do
	if ! dd if="$W/blk9.bin" of="$FH" bs=64k seek=9 count=1 iflag=fullblock conv=notrunc status=none; then
		echo "write $try: dd failed"
	fi
	if [ $TEST = 1 ] && [ "${FLASH_TEST_FAULT:-0}" -ge "$try" ]; then
		printf '\x5a' | dd of="$FH" bs=1 seek=$((9 * 65536 + 100)) conv=notrunc status=none   # test mode: a bad write
	fi
	[ $TEST = 0 ] && dmesg | grep -i 'fh:' | tail -2
	for i in 1 2; do readchip "$W/post$i.bin"; done
	if [ "$(m "$W/post1.bin")" = "$(m "$W/post2.bin")" ] && [ "$(out9 "$W/post1.bin")" = $OUT9 ] && [ "$(b9 "$W/post1.bin")" = $NEW9 ]; then
		cmp -s <(head -c 65536 "$W/pre1.bin") <(head -c 65536 "$W/post1.bin") && echo "block 0 (NVRAM) unchanged by the flash" || echo "NOTE: block 0 differs from before the flash"
		echo "FLASH VERIFIED: the chip holds $NEW (read-back $(m "$W/post1.bin")); dumps kept in $W"
		exit 0
	fi
	echo "write $try: the read-back does not match $NEW"
done
if [ "$(out9 "$W/post1.bin")" = $OUT9 ] && [ "$(b9 "$W/post1.bin")" = $OLD9 ]; then
	echo "STOP: block 9 could not be written; the chip still holds $OLD, so rebooting is safe. Dumps in $W"
	exit 1
fi
echo "FLASH FAILED: block 9 does not verify after $TRIES tries. DO NOT REBOOT OR POWER OFF: the unit may not start."
echo "Run this script again; if it still fails, leave the unit on and get help. Dumps in $W"
exit 2
