# Write a MAC into the RTL8168's MAC0 registers through its MMIO BAR (driver must be unbound).
import mmap, os, struct, sys
dev = '/sys/bus/pci/devices/0000:01:00.0'
mac = bytes.fromhex(sys.argv[1].replace(':', ''))
os.system(f'setpci -s 0000:01:00.0 COMMAND=0x0006:0x0006')   # memory decode + bus master
fd = os.open(dev + '/resource2', os.O_RDWR | os.O_SYNC)
m = mmap.mmap(fd, 4096, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE)
rd = lambda o: struct.unpack('<I', m[o:o+4])[0]
print('MAC0 before', m[0:6].hex(':'))
m[0x50:0x51] = b'\xc0'                        # Cfg9346: unlock config writes
m[0x04:0x08] = struct.pack('<I', struct.unpack('<H', mac[4:6])[0])
m[0x00:0x04] = mac[0:4]
m[0x50:0x51] = b'\x00'                        # lock
print('MAC0 after ', m[0:6].hex(':'))
