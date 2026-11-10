; mmc_fix: makes the firmware's MMC/SD media driver read and write more than 1 MB at a time
; correctly, so the shell can load a kernel from an OpenFrame's internal memory.
;
; The bug (MmcMediaDevice, DD223EF8-6D1B-490A-A53E-BA86FAAEB778, every OF1 build): BlockIo
; ReadBlocks and WriteBlocks split a transfer into chunks of the host's DMA boundary size (512 KB,
; SdHost's HostCapability.BoundarySize) through an aligned bounce buffer, but work out each
; chunk's card address as BlockSize * the caller's LBA + the previous chunk's length. That is
; right for the first two chunks only: the third and every later chunk read (or write) the
; second chunk's place again. The FAT driver reads a file's contiguous clusters in one call, so a
; file over 1 MB loads with everything past its first megabyte wrong: the shell starts a
; corrupted kernel, which hangs with no output. GRUB reads FAT cluster by cluster (2 KB), which is
; why it could load the same files.
;
; The fix: each BlockIo instance whose ReadBlocks/WriteBlocks is that driver's (recognised by
; their code, mmc_sig.inc) gets wrappers that hand the original requests of at most 512 KB, one
; chunk each. Pieces stay correct while the boundary size is 256 KB or more (two chunks). The
; instance's protocol structure is changed in place, so the partition and disk I/O layers above
; it, which call through it, use the wrappers at once.
;
; Position-independent (call/pop). IA32 EFIAPI is cdecl. The wrappers keep the originals in this
; image, so whatever includes this must stay loaded while the instances are in use.

MMC_PIECE       equ     0x80000                 ; 512 KB: one chunk of the driver's split

; UINT32 mmc_fix_install(EFI_BOOT_SERVICES *BS): wraps every BlockIo instance of the buggy driver
; not wrapped yet. Returns how many it wrapped. Keeps ebx, esi, edi.
mmc_fix_install:
        push    ebp
        mov     ebp, esp
        sub     esp, 16                         ; -4 count, -8 handle buffer, -12 BlockIo, -16 index
        push    ebx
        push    esi
        push    edi
        call    .here
.here:  pop     edi
        xor     eax, eax
        mov     [ebp - 4], eax
        mov     [ebp - 8], eax
        mov     [ebp - 16], eax
        xor     esi, esi                        ; esi = wrapped so far
        mov     ebx, [ebp + 8]                  ; ebx = BS

        lea     eax, [ebp - 8]                  ; LocateHandleBuffer(ByProtocol, &BlockIo, NULL,
        push    eax                             ;                    &count, &buffer)
        lea     eax, [ebp - 4]
        push    eax
        push    0
        lea     eax, [edi + (mmc_bio_guid - .here)]
        push    eax
        push    2                               ; ByProtocol
        call    [ebx + 0xa8]                    ; BS->LocateHandleBuffer
        add     esp, 20
        test    eax, eax
        jnz     .out

.loop:  mov     eax, [ebp - 16]
        cmp     eax, [ebp - 4]
        jae     .free
        mov     edx, [ebp - 8]
        mov     edx, [edx + eax * 4]
        lea     eax, [ebp - 12]                 ; HandleProtocol(handle, &BlockIo, &bio)
        push    eax
        lea     eax, [edi + (mmc_bio_guid - .here)]
        push    eax
        push    edx
        call    [ebx + 0x58]                    ; BS->HandleProtocol
        add     esp, 12
        test    eax, eax
        jnz     .next
        push    dword [ebp - 12]
        call    mmc_fix_wrap
        add     esp, 4
        add     esi, eax
.next:  inc     dword [ebp - 16]
        jmp     .loop

.free:  push    dword [ebp - 8]
        call    [ebx + 0x30]                    ; BS->FreePool
        add     esp, 4
.out:   mov     eax, esi
        pop     edi
        pop     esi
        pop     ebx
        leave
        ret

; UINT32 mmc_fix_wrap(EFI_BLOCK_IO_PROTOCOL *Bio): 1 if it wrapped this instance, else 0 (not the
; buggy driver's, or already wrapped). Keeps ebx, esi, edi.
; EFI_BLOCK_IO_PROTOCOL (IA32): +0 Revision (UINT64), +8 Media, +0xc Reset, +0x10 ReadBlocks,
; +0x14 WriteBlocks, +0x18 FlushBlocks.
mmc_fix_wrap:
        push    ebx
        push    esi
        push    edi
        call    .here
.here:  pop     edi
        mov     ebx, [esp + 16]                 ; Bio

        mov     esi, [ebx + 0x10]               ; ReadBlocks must be the buggy one...
        lea     eax, [edi + (mmc_sig_read - .here)]
        push    eax
        push    esi
        call    mmc_sig_match
        add     esp, 8
        test    eax, eax
        jz      .no
        lea     ecx, [edi + (mmc_orig_read - .here)]
        call    .record                         ; ...and the same one as any already wrapped
        jnz     .no

        mov     esi, [ebx + 0x14]               ; WriteBlocks too, if it is the buggy one
        lea     eax, [edi + (mmc_sig_write - .here)]
        push    eax
        push    esi
        call    mmc_sig_match
        add     esp, 8
        test    eax, eax
        jz      .read
        lea     ecx, [edi + (mmc_orig_write - .here)]
        call    .record
        jnz     .read
        lea     eax, [edi + (mmc_wrap_write - .here)]
        mov     [ebx + 0x14], eax
.read:  lea     eax, [edi + (mmc_wrap_read - .here)]
        mov     [ebx + 0x10], eax
        mov     eax, 1
        jmp     .out
.no:    xor     eax, eax
.out:   pop     edi
        pop     esi
        pop     ebx
        ret

; .record: [ecx] = original slot, esi = this instance's function. Stores esi in an empty slot.
; ZF set if the slot now holds esi (fine to wrap), clear if it holds a different function.
.record:
        mov     eax, [ecx]
        test    eax, eax
        jnz     .cmp
        mov     [ecx], esi
        ret                                     ; ZF still set from the test
.cmp:   cmp     eax, esi
        ret

; UINT32 mmc_sig_match(UINT8 *Code, mmc_sig *Sig): 1 if Code matches every run of Sig.
; Sig: runs of (dw offset, dw length, db bytes...), ended by a zero length. Keeps ebx, esi, edi.
mmc_sig_match:
        push    esi
        push    edi
        mov     edx, [esp + 12]                 ; Code
        mov     esi, [esp + 16]                 ; Sig
.run:   movzx   ecx, word [esi + 2]             ; length
        jecxz   .yes
        movzx   edi, word [esi]                 ; offset
        add     edi, edx
        add     esi, 4
        repe    cmpsb
        jne     .no
        jmp     .run
.yes:   mov     eax, 1
        jmp     .out
.no:    xor     eax, eax
.out:   pop     edi
        pop     esi
        ret

; The wrappers: EFI_STATUS (This, MediaId, Lba (UINT64), BufferSize, Buffer), as the originals.
mmc_wrap_read:
        call    .here
.here:  pop     eax
        mov     eax, [eax + (mmc_orig_read - .here)]
        jmp     mmc_split
mmc_wrap_write:
        call    .here
.here:  pop     eax
        mov     eax, [eax + (mmc_orig_write - .here)]
        jmp     mmc_split

; mmc_split: eax = the original function, the stack as the wrapper was called. Requests of up to
; one piece, and sizes that aren't whole blocks (which the original refuses before transferring),
; go to the original unchanged. Larger ones go in pieces, stopping at the first error, whose status
; is returned; with a NULL buffer that is the first piece's EFI_INVALID_PARAMETER.
mmc_split:
        push    ebp
        mov     ebp, esp
        push    ebx
        push    esi
        push    edi
        sub     esp, 20                         ; -16 piece, -20 original, -24/-28 LBA, -32 block size
        mov     [ebp - 20], eax
        mov     esi, [ebp + 0x18]               ; esi = bytes left
        cmp     esi, MMC_PIECE
        jbe     .whole
        mov     edi, [ebp + 0x1c]               ; edi = buffer (a NULL one fails the first piece)
        mov     eax, [ebp + 8]
        mov     eax, [eax + 8]                  ; This->Media
        mov     ecx, [eax + 0xc]                ; Media->BlockSize
        test    ecx, ecx
        jz      .whole
        mov     [ebp - 32], ecx
        mov     eax, esi
        xor     edx, edx
        div     ecx
        test    edx, edx
        jnz     .whole
        mov     eax, [ebp + 0x10]
        mov     [ebp - 24], eax                 ; LBA low
        mov     eax, [ebp + 0x14]
        mov     [ebp - 28], eax                 ; LBA high

.piece: mov     eax, esi
        cmp     eax, MMC_PIECE
        jbe     .call
        mov     eax, MMC_PIECE
.call:  mov     [ebp - 16], eax
        push    edi                             ; Buffer
        push    eax                             ; BufferSize
        push    dword [ebp - 28]                ; Lba
        push    dword [ebp - 24]
        push    dword [ebp + 0x0c]              ; MediaId
        push    dword [ebp + 8]                 ; This
        call    [ebp - 20]
        add     esp, 24
        test    eax, eax
        jnz     .done
        mov     eax, [ebp - 16]
        add     edi, eax
        sub     esi, eax
        xor     edx, edx
        div     dword [ebp - 32]
        add     [ebp - 24], eax
        adc     dword [ebp - 28], 0
        test    esi, esi
        jnz     .piece
        xor     eax, eax
.done:  lea     esp, [ebp - 12]
        pop     edi
        pop     esi
        pop     ebx
        pop     ebp
        ret

.whole: mov     eax, [ebp - 20]                 ; tail call with the caller's own arguments
        lea     esp, [ebp - 12]
        pop     edi
        pop     esi
        pop     ebx
        pop     ebp
        jmp     eax

mmc_orig_read:  dd 0                            ; the driver's ReadBlocks, once one is wrapped
mmc_orig_write: dd 0                            ; and its WriteBlocks

mmc_bio_guid:                                   ; 964E5B21-6459-11D2-8E39-00A0C969723B BlockIo
        dd      0x964e5b21
        dw      0x6459, 0x11d2
        db      0x8e, 0x39, 0x00, 0xa0, 0xc9, 0x69, 0x72, 0x3b

%include "mmc_sig.inc"
