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
# A write that takes a while, for a stop signal to land in (as the real one's last ~30 s).
[ -n "${STANDIN_SLEEP:-}" ] && sleep "$STANDIN_SLEEP"
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
    -e "s|^LOGF=.*|LOGF=$T/last-flash.log|" \
    -e 's|^\tsystemctl --no-block start --job-mode=replace-irreversibly reboot.target$|\techo "TEST: restart"|' -e 's|^\texec sleep 120$|\texit 0|' \
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

# The log kept beside the pending mark (the journal goes with the restart): a fresh one each
# run, with the check, each try's output and what was decided.
logcheck() {
	local name=$1 seq=$2; shift 2
	rm -rf "$T/dumps" "$T/runs" "$T/lock-reboot"; mkdir -p "$T/dumps"; : > "$T/tty"
	echo "stale line from an earlier run" > "$T/last-flash.log"
	tr ',' '\n' <<< "$seq" > "$T/seq"; touch "$T/pending"
	timeout 60 bash "$T/boot" > /dev/null 2>&1
	local miss="" w
	grep -q "stale line" "$T/last-flash.log" && miss+=" (kept an earlier run's lines)"
	for w in "$@"; do grep -qF -- "$w" "$T/last-flash.log" || miss+=" [$w]"; done
	if [ -z "$miss" ]; then echo "ok   log, $name"
	else echo "FAIL log, $name: missing$miss"; sed 's/^/       /' "$T/last-flash.log" | tail -8; FAILS=$((FAILS + 1)); fi
}
logcheck "updated" 0 "of-firmware-boot 26.11" "s after power-on" "plan: write blocks" "UPDATED" "restarting: The firmware is up to date."
logcheck "not recognised" 3 "plan: write blocks" "is left alone" "firmware not recognised: left alone"
logcheck "didn't verify, then updated" 2,0 "FAILED: block 5" "didn't verify (exit 2)" "UPDATED"

# Many tries that don't verify: only the newest dump folders stay (the one kept before the
# last try, and the last try's own).
rm -rf "$T/dumps" "$T/runs"; mkdir -p "$T/dumps"; printf '2\n%.0s' $(seq 12) > "$T/seq"; echo 0 >> "$T/seq"; touch "$T/pending"
timeout 120 bash "$T/boot" > /dev/null 2>&1
N=$(ls -1d "$T/dumps/of-firmware-update."* 2>/dev/null | wc -l)
if [ "$N" -le 2 ]; then echo "ok   12 tries that didn't verify, then updated: $N dump folders left"
else echo "FAIL 12 tries that didn't verify, then updated: $N dump folders left (want 2 at most)"; FAILS=$((FAILS + 1)); fi

# A shutdown (or Ctrl-C, or a dropped session) part way through: the update carries on to
# the end, its restart included, and the stand-in, ignoring them as the updater's writes
# do, isn't stopped either.
rm -rf "$T/dumps" "$T/runs" "$T/lock-reboot"; mkdir -p "$T/dumps"; echo 0 > "$T/seq"; touch "$T/pending"
STANDIN_SLEEP=1.5 timeout 60 bash "$T/boot" > "$T/stop.out" 2>&1 &
P=$!
for i in $(seq 50); do [ -s "$T/runs" ] && break; sleep 0.1; done
sleep 0.3
for SIG in TERM INT HUP; do kill -s $SIG $P 2>/dev/null; pkill --signal $SIG -f "$T/upd" 2>/dev/null; done
wait $P
if grep -q "TEST: restart" "$T/stop.out" && grep -q "UPDATED" "$T/stop.out" && [ ! -e "$T/pending" ]; then
	echo "ok   stop signals part way through: updated and restarted"
else
	echo "FAIL stop signals part way through: no update and restart"
	sed 's/^/       /' "$T/stop.out" | grep -v ': block [0-9]* written' | tail -8
	FAILS=$((FAILS + 1))
fi

# of-firmware-generator: with an update pending, drop-ins that put of-firmware.service (and
# of-flash.service, ordered before it) ahead of basic.target; none with nothing pending, or
# on a stick holding OpenFrame Flash's image copy (it runs the updater itself).
GEN=${2:-$HERE/../package/of-firmware-generator}
sed -e "s|^PENDING=.*|PENDING=$T/gpending|" -e "s|^FLASHIMG=.*|FLASHIMG=\"$T/gimage $T/gimage2\"|" \
    -e "s|^UNITDIRS=.*|UNITDIRS=\"$T/getc $T/glib\"|" "$GEN" > "$T/gen"
# The units ordered before the update in this system: of-flash (OpenFrame Flash), and the
# of-installer it was before, as their unit files have them.
unit_before() {	# <dir> <name> <After= line>
	mkdir -p "$1"
	printf '[Unit]\nDescription=test\nAfter=%s\nBefore=of-firmware.service of-mac-reboot.service getty@tty1.service\n[Service]\nExecStart=/bin/true\n' "$3" > "$1/$2.service"
}
# gencase <name> <pending> <image copy: no | flash | install> <want: none | units moved, in order>
#         [units: flash | installer | both | late | masked]
gencase() {
	local name=$1 pending=$2 image=$3 want=$4 units=${5:-flash} got out U F
	rm -rf "$T/gout" "$T/gpending" "$T/gimage" "$T/gimage2" "$T/getc" "$T/glib"; mkdir -p "$T/gout" "$T/getc" "$T/glib"
	printf '[Unit]\nBefore=getty@tty1.service\n[Service]\nExecStart=/bin/true\n' > "$T/glib/of-firmware.service"
	printf '[Unit]\nAfter=of-firmware.service\n[Service]\nExecStart=/bin/true\n' > "$T/glib/of-mac-reboot.service"
	case $units in
		flash) unit_before "$T/glib" of-flash "local-fs.target systemd-udevd.service" ;;
		installer) unit_before "$T/glib" of-installer "local-fs.target systemd-udevd.service" ;;
		both) unit_before "$T/glib" of-flash "local-fs.target systemd-udevd.service"
		      unit_before "$T/glib" of-installer "local-fs.target systemd-udevd.service" ;;
		late) unit_before "$T/glib" of-flash "local-fs.target systemd-udevd.service"
		      unit_before "$T/glib" late-thing "network-online.target" ;;
		masked) unit_before "$T/glib" of-installer "local-fs.target systemd-udevd.service"
		        ln -s /dev/null "$T/getc/of-installer.service" ;;
	esac
	[ "$pending" = yes ] && touch "$T/gpending"
	[ "$image" = flash ] && touch "$T/gimage"; [ "$image" = install ] && touch "$T/gimage2"
	sh "$T/gen" "$T/gout" "$T/gout.early" "$T/gout.late"
	got=$(cd "$T/gout" && find . -name '*.conf' | sed 's|^\./||; s|\.service\.d/50-quiet-flash\.conf||' | sort | tr '\n' ' ')
	got=${got% }
	out=ok
	[ "${got:-none}" = "$want" ] || out=FAIL
	for U in $got; do
		F="$T/gout/$U.service.d/50-quiet-flash.conf"
		grep -qx 'DefaultDependencies=no' "$F" && grep -qx 'Before=basic.target' "$F" &&
			grep -qx 'After=sysinit.target' "$F" && grep -qx 'Conflicts=shutdown.target' "$F" || out=FAIL
	done
	[ "$out" = ok ] && echo "ok   generator, $name: ${got:-no drop-ins}" ||
		{ echo "FAIL generator, $name: ${got:-no drop-ins} (want $want)"; FAILS=$((FAILS + 1)); }
}
gencase "update pending (of-flash)"                  yes no      "of-firmware of-flash"
gencase "update pending (of-installer, older)"       yes no      "of-firmware of-installer" installer
gencase "update pending (both)"                      yes no      "of-firmware of-flash of-installer" both
gencase "of-installer masked in /etc"                yes no      "of-firmware" masked
gencase "a unit before it that waits for the network" yes no     none late
gencase "nothing pending"                            no  no      none
gencase "pending, on OpenFrame Flash's stick"        yes flash   none
gencase "pending, on an older installer's stick"     yes install none installer

[ $FAILS = 0 ] && echo "All passed." || echo "$FAILS failed."
exit $FAILS
