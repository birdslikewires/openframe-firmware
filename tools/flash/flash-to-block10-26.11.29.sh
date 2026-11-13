#!/bin/bash
# flash-to-block10-26.11.29.sh: 26.11.28 -> 26.11.29, the one-time move of the version marker and BlwPlatformDxe from
# block 9 to block 10 (from then on updates write block 10 only). Writes the image's blocks 10 and then 9 to an
# OpenFrame 1 holding $OLD, through fh. Never touches block 0 or the boot block. Run as root on the unit, with the
# image beside it in /tmp:
#     sudo bash flash-to-block10-26.11.29.sh
# Block 10 first: while block 9 still holds $OLD's marker and driver, the firmware's file list ends in block 9 and
# never reaches block 10, so the chip stays a working $OLD. Then block 9 (the end of the stock GOP, and the spacer
# that leads to block 10): the last time an update ever erases a block holding stock code.
# Exit status: 0 done and verified (or already there); 1 refused or nothing written (rebooting is safe);
# 2 not verified after 3 tries: DO NOT REBOOT OR POWER OFF, run this again (it rewrites a torn block).
# Test mode: FH=/path/to/a/1MB/file works on that file (no module, no lock registers); FLASH_TEST_FAULT=N corrupts
# the block just written on its first N writes (FLASH_TEST_FAULT_BLANK=N: the same for putting block 10 back to blank).
# None of these can touch a real chip.
set -u
NEW=26.11.29; OLD=26.11.28
IMG=${IMG:-/tmp/of1-blw-$NEW.bin}; IMG_MD5=af42bbbad500c2959ca2df75da4ed529
OUT910=afa006adba34399f60b4ad30e03e5819    # md5 of blocks 1-8 and 11-15 (the same in $OLD and $NEW)
OLD9=bd74f39ecc672a48f1f879d0ae828704; OLD10=ecb99e6ffea7be1e5419350f725da86b     # $OLD: block 10 is blank
NEW9=98192835762a6a466815df937e2facba; NEW10=16450e6793e7991325f7144e94e75036
TRIES=3
FH=${FH:-/dev/fh}; TEST=0; [ -f "$FH" ] && TEST=1

refuse() { [ -n "${W:-}" ] && rm -rf "$W"; echo "STOP: $*. Nothing was written; the chip is as it was."; exit 1; }
m() { md5sum "$1" | cut -d' ' -f1; }
blk() { dd if="$1" bs=64k skip="$2" count="$3" iflag=fullblock status=none; }
bm() { blk "$1" "$2" 1 | md5sum | cut -d' ' -f1; }
out910() { { blk "$1" 1 8; blk "$1" 11 5; } | md5sum | cut -d' ' -f1; }
readchip() { dd if="$FH" bs=64k iflag=fullblock status=none of="$1"; }

[ "$(id -u)" = 0 ] || refuse "run it as root (sudo)"
W=$(mktemp -d /tmp/flash-$NEW.XXXXXX) || refuse "could not make a work directory"
[ -f "$IMG" ] && [ "$(m "$IMG")" = $IMG_MD5 ] || refuse "$IMG is missing or its md5 is wrong"
blk "$IMG" 9 1 > "$W/b9.bin"; blk "$IMG" 10 1 > "$W/b10.bin"
[ "$(m "$W/b9.bin")" = $NEW9 ] && [ "$(m "$W/b10.bin")" = $NEW10 ] || refuse "block 9/10 extract wrong"

if [ $TEST = 0 ]; then
	KO=$(find /lib/modules/$(uname -r) -name 'gma500_gfx.ko*' | head -1)
	case "$KO" in *.xz) KCAT=xzcat ;; *.zst) KCAT=zstdcat ;; *.gz) KCAT=zcat ;; *) KCAT=cat ;; esac
	[ -n "$KO" ] && $KCAT "$KO" | grep -aq "pre-programmed mode is" || refuse "this kernel's gma500 lacks the 60 Hz panel fix: install 6.18.63-4 or later first"
	LOCKED=$(python3 -c '
import mmap, os
f = os.open("/dev/mem", os.O_RDONLY | os.O_SYNC)
m = mmap.mmap(f, 0x100000, mmap.MAP_SHARED, mmap.PROT_READ, offset=0xFFB00000)
print(" ".join(str(b) for b in range(1, 16) if m[b * 0x10000 + 2] & 2))') || refuse "could not read the lock registers"
	[ -z "$LOCKED" ] || refuse "flash blocks $LOCKED are locked down until the next reset: reboot, then run this again"
	for p in writable; do [ -e /sys/module/fh/parameters/$p ] && [ "$(cat /sys/module/fh/parameters/$p)" != Y ] && rmmod fh; done
	lsmod | grep -qw fh || modprobe fh writable=1 2>/dev/null || insmod "${FH_KO:?fh is not installed: set FH_KO to the path of fh.ko}" writable=1 2>/dev/null || insmod "$FH_KO"
	[ ! -e /sys/module/fh/parameters/writable ] || [ "$(cat /sys/module/fh/parameters/writable)" = Y ] || refuse "fh is not writable"
else
	echo "TEST MODE: working on the file $FH, not the chip"
fi

for i in 1 2; do readchip "$W/pre$i.bin" || refuse "could not read the chip"; done
[ "$(m "$W/pre1.bin")" = "$(m "$W/pre2.bin")" ] || refuse "two reads of the chip differ"
[ "$(out910 "$W/pre1.bin")" = $OUT910 ] || refuse "blocks 1-8 and 11-15 are not $OLD's (this script is only for a chip holding $OLD)"
b9=$(bm "$W/pre1.bin" 9); b10=$(bm "$W/pre1.bin" 10)
if [ $b9 = $NEW9 ] && [ $b10 = $NEW10 ]; then echo "The chip already holds $NEW: nothing to do."; exit 0; fi
if [ $b9 = $OLD9 ] && [ $b10 = $OLD10 ]; then echo "pre-check ok: chip = $OLD, full read $(m "$W/pre1.bin")"
else echo "pre-check: blocks outside 9 and 10 = $OLD; block 9 $([ $b9 = $OLD9 ] && echo "= $OLD" || { [ $b9 = $NEW9 ] && echo "= $NEW" || echo "torn"; }), block 10 $([ $b10 = $OLD10 ] && echo "= $OLD (blank)" || { [ $b10 = $NEW10 ] && echo "= $NEW" || echo "something else (torn, or a later release)"; }): carrying on"
fi

# writeblock <n> <file> <md5>: erase + program block n (fh), read back twice, up to $TRIES times. 0 = verified.
writeblock() {
	local n=$1 src=$2 want=$3 try
	for try in $(seq 1 $TRIES); do
		dd if="$src" of="$FH" bs=64k seek=$n count=1 iflag=fullblock conv=notrunc status=none || echo "block $n write $try: dd failed"
		if [ $TEST = 1 ] && [ "${FLASH_TEST_FAULT:-0}" -ge "$try" ]; then
			printf '\x5a' | dd of="$FH" bs=1 seek=$((n * 65536 + 200)) conv=notrunc status=none
		fi
		readchip "$W/r1.bin"; readchip "$W/r2.bin"
		[ "$(bm "$W/r1.bin" $n)" = $want ] && [ "$(bm "$W/r2.bin" $n)" = $want ] && { echo "block $n written and verified"; return 0; }
		echo "block $n write $try: the read-back does not match"
	done
	return 1
}
fail2() { echo "FLASH FAILED: block $1 does not verify after $TRIES tries. DO NOT REBOOT OR POWER OFF: the unit may not start."
	echo "Run this script again; if it still fails, leave the unit on and get help. Dumps in $W"; exit 2; }

if [ $b10 != $NEW10 ]; then
	writeblock 10 "$W/b10.bin" $NEW10 || {
		# Block 9 is still $OLD's, so the firmware's files end there. Put block 10 back to blank as well (what
		# $OLD has always booted with): only then is rebooting known to be safe.
		echo "block 10 does not verify: putting it back to blank"
		head -c 65536 /dev/zero | tr '\0' '\377' > "$W/blank.bin"
		[ "$(bm "$W/r1.bin" 9)" = $OLD9 ] && FLASH_TEST_FAULT=${FLASH_TEST_FAULT_BLANK:-0} writeblock 10 "$W/blank.bin" $OLD10 && [ "$(bm "$W/r1.bin" 9)" = $OLD9 ] &&
			{ echo "STOP: block 10 could not be written; it is blank again and block 9 is still $OLD's, so the chip is $OLD again: rebooting is safe. Dumps in $W"; exit 1; }
		fail2 10; }
fi
if [ $b9 != $NEW9 ]; then writeblock 9 "$W/b9.bin" $NEW9 || fail2 9; fi
for i in 1 2; do readchip "$W/post$i.bin"; done
if [ "$(m "$W/post1.bin")" = "$(m "$W/post2.bin")" ] && [ "$(out910 "$W/post1.bin")" = $OUT910 ] && [ "$(bm "$W/post1.bin" 9)" = $NEW9 ] && [ "$(bm "$W/post1.bin" 10)" = $NEW10 ]; then
	cmp -s <(head -c 65536 "$W/pre1.bin") <(head -c 65536 "$W/post1.bin") && echo "block 0 (NVRAM) unchanged by the flash" || echo "NOTE: block 0 differs from before the flash"
	echo "FLASH VERIFIED: the chip holds $NEW (read-back $(m "$W/post1.bin")); dumps kept in $W"
	exit 0
fi
fail2 "9/10"
