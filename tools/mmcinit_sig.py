"""Writes driver/mmcinit_sig.inc: the byte signatures and new bytes by which mmcinit.inc shortens
two waits in the firmware's MMC start-up, in memory, as the drivers load.

    venv/bin/python -I tools/mmcinit_sig.py releases/of1-blw-26.11.27.bin driver/mmcinit_sig.inc

1. SdHost (FFS file 89C24DFA-A105-4E8D-AFE6-0BA7D40972AE), SendCommand's wait for the command
   to finish (RVA 0x4f6-0x546). Each pass reads the error and normal interrupt status (16-bit
   reads through PciIo), leaves if the command (and any data) is done or failed, and otherwise
   stalls 1000 us and counts one off the caller's timeout (in ms, [ebp+0x24]). Commands finish in
   microseconds, but the first read comes before that, so nearly every command pays a whole
   millisecond (about 360 ms per boot on an OF1).

   The new loop stalls 4 us per pass, and counts passes in the status word's upper byte
   ([ebp-2], zeroed before the loop by 'mov [ebp-4], edi'; the reads are 16-bit, every test
   looks at the low word, and [ebp-4] is rewritten before any later use), taking a millisecond
   off the timeout every 256 passes, so no timeout is shorter than before. To make room, the two
   'done' branches go straight to 0x584, past 'cmp [ebp+0x24], 0 / jne 0x584': the counter is
   never 0 there unless the caller passed a timeout of 0, which the stock code reports as a
   timeout even when the command worked. The timeout path falls through to 0x54c as before.

2. MmcMediaDevice (FFS file DD223EF8-6D1B-490A-A53E-BA86FAAEB778): the two waits after CMD0
   (GO_IDLE_STATE), 50 ms each (SD path, RVA 0x170a: push 50000; MMC path, RVA 0x187b:
   imul eax, eax, 50000, for (retry + 1) * 50 ms). Linux waits 1 ms (mmc_go_idle); the card's
   real start-up time is spent in the CMD1 (or ACMD41) polls after it, which allow 5 s (MMC) and
   500 ms (SD). Both become 1000 us.

Each signature is the bytes around the change, less the dwords the PE relocations patch.
"""
import struct
import sys
import uefi_firmware
import pefile
sys.path.insert(0, __file__.rsplit('/', 1)[0])
from mmc_sig import find_pe, MMC_GUID  # noqa: E402

SDH_GUID = '89c24dfa-a105-4e8d-afe6-0ba7d40972ae'

# SdHost: the signature covers the loop and what it jumps to.
SDH_SIG = (0x4f0, 0x5a0)
SDH_LOOP = 0x4f6
SDH_NEW = 0x536                 # new bytes from here (0x531-0x535: mov eax, [gBS], relocated, kept)
SDH_NEW_END = 0x54c             # up to the timeout path, which stays
SDH_DONE = 0x584
SDH_BR = (0x529, 0x52f)         # the two 'done' branches

# MmcMediaDevice: the two 50 ms operands.
MMW_SITES = (0x170b, 0x187d)
MMW_SIG = ((0x16f4, 0x1720), (0x1869, 0x1895))
MMW_NEW_US = 1000


def pe_of(fw, guid):
    pe = pefile.PE(data=find_pe(fw, guid))
    relocs = sorted(e.rva for b in pe.DIRECTORY_ENTRY_BASERELOC for e in b.entries if e.type == 3)
    return pe, pe.get_memory_mapped_image(), relocs


def runs(mem, relocs, lo, hi):
    """(offset from lo, bytes) runs of mem[lo:hi], less the relocated dwords."""
    skip = set()
    for r in relocs:
        skip.update(range(r, r + 4))
    out, start = [], None
    for a in range(lo, hi + 1):
        if a < hi and a not in skip:
            if start is None:
                start = a
        elif start is not None:
            out.append((start - lo, mem[start:a]))
            start = None
    return out


def rel8(at, target):
    """The displacement byte of a 2-byte short jump at 'at' to 'target'."""
    d = target - (at + 2)
    assert -128 <= d <= 127, 'short jump out of range'
    return d & 0xff


def sdhost(fw):
    pe, mem, relocs = pe_of(fw, SDH_GUID)
    m = lambda a, h: mem[a:a + len(bytes.fromhex(h))] == bytes.fromhex(h)  # noqa: E731
    # The loop: read ERROR_INT_STATUS (0x32) and NORMAL_INT_STATUS (0x30), 16-bit, through PciIo->Mem.Read.
    assert m(0x4f3, '897dfc'), 'no mov [ebp-4], edi before the loop'
    assert m(0x4f6, '8d45fc5053576a32575356ff5608'), 'not the error-status read'
    assert m(0x504, '8b45fc') and m(0x50a, '66a9ff01') and mem[0x50e] == 0x75, 'not the error test'
    assert m(0x510, '8d45fc5053576a30575356ff5608'), 'not the normal-status read'
    assert m(0x521, '845dfc'), 'not the command-complete test'
    assert mem[0x524:0x526] == bytes([0x74, 0x0b]), 'no je 0x531'
    assert m(0x526, '397d14'), 'no data test'
    assert mem[0x529] == 0x74 and 0x52b + mem[0x52a] == 0x547, 'no je 0x547'
    assert m(0x52b, 'f645fc02'), 'no transfer-complete test'
    assert mem[0x52f] == 0x75 and 0x531 + mem[0x530] == 0x547, 'no jne 0x547'
    assert mem[0x531] == 0xa1 and 0x532 in relocs, 'no mov eax, [gBS]'
    assert m(0x536, '68e8030000ff9088000000'), 'not Stall(1000)'
    assert m(0x541, 'ff4d2459') and mem[0x545] == 0x75 and (0x547 + struct.unpack('b', mem[0x546:0x547])[0]) == SDH_LOOP, 'not the count'
    assert m(0x547, '397d24') and mem[0x54a] == 0x75 and 0x54c + mem[0x54b] == SDH_DONE, 'not the timeout test'
    assert m(0x54c, 'c745f412000080'), 'not the timeout path'
    assert m(SDH_DONE, '397d28'), 'not the response read at 0x584'
    # ebx = 1 (Uint16 width, and the CC mask), edi = 0 throughout: both set once, before the loop.
    # Nothing else jumps into the bytes being replaced (checked over the whole image).
    import capstone
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    md.skipdata = True
    text = [s for s in pe.sections if s.Characteristics & 0x20000000]
    for s in text:
        for i in md.disasm(mem[s.VirtualAddress:s.VirtualAddress + s.Misc_VirtualSize], s.VirtualAddress):
            if i.mnemonic.startswith('j') or i.mnemonic in ('call', 'loop'):
                try:
                    t = int(i.op_str, 16)
                except ValueError:
                    continue
                if 0x531 < t < SDH_NEW_END and i.address not in SDH_BR:
                    raise SystemExit('0x%x jumps into the replaced bytes (0x%x)' % (i.address, t))
                if t in (0x547, 0x531) and i.address not in SDH_BR + (0x524,):
                    raise SystemExit('0x%x also jumps to 0x%x' % (i.address, t))

    new = bytes([0x6a, 0x04])                               # push 4
    new += bytes.fromhex('ff9088000000')                    # call [eax + 0x88]   (Stall)
    new += bytes([0x59])                                    # pop ecx
    new += bytes.fromhex('fe4dfe')                          # dec byte [ebp - 2]
    a = SDH_NEW + len(new)
    new += bytes([0x75, rel8(a, SDH_LOOP)])                 # jne loop
    new += bytes.fromhex('ff4d24')                          # dec dword [ebp + 0x24]
    a = SDH_NEW + len(new)
    new += bytes([0x75, rel8(a, SDH_LOOP)])                 # jne loop
    new += b'\x90' * (SDH_NEW_END - SDH_NEW - len(new))     # nop: falls into the timeout path
    assert len(new) == SDH_NEW_END - SDH_NEW
    br = [rel8(b, SDH_DONE) for b in SDH_BR]
    return dict(sig=runs(mem, relocs, *SDH_SIG), new=new, br=br, size=len(mem))


def mmcmedia(fw):
    pe, mem, relocs = pe_of(fw, MMC_GUID)
    # SD path: CMD0 (helper 0x1286), then mov eax, [gBS]; push esi; push 50000; call [eax + 0x88].
    assert mem[0x16f4] == 0xe8 and 0x16f9 + struct.unpack_from('<i', mem, 0x16f5)[0] == 0x1286, 'SD: not the command helper'
    assert mem[0x16e8:0x16ea] == bytes.fromhex('6a64') and mem[0x16e7] == 0x53, 'SD: not CMD0'
    assert mem[0x1704] == 0xa1 and 0x1705 in relocs and mem[0x1709] == 0x56, 'SD: no mov eax, [gBS]'
    assert mem[0x170a] == 0x68 and struct.unpack_from('<I', mem, 0x170b)[0] == 50000 and mem[0x170f:0x1715] == bytes.fromhex('ff9088000000'), 'SD: not Stall(50000)'
    # MMC path: CMD0 at 0x1869, then (retry + 1) * 50000 us, then CMD1 (push 1) at 0x189c.
    assert mem[0x1869] == 0xe8 and 0x186e + struct.unpack_from('<i', mem, 0x186a)[0] == 0x1286, 'MMC: not the command helper'
    assert mem[0x187b:0x187d] == bytes.fromhex('69c0') and struct.unpack_from('<I', mem, 0x187d)[0] == 50000, 'MMC: not imul eax, eax, 50000'
    assert mem[0x1881] == 0x50 and mem[0x1882] == 0xa1 and 0x1883 in relocs and mem[0x1887:0x188d] == bytes.fromhex('ff9088000000'), 'MMC: not Stall'
    assert mem[0x1899:0x189b] == bytes.fromhex('6a01'), 'MMC: CMD1 does not follow'
    # The CMD1 poll after it allows 5000 passes of Stall(1000).
    assert mem[0x18fd:0x1905] == bytes.fromhex('c744241488130000') and mem[0x192b:0x1930] == bytes.fromhex('68e8030000'), 'MMC: CMD1 poll is not 5000 x 1 ms'
    for s in MMW_SITES:
        assert not any(s - 3 <= r < s + 4 for r in relocs), 'relocation at 0x%x' % s
    sig = []
    for lo, hi in MMW_SIG:
        sig.append((lo, runs(mem, relocs, lo, hi)))
    return dict(sig=sig, size=len(mem))


def emit_runs(f, rs):
    for off, b in rs:
        f.write('        dw      0x%04x, 0x%04x\n' % (off, len(b)))
        for i in range(0, len(b), 16):
            f.write('        db      ' + ', '.join('0x%02x' % x for x in b[i:i + 16]) + '\n')
    f.write('        dw      0, 0\n')


def main():
    img, out = sys.argv[1], sys.argv[2]
    fw = uefi_firmware.AutoParser(open(img, 'rb').read()).parse()
    s = sdhost(fw)
    fw = uefi_firmware.AutoParser(open(img, 'rb').read()).parse()
    w = mmcmedia(fw)
    with open(out, 'w') as f:
        f.write('; Generated by tools/mmcinit_sig.py from %s: do not edit.\n' % img.split('/')[-1])
        f.write('; SdHost (%s) SendCommand wait loop, MmcMediaDevice (%s) waits after CMD0.\n' % (SDH_GUID.upper(), MMC_GUID.upper()))
        f.write('SDH_SIG_RVA     equ     0x%x\n' % SDH_SIG[0])
        f.write('SDH_END_RVA     equ     0x%x\n' % SDH_SIG[1])
        f.write('SDH_NEW_RVA     equ     0x%x\n' % SDH_NEW)
        f.write('SDH_NEW_LEN     equ     %d\n' % len(s['new']))
        f.write('SDH_BR1_RVA     equ     0x%x\n' % (SDH_BR[0] + 1))
        f.write('SDH_BR1_NEW     equ     0x%02x\n' % s['br'][0])
        f.write('SDH_BR2_RVA     equ     0x%x\n' % (SDH_BR[1] + 1))
        f.write('SDH_BR2_NEW     equ     0x%02x\n' % s['br'][1])
        f.write('MMW_SD_RVA      equ     0x%x\n' % MMW_SITES[0])
        f.write('MMW_MMC_RVA     equ     0x%x\n' % MMW_SITES[1])
        f.write('MMW_SIG1_RVA    equ     0x%x\n' % MMW_SIG[0][0])
        f.write('MMW_SIG2_RVA    equ     0x%x\n' % MMW_SIG[1][0])
        f.write('MMW_END_RVA     equ     0x%x\n' % MMW_SIG[1][1])
        f.write('MMW_NEW_US      equ     %d\n' % MMW_NEW_US)
        f.write('sdh_sig:\n')
        emit_runs(f, s['sig'])
        f.write('sdh_new:\n')
        f.write('        db      ' + ', '.join('0x%02x' % x for x in s['new']) + '\n')
        f.write('mmw_sig1:\n')
        emit_runs(f, w['sig'][0][1])
        f.write('mmw_sig2:\n')
        emit_runs(f, w['sig'][1][1])
    print('SdHost loop 0x%x-0x%x: %d new bytes at 0x%x, done branches -> 0x%x; MmcMediaDevice 50 ms at 0x%x, 0x%x -> %d us'
          % (SDH_LOOP, SDH_NEW_END, len(s['new']), SDH_NEW, SDH_DONE, MMW_SITES[0], MMW_SITES[1], MMW_NEW_US))


if __name__ == '__main__':
    main()
