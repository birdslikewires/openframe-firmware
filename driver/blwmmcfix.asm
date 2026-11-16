; BlwMmcFix.efi: mmc_fix.inc as a shell app, run before starting a kernel from the internal
; memory (boot.nsh: `BlwMmcFix.efi`, then the kernel if it succeeded, else GRUB). This shell has no
; `load`, and an app's image is freed when it returns, so the app copies mmc_fix.inc
; (position-independent, with its data) into boot-services code pool and wraps from the copy, which
; stays.
;
; It succeeds if the internal memory's driver can now be trusted with a kernel: it wrapped at least
; one BlockIo instance of the buggy driver, or found at least one already fixed, that is, whose
; ReadBlocks is an mmc_fix.inc wrapper around the buggy driver's (our firmware's BlwPlatformDxe
; does that at boot from 26.11.22; so does an earlier run of this app). So one boot.nsh starts the
; kernel directly on any firmware: stock (wrapped here), ours (already fixed), or one cut off part
; way through an update. It prints both counts. With neither (no internal memory, or a driver it
; doesn't know) it frees the copy and returns EFI_NOT_FOUND, and boot.nsh goes on to GRUB.
;
; A wrapper is recognised by what it does, not by where it is, so any release's copy is: its code
; starts `call $+5; pop eax; mov eax, [eax + disp32]` (the original's slot), the slot holds a
; ReadBlocks that matches the buggy driver's signature (mmc_sig.inc), and its jmp goes to code that
; starts as this app's own mmc_split does, up to and including the 512 KB piece size.
;
; Entry: EFI_STATUS EFIAPI Entry(EFI_HANDLE ImageHandle, EFI_SYSTEM_TABLE *SystemTable)

bits 32

RES_SIZE        equ     mmc_res_end - mmc_res_start

entry:
        push    ebp
        mov     ebp, esp
        sub     esp, 8                          ; -4 the copy, -8 already fixed
        push    ebx
        push    esi
        push    edi
        call    .here
.here:  pop     edi
        mov     ebx, [ebp + 12]                 ; ebx = SystemTable
        mov     eax, [ebx + 0x3c]               ; instances fixed already, counted before this
        push    eax                             ; run wraps any (its own wrappers would count)
        call    fixed_count
        add     esp, 4
        mov     [ebp - 8], eax
        xor     esi, esi                        ; esi = wrapped

        mov     dword [ebp - 4], 0
        lea     eax, [ebp - 4]
        push    eax
        push    RES_SIZE
        push    3                               ; EfiBootServicesCode
        mov     eax, [ebx + 0x3c]
        call    [eax + 0x2c]                    ; BS->AllocatePool
        add     esp, 12
        test    eax, eax
        jnz     .print

        push    esi
        push    edi
        lea     esi, [edi + (mmc_res_start - .here)]
        mov     edi, [ebp - 4]
        mov     ecx, RES_SIZE
        cld
        rep     movsb
        pop     edi
        pop     esi

        mov     eax, [ebx + 0x3c]
        push    eax
        mov     eax, [ebp - 4]
        add     eax, mmc_fix_install - mmc_res_start
        call    eax                             ; the copy's mmc_fix_install(BS)
        add     esp, 4
        mov     esi, eax
        test    esi, esi
        jnz     .print
        push    dword [ebp - 4]                 ; nothing wrapped: nothing uses the copy
        mov     eax, [ebx + 0x3c]
        call    [eax + 0x30]                    ; BS->FreePool
        add     esp, 4

.print: lea     eax, [edi + (msg - .here)]
        push    eax
        push    ebx
        call    con_out
        add     esp, 8
        push    esi
        push    ebx
        call    con_hex
        add     esp, 8
        lea     eax, [edi + (msg2 - .here)]
        push    eax
        push    ebx
        call    con_out
        add     esp, 8
        push    dword [ebp - 8]
        push    ebx
        call    con_hex
        add     esp, 8
        lea     eax, [edi + (crlf - .here)]
        push    eax
        push    ebx
        call    con_out
        add     esp, 8

        mov     eax, 0x8000000e                 ; EFI_NOT_FOUND
        or      esi, [ebp - 8]                  ; wrapped or already fixed: the kernel can start
        jz      .out
        xor     eax, eax
.out:   pop     edi
        pop     esi
        pop     ebx
        leave
        ret

msg:    dw      __utf16__('BlwMmcFix: BlockIo instances wrapped: '), 0
msg2:   dw      __utf16__(', already fixed: '), 0
crlf:   dw      13, 10, 0

SPLIT_HEAD      equ     21                      ; mmc_split up to and including cmp esi, MMC_PIECE

; UINT32 fixed_count(EFI_BOOT_SERVICES *BS): how many BlockIo instances already have the MMC fix
; (see the top). Keeps ebx, esi, edi.
fixed_count:
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
        xor     esi, esi                        ; esi = fixed so far
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
        call    is_fixed
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

; UINT32 is_fixed(EFI_BLOCK_IO_PROTOCOL *Bio): 1 if its ReadBlocks is an mmc_fix.inc wrapper around
; the buggy driver's (see the top), else 0. Keeps ebx, esi, edi.
is_fixed:
        push    ebx
        push    esi
        push    edi
        call    .here
.here:  pop     edi
        mov     ebx, [esp + 16]                 ; Bio
        mov     esi, [ebx + 0x10]               ; ReadBlocks
        cmp     dword [esi], 0x000000e8         ; e8 00 00 00 00: call $+5
        jne     .no
        cmp     dword [esi + 4], 0x808b5800     ; 58: pop eax; 8b 80: mov eax, [eax + disp32]
        jne     .no
        mov     eax, [esi + 8]
        mov     eax, [esi + 5 + eax]            ; the original, from the wrapper's slot
        test    eax, eax
        jz      .no
        lea     ecx, [edi + (mmc_sig_read - .here)]
        push    ecx
        push    eax
        call    mmc_sig_match                   ; the buggy driver's ReadBlocks
        add     esp, 8
        test    eax, eax
        jz      .no
        movzx   eax, byte [esi + 12]            ; then the jump into its mmc_split
        cmp     al, 0xeb
        jne     .near
        movsx   eax, byte [esi + 13]
        lea     esi, [esi + 14 + eax]
        jmp     .split
.near:  cmp     al, 0xe9
        jne     .no
        mov     eax, [esi + 13]
        lea     esi, [esi + 17 + eax]
.split: lea     edi, [edi + (mmc_split - .here)]
        mov     ecx, SPLIT_HEAD
        cld
        repe    cmpsb                           ; as this app's own mmc_split starts
        jne     .no
        mov     eax, 1
        jmp     .out
.no:    xor     eax, eax
.out:   pop     edi
        pop     esi
        pop     ebx
        ret

%include "con.inc"

mmc_res_start:
%include "mmc_fix.inc"
mmc_res_end:
