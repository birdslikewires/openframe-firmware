; BlwLanMacDel: a one-off EFI application (IA32) for the OpenFrame, run once from boot.nsh:
;
;     fs1:\BlwLanMacDel.efi
;
; Deletes NVRAM variable BlwLanMac (GUID EC87D643, 6 bytes), which BlwLanMac.efi stored up to
; firmware 26.11.19. Since 26.11.20 nothing reads it: BlwMac replaced it. AMI's store deletes by
; clearing the record's valid bit in place: no erase, no new space used.
;
; 1. GetVariable(BlwLanMac). If it isn't there, nothing else is done.
; 2. Locks down flash blocks 1-15 as BlwMac.efi does (FWH block-lock registers: write-lock +
;    lock-down, cleared only by reset). If that doesn't read back, nothing is written.
; 3. SetVariable(BlwLanMac, GUID, NV+BS+RT, DataSize 0): the delete, on the boot-services path.
; 4. GetVariable(BlwLanMac) again: EFI_NOT_FOUND (0x8000000E) if it's gone.
; 5. Records the four results in volatile variable BlwLanMacDel (BS+RT, RAM only, 16 bytes):
;    GetVariable before, lock-down (0 = ok), SetVariable, GetVariable after.
;
; Always returns EFI_SUCCESS so boot.nsh carries on. Position-independent; assembled with
; `nasm -f bin`, wrapped as a PE32 application by build_blwmac_app.py --del.

bits 32

FWH_LOCK        equ 0xFFB00002      ; block-lock register of block 0; block n is + n * 0x10000

; locals (ebp-relative)
L_RES           equ -16             ; 4 dwords: get before, lock-down, set, get after
L_SIZE          equ -20
L_ATTR          equ -24
L_BUF           equ -32             ; 8 bytes
L_FRAME         equ 32

entry:
        push    ebp
        mov     ebp, esp
        sub     esp, L_FRAME
        push    ebx
        push    esi
        push    edi
        cld
        lea     edi, [ebp - L_FRAME]            ; zero the locals
        mov     ecx, L_FRAME / 4
        xor     eax, eax
        rep stosd
        call    .here
.here:  pop     ebx                             ; ebx = PIC base

        call    get_old
        mov     [ebp + L_RES], eax
        test    eax, eax
        jnz     .report                         ; not there: nothing to do
        call    lockdown
        mov     [ebp + L_RES + 4], eax
        test    eax, eax
        jnz     .report                         ; not locked down: nothing written
        lea     eax, [ebp + L_BUF]
        push    eax                             ; Data (not read: size 0)
        push    0                               ; DataSize 0: delete
        push    7                               ; NV | BS | RT, as it was stored
        lea     eax, [ebx + (blw_guid - entry.here)]
        push    eax
        lea     eax, [ebx + (name_old - entry.here)]
        push    eax
        mov     eax, [ebp + 12]
        mov     eax, [eax + 0x38]               ; RuntimeServices
        call    [eax + 0x38]                    ; SetVariable
        add     esp, 20
        mov     [ebp + L_RES + 8], eax
        call    get_old
        mov     [ebp + L_RES + 12], eax

.report:
        lea     eax, [ebp + L_RES]
        push    eax
        push    16
        push    6                               ; BS | RT: volatile
        lea     eax, [ebx + (blw_guid - entry.here)]
        push    eax
        lea     eax, [ebx + (name_status - entry.here)]
        push    eax
        mov     eax, [ebp + 12]
        mov     eax, [eax + 0x38]
        call    [eax + 0x38]                    ; SetVariable
        add     esp, 20

        xor     eax, eax                        ; EFI_SUCCESS
        pop     edi
        pop     esi
        pop     ebx
        mov     esp, ebp
        pop     ebp
        ret

; get_old: GetVariable(BlwLanMac) into L_BUF (8 bytes). Returns its status.
get_old:
        mov     dword [ebp + L_SIZE], 8
        lea     eax, [ebp + L_BUF]
        push    eax                             ; Data
        lea     eax, [ebp + L_SIZE]
        push    eax                             ; &DataSize
        lea     eax, [ebp + L_ATTR]
        push    eax                             ; &Attributes
        lea     eax, [ebx + (blw_guid - entry.here)]
        push    eax
        lea     eax, [ebx + (name_old - entry.here)]
        push    eax
        mov     eax, [ebp + 12]
        mov     eax, [eax + 0x38]
        call    [eax + 0x30]                    ; RT->GetVariable
        add     esp, 20
        ret

; lockdown: write-lock + lock-down (0x03) flash blocks 1-15 and read them back.
; eax = 0 if every one reads back with both bits set, else 1. Never touches block 0 (NVRAM) or
; the flash data space. (The same as blwmac.asm's.)
lockdown:
        mov     edx, FWH_LOCK + 0x10000         ; block 1
        mov     ecx, 15
.blk:   mov     byte [edx], 0x03
        mov     al, [edx]
        and     al, 0x03
        cmp     al, 0x03
        jne     .bad
        add     edx, 0x10000
        dec     ecx
        jnz     .blk
        xor     eax, eax
        ret
.bad:   mov     eax, 1
        ret

        align   4, db 0
blw_guid:                                       ; EC87D643-EBA4-4BB5-A1E5-3F3E36B20DA9: AMI's, already in
        dd      0xec87d643                      ; this firmware's GUID store
        dw      0xeba4, 0x4bb5
        db      0xa1, 0xe5, 0x3f, 0x3e, 0x36, 0xb2, 0x0d, 0xa9
name_old:
        dw      __utf16__('BlwLanMac'), 0
name_status:
        dw      __utf16__('BlwLanMacDel'), 0
