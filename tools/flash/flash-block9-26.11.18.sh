#!/bin/bash
# flash-block9.sh: write ONLY block 9 of of1-blw-26.11.18.bin to the OF1 chip via fh. Never touches block 0.
set -u
IMG=/tmp/of1-blw-26.11.18.bin; IMG_MD5=87ed0cb283c7bfd518b8153f0c23e1aa; OLD15=70aafdf0d5c078b2192f7943f87cd515; NEW15=dc0e83cf91892ae5b51d3a341ea542ec; BLK9=251158cd11166a0de608977b901d4a93
m() { md5sum "$1" | cut -d' ' -f1; }
b15() { tail -c +$((0x10000+1)) "$1" | md5sum | cut -d' ' -f1; }
[ "$(m $IMG)" = $IMG_MD5 ] || { echo "STOP: image md5 wrong"; exit 1; }
dd if=$IMG bs=64k skip=9 count=1 iflag=fullblock status=none of=/tmp/blk9.bin
[ "$(m /tmp/blk9.bin)" = $BLK9 ] && [ "$(stat -c %s /tmp/blk9.bin)" = 65536 ] || { echo "STOP: block 9 extract wrong"; exit 1; }
lsmod | grep -qw fh || modprobe fh 2>/dev/null || insmod "${FH_KO:?fh is not installed: set FH_KO to the path of fh.ko}"
for i in 1 2; do dd if=/dev/fh bs=64k iflag=fullblock status=none of=/tmp/pre$i.bin; done
[ "$(m /tmp/pre1.bin)" = "$(m /tmp/pre2.bin)" ] || { echo "STOP: two pre-reads differ"; exit 1; }
[ "$(b15 /tmp/pre1.bin)" = $OLD15 ] || { echo "STOP: blocks 1-15 are not 26.11.17: $(b15 /tmp/pre1.bin)"; exit 1; }
echo "pre-check ok: chip blocks 1-15 = 26.11.17, full read $(m /tmp/pre1.bin)"
dmesg -C 2>/dev/null; true
dd if=/tmp/blk9.bin of=/dev/fh bs=64k seek=9 count=1 iflag=fullblock conv=notrunc status=none; echo "dd exit $?"
dmesg | grep -i fh | tail -5
for i in 1 2; do dd if=/dev/fh bs=64k iflag=fullblock status=none of=/tmp/post$i.bin; done
echo "post: $(m /tmp/post1.bin) $(m /tmp/post2.bin)"
cmp -s <(head -c 65536 /tmp/pre1.bin) <(head -c 65536 /tmp/post1.bin) && echo "block 0 unchanged by the flash" || echo "NOTE: block 0 differs pre/post"
[ "$(b15 /tmp/post1.bin)" = $NEW15 ] && [ "$(b15 /tmp/post2.bin)" = $NEW15 ] && echo "FLASH VERIFIED: blocks 1-15 = 26.11.18" || echo "FLASH MISMATCH"
