#!/bin/bash
set -u
DAMAGED=8c8810078f671c3025870a377d07ef10; REPAIRED=9e68b8c7190033e7dc48a14356bf7692
m() { md5sum "$1" | cut -d' ' -f1; }
[ "$(m /tmp/nv-repaired.bin)" = $REPAIRED ] || { echo "STOP: repaired image md5 wrong"; exit 1; }
[ "$(stat -c %s /tmp/nv-repaired.bin)" = 65536 ] || { echo "STOP: size"; exit 1; }
# fh writes only when loaded with writable=1 unprotect=1 (an fh from before those options has none, and writes
# anyway): reload it if it is loaded without them.
for p in writable unprotect; do [ -e /sys/module/fh/parameters/$p ] && [ "$(cat /sys/module/fh/parameters/$p)" != Y ] && rmmod fh; done
lsmod | grep -qw fh || modprobe fh writable=1 unprotect=1 2>/dev/null || insmod "${FH_KO:?fh is not installed: set FH_KO to the path of fh.ko}" writable=1 unprotect=1 2>/dev/null || insmod "$FH_KO"
for i in 1 2; do dd if=/dev/fh bs=64k count=1 iflag=fullblock status=none of=/tmp/pre$i.bin; done
[ "$(m /tmp/pre1.bin)" = $DAMAGED ] && [ "$(m /tmp/pre2.bin)" = $DAMAGED ] || { echo "STOP: block 0 is not in the analysed state: $(m /tmp/pre1.bin) $(m /tmp/pre2.bin)"; exit 1; }
echo "pre-check ok: block 0 = damaged state, image = repaired"
dmesg -C 2>/dev/null; true
dd if=/tmp/nv-repaired.bin of=/dev/fh bs=64k count=1 iflag=fullblock conv=notrunc status=none; echo "dd exit $?"
dmesg | grep -i fh | tail -5
for i in 1 2; do dd if=/dev/fh bs=64k count=1 iflag=fullblock status=none of=/tmp/post$i.bin; done
echo "post: $(m /tmp/post1.bin) $(m /tmp/post2.bin)"
[ "$(m /tmp/post1.bin)" = $REPAIRED ] && [ "$(m /tmp/post2.bin)" = $REPAIRED ] && echo "REPAIR VERIFIED" || echo "REPAIR MISMATCH"
dd if=/dev/fh bs=64k iflag=fullblock status=none of=/tmp/full.bin; md5sum /tmp/full.bin
