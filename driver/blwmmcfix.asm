; BlwMmcFix.efi: mmc_fix.inc as a shell app, run before starting a kernel from the internal
; memory (boot.nsh: `fs0:\BlwMmcFix.efi`), for firmware without the fix. This shell has no `load`,
; and an app's image is freed when it returns, so the app copies mmc_fix.inc (position-independent,
; with its data) into boot-services code pool and wraps from the copy, which stays. Prints how many
; BlockIo instances it wrapped. With none to wrap (no internal memory, a fixed driver, or already
; run) it frees the copy and returns EFI_NOT_FOUND.
;
; Entry: EFI_STATUS EFIAPI Entry(EFI_HANDLE ImageHandle, EFI_SYSTEM_TABLE *SystemTable)

bits 32

RES_SIZE        equ     mmc_res_end - mmc_res_start

entry:
        push    ebp
        mov     ebp, esp
        sub     esp, 4                          ; -4 the copy
        push    ebx
        push    esi
        push    edi
        call    .here
.here:  pop     edi
        mov     ebx, [ebp + 12]                 ; ebx = SystemTable
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
        lea     eax, [edi + (crlf - .here)]
        push    eax
        push    ebx
        call    con_out
        add     esp, 8

        mov     eax, 0x8000000e                 ; EFI_NOT_FOUND
        test    esi, esi
        jz      .out
        xor     eax, eax
.out:   pop     edi
        pop     esi
        pop     ebx
        leave
        ret

msg:    dw      __utf16__('BlwMmcFix: BlockIo instances wrapped: '), 0
crlf:   dw      13, 10, 0

%include "con.inc"

mmc_res_start:
%include "mmc_fix.inc"
mmc_res_end:
