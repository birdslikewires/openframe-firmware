#!/bin/bash
# test_of_firmware_update.sh: of-firmware-update against fake chips, from every known starting
# firmware, with interrupted and failing writes. Run as root on a Linux box (GNU dd), with:
#   D/ of-firmware-update, firmware-manifest.txt, mkmanifest.py, of1-blw-TARGET.bin,
#      o2-stock.bin, verizon.bin, telstra.bin, op1.rom (from openbeak.net/openframe/firmware/efi/),
#   and optionally old-release.bin (any earlier BirdsLikeWires release: not in the manifest, so
#   refused) and a block-10 development build as dev-block10.bin
#     sudo bash test_of_firmware_update.sh D TARGET-VERSION
# Prints one line per case; exits non-zero if any case fails.
set -u
D=$(cd "$1" && pwd); V=$2
U=$D/of-firmware-update; F=$(mktemp -d)/chip.bin; DUMPS=$(dirname $F)/dumps
export OF_FW_IMAGE=$D/of1-blw-$V.bin OF_FW_MANIFEST=$D/firmware-manifest.txt OF_FW_DUMPDIR=$DUMPS FH=$F
FAILS=0
b() { dd if="$1" bs=64k skip=$2 count=$3 status=none | md5sum | cut -c1-32; }
T15=$(b $OF_FW_IMAGE 1 15)
# run <name> <expected exit> <expected blocks 1-15: md5 | same | target | any> [VAR=value ...]
run() {
	local name=$1 want=$2 end=$3; shift 3
	local before; before=$(b $F 1 15)
	env "$@" bash $U ${ARGS:-} > /tmp/oftest.out 2>&1; local rc=$?
	local now; now=$(b $F 1 15)
	case $end in same) end=$before ;; target) end=$T15 ;; any) end=$now ;; esac
	if [ $rc = $want ] && [ $now = $end ]; then echo "ok   $name (exit $rc)"
	else echo "FAIL $name: exit $rc (want $want), blocks 1-15 $now (want $end)"; sed 's/^/       /' /tmp/oftest.out; FAILS=$((FAILS + 1)); fi
}
start() { cp "$1" $F; }

for s in o2-stock verizon telstra; do start $D/$s.bin; run "$s -> $V" 0 target; done
[ -f $D/old-release.bin ] && { start $D/old-release.bin; run "an earlier release (not in the manifest): refused" 3 same; }
start $OF_FW_IMAGE; run "$V -> $V: nothing to do" 0 same
if [ -f $D/old-release.bin ]; then
	OLDV=$(grep -aoE 'BirdsLikeWires OpenFrame EFI [0-9.]+ \(OF1\)' $D/old-release.bin | head -1 | awk '{print $4}')
	python3 -I $D/mkmanifest.py $(dirname $F)/old-manifest.txt $OLDV=$D/old-release.bin $V=$OF_FW_IMAGE >/dev/null
	start $OF_FW_IMAGE; run "$V with an older image ($OLDV): left alone" 0 same OF_FW_IMAGE=$D/old-release.bin OF_FW_MANIFEST=$(dirname $F)/old-manifest.txt
fi
[ -f $D/dev-block10.bin ] && { start $D/dev-block10.bin; run "block-10 dev build -> $V (headers last)" 0 target; }

start $D/op1.rom; run "op1: boot block differs, refused" 3 same
ls $DUMPS/of1-firmware-*.bin >/dev/null 2>&1 && echo "ok   op1: dump saved" || { echo "FAIL op1: no dump"; FAILS=$((FAILS + 1)); }
start $D/o2-stock.bin; printf 'X' | dd of=$F bs=1 seek=$((7 * 65536 + 4000)) conv=notrunc status=none
run "stock with an unknown block 7: refused" 3 same
start $D/o2-stock.bin; head -c 100 /dev/urandom | dd of=$F bs=1 seek=$((10 * 65536 + 500)) conv=notrunc status=none
run "stock with junk in block 10 (an earlier first write, cut off): rewritten" 0 target
start $D/o2-stock.bin; head -c 100 /dev/urandom | dd of=$F bs=1 seek=$((10 * 65536 + 500)) conv=notrunc status=none
run "  ... and it never verifies: put back blank, as stock has it, safe" 1 $(b $D/o2-stock.bin 1 15) FLASH_TEST_FAULT_BLOCK=10 FLASH_TEST_FAULT=3

ARGS=--check; start $D/o2-stock.bin; run "--check on stock: update needed" 5 same
start $OF_FW_IMAGE; run "--check on $V: up to date" 0 same; ARGS=

start $D/o2-stock.bin; run "stock, power cut after block 10" 9 any FLASH_TEST_STOP=10
run "  ... re-run completes" 0 target
start $D/o2-stock.bin; run "stock, power cut after block 6" 9 any FLASH_TEST_STOP=6
head -c 30000 /dev/urandom | dd of=$F bs=1 seek=$((7 * 65536 + 100)) conv=notrunc status=none
run "  ... block 7 torn too: re-run rewrites it" 0 target
start $D/o2-stock.bin; run "stock, block 10 never verifies: put back blank, safe" 1 same FLASH_TEST_FAULT_BLOCK=10 FLASH_TEST_FAULT=3
start $D/o2-stock.bin; run "stock, block 10 and the blank never verify" 2 any FLASH_TEST_FAULT_BLOCK=10 FLASH_TEST_FAULT=3 FLASH_TEST_FAULT_BLANK=3
start $D/o2-stock.bin; run "stock, block 7 fails once, retried" 0 target FLASH_TEST_FAULT_BLOCK=7 FLASH_TEST_FAULT=1
start $D/o2-stock.bin; run "stock, block 7 never verifies" 2 any FLASH_TEST_FAULT_BLOCK=7 FLASH_TEST_FAULT=3
run "  ... re-run completes" 0 target
if [ -f $D/dev-block10.bin ]; then
	for s in 10.1 10.2; do
		start $D/dev-block10.bin; run "block-10 dev build, power cut after step $s" 9 any FLASH_TEST_STOP=$s
		run "  ... re-run completes" 0 target
	done
	start $D/dev-block10.bin; run "block-10 dev build, bodies never verify" 2 any FLASH_TEST_FAULT_BLOCK=10 FLASH_TEST_FAULT=3
	run "  ... re-run completes" 0 target
fi
cp $OF_FW_IMAGE $(dirname $F)/img.bin; printf 'X' | dd of=$(dirname $F)/img.bin bs=1 seek=$((5 * 65536)) conv=notrunc status=none
start $D/o2-stock.bin; run "an image whose blocks aren't in the manifest: refused" 1 same OF_FW_IMAGE=$(dirname $F)/img.bin
md5sum $OF_FW_IMAGE | sed 's/^0/1/;t;s/^./0/' > $(dirname $F)/img2.bin.md5; cp $OF_FW_IMAGE $(dirname $F)/img2.bin   # (first digit changed)
start $D/o2-stock.bin; run "an image that doesn't match its .md5: refused" 1 same OF_FW_IMAGE=$(dirname $F)/img2.bin

# Later releases of the block-10 layout, from this one: the next (its marker's version one on)
# with block 9 changed too, and another with block 7 changed. Block 10 goes headers last around
# the other block: its bodies (headers blank), the other block, the driver's header, the marker's.
NV=$(echo $V | awk -F. '{ printf "%s.%s.%d", $1, $2, $3 + 1 }')
mknext() {	# <out> <the other block to change>
	cp $OF_FW_IMAGE $1
	python3 -I - "$1" "$2" "$V" "$NV" <<-'EOF'
	import sys
	p, blk, v, nv = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4]
	d = bytearray(open(p, 'rb').read())
	old, new = [('BirdsLikeWires OpenFrame EFI %s (OF1)' % x).encode() for x in (v, nv)]
	if len(old) != len(new) or d.count(old) != 1: sys.exit('no next release from this one')
	i = d.index(old); d[i:i + len(new)] = new
	o = blk * 65536 + 4096; d[o:o + 16] = bytes(x ^ 0x5a for x in d[o:o + 16])
	open(p, 'wb').write(d)
	EOF
}
X=$(dirname $F)
if mknext $X/next9.bin 9 && mknext $X/next7.bin 7; then
	for k in 9 7; do
		python3 -I $D/mkmanifest.py $X/next$k.txt $V=$OF_FW_IMAGE $NV=$X/next$k.bin >/dev/null
		NX="OF_FW_IMAGE=$X/next$k.bin OF_FW_MANIFEST=$X/next$k.txt"
		WANT=$(b $X/next$k.bin 1 15)
		start $OF_FW_IMAGE; run "$V -> $NV with block $k changed" 0 $WANT $NX
		# In order: bodies, block k, driver header, marker header.
		ORDERSEEN=$(grep -oE 'block 10: (bodies|driver header|marker header) written|block [0-9]+ written and verified' /tmp/oftest.out | sed -E 's/block 10: //; s/ written.*//; s/^block ([0-9]+)$/\1/' | paste -sd, -)
		if [ "$ORDERSEEN" = "bodies,$k,driver header,marker header" ]; then echo "ok     ... in order: $ORDERSEEN"
		else echo "FAIL   ... in order: $ORDERSEEN (want bodies,$k,driver header,marker header)"; FAILS=$((FAILS + 1)); fi
		for s in 10.1 $k 10.2; do
			start $OF_FW_IMAGE; run "$V -> $NV (block $k), power cut after step $s" 9 any $NX FLASH_TEST_STOP=$s
			run "  ... re-run completes" 0 $WANT $NX
		done
		start $OF_FW_IMAGE; run "$V -> $NV (block $k), cut after the bodies" 9 any $NX FLASH_TEST_STOP=10.1
		head -c 3000 /dev/urandom | dd of=$F bs=1 seek=$((k * 65536 + 200)) conv=notrunc status=none
		run "  ... and block $k torn too: re-run rewrites it" 0 $WANT $NX
	done
else
	echo "note: no next release could be made from $V (its version's length would change): skipped"
fi

# Stop signals part way through (a shutdown, a Ctrl-C, a dropped session): once writing, the
# updater (and its writes) ignore them and finish. A second a block, so they land mid-update.
start $D/o2-stock.bin
env FLASH_TEST_PAUSE=1 bash $U > /tmp/oftest.out 2>&1 &
P=$!
for i in $(seq 100); do grep -q "block 10 written" /tmp/oftest.out && break; sleep 0.1; done
for SIG in TERM INT HUP; do kill -s $SIG $P 2>/dev/null; pkill --signal $SIG -P $P 2>/dev/null; done
wait $P; rc=$?
if [ $rc = 0 ] && [ "$(b $F 1 15)" = $T15 ]; then echo "ok   stock, stop signals after block 10: finished anyway (exit 0)"
else echo "FAIL stock, stop signals after block 10: exit $rc, blocks 1-15 $(b $F 1 15) (want 0, $T15)"; sed 's/^/       /' /tmp/oftest.out; FAILS=$((FAILS + 1)); fi

rm -rf $(dirname $F) /tmp/of-firmware-update.*
[ $FAILS = 0 ] && echo "all passed" || echo "$FAILS FAILED"
exit $FAILS
