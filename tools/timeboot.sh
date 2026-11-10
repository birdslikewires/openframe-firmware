#!/bin/bash
# Clean shutdown, power cycle via smart plug, time plug-on -> SSH port open.
# Needs OF_HOST (see of.sh) and a shell function 'openframe on|off' that switches the unit's smart plug.
: "${OF_HOST:?set OF_HOST to the unit's address}"; H="$OF_HOST"
./of.sh 'echo joggler | sudo -S -p "" true; sudo systemctl poweroff' >/dev/null 2>&1
for i in $(seq 1 60); do ping6 -c1 -i1 "$H" >/dev/null 2>&1 || break; sleep 1; done
sleep 8
bash -lic 'openframe off' >/dev/null 2>&1; sleep 6
t0=$(python3 -c 'import time;print(time.time())')
bash -lic 'openframe on' >/dev/null 2>&1
while ! nc -z -G 1 "$H" 22 >/dev/null 2>&1; do sleep 0.25; done
t1=$(python3 -c 'import time;print(time.time())')
python3 -c "print('plug-on to ssh: %.1f s' % ($t1-$t0))"
k=$(./of.sh 'systemctl show ssh -p ActiveEnterTimestampMonotonic --value')
python3 -c "t=$t1-$t0; k=$k/1e6; print('kernel start to sshd: %.1f s, so plug-on to kernel: %.1f s' % (k, t-k))"
