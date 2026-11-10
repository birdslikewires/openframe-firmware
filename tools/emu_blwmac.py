#!/usr/bin/env python3
"""
Run BlwMac.efi under Unicorn against a fake OpenFrame and check what it does, scenario by
scenario: fake system table, BS->HandleProtocol (LoadedImage, EDK1 shell interface),
RT->GetVariable / SetVariable over an in-memory store, PCI configuration mechanism 1 (root port
00:1c.0 and an RTL8168 at 01:00.0 with an I/O BAR), the NIC's I/O ports, and the firmware hub's
block-lock registers at 0xFFB00000.

Any write to the flash data space (0xFFF00000-0xFFFFFFFF) faults the run, and any lock-register
write other than 0x03 to blocks 1-15 is an error. So is any port write outside config space and
the NIC's MAC0/Cfg9346 registers.

Usage: emu_blwmac.py <BlwMac.efi>
"""
import struct, sys, uuid
from unicorn import (Uc, UcError, UC_ARCH_X86, UC_MODE_32, UC_HOOK_CODE, UC_HOOK_INSN,
                     UC_HOOK_MEM_WRITE, UC_PROT_READ, UC_PROT_ALL)
from unicorn.x86_const import UC_X86_REG_ESP, UC_X86_REG_EAX, UC_X86_REG_EIP, UC_X86_INS_IN, UC_X86_INS_OUT

LOAD = 0x10400000                 # not the image base: the code must be position-independent
ST, BS, RT, STUBS, DATA, STACK = 0x20000000, 0x20001000, 0x20002000, 0x30000000, 0x31000000, 0x40000000
RET_MAGIC = 0x30000F00
FWH_REG, FLASH = 0xFFB00000, 0xFFF00000
BLW = uuid.UUID('ec87d643-eba4-4bb5-a1e5-3f3e36b20da9').bytes_le
LOADED_IMAGE = uuid.UUID('5b1b31a1-9562-11d2-8e3f-00a0c969723b').bytes_le
SHELL_IF = uuid.UUID('47c7b223-c42a-11d2-8e57-00a0c969723b').bytes_le
SMBIOS_GUID = uuid.UUID('eb9d2d31-2d88-11d3-9a16-0090273fc14d').bytes_le
IMAGE_HANDLE = 0x5150
EXISTING = {uuid.UUID(g).bytes_le for g in (       # the OF1 test unit's GUID store, 2026-11-09
    '4599d26f-1a11-49b8-b91f-858745cff824', '8be4df61-93ca-11d2-aa0d-00e098032b8c',
    'ec87d643-eba4-4bb5-a1e5-3f3e36b20da9', '560bf58a-1e0d-4d7e-953f-2980a261e031',
    'af9ffd67-ec10-488a-9dfc-6cbf5ee22c2e', '47c7b227-c42a-11d2-8e57-00a0c969723b',
    '30b98b95-dfa3-4501-a3ce-e38c186384a0')}
NIC_IO = 0xE000

F = dict(ARG_LOADOPT=0x1, ARG_SHELLIF=0x2, ARG_NONE=0x4, HW_MAC=0x8, NV_SAME=0x10, NV_WRITTEN=0x20,
         NV_FAILED=0x40, LOCKDOWN_OK=0x80, LOCKDOWN_BAD=0x100, NV_READ_OK=0x200, SMBIOS_DONE=0x400,
         NIC_MISSING=0x1000, NIC_DONE=0x2000, NIC_VERIFY=0x4000)


def flags_str(v):
    return '|'.join(k for k, b in F.items() if v & b) or '0'


SMB = DATA + 0x4000              # SMBIOS entry point; the structure table follows at +32


def run(pe, loadopts=None, argv=None, store=None, lock_ignores=False, nic_present=True,
        setvar_fails=False, nic_mac_readonly=False, smbios=None):
    store = dict(store or {})
    errors, events = [], []
    mu = Uc(UC_ARCH_X86, UC_MODE_32)
    peo = struct.unpack_from('<I', pe, 0x3c)[0]
    entry = struct.unpack_from('<I', pe, peo + 0x28)[0]
    size_img = struct.unpack_from('<I', pe, peo + 0x50)[0]
    mu.mem_map(LOAD, (size_img + 0xfff) & ~0xfff)
    mu.mem_write(LOAD, pe[:size_img])
    for a in (ST, STUBS, DATA, STACK):
        mu.mem_map(a, 0x10000)
    mu.mem_map(FWH_REG, 0x100000)
    for b in range(16):
        mu.mem_write(FWH_REG + b * 0x10000 + 2, b'\x01')           # power-on: write-locked
    mu.mem_map(FLASH, 0x100000, UC_PROT_READ)                       # writes here fault
    mu.mem_write(ST + 0x38, struct.pack('<I', RT))
    mu.mem_write(ST + 0x3c, struct.pack('<I', BS))
    mu.mem_write(BS + 0x58, struct.pack('<I', STUBS + 0x10))        # HandleProtocol
    mu.mem_write(RT + 0x30, struct.pack('<I', STUBS + 0x20))        # GetVariable
    mu.mem_write(RT + 0x38, struct.pack('<I', STUBS + 0x30))        # SetVariable
    for s in (0x10, 0x20, 0x30):
        mu.mem_write(STUBS + s, b'\xc3')                              # ret
    mu.mem_write(RET_MAGIC, b'\xf4')                                  # hlt

    # LoadedImage at DATA, shell interface at DATA+0x100, strings from DATA+0x1000
    def wstr(addr, s):
        b = s.encode('utf-16le') + b'\0\0'
        mu.mem_write(addr, b)
        return addr + len(b)
    nxt = DATA + 0x1000
    if loadopts is not None:
        lo = nxt
        nxt = wstr(lo, loadopts)
        mu.mem_write(DATA + 0x18, struct.pack('<II', (len(loadopts) + 1) * 2, lo))
    if argv is not None:
        ptrs = []
        for a in argv:
            ptrs.append(nxt)
            nxt = wstr(nxt, a)
        av = nxt
        mu.mem_write(av, struct.pack('<%dI' % len(ptrs), *ptrs))
        mu.mem_write(DATA + 0x100 + 8, struct.pack('<II', av, len(argv)))

    # The configuration table: ACPI 2.0 then, if given, SMBIOS (entry point + structure table)
    mu.mem_write(DATA + 0x3000, uuid.UUID('8868e871-e4f1-11d3-bc22-0080c73c8881').bytes_le + struct.pack('<I', DATA + 0x3800))
    if smbios is not None:
        eps = bytearray(smbios[:32])
        struct.pack_into('<I', eps, 0x18, SMB + 32)
        mu.mem_write(SMB, bytes(eps) + smbios[32:])
        mu.mem_write(DATA + 0x3014, SMBIOS_GUID + struct.pack('<I', SMB))
    mu.mem_write(ST + 0x40, struct.pack('<II', 2 if smbios is not None else 1, DATA + 0x3000))

    cfg = {(0, 0x1c, 0): {0x04: 0x00100000, 0x18: 0x00010100},
           (1, 0, 0): {0x00: 0x816810ec if nic_present else 0x12345678, 0x04: 0x00100000, 0x10: NIC_IO | 1}}
    state = dict(cf8=0, cfg9346=0, mac=bytearray(b'\xb8\xfc\xff\xff\xff\xff'))

    def rd(a):
        return struct.unpack('<I', mu.mem_read(a, 4))[0]

    def wstring(a):
        out = b''
        while True:
            c = bytes(mu.mem_read(a, 2))
            if c == b'\0\0':
                return out.decode('utf-16le')
            out += c
            a += 2

    def hook_code(uc, addr, size, _):
        esp = uc.reg_read(UC_X86_REG_ESP)
        a = [rd(esp + 4 + 4 * i) for i in range(5)]
        if addr == STUBS + 0x10:                                    # HandleProtocol(h, guid, &iface)
            g = bytes(uc.mem_read(a[1], 16))
            iface = 0
            if a[0] != IMAGE_HANDLE:
                errors.append('HandleProtocol on handle %#x' % a[0])
            if g == LOADED_IMAGE and loadopts is not None:
                iface = DATA
            elif g == SHELL_IF and argv is not None:
                iface = DATA + 0x100
            if iface:
                uc.mem_write(a[2], struct.pack('<I', iface))
            uc.reg_write(UC_X86_REG_EAX, 0 if iface else 0x80000003)
        elif addr == STUBS + 0x20:                                  # GetVariable(n, g, &attr, &size, data)
            key = (wstring(a[0]), bytes(uc.mem_read(a[1], 16)))
            if key not in store:
                uc.reg_write(UC_X86_REG_EAX, 0x8000000E)
                return
            attr, data = store[key]
            if a[2]:
                uc.mem_write(a[2], struct.pack('<I', attr))
            if rd(a[3]) < len(data):
                uc.mem_write(a[3], struct.pack('<I', len(data)))
                uc.reg_write(UC_X86_REG_EAX, 0x80000005)
                return
            uc.mem_write(a[3], struct.pack('<I', len(data)))
            uc.mem_write(a[4], data)
            uc.reg_write(UC_X86_REG_EAX, 0)
        elif addr == STUBS + 0x30:                                  # SetVariable(n, g, attr, size, data)
            name, g = wstring(a[0]), bytes(uc.mem_read(a[1], 16))
            data = bytes(uc.mem_read(a[4], a[3]))
            events.append('SetVariable(%s, %s, attr=%#x, %s)' % (name, 'BLW' if g == BLW else g.hex(), a[2], data.hex()))
            if g not in EXISTING:
                errors.append('SetVariable under a GUID not already in the NVRAM GUID store (AMI new-GUID bug)')
            if setvar_fails and a[2] & 1:
                uc.reg_write(UC_X86_REG_EAX, 0x80000009)
                return
            if a[3] == 0:                                           # DataSize 0: delete
                if (name, g) not in store:
                    uc.reg_write(UC_X86_REG_EAX, 0x8000000E)
                    return
                del store[(name, g)]
            else:
                store[(name, g)] = (a[2], data)
            uc.reg_write(UC_X86_REG_EAX, 0)

    def hook_write(uc, access, addr, size, value, _):
        if FWH_REG <= addr < FWH_REG + 0x100000:
            off = addr - FWH_REG
            b, r = divmod(off, 0x10000)
            events.append('lock[%d] <- %#x' % (b, value))
            if r != 2 or size != 1 or not 1 <= b <= 15 or value != 0x03:
                errors.append('bad lock-register write: block %d off %#x size %d value %#x' % (b, r, size, value))

    def port_in(uc, port, size, _):
        if port == 0xCFC:
            a = state['cf8']
            bus, dev, fn, reg = (a >> 16) & 0xff, (a >> 11) & 0x1f, (a >> 8) & 7, a & 0xfc
            return cfg.get((bus, dev, fn), {}).get(reg, 0xffffffff) & ((1 << (8 * size)) - 1)
        if NIC_IO <= port < NIC_IO + 6 and size == 1:
            return state['mac'][port - NIC_IO]
        errors.append('IN from port %#x size %d' % (port, size))
        return 0xff

    def port_out(uc, port, size, value, _):
        if port == 0xCF8 and size == 4:
            state['cf8'] = value
            return
        if 0xCFC <= port <= 0xCFF:
            a = state['cf8']
            key, reg = ((a >> 16) & 0xff, (a >> 11) & 0x1f, (a >> 8) & 7), (a & 0xfc)
            sh = 8 * (port - 0xCFC)
            if reg != 4 or size != 2 or sh not in (0,) or key not in cfg:
                errors.append('config write %s reg %#x+%d size %d' % (key, reg, port - 0xCFC, size))
            old = cfg[key].get(reg, 0)
            cfg[key][reg] = (old & 0xffff0000) | (value & 0xffff)
            events.append('cfg %02x:%02x.%d cmd <- %#06x' % (key + (value,)))
            return
        if port == NIC_IO + 0x50 and size == 1:
            state['cfg9346'] = value
            events.append('Cfg9346 <- %#x' % value)
            return
        if port in (NIC_IO, NIC_IO + 4) and size == 4:
            nic_cmd = cfg[(1, 0, 0)][4] & 0xffff
            rp_cmd = cfg[(0, 0x1c, 0)][4] & 0xffff
            if not (nic_cmd & 1 and rp_cmd & 1):
                errors.append('NIC I/O write with decode off (nic %#x rp %#x)' % (nic_cmd, rp_cmd))
            if state['cfg9346'] != 0xC0:
                errors.append('MAC0 write with Cfg9346 locked')
            if not nic_mac_readonly:
                o = port - NIC_IO
                n = 4 if o == 0 else 2
                state['mac'][o:o + n] = struct.pack('<I', value)[:n]
                if o == 4 and value >> 16:
                    errors.append('MAC4 upper half not zero: %#x' % value)
            events.append('MAC%d <- %#010x' % (port - NIC_IO, value))
            return
        errors.append('OUT to port %#x size %d value %#x' % (port, size, value))

    mu.hook_add(UC_HOOK_CODE, hook_code, begin=STUBS, end=STUBS + 0x40)
    mu.hook_add(UC_HOOK_MEM_WRITE, hook_write, begin=FWH_REG, end=FWH_REG + 0xFFFFF)
    mu.hook_add(UC_HOOK_INSN, port_in, None, 1, 0, UC_X86_INS_IN)
    mu.hook_add(UC_HOOK_INSN, port_out, None, 1, 0, UC_X86_INS_OUT)

    if lock_ignores:              # a chip that ignores lock-down: registers read 0x01 again at once
        def relock(uc, addr, size, _):
            for b in range(1, 16):
                uc.mem_write(FWH_REG + b * 0x10000 + 2, b'\x01')
        mu.hook_add(UC_HOOK_CODE, relock, begin=LOAD, end=LOAD + size_img)

    esp = STACK + 0x8000
    mu.mem_write(esp, struct.pack('<III', RET_MAGIC, IMAGE_HANDLE, ST))
    mu.reg_write(UC_X86_REG_ESP, esp)
    try:
        mu.emu_start(LOAD + entry, RET_MAGIC, count=2_000_000)
    except UcError as e:
        errors.append('emulation stopped: %s at eip %#x' % (e, mu.reg_read(UC_X86_REG_EIP)))
    if mu.reg_read(UC_X86_REG_ESP) != esp + 4:
        errors.append('stack unbalanced: esp %#x, expected %#x' % (mu.reg_read(UC_X86_REG_ESP), esp + 4))
    st = store.get(('BlwMacStatus', BLW))
    flags = struct.unpack_from('<I', st[1])[0] if st else None
    table = None
    if smbios is not None:                  # handed back with the table address as it was given (0)
        table = bytearray(mu.mem_read(SMB, len(smbios)))
        if struct.unpack_from('<I', table, 0x18)[0] != SMB + 32 or table[:0x18] != smbios[:0x18]:
            errors.append('SMBIOS entry point changed')
        table[0x18:0x1c] = smbios[0x18:0x1c]
        table = bytes(table)
    return dict(eax=mu.reg_read(UC_X86_REG_EAX), flags=flags, status=st, store=store, errors=errors,
                events=events, nic_mac=bytes(state['mac']), cfg=cfg, smbios=table)


def smbios_tables():
    """This build's SMBIOS (entry point + 1 pad + table, address 0) and 26.11.19's, which has no
    serial number placeholder."""
    import importlib.util, os
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, here)
    import build_ucode_driver as cur
    args = ('26.11.20', '11/09/2026', (26, 11), ('OpenPeak', 'OpenFrame 1', 'OF1', 'OpenFrame'))
    eps, tbl, _ = cur.smbios_blobs(*args)
    old = bytearray(tbl)        # 26.11.19: Type 1 without the placeholder string
    ph = b'OpenFrame\0' + b'0' * 12 + b'\0\0'
    assert old.count(ph) == 1
    old = bytes(old).replace(ph, b'OpenFrame\0\0')
    eps_old = bytearray(eps)
    struct.pack_into('<H', eps_old, 0x16, len(old))
    return eps + tbl, bytes(eps_old) + old


def type1(sm):
    """(serial number string or None, UUID bytes) of Type 1 in an SMBIOS blob from run()."""
    tlen = struct.unpack_from('<H', sm, 0x16)[0]
    tbl, o = sm[32:32 + tlen], 0
    while o < len(tbl):
        t, ln = tbl[o], tbl[o + 1]
        e = tbl.index(b'\0\0', o + ln)
        if t == 1:
            ss = tbl[o + ln:e].split(b'\0')
            n = tbl[o + 7]
            return (ss[n - 1].decode() if n else None), tbl[o + 8:o + 24]
        o = e + 2


def main():
    pe = open(sys.argv[1], 'rb').read()
    MAC = bytes.fromhex('020e8e123456')
    CARD = bytes.fromhex('000e8e123456')
    CARD2 = bytes.fromhex('001122334455')
    OTHER = bytes.fromhex('02aabbccddee')
    Z = bytes(6)
    NEW, OLD = smbios_tables()
    CARD3 = bytes.fromhex('4c5e0c112233')      # a replacement card from another maker
    GEN = bytes.fromhex('02a1b2c3d4e5')        # a LAN MAC of-netplan generated (no card)
    cl = 'fs1:\\BlwMac.efi 00:0e:8e:12:34:56'
    clg = 'fs1:\\BlwMac.efi 02:a1:b2:c3:d4:e5'
    lm = lambda m: {('BlwMac', BLW): (7, m)}
    uuid_of = lambda m: bytes.fromhex('00000000000080008000') + m
    filled = lambda c: (c.hex().upper(), uuid_of(c))
    uuid_only = lambda m: (None, uuid_of(m))
    UNFILLED = (None, b'\xff' * 16)
    W, S = F['LOCKDOWN_OK'] | F['NV_WRITTEN'], F['NV_SAME']
    R, SM, ND, HW = F['NV_READ_OK'], F['SMBIOS_DONE'], F['NIC_DONE'], F['HW_MAC']
    LO, SH = F['ARG_LOADOPT'], F['ARG_SHELLIF']
    def prefilled(c):                       # NEW as the firmware leaves it at ReadyToBoot for card c
        b = bytearray(NEW)
        t1 = b.index(b'\x01\x1b\x01\x00')
        b[t1 + 7] = 5
        b[t1 + 8:t1 + 24] = filled(c)[1]
        i = b.index(b'0' * 12)
        b[i:i + 12] = c.hex().upper().encode()
        return bytes(b)
    cases = [
        # name, run kwargs, flags, NV write (bytes or None), NIC MAC0 after (None: untouched), Type 1 after
        ('fresh unit, card MAC in LoadOptions', dict(loadopts=cl, smbios=NEW),
         LO | W | R | HW | SM | ND, CARD, MAC, filled(CARD)),
        ('already stored: nothing written', dict(loadopts=cl, store=lm(CARD), smbios=NEW),
         LO | S | R | HW | SM | ND, None, MAC, filled(CARD)),
        ('firmware filled an old card: app overwrites it', dict(loadopts=cl, store=lm(CARD2), smbios=prefilled(CARD2)),
         LO | W | R | HW | SM | ND, CARD, MAC, filled(CARD)),
        ('card changed: rewritten, serial follows', dict(loadopts=cl, store=lm(CARD2), smbios=NEW),
         LO | W | R | HW | SM | ND, CARD, MAC, filled(CARD)),
        ('replacement card: LAN MAC, serial number and UUID alike', dict(loadopts='x 4c:5e:0c:11:22:33', smbios=NEW),
         LO | W | R | HW | SM | ND, CARD3, b'\x02' + CARD3[1:], filled(CARD3)),
        ('generated MAC: stored, applied as it is, UUID only', dict(loadopts=clg, smbios=NEW),
         LO | W | R | SM | ND, GEN, GEN, uuid_only(GEN)),
        ('card gone, generated MAC: serial number taken away', dict(loadopts=clg, store=lm(CARD), smbios=prefilled(CARD)),
         LO | W | R | SM | ND, GEN, GEN, uuid_only(GEN)),
        ('old BlwLanMac variable there: BlwMac written, it is left', dict(loadopts=cl, store={('BlwLanMac', BLW): (7, MAC)}, smbios=NEW),
         LO | W | R | HW | SM | ND, CARD, MAC, filled(CARD)),
        ('BlwMac in the earlier 12-byte layout: rewritten', dict(loadopts=cl, store=lm(CARD + MAC), smbios=NEW),
         LO | W | R | HW | SM | ND, CARD, MAC, filled(CARD)),
        ('in shell argv (upper case, dashes)', dict(loadopts='', argv=['BlwMac.efi', '00-0E-8E-12-34-56'], smbios=NEW),
         SH | W | R | HW | SM | ND, CARD, MAC, filled(CARD)),
        ('two MACs: the second ignored', dict(loadopts=cl + ' 02:aa:bb:cc:dd:ee', smbios=NEW),
         LO | W | R | HW | SM | ND, CARD, MAC, filled(CARD)),
        ('multicast MAC rejected', dict(loadopts='x 01:0e:8e:12:34:56', smbios=NEW),
         F['ARG_NONE'], None, None, UNFILLED),
        ('all-zero MAC rejected', dict(loadopts='x 00:00:00:00:00:00', smbios=NEW),
         F['ARG_NONE'], None, None, UNFILLED),
        ('no argument, stored card applied', dict(loadopts='fs1:\\BlwMac.efi', argv=['BlwMac.efi'], store=lm(CARD), smbios=NEW),
         F['ARG_NONE'] | R | HW | SM | ND, None, MAC, filled(CARD)),
        ('no argument, stored generated MAC applied', dict(loadopts='x', store=lm(GEN), smbios=NEW),
         F['ARG_NONE'] | R | SM | ND, None, GEN, uuid_only(GEN)),
        ('no argument, nothing stored: NIC untouched', dict(loadopts='x', smbios=NEW),
         F['ARG_NONE'], None, None, UNFILLED),
        ('no argument, 12-byte BlwMac: ignored', dict(loadopts='x', store=lm(CARD + MAC), smbios=NEW),
         F['ARG_NONE'], None, None, UNFILLED),
        ('26.11.19 firmware SMBIOS: left alone', dict(loadopts=cl, smbios=OLD),
         LO | W | R | HW | ND, CARD, MAC, 'same'),
        ('no SMBIOS table', dict(loadopts=cl),
         LO | W | R | HW | ND, CARD, MAC, 'none'),
        ('lock-down does not stick: nothing written', dict(loadopts=cl, lock_ignores=True, smbios=NEW),
         LO | F['LOCKDOWN_BAD'], None, None, UNFILLED),
        ('SetVariable fails: NIC untouched', dict(loadopts=cl, setvar_fails=True, smbios=NEW),
         LO | F['LOCKDOWN_OK'] | F['NV_FAILED'], None, None, UNFILLED),
        ('no RTL8168 behind 00:1c.0', dict(loadopts=cl, store=lm(CARD), nic_present=False, smbios=NEW),
         LO | S | R | HW | SM | F['NIC_MISSING'], None, None, filled(CARD)),
        ('MAC0 does not take the write', dict(loadopts=cl, store=lm(CARD), nic_mac_readonly=True, smbios=NEW),
         LO | S | R | HW | SM | F['NIC_VERIFY'], None, None, filled(CARD)),
    ]
    bad = 0
    for name, kw, want, want_nv, want_mac, want_t1 in cases:
        r = run(pe, **kw)
        probs = list(r['errors'])
        if r['eax'] != 0:
            probs.append('returned %#x' % r['eax'])
        if r['flags'] != want:
            probs.append('flags %s, expected %s' % (flags_str(r['flags'] or 0), flags_str(want)))
        nv_writes = [e for e in r['events'] if e.startswith('SetVariable(BlwMac,')]
        if any(e.startswith('SetVariable(BlwLanMac,') for e in r['events']):
            probs.append('old BlwLanMac variable written')
        locks = [e for e in r['events'] if e.startswith('lock[')]
        if want_nv is not None:
            if nv_writes != ['SetVariable(BlwMac, BLW, attr=0x7, %s)' % want_nv.hex()]:
                probs.append('NV writes %s' % nv_writes)
            if locks != ['lock[%d] <- 0x3' % b for b in range(1, 16)]:
                probs.append('lock writes %s' % locks)
        else:
            if nv_writes and not kw.get('setvar_fails'):
                probs.append('unexpected NV write %s' % nv_writes)
            if locks and not (kw.get('lock_ignores') or kw.get('setvar_fails')):
                probs.append('unexpected lock writes %s' % locks)
        nic_written = any(e.startswith('MAC') for e in r['events'])
        if want_mac is not None:
            if r['nic_mac'] != want_mac:
                probs.append('NIC MAC0 %s, expected %s' % (r['nic_mac'].hex(':'), want_mac.hex(':')))
        elif nic_written and not kw.get('nic_mac_readonly'):
            probs.append('NIC was written')
        for key in ((0, 0x1c, 0), (1, 0, 0)):
            if r['cfg'][key][4] & 0xffff != 0:
                probs.append('command register of %s left at %#x' % (key, r['cfg'][key][4] & 0xffff))
        st = r['status']
        if st and st[0] != 6:
            probs.append('status variable attributes %#x (must be volatile BS|RT = 6)' % st[0])
        if st and len(st[1]) != 24:
            probs.append('status variable %d bytes, expected 24' % len(st[1]))
        elif st:                            # the stored MAC and the LAN MAC made from it, if usable
            nv = r['store'].get(('BlwMac', BLW), (0, b''))[1]
            ok = len(nv) == 6 and not nv[0] & 1 and any(nv)
            want_m = nv if ok else Z
            want_l = (nv if nv[0] & 2 else b'\x02' + nv[1:]) if ok else Z
            if (st[1][8:14], st[1][14:20]) != (want_m, want_l):
                probs.append('status MACs stored %s LAN %s' % (st[1][8:14].hex(), st[1][14:20].hex()))
        if want_t1 == 'none':
            pass
        elif want_t1 == 'same':
            if r['smbios'] != kw['smbios']:
                probs.append('SMBIOS table changed')
        else:
            got = type1(r['smbios'])
            if want_t1[0] is None and r['smbios'].count(b'0' * 12) != 1:
                probs.append('serial placeholder not back to 000000000000')
            if got != want_t1:
                probs.append('Type 1 serial/UUID %r %s, expected %r %s' % (got[0], got[1].hex(), want_t1[0], want_t1[1].hex()))
            given = kw['smbios']                # Type 1 from its serial index to the placeholder's end
            changed = [i for i in range(len(given)) if r['smbios'][i] != given[i]]
            if want_t1 is not UNFILLED:
                t1 = NEW.index(b'\x01\x1b\x01\x00')
                lo, hi = t1 + 7, t1 + len(NEW[t1:].split(b'\0\0')[0]) + 1
                if any(not (lo <= i < hi) for i in changed):
                    probs.append('SMBIOS bytes outside Type 1 changed: %s' % changed[:8])
            elif changed:
                probs.append('SMBIOS changed with nothing to fill it from')
        print('%-4s %-52s %s' % ('ok' if not probs else 'FAIL', name, flags_str(r['flags'] or 0)))
        for p in probs:
            print('       ' + p)
        bad += bool(probs)
    print('%d of %d scenarios passed' % (len(cases) - bad, len(cases)))
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
