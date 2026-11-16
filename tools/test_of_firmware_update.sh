#!/bin/bash
# test_of_firmware_update.sh: of-firmware-update against fake chips, from every known starting
# firmware, with power cuts after every step and failing writes. Run as root on a Linux box (GNU
# dd, Python 3), with:
#   D/ of-firmware-update, fwplan.py, mkmanifest.py, release.bin (the release under test, of the
#      block-11 layout), o2-stock.bin, verizon.bin, telstra.bin, op1.rom (from
#      openbeak.net/openframe/firmware/efi/); optionally r30.bin (release 26.11.30, the old
#      layout), of2-cisco.bin (an OpenFrame 2: not ours to update yet) and old-release.bin (an
#      earlier release that isn't in the manifest)
#     sudo bash test_of_firmware_update.sh D
# Made here from the release: "old", its files moved into block 10 as releases up to 26.11.30 had
# them (its version's digits all 0), and "next", one byte of its driver changed (digits all 9).
# Prints one line per case; exits non-zero if any case fails.
set -u
D=$(cd "$1" && pwd)
U=$D/of-firmware-update; T=$(mktemp -d); F=$T/chip.bin; DUMPS=$T/dumps
export OF_FW_PLAN=$D/fwplan.py OF_FW_DUMPDIR=$DUMPS FH=$F
FAILS=0

# The releases, and a checker: "ours.py CHIP START REL" = the chip runs REL's files, and every
# block but 9-11 is START's (block 9 only by its spacer headers).
cat > $T/mk.py <<'PY'
import re, sys
sys.path.insert(0, sys.argv[1])
import fwplan as fp
D, T = sys.argv[1], sys.argv[2]
rel = bytearray(open(D + '/release.bin', 'rb').read())
fp.release_files(rel)
m = re.search(rb'BirdsLikeWires OpenFrame EFI ([0-9.]+(-dev[0-9]+)?) \(OF1\)', rel)
v = m.group(1).decode()


def mark(img, ver):
    i = img.find(m.group(0))
    img[i:i + len(m.group(0))] = m.group(0).replace(v.encode(), ver.encode())


old, nxt = bytearray(rel), bytearray(rel)
old[fp.GATE:fp.FILES] = rel[fp.FILES:fp.FILES + fp.BLOCK]
old[fp.FILES:fp.FILES + fp.BLOCK] = b'\xff' * fp.BLOCK
mark(old, re.sub(r'[0-9]', '0', v))
mark(nxt, re.sub(r'[0-9]', '9', v))
o = fp.FILES + ((fp.size(nxt, fp.FILES) + 7) & ~7)
sz = fp.size(nxt, o)
nxt[o + sz // 2] ^= 0x55
nxt[o + 17] = 0
nxt[o + 17] = -sum(nxt[o + 24:o + sz]) & 0xFF      # the driver has a data checksum
assert fp.state(bytes(old))['old_layout'] and fp.state(bytes(nxt))['files_reached']
open(T + '/old.bin', 'wb').write(old)
open(T + '/rel.bin', 'wb').write(rel)
open(T + '/next.bin', 'wb').write(nxt)
print(v)
PY
cat > $T/ours.py <<'PY'
import sys
sys.path.insert(0, sys.argv[4])
import fwplan as fp
chip, start, rel = (open(p, 'rb').read() for p in sys.argv[1:4])
B = fp.BLOCK
st = fp.state(chip)
c, s = fp.canon(chip)[0], fp.canon(start)[0]
bad = [n for n in (1, 2, 3, 4, 5, 6, 7, 8, 9, 12, 13, 14, 15) if c[n * B:(n + 1) * B] != s[n * B:(n + 1) * B]]
ok = st['files_reached'] and not st['old_layout'] and chip[11 * B:12 * B] == rel[11 * B:12 * B] and not bad
print('ok' if ok else 'files %s, blocks changed %s' % (st['files_reached'], bad))
PY
V=$(python3 -I $T/mk.py "$D" "$T") || { echo "FAIL: could not make the test releases from $D/release.bin"; exit 1; }
VO=$(echo "$V" | tr '0-9' '0'); VN=$(echo "$V" | tr '0-9' '9')
STOCK="o2-stock=$D/o2-stock.bin verizon=$D/verizon.bin telstra=$D/telstra.bin"
[ -f $D/r30.bin ] && STOCK="$STOCK 26.11.30=$D/r30.bin"
python3 -I $D/mkmanifest.py $T/mrel.txt $STOCK $VO=$T/old.bin $V=$T/rel.bin >/dev/null
python3 -I $D/mkmanifest.py $T/mnext.txt $STOCK $VO=$T/old.bin $V=$T/rel.bin $VN=$T/next.bin >/dev/null
echo "release under test: $V (made here: $VO in the old layout, $VN)"
to() { export OF_FW_IMAGE=$T/$1.bin OF_FW_MANIFEST=$T/m$1.txt; }

b() { dd if="$1" bs=64k skip=$2 count=$3 status=none | md5sum | cut -c1-32; }
# run <name> <expected exit> <expected end: same | ours | any> [VAR=value ...]
run() {
	local name=$1 want=$2 end=$3; shift 3
	local before; before=$(b $F 0 16)
	env "$@" bash $U ${ARGS:-} > $T/out 2>&1; local rc=$?
	local got=ok
	case $end in
		same) [ "$(b $F 0 16)" = $before ] || got="the chip changed" ;;
		ours) got=$(python3 -I $T/ours.py $F $START $OF_FW_IMAGE $D) ;;
	esac
	if [ $rc = $want ] && [ "$got" = ok ]; then echo "ok   $name (exit $rc)"
	else echo "FAIL $name: exit $rc (want $want), $got"; sed 's/^/       /' $T/out; FAILS=$((FAILS + 1)); fi
}
start() { cp "$1" $F; cp "$1" $T/start.bin; START=$T/start.bin; }
steps() { rm -rf $T/p; python3 -I $D/fwplan.py plan $F $OF_FW_IMAGE $T/p >/dev/null && wc -l < $T/p/steps.txt; }

to rel
for s in o2-stock verizon telstra; do start $D/$s.bin; run "$s -> $V" 0 ours; done
start $T/old.bin; run "$VO (old layout: files in block 10) -> $V" 0 ours
[ -f $D/r30.bin ] && { start $D/r30.bin; run "26.11.30 -> $V" 0 ours; }
start $T/rel.bin; run "$V -> $V: nothing to do" 0 same
grep -q "nothing to do" $T/out || { echo "FAIL   ... it didn't say so"; FAILS=$((FAILS + 1)); }
start $D/o2-stock.bin; bash $U >/dev/null 2>&1; cp $F $T/c-rel.bin
to next; start $T/c-rel.bin; run "$V -> $VN (block 10 steps)" 0 ours
[ "$(b $F 9 1)" = "$(b $T/c-rel.bin 9 1)" ] && echo "ok     ... block 9 unchanged" || { echo "FAIL   ... block 9 changed"; FAILS=$((FAILS + 1)); }
cp $F $T/c-next.bin
to rel; start $T/c-next.bin; run "$VN with the $V image: left alone" 0 same OF_FW_MANIFEST=$T/mnext.txt
to next; start $T/old.bin; run "$VO -> $VN" 0 ours

to rel
start $D/op1.rom; run "op1: boot block differs, refused" 3 same
ls $DUMPS/of1-firmware-*.bin >/dev/null 2>&1 && echo "ok   op1: dump saved" || { echo "FAIL op1: no dump"; FAILS=$((FAILS + 1)); }
[ -f $D/of2-cisco.bin ] && { start $D/of2-cisco.bin; run "an OpenFrame 2: not known yet, refused" 3 same; }
[ -f $D/old-release.bin ] && { start $D/old-release.bin; run "an earlier release (not in the manifest): refused" 3 same; }
start $D/o2-stock.bin; printf 'X' | dd of=$F bs=1 seek=$((7 * 65536 + 4000)) conv=notrunc status=none; cp $F $START
run "stock with an unknown block 7: refused" 3 same
start $T/old.bin; printf 'X' | dd of=$F bs=1 seek=$((10 * 65536 + 4000)) conv=notrunc status=none; cp $F $START
run "old layout with files we don't know in block 10: refused" 3 same
start $D/o2-stock.bin; head -c 3000 /dev/urandom | dd of=$F bs=1 seek=$((10 * 65536 + 500)) conv=notrunc status=none
head -c 3000 /dev/urandom | dd of=$F bs=1 seek=$((11 * 65536 + 9000)) conv=notrunc status=none
run "stock with junk in blocks 10 and 11 (out of reach): rewritten" 0 ours
start $D/o2-stock.bin; run "an image of the old layout: refused" 1 same OF_FW_IMAGE=$T/old.bin

ARGS=--check; start $D/o2-stock.bin; run "--check on stock: update needed" 5 same
start $T/c-rel.bin; run "--check on $V: up to date" 0 same; ARGS=

# A power cut after every step, then a re-run, from each kind of start.
for s in o2-stock:rel:$D/o2-stock.bin $VO:rel:$T/old.bin $V:next:$T/c-rel.bin; do
	n=${s%%:*}; v=$(echo $s | cut -d: -f2); p=${s#*:*:}
	to $v; cp $p $F; N=$(steps)
	for k in $(seq 1 $N); do
		start $p; run "$n -> $v, power cut after step $k of $N" 9 any FLASH_TEST_STOP=$k
		run "  ... re-run completes" 0 ours
	done
done
to rel
start $D/o2-stock.bin; run "stock, step 2 lost once, retried" 0 ours FLASH_TEST_FAULT_STEP=2 FLASH_TEST_FAULT=1
start $T/old.bin; run "old layout, step 1 (closing the gate) never verifies" 2 any FLASH_TEST_FAULT_STEP=1 FLASH_TEST_FAULT=3
run "  ... re-run completes" 0 ours
start $T/old.bin; run "old layout, step 4 never verifies" 2 any FLASH_TEST_FAULT_STEP=4 FLASH_TEST_FAULT=3
run "  ... re-run completes" 0 ours

# A system on the internal memory: boot.nsh held to BlwMmcFix.efi + GRUB while the chip is
# written. A stand-in of-bootnsh writes the fallback lines while the flag is there, else the
# direct line.
B=$T/boot; mkdir -p $B
cat > $T/of-bootnsh <<'SH'
#!/bin/bash
# stand-in: knows /var/lib/openframe-firmware/writing (as $OF_FW_WRITING)
echo "$*" >> "$OF_FW_BOOT/calls"
if [ -e "$OF_FW_WRITING" ]; then printf 'BlwMmcFix.efi\nif %%lasterror%% == 0 then\n  vmlinuz root=x\nendif\ngrubq\n' > "$OF_FW_BOOT/boot.nsh"
else printf 'vmlinuz root=x\n' > "$OF_FW_BOOT/boot.nsh"; fi
SH
chmod +x $T/of-bootnsh; : > $T/BlwMmcFix.efi
MMC="OF_FW_TEST_MMC=1 OF_FW_BOOTNSH=$T/of-bootnsh OF_FW_BOOT=$B OF_FW_WRITING=$T/writing OF_FW_MMCFIX=$T/BlwMmcFix.efi"
nsh() {    # nsh <name> <direct|fallback> <flag: yes|no>
	local mode=direct; grep -q BlwMmcFix $B/boot.nsh && mode=fallback
	local flag=no; [ -e $T/writing ] && flag=yes
	if [ $mode = $2 ] && [ $flag = $3 ]; then echo "ok     ... boot.nsh $mode, flag $flag"
	else echo "FAIL   ... boot.nsh $mode (want $2), flag $flag (want $3)"; FAILS=$((FAILS + 1)); fi
}
to rel
printf 'vmlinuz root=x\n' > $B/boot.nsh; rm -f $T/writing
start $D/o2-stock.bin; run "MMC system: stock -> $V" 0 ours $MMC
nsh "after" direct no
grep -q "boot.nsh: starting through BlwMmcFix.efi" $T/out && echo "ok     ... it said so" || { echo "FAIL   ... no fallback line"; FAILS=$((FAILS + 1)); }
start $T/old.bin; run "MMC system: old layout, power cut after step 3" 9 any $MMC FLASH_TEST_STOP=3
nsh "cut" fallback yes
run "  ... re-run completes" 0 ours $MMC
nsh "re-run" direct no
touch $T/writing; OF_FW_BOOT=$B OF_FW_WRITING=$T/writing $T/of-bootnsh mac
start $T/c-rel.bin; run "MMC system: cut after the last write (flag left), chip done" 0 same $MMC
nsh "nothing to do" direct no
touch $T/writing; ARGS=--check; start $D/o2-stock.bin; run "MMC system: --check leaves the flag alone" 5 same $MMC; ARGS=
[ -e $T/writing ] && echo "ok     ... flag kept" || { echo "FAIL   ... --check removed the flag"; FAILS=$((FAILS + 1)); }
rm -f $T/writing; printf 'vmlinuz root=x\n' > $B/boot.nsh
sed 's|/var/lib/openframe-firmware/writing|(older)|' $T/of-bootnsh > $T/old-of-bootnsh; chmod +x $T/old-of-bootnsh
start $D/o2-stock.bin; run "MMC system with an older of-bootnsh: refused" 1 same $MMC OF_FW_BOOTNSH=$T/old-of-bootnsh
start $D/o2-stock.bin; run "MMC system without BlwMmcFix.efi: refused" 1 same $MMC OF_FW_MMCFIX=$T/none
printf '#!/bin/bash\n# /var/lib/openframe-firmware/writing\nexit 0\n' > $T/bad-of-bootnsh; chmod +x $T/bad-of-bootnsh
start $D/o2-stock.bin; run "MMC system, boot.nsh won't take the fallback: refused" 1 same $MMC OF_FW_BOOTNSH=$T/bad-of-bootnsh
start $D/o2-stock.bin; rm -f $T/writing; run "USB stick system: boot.nsh left alone" 0 ours OF_FW_BOOTNSH=$T/of-bootnsh OF_FW_BOOT=$B OF_FW_WRITING=$T/writing
[ ! -e $T/writing ] && echo "ok     ... no flag left" || { echo "FAIL   ... flag left"; FAILS=$((FAILS + 1)); }

rm -rf $T
[ $FAILS = 0 ] && echo "all passed" || { echo "$FAILS FAILED"; exit 1; }
