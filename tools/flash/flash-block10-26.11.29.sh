#!/bin/bash
# flash-block10-26.11.29.sh: update an OpenFrame 1 that already has the block-10 layout (version marker and
# BlwPlatformDxe alone in block 10: 26.11.29 on, and its development builds) to 26.11.29 by writing ONLY block 10,
# headers last. Never touches block 0, the boot block, or any stock code. A chip on 26.11.28 or earlier needs
# flash-to-block10-26.11.29.sh instead. Run as root on the unit, with the image beside it in /tmp:
#     sudo bash flash-block10-26.11.29.sh
# Order, so that every moment of the write leaves a chip the firmware can boot:
#   1. erase block 10 and program both files' bodies with BOTH headers left blank (FF). The firmware's file walk
#      stops at a blank header, so the chip boots like stock firmware (no BlwPlatformDxe, no marker);
#   2. program the driver's 24-byte header (/dev/fhp: program only, no erase). The marker header is still blank,
#      so the walk still stops at 0xA0000;
#   3. program the marker's header (/dev/fhp), which programs in address order, so its State byte goes in last.
# Exit status: 0 done and verified (or already there); 1 refused or nothing written (rebooting is safe);
# 2 not verified after 3 tries: DO NOT REBOOT OR POWER OFF, run this again (it starts again from step 1).
# Needs fh with /dev/fhp (openframe-firmware 0745fbc on); FH_KO=/path/to/fh.ko loads one that isn't installed.
# Test mode: FH=/path/to/a/1MB/file works on that file (no module, no lock registers; step 1 writes it as an erase
# would, steps 2 and 3 as programming would: ANDed in, refused if a bit would need setting); FLASH_TEST_FAULT=N
# corrupts the block on its first N step-1 writes; FLASH_TEST_STOP=1|2 stops (exit 3) after that step, leaving the
# file as a power cut there would. None of these can touch a real chip.
set -u
NEW=26.11.29; FROM=       # FROM: the previous block-10 release (none before 26.11.29)
IMG=${IMG:-/tmp/of1-blw-$NEW.bin}; IMG_MD5=af42bbbad500c2959ca2df75da4ed529
OUT10=d11b766adc57bd8f676fd831ad0845e1     # md5 of blocks 1-9 and 11-15: every block-10-layout release has these
FROM10=; NEW10=16450e6793e7991325f7144e94e75036
TRIES=3
FH=${FH:-/dev/fh}; FHP=${FHP:-/dev/fhp}; TEST=0; [ -f "$FH" ] && { TEST=1; FHP=$FH; }

refuse() { [ -n "${W:-}" ] && rm -rf "$W"; echo "STOP: $*. Nothing was written; the chip is as it was."; exit 1; }
m() { md5sum "$1" | cut -d' ' -f1; }
blk() { dd if="$1" bs=64k skip="$2" count="$3" iflag=fullblock status=none; }
bm() { blk "$1" "$2" 1 | md5sum | cut -d' ' -f1; }
out10() { { blk "$1" 1 9; blk "$1" 11 5; } | md5sum | cut -d' ' -f1; }
readchip() { dd if="$FH" bs=64k iflag=fullblock status=none of="$1"; }
blank() { head -c "$3" /dev/zero | tr '\0' '\377' | dd of="$1" bs=1 seek="$2" conv=notrunc status=none; }
u24() { od -An -tu1 -j"$2" -N3 "$1" | awk '{print $1 + $2 * 256 + $3 * 65536}'; }

[ "$(id -u)" = 0 ] || refuse "run it as root (sudo)"
W=$(mktemp -d /tmp/flash-$NEW.XXXXXX) || refuse "could not make a work directory"
[ -f "$IMG" ] && [ "$(m "$IMG")" = $IMG_MD5 ] || refuse "$IMG is missing or its md5 is wrong"
blk "$IMG" 10 1 > "$W/full.bin"
[ "$(m "$W/full.bin")" = $NEW10 ] || refuse "block 10 extract wrong"
# The marker is the block's first file; the driver follows at the next 8-byte boundary.
MSIZE=$(u24 "$W/full.bin" 20); DRV=$(( (MSIZE + 7) & ~7 ))
[ "$MSIZE" -ge 24 ] && [ $DRV -lt 65512 ] && [ "$(u24 "$W/full.bin" $((DRV + 20)))" -ge 24 ] || refuse "block 10 of $IMG is not marker + driver"
cp "$W/full.bin" "$W/bodies.bin"; blank "$W/bodies.bin" 0 24; blank "$W/bodies.bin" $DRV 24
cp "$W/full.bin" "$W/drvhdr.bin"; blank "$W/drvhdr.bin" 0 24
BODIES=$(m "$W/bodies.bin"); DRVHDR=$(m "$W/drvhdr.bin")
[ "$(head -c 24 "$W/bodies.bin" | tr -d '\377' | wc -c)" = 0 ] || refuse "bodies image still has a marker header"

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
	# Needs an fh that is writable and has /dev/fhp: reload it if the loaded one isn't, preferring FH_KO if given.
	if lsmod | grep -qw fh; then
		{ [ -e "$FHP" ] && [ "$(cat /sys/module/fh/parameters/writable 2>/dev/null)" = Y ]; } || rmmod fh || refuse "could not unload fh"
	fi
	if ! lsmod | grep -qw fh; then
		if [ -n "${FH_KO:-}" ]; then insmod "$FH_KO" writable=1 || refuse "could not load $FH_KO"
		else modprobe fh writable=1 || refuse "could not load fh (set FH_KO to the path of an fh.ko with /dev/fhp)"; fi
	fi
	for i in 1 2 3 4 5; do [ -e "$FHP" ] && break; sleep 0.2; done
	[ -e "$FHP" ] || refuse "this fh has no $FHP (it needs openframe-firmware 0745fbc or later): set FH_KO"
	[ "$(cat /sys/module/fh/parameters/writable)" = Y ] || refuse "fh is not writable"
else
	echo "TEST MODE: working on the file $FH, not the chip"
fi

for i in 1 2; do readchip "$W/pre$i.bin" || refuse "could not read the chip"; done
[ "$(m "$W/pre1.bin")" = "$(m "$W/pre2.bin")" ] || refuse "two reads of the chip differ"
[ "$(out10 "$W/pre1.bin")" = $OUT10 ] || refuse "blocks 1-9 and 11-15 are not the block-10 layout's (this script needs a chip already moved to block 10)"
b10=$(bm "$W/pre1.bin" 10)
if [ $b10 = $NEW10 ]; then echo "The chip already holds $NEW: nothing to do."; exit 0
elif [ -n "$FROM10" ] && [ $b10 = $FROM10 ]; then echo "pre-check ok: chip = $FROM, full read $(m "$W/pre1.bin")"
else WHOSE="$NEW's"; [ -n "$FROM" ] && WHOSE="$FROM's or $NEW's"
	echo "pre-check: blocks outside 10 are right; block 10 is not $WHOSE (a development build, or an interrupted update): writing it"
fi

# put <device> <file>: write the 64 KB file to block 10 of <device>. In test mode /dev/fhp is emulated on the file:
# refused (nothing written) if any bit would go 0 -> 1, else ANDed in.
put() {
	if [ $TEST = 1 ] && [ "$1" = fhp ]; then
		python3 - "$FH" "$2" <<-'EOF'
		import sys
		chip, new = sys.argv[1], open(sys.argv[2], 'rb').read()
		with open(chip, 'r+b') as f:
		    f.seek(10 * 65536); was = f.read(65536)
		    if any((w & n) != n for w, n in zip(was, new)): sys.exit("fhp: a bit would need setting; nothing written")
		    f.seek(10 * 65536); f.write(bytes(w & n for w, n in zip(was, new)))
		EOF
	else
		dd if="$2" of="$([ "$1" = fhp ] && echo "$FHP" || echo "$FH")" bs=64k seek=10 count=1 iflag=fullblock conv=notrunc status=none
	fi
}
# verify <md5>: two reads of the chip, block 10 = md5 and everything outside 10 untouched.
verify() { readchip "$W/r1.bin"; readchip "$W/r2.bin"
	[ "$(bm "$W/r1.bin" 10)" = $1 ] && [ "$(bm "$W/r2.bin" 10)" = $1 ] && [ "$(out10 "$W/r1.bin")" = $OUT10 ]; }
stop_test() { [ $TEST = 1 ] && [ "${FLASH_TEST_STOP:-0}" = $1 ] && { echo "TEST: stopped after step $1"; exit 3; }; return 0; }

ok=0
for try in $(seq 1 $TRIES); do
	put fh "$W/bodies.bin" || echo "step 1, try $try: the write failed"
	[ $TEST = 1 ] && [ "${FLASH_TEST_FAULT:-0}" -ge "$try" ] && printf '\x5a' | dd of="$FH" bs=1 seek=$((10 * 65536 + 300)) conv=notrunc status=none
	verify $BODIES && { echo "step 1: block 10 erased and the bodies written (headers blank: the chip now boots stock-like)"; ok=1; break; }
	echo "step 1, try $try: the read-back does not match"
done
[ $ok = 1 ] || { echo "FLASH FAILED: block 10 does not verify after $TRIES tries. DO NOT REBOOT OR POWER OFF: the unit may not start."
	echo "Run this script again; if it still fails, leave the unit on and get help. Dumps in $W"; exit 2; }
stop_test 1
put fhp "$W/drvhdr.bin" && verify $DRVHDR || { echo "FLASH FAILED at step 2 (driver header). DO NOT REBOOT OR POWER OFF: run this script again. Dumps in $W"; exit 2; }
echo "step 2: driver header written"
stop_test 2
put fhp "$W/full.bin" && verify $NEW10 || { echo "FLASH FAILED at step 3 (marker header). DO NOT REBOOT OR POWER OFF: run this script again. Dumps in $W"; exit 2; }
echo "step 3: marker header written"
cmp -s <(head -c 65536 "$W/pre1.bin") <(head -c 65536 "$W/r1.bin") && echo "block 0 (NVRAM) unchanged by the flash" || echo "NOTE: block 0 differs from before the flash"
echo "FLASH VERIFIED: the chip holds $NEW (read-back $(m "$W/r1.bin")); dumps kept in $W"
exit 0
