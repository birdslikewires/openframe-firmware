#!/bin/bash
# test_of_firmware_boot.sh: of-firmware-boot (the boot-time update, package/of-firmware-boot)
# against a stand-in for of-firmware-update that exits as each case says, try by try: what
# it does after each (restart, or let the boot carry on), and that it never lets the boot
# carry on once a try hasn't verified, whatever a later try says. Also that it keeps only
# the newest of the updater's dump folders. Nothing real is touched: the script runs from a
# copy with its paths, the restart and the console calls changed for the test, in a
# directory of its own. Run on Linux (it reads /proc/uptime), as any user:
#   bash tools/test_of_firmware_boot.sh
# Prints one line per case; exits non-zero if any case fails.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
SRC=${1:-$HERE/../package/of-firmware-boot}
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
FAILS=0

# The stand-in updater: --check gives a stock O2 unit's check; otherwise the next exit code
# from $T/seq (2 once they run out, as a write that won't verify would), with a dump folder
# made as the real one makes it, and the lines the screen reads.
cat > "$T/upd" <<'EOF'
#!/bin/bash
T=$(dirname "$0")
say() { echo "of-firmware-update: $*"; }
say "firmware now: blocks that differ from 26.11.30: 5:o2-stock+ 6:o2-stock+ 7:o2-stock 8:o2-stock 9:o2-stock 10:blank"
say "plan: write blocks 10 5 6 7 8 9"
[ "${1:-}" = --check ] && exit 5
N=$(( $(cat "$T/runs" 2>/dev/null || echo 0) + 1 )); echo $N > "$T/runs"
RC=$(sed -n "${N}p" "$T/seq"); RC=${RC:-2}
D=$(mktemp -d "$T/dumps/of-firmware-update.XXXXXX"); head -c 1024 /dev/zero > "$D/pre1.bin"
case $RC in
	0) for n in 10 5 6 7 8 9; do say "block $n written and verified"; done
	   say "UPDATED: the firmware is 26.11.30, verified (read-back x); dumps in $D" ;;
	1) rm -rf "$D"; say "STOP: could not make a work directory. Nothing was written; the chip is as it was." ;;
	2) say "block 10 written and verified"; say "FAILED: block 5 does not verify after 3 tries. DO NOT REBOOT OR POWER OFF: the unit may not start." ;;
	3) rm -rf "$D"; say "this firmware is not one we know (block 7 matches no release or stock firmware we have), so it is left alone. Nothing was written." ;;
	4) rm -rf "$D"; say "flash blocks 10 are locked down until the next reset: reboot, then run this again" ;;
esac
exit $RC
EOF
chmod +x "$T/upd"
printf 'BirdsLikeWires OpenFrame EFI 26.11.30 (OF1)\n' > "$T/img"

# The script under test, from $SRC: its paths in $T; the restart says so and ends the run
# instead (as the real one's service waits there for the shutdown); no waits; and the
# console left alone (systemd's status, the kernel's log level, the splash).
sed -e "s|^PENDING=.*|PENDING=$T/pending|" -e "s|^LOCKREBOOT=.*|LOCKREBOOT=$T/lock-reboot|" \
    -e "s|^UPDATE=.*|UPDATE=$T/upd|" -e "s|^IMG=.*|IMG=$T/img|" -e "s|^TTY=.*|TTY=$T/tty|" \
    -e "s|^DUMPS=.*|DUMPS=\"$T/dumps/of-firmware-update.\"|" \
    -e 's|^\tsystemctl reboot$|\techo "TEST: restart"|' -e 's|^\texec sleep 120$|\texit 0|' \
    -e 's|sleep 1$|sleep 0.05|' -e 's|sleep 5; else sleep 60|sleep 0; else sleep 0|' \
    -e 's|kill -s RTMIN+21 1|:|' -e 's|dmesg -n|: dmesg -n|g' -e 's|busctl call|: busctl call|' \
    -e 's|plymouth --ping|false|' -e 's|setterm |: setterm |' "$SRC" > "$T/boot"
# (No DUMPS line to change in a script from before it pruned them: it then fails that case.)
grep -q "^UPDATE=$T/upd" "$T/boot" && grep -q 'echo "TEST: restart"' "$T/boot" ||
	{ echo "FAIL: couldn't make the test copy of $SRC"; exit 1; }

# case <name> <exit codes, try by try> <want: restart | carry-on> <want pending: kept | gone>
#      [lock-reboot flag set before: yes]
case_() {
	local name=$1 seq=$2 want=$3 wantp=$4 lock=${5:-} out got gotp runs
	rm -rf "$T/dumps" "$T/runs" "$T/lock-reboot"; mkdir -p "$T/dumps"; : > "$T/tty"
	tr ',' '\n' <<< "$seq" > "$T/seq"; touch "$T/pending"; [ "$lock" = yes ] && touch "$T/lock-reboot"
	out=$(timeout 60 bash "$T/boot" 2>&1)
	runs=$(cat "$T/runs" 2>/dev/null || echo 0)
	if grep -q "TEST: restart" <<< "$out"; then got=restart; else got=carry-on; fi
	[ -e "$T/pending" ] && gotp=kept || gotp=gone
	if [ "$got" = "$want" ] && [ "$gotp" = "$wantp" ]; then
		echo "ok   $name: $got after $runs tr$([ "$runs" = 1 ] && echo y || echo ies), pending $gotp"
	else
		echo "FAIL $name: $got after $runs tries, pending $gotp (want $want, pending $wantp)"
		sed 's/^/       /' <<< "$out" | grep -v ': block [0-9]* written' | tail -12
		FAILS=$((FAILS + 1))
	fi
}

case_ "updated"                                     0       restart  gone
case_ "refused, nothing written"                    1       carry-on kept
case_ "not recognised"                              3       carry-on gone
case_ "locked: restart once"                        4       restart  kept
case_ "still locked after the restart"              4       carry-on kept yes
case_ "didn't verify, then updated"                 2,0     restart  gone
case_ "didn't verify, then 'nothing written' (1)"   2,1,0   restart  gone
case_ "didn't verify, then not recognised (3)"      2,3,0   restart  gone
case_ "didn't verify, then locked (4)"              2,4,0   restart  gone
case_ "didn't verify, then 1, 3 and 4, then updated" 2,1,3,4,0 restart gone

# Many tries that don't verify: only the newest dump folders stay (the one kept before the
# last try, and the last try's own).
rm -rf "$T/dumps" "$T/runs"; mkdir -p "$T/dumps"; printf '2\n%.0s' $(seq 12) > "$T/seq"; echo 0 >> "$T/seq"; touch "$T/pending"
timeout 120 bash "$T/boot" > /dev/null 2>&1
N=$(ls -1d "$T/dumps/of-firmware-update."* 2>/dev/null | wc -l)
if [ "$N" -le 2 ]; then echo "ok   12 tries that didn't verify, then updated: $N dump folders left"
else echo "FAIL 12 tries that didn't verify, then updated: $N dump folders left (want 2 at most)"; FAILS=$((FAILS + 1)); fi

[ $FAILS = 0 ] && echo "All passed." || echo "$FAILS failed."
exit $FAILS
