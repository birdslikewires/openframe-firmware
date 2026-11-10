#!/usr/bin/env python3
"""
Patch the OF1 boot block's reset-vector code (volume top file 1BA0062E, at 0xFF9B0).

  --skip-perfctl   NOP the WRMSR at 0xFFFFFBF4 that writes IA32_PERF_CTL (0x199) with the
                   maximum ratio/VID from IA32_PERF_STATUS (0x198). (Test v7: did NOT fix the
                   0x217 hang.)

  --two-stage-ucode FILE
                   Keep the base image's microcode entry first in the microcode file (the reset
                   loader takes the first match, so it still loads 0x20a), add the newer entry for
                   the same signature and platform flags from FILE as the next entry, and make the
                   reset code load both, one after the other: 0x20a, then 0x217. Linux did exactly
                   this successfully. The 12-byte load at 0xFFFFFC74 becomes a jump to a stub in
                   the boot volume's pad file (all FF, no data checksum) at 0xFFFFF800. The stub
                   loads the first entry, steps to the next entry (TotalSize or 2048, rounded up to
                   512, as the reset loader does) and, if it lies inside the file and has header
                   version 1, the same signature and an overlapping platform-flag bit, loads it too.
                   Then it jumps back to 0xFFFFFC80 (the original revision check).

The exact original bytes are checked, the volume top file's FFS data checksum is
recomputed, and the tool checks that nothing else in the image changed.
"""
import argparse, struct, sys

VTF_OFF, VTF_SIZE = 0xFF9B0, 0x650
VTF_GUID = bytes.fromhex('2e06a01b79c782458566336ae8f78f09')
PERFCTL_OFF = 0xFFBE6
PERFCTL_ORIG = bytes.fromhex('b9990100000f326625c0e0660bc30f30')   # mov ecx,0x199 .. wrmsr
WRMSR_OFF = 0xFFBF4

LOAD_OFF = 0xFFC74
LOAD_ORIG = bytes.fromhex('b97900000033d283c0300f30')               # mov ecx,0x79; xor edx,edx; add eax,0x30; wrmsr
RESUME = 0xFFFFFC80
PAD_OFF, PAD_END = 0xEE678, 0xFF9B0
PAD_GUID = bytes.fromhex('d4548f0722cc48409e94879c214d562f')   # 078F54D4-CC22-4048-9E94-879C214D562F
STUB_OFF = 0xFF800

UCODE_FILE_OFF = 0x10048                     # FFS file 17088572 in FV_MAIN
UCODE_GUID = bytes.fromhex('728508177f37ef448f4eb09fff46a070')

FLASH_BASE = 0xFFF00000


def fail(msg):
    sys.exit('ERROR: ' + msg)


def vtf_data_checksum(img):
    """FFS1 with FFS_ATTRIB_CHECKSUM: data bytes plus IntegrityCheck.File sum to 0."""
    return (-sum(img[VTF_OFF + 24:VTF_OFF + VTF_SIZE])) & 0xff


def header_ok(img, off):
    h = bytearray(img[off:off + 24])
    h[17] = 0
    h[23] = 0
    return sum(h) & 0xff == 0


def ucode_entry_size(hdr):
    data_size, total_size = struct.unpack_from('<II', hdr, 0x1C)
    return total_size if data_size else 2048


def stub_code(stub_addr):
    """Hand-assembled; the tool disassembles nothing itself, see emu_reset.py for the check."""
    c = bytearray()
    c += b'\x89\xc6'                      # mov esi, eax            ; first matching entry
    c += b'\xb9\x79\x00\x00\x00'          # mov ecx, 0x79
    c += b'\x31\xd2'                      # xor edx, edx
    c += b'\x8d\x46\x30'                  # lea eax, [esi+0x30]
    c += b'\x0f\x30'                      # wrmsr                   ; load entry 1
    c += b'\xb9\x00\x08\x00\x00'          # mov ecx, 0x800
    c += b'\x83\x7e\x1c\x00'              # cmp dword [esi+0x1c], 0
    c += b'\x74\x03'                      # je +3
    c += b'\x8b\x4e\x20'                  # mov ecx, [esi+0x20]
    c += b'\x81\xc1\xff\x01\x00\x00'      # add ecx, 0x1ff
    c += b'\x81\xe1\x00\xfe\xff\xff'      # and ecx, 0xfffffe00
    c += b'\x8d\x1c\x0e'                  # lea ebx, [esi+ecx]      ; next entry
    c += b'\x8d\x43\x30'                  # lea eax, [ebx+0x30]
    c += b'\x39\xf8'                      # cmp eax, edi            ; edi = end of microcode file
    j1 = len(c); c += b'\x73\x00'         # jae done
    c += b'\x83\x3b\x01'                  # cmp dword [ebx], 1
    j2 = len(c); c += b'\x75\x00'         # jne done
    c += b'\x8b\x43\x0c'                  # mov eax, [ebx+0xc]
    c += b'\x3b\x46\x0c'                  # cmp eax, [esi+0xc]      ; same signature
    j3 = len(c); c += b'\x75\x00'         # jne done
    c += b'\x8b\x43\x18'                  # mov eax, [ebx+0x18]
    c += b'\x85\x46\x18'                  # test [esi+0x18], eax    ; overlapping platform flags
    j4 = len(c); c += b'\x74\x00'         # je done
    c += b'\xb9\x79\x00\x00\x00'          # mov ecx, 0x79
    c += b'\x31\xd2'                      # xor edx, edx
    c += b'\x8d\x43\x30'                  # lea eax, [ebx+0x30]
    c += b'\x0f\x30'                      # wrmsr                   ; load entry 2
    done = len(c)
    c += b'\xe9' + struct.pack('<i', RESUME - (stub_addr + done + 5))   # jmp 0xfffffc80
    for j in (j1, j2, j3, j4):
        c[j + 1] = done - (j + 2)
    return bytes(c)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('base')
    ap.add_argument('out')
    ap.add_argument('--skip-perfctl', action='store_true')
    ap.add_argument('--two-stage-ucode', metavar='FILE')
    a = ap.parse_args()

    orig = open(a.base, 'rb').read()
    img = bytearray(orig)
    if len(img) != 0x100000:
        fail('base is not 1 MB')
    if img[VTF_OFF:VTF_OFF + 16] != VTF_GUID:
        fail('volume top file not where expected')
    if int.from_bytes(img[VTF_OFF + 20:VTF_OFF + 23], 'little') != VTF_SIZE or img[VTF_OFF + 19] != 0x40:
        fail('volume top file size/attributes not as expected')
    if not header_ok(img, VTF_OFF) or vtf_data_checksum(img) != img[VTF_OFF + 17]:
        fail('base volume top file checksums are not valid to begin with')

    notes, allowed = [], {VTF_OFF + 17}
    if a.skip_perfctl:
        if img[PERFCTL_OFF:PERFCTL_OFF + len(PERFCTL_ORIG)] != PERFCTL_ORIG:
            fail('IA32_PERF_CTL sequence not found at 0x%x' % PERFCTL_OFF)
        img[WRMSR_OFF:WRMSR_OFF + 2] = b'\x90\x90'
        allowed |= {WRMSR_OFF, WRMSR_OFF + 1}
        notes.append('reset code: WRMSR IA32_PERF_CTL at 0x%08x -> NOP NOP' % (FLASH_BASE + WRMSR_OFF))

    if a.two_stage_ucode:
        # Microcode file: first entry stays, newer entry goes in the next slot
        if img[UCODE_FILE_OFF:UCODE_FILE_OFF + 16] != UCODE_GUID or img[UCODE_FILE_OFF + 18] != 0x01 \
                or img[UCODE_FILE_OFF + 19] != 0x00:
            fail('microcode RAW file not where expected')
        fsize = int.from_bytes(img[UCODE_FILE_OFF + 20:UCODE_FILE_OFF + 23], 'little')
        first = UCODE_FILE_OFF + 24
        hdr = img[first:first + 48]
        hv, rev, date, sig, cks, ldr, pf = struct.unpack_from('<7I', hdr)
        if hv != 1:
            fail('first microcode entry has header version %d' % hv)
        nxt = first + ((ucode_entry_size(hdr) + 0x1ff) & ~0x1ff)
        blobs = open(a.two_stage_ucode, 'rb').read()
        o, new = 0, None
        while o + 48 <= len(blobs):
            h = blobs[o:o + 48]
            n = ucode_entry_size(h)
            hv2, rev2, _, sig2, _, _, pf2 = struct.unpack_from('<7I', h)
            if hv2 == 1 and sig2 == sig and pf2 & pf:
                new = blobs[o:o + n]
                break
            o += n
        if new is None:
            fail('no microcode for sig 0x%x pf 0x%x in %s' % (sig, pf, a.two_stage_ucode))
        if struct.unpack_from('<I', new, 4)[0] <= rev:
            fail('new microcode is not newer than 0x%x' % rev)
        if sum(struct.unpack('<%dI' % (len(new) // 4), new)) & 0xffffffff:
            fail('new microcode checksum is bad')
        if nxt + len(new) > UCODE_FILE_OFF + fsize:
            fail('second entry does not fit in the microcode file')
        if set(img[nxt:UCODE_FILE_OFF + fsize]) != {0xff}:
            fail('microcode file has no free space after the first entry')
        img[nxt:nxt + len(new)] = new
        allowed |= set(range(nxt, nxt + len(new)))
        notes.append('microcode file: entry 1 rev 0x%x kept at 0x%x, entry 2 rev 0x%x added at 0x%x (%d bytes)'
                     % (rev, first, struct.unpack_from('<I', new, 4)[0], nxt, len(new)))

        # Reset code: jump to the stub instead of loading
        if img[LOAD_OFF:LOAD_OFF + len(LOAD_ORIG)] != LOAD_ORIG:
            fail('microcode load sequence not found at 0x%x' % LOAD_OFF)
        if img[PAD_OFF:PAD_OFF + 16] != PAD_GUID or img[PAD_OFF + 18] != 0xF0 or img[PAD_OFF + 19] != 0x00:
            fail('boot volume pad file not where expected')
        stub_addr = FLASH_BASE + STUB_OFF
        stub = stub_code(stub_addr)
        if not (PAD_OFF + 24 <= STUB_OFF and STUB_OFF + len(stub) <= PAD_END):
            fail('stub outside the pad file')
        if set(img[STUB_OFF:STUB_OFF + len(stub)]) != {0xff}:
            fail('stub space is not free')
        img[STUB_OFF:STUB_OFF + len(stub)] = stub
        jmp = b'\xe9' + struct.pack('<i', stub_addr - (FLASH_BASE + LOAD_OFF + 5))
        img[LOAD_OFF:LOAD_OFF + len(LOAD_ORIG)] = jmp + b'\x90' * (len(LOAD_ORIG) - len(jmp))
        allowed |= set(range(STUB_OFF, STUB_OFF + len(stub))) | set(range(LOAD_OFF, LOAD_OFF + len(LOAD_ORIG)))
        notes.append('reset code: load at 0x%08x -> jmp 0x%08x; %d-byte stub, returns to 0x%08x'
                     % (FLASH_BASE + LOAD_OFF, stub_addr, len(stub), RESUME))

    if not notes:
        fail('no patch selected')

    img[VTF_OFF + 17] = vtf_data_checksum(img)
    if not header_ok(img, VTF_OFF) or (sum(img[VTF_OFF + 24:VTF_OFF + VTF_SIZE]) + img[VTF_OFF + 17]) & 0xff:
        fail('checksums wrong after patching')
    changed = [i for i in range(len(img)) if img[i] != orig[i]]
    if set(changed) - allowed:
        fail('unexpected bytes changed: %s' % [hex(i) for i in changed if i not in allowed][:20])

    open(a.out, 'wb').write(img)
    for n in notes:
        print(n)
    print('%d bytes changed; 64K blocks changed: %s' % (len(changed), sorted({i // 0x10000 for i in changed})))


if __name__ == '__main__':
    main()
