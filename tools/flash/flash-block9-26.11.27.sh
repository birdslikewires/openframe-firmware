#!/bin/bash
# flash-block9.sh: write ONLY block 9 of of1-blw-26.11.27.bin to the OF1 chip via fh. Never touches block 0.
set -u
IMG=/tmp/of1-blw-26.11.27.bin; IMG_MD5=1de33e3ff196ed516514d5557ff07e21; OLD15=654dfeef1c84d389096c91cae75e7156; NEW15=6e687d8748580951b985f5236696e4dd; BLK9=acafc0c5dcf16ad73e18b1c11fbe705a
m() { md5sum "$1" | cut -d' ' -f1; }
b15() { tail -c +$((0x10000+1)) "$1" | md5sum | cut -d' ' -f1; }
[ "$(m $IMG)" = $IMG_MD5 ] || { echo "STOP: image md5 wrong"; exit 1; }
# 26.11.23 on leaves the panel clock as the display driver set it (96 MHz reference). Linux's gma500
# without openframe-kernel patch 07 (6.18.63-4 on) misreads that and runs the panel at 30 Hz.
KO=$(find /lib/modules/$(uname -r) -name 'gma500_gfx.ko*' | head -1)
case "$KO" in *.xz) KCAT=xzcat ;; *.zst) KCAT=zstdcat ;; *.gz) KCAT=zcat ;; *) KCAT=cat ;; esac
[ -n "$KO" ] && $KCAT "$KO" | grep -aq "pre-programmed mode is" || { echo "STOP: this kernel's gma500 lacks the 60 Hz panel fix: install 6.18.63-4 or later first"; exit 1; }
dd if=$IMG bs=64k skip=9 count=1 iflag=fullblock status=none of=/tmp/blk9.bin
[ "$(m /tmp/blk9.bin)" = $BLK9 ] && [ "$(stat -c %s /tmp/blk9.bin)" = 65536 ] || { echo "STOP: block 9 extract wrong"; exit 1; }
# fh writes only when loaded with writable=1 (an fh from before those options has none, and writes
# anyway): reload it if it is loaded without them.
for p in writable; do [ -e /sys/module/fh/parameters/$p ] && [ "$(cat /sys/module/fh/parameters/$p)" != Y ] && rmmod fh; done
lsmod | grep -qw fh || modprobe fh writable=1 2>/dev/null || insmod "${FH_KO:?fh is not installed: set FH_KO to the path of fh.ko}" writable=1 2>/dev/null || insmod "$FH_KO"
for i in 1 2; do dd if=/dev/fh bs=64k iflag=fullblock status=none of=/tmp/pre$i.bin; done
[ "$(m /tmp/pre1.bin)" = "$(m /tmp/pre2.bin)" ] || { echo "STOP: two pre-reads differ"; exit 1; }
[ "$(b15 /tmp/pre1.bin)" = $OLD15 ] || { echo "STOP: blocks 1-15 are not 26.11.26: $(b15 /tmp/pre1.bin)"; exit 1; }
echo "pre-check ok: chip blocks 1-15 = 26.11.26, full read $(m /tmp/pre1.bin)"
dmesg -C 2>/dev/null; true
dd if=/tmp/blk9.bin of=/dev/fh bs=64k seek=9 count=1 iflag=fullblock conv=notrunc status=none; echo "dd exit $?"
dmesg | grep -i fh | tail -5
for i in 1 2; do dd if=/dev/fh bs=64k iflag=fullblock status=none of=/tmp/post$i.bin; done
echo "post: $(m /tmp/post1.bin) $(m /tmp/post2.bin)"
cmp -s <(head -c 65536 /tmp/pre1.bin) <(head -c 65536 /tmp/post1.bin) && echo "block 0 unchanged by the flash" || echo "NOTE: block 0 differs pre/post"
[ "$(b15 /tmp/post1.bin)" = $NEW15 ] && [ "$(b15 /tmp/post2.bin)" = $NEW15 ] && echo "FLASH VERIFIED: blocks 1-15 = 26.11.27" || echo "FLASH MISMATCH"
