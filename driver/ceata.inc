; ceata.inc: stops the firmware's MMC/SD media driver from probing the OF1's internal memory for
; a CE-ATA disk on every boot, which cost 2.8 s.
;
; When MmcMediaDevice (DD223EF8-6D1B-490A-A53E-BA86FAAEB778, Intel EDK) has identified an MMC
; card, its Start asks whether the card is really a CE-ATA device (ATA commands carried over the
; MMC bus, for the tiny hard disks of 2005-2008): its check (RVA 0x72b) reads 16 CE-ATA registers
; with CMD60 (RW_MULTIPLE_REGISTER) and looks for the signature CE AA. No MMC answers CMD60, so the
; command runs to its timeout: 2797 ms on an OF1 (measured with MmcProbe.efi), then the driver
; resets the host's command and data lines and sets the card up as MMC. Linux identifies the same
; card in 81 ms.
;
; This makes the check return FALSE at once (xor al, al; ret over its first three bytes). The card
; is then set up exactly as before, without the CMD60 and the reset after it: measured on an OF1
; (in memory, from the shell), the MMC's connect took 194 ms instead of 2993 ms, with the same
; commands otherwise. Nothing else is affected: the internal IDE port has no driver in this
; firmware at all, and CE-ATA was never fitted to a Joggler. Only the exact stock function is
; changed (ceata_sig.inc, generated and checked by tools/ceata_sig.py).

; UINT32 cea_patch_image(EFI_LOADED_IMAGE_PROTOCOL *Li): 1 if this is the stock MmcMediaDevice and
; its CE-ATA check now answers FALSE at once. Keeps ebx, esi, edi.
cea_patch_image:
        push    ebx
        push    esi
        push    edi
        call    .here
.here:  pop     edi
        mov     ebx, [esp + 16]                 ; Li
        mov     esi, [ebx + 0x10]               ; FilePath: one MEDIA_FW_VOL_FILEPATH node
        test    esi, esi
        jz      .no
        cmp     dword [esi], 0x00140604         ; type 4, subtype 6, length 20
        jne     .no
        add     esi, 4                          ; its file GUID
        push    edi
        lea     edi, [edi + (cea_mmc_guid - .here)]
        mov     ecx, 16
        cld
        repe    cmpsb
        pop     edi
        jne     .no
        cmp     dword [ebx + 0x28], CEA_END_RVA ; ImageSize (low half: IA32)
        jb      .no
        mov     esi, [ebx + 0x20]               ; ImageBase
        test    esi, esi
        jz      .no
        lea     eax, [edi + (cea_sig - .here)]
        push    eax
        lea     eax, [esi + CEA_FN_RVA]
        push    eax
        call    mmc_sig_match
        add     esp, 8
        test    eax, eax
        jz      .no
        mov     word [esi + CEA_FN_RVA], 0xc032         ; xor al, al
        mov     byte [esi + CEA_FN_RVA + 2], 0xc3       ; ret
        cmp     word [esi + CEA_FN_RVA], 0xc032
        jne     .no
        cmp     byte [esi + CEA_FN_RVA + 2], 0xc3
        jne     .no
        mov     eax, 1
        jmp     .out
.no:    xor     eax, eax
.out:   pop     edi
        pop     esi
        pop     ebx
        ret

cea_done:       db 0                            ; set once MmcMediaDevice is patched
        align   4, db 0
cea_mmc_guid:                                   ; DD223EF8-6D1B-490A-A53E-BA86FAAEB778 MmcMediaDevice
        dd      0xdd223ef8
        dw      0x6d1b, 0x490a
        db      0xa5, 0x3e, 0xba, 0x86, 0xfa, 0xae, 0xb7, 0x78

%include "ceata_sig.inc"
