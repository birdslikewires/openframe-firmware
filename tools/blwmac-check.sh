#!/bin/bash
# blwmac-check.sh: what the firmware and BlwMac.efi did this boot (run as root on the OpenFrame).
#   - eth0's MAC as the r8169 driver found it at probe, and now
#   - the BlwMac and BlwMacStatus variables (and the old BlwLanMac, if it's still there)
#   - the flash lock registers (03 = locked down this boot: a BlwMac write happened)
#   - two reads of NVRAM (block 0) to /tmp/nvA.bin and /tmp/nvB.bin, for diffing against earlier dumps
# OpenFrame kernels leave efivarfs out on purpose (see docs/nvram.md), so it needs an efivarfs.ko
# built for the running kernel (EFIVARFS_KO). It's mounted read-only, and unmounted and unloaded
# again at the end if this script put it there. Reading block 0 needs fh (or FH_KO).
GUID=ec87d643-eba4-4bb5-a1e5-3f3e36b20da9
dmesg | grep -E 'eth0: RTL8168|r8169.*random'
echo "addr $(cat /sys/class/net/eth0/address) assign_type $(cat /sys/class/net/eth0/addr_assign_type) $(ip -d link show eth0 | grep -o 'permaddr [^ ]*')"
LOADED=0; MOUNTED=0
if ! lsmod | grep -qw efivarfs; then
	insmod "${EFIVARFS_KO:?set EFIVARFS_KO to an efivarfs.ko built for this kernel (OpenFrame kernels leave it out)}" && LOADED=1
fi
if ! findmnt -n /sys/firmware/efi/efivars >/dev/null; then
	mount -t efivarfs -o ro efivarfs /sys/firmware/efi/efivars && MOUNTED=1
fi
for v in BlwMac BlwMacStatus BlwLanMac; do
	f=/sys/firmware/efi/efivars/$v-$GUID
	[ -e $f ] && echo "$v: $(od -An -tx1 -v $f | tr -s ' ')" || echo "$v: absent"
done
[ $MOUNTED = 1 ] && umount /sys/firmware/efi/efivars
[ $LOADED = 1 ] && rmmod efivarfs
python3 "$(dirname "$0")/lockregs.py"
lsmod | grep -qw fh || modprobe fh 2>/dev/null || insmod "${FH_KO:?fh is not installed: set FH_KO to the path of fh.ko}"
dd if=/dev/fh bs=64k count=1 iflag=fullblock status=none of=/tmp/nvA.bin
dd if=/dev/fh bs=64k count=1 iflag=fullblock status=none of=/tmp/nvB.bin
md5sum /tmp/nvA.bin /tmp/nvB.bin
