; mmcinit.inc: a shorter wait in the firmware's start-up of the OF1's internal memory (MMC),
; changed in memory as the driver loads, like ceata.inc. The kernel starts about 0.1 s sooner
; (5.24 s against 5.35 s from power-on on an OF1, booting from the internal memory).
;
; 1. SdHost (89C24DFA-A105-4E8D-AFE6-0BA7D40972AE, Intel EDK), SendCommand. After writing a
;    command it reads the error and normal interrupt status, and while the command (and any
;    data) isn't done it stalls 1000 us and counts one off the caller's timeout (ms). Commands
;    take microseconds, but the first read comes before that, so nearly every command paid a
;    whole millisecond. The whole wait (0x4f6-0x54b) becomes a call of sdh_wait (sdh_wait.inc):
;    10 us polls, no timeout shorter than before, and once the command is done a wait for the
;    controller to free its command and data lines, which nothing in the stock driver waits for.
;    -DMMI_INPLACE builds the in-place loop of 26.11.27-dev1 to -dev3 instead. Checked against
;    the stock loop under emulation (tools/emu_mmcinit.py).
;
; 2. Only with -DMMI_CMD0 (a development build): MmcMediaDevice
;    (DD223EF8-6D1B-490A-A53E-BA86FAAEB778) waits 1 ms, not 50, after each CMD0 (GO_IDLE), as
;    Linux does (mmc_go_idle). Measured on an OF1 (26.11.27-dev5 against -dev4) it made no
;    difference to when the kernel starts, so releases leave it alone.
;
; Only the exact stock code is changed (mmcinit_sig.inc, generated and checked by
; tools/mmcinit_sig.py), and only once per image.

; UINT32 sdh_patch_image(EFI_LOADED_IMAGE_PROTOCOL *Li): 1 if this is the stock SdHost and its
; SendCommand wait now goes through sdh_wait. Keeps ebx, esi, edi.
sdh_patch_image:
%ifdef MMI_CMD0_ONLY
        mov     eax, 1                          ; development build: SdHost left as it is
        ret
%endif
        push    ebx
        push    esi
        push    edi
        call    .here
.here:  pop     edi
        lea     eax, [edi + (sdh_guid - .here)]
        push    SDH_END_RVA
        push    eax
        push    dword [esp + 24]                ; Li
        call    mmi_image_base
        add     esp, 12
        test    eax, eax
        jz      .no
        mov     esi, eax                        ; ImageBase
        lea     eax, [edi + (sdh_sig - .here)]
        push    eax
        lea     eax, [esi + SDH_SIG_RVA]
        push    eax
        call    mmc_sig_match
        add     esp, 8
        test    eax, eax
        jz      .no
%ifdef MMI_INPLACE
        push    esi                             ; the loop's new bytes, then the two branches
        push    edi
        lea     edi, [esi + SDH_NEW_RVA]
        mov     esi, [esp]                      ; .here
        lea     esi, [esi + (sdh_new - .here)]
        mov     ecx, SDH_NEW_LEN
        cld
        rep     movsb
        pop     edi
        pop     esi
        mov     byte [esi + SDH_BR1_RVA], SDH_BR1_NEW
        mov     byte [esi + SDH_BR2_RVA], SDH_BR2_NEW
        push    esi                             ; check it all landed
        push    edi
        lea     edi, [esi + SDH_NEW_RVA]
        mov     esi, [esp]
        lea     esi, [esi + (sdh_new - .here)]
        mov     ecx, SDH_NEW_LEN
        repe    cmpsb
        pop     edi
        pop     esi
        jne     .no
        cmp     byte [esi + SDH_BR1_RVA], SDH_BR1_NEW
        jne     .no
        cmp     byte [esi + SDH_BR2_RVA], SDH_BR2_NEW
        jne     .no
%else
        ; The glue in place of the whole wait: a call of sdh_wait (sdh_wait.inc), with SdHost's own
        ; gBS pointer (the relocated operand at SDH_GBS_RVA, read before it is overwritten).
        push    dword [esi + SDH_GBS_RVA]
        push    esi
        push    edi
        lea     edi, [esi + SDH_GLUE_RVA]
        mov     esi, [esp]                      ; .here
        lea     esi, [esi + (sdh_glue - .here)]
        mov     ecx, SDH_GLUE_LEN
        cld
        rep     movsb
        pop     edi
        pop     esi
        pop     eax
        mov     [esi + SDH_GLUE_RVA + SDH_GLUE_GBS], eax
        lea     eax, [edi + (sdh_wait - .here)]
        lea     ecx, [esi + SDH_GLUE_RVA + SDH_GLUE_CALL + 4]
        sub     eax, ecx
        mov     [esi + SDH_GLUE_RVA + SDH_GLUE_CALL], eax
        push    esi                             ; check it landed: the template's fixed bytes...
        push    edi
        lea     edi, [esi + SDH_GLUE_RVA + SDH_GLUE_CALL + 4]
        mov     esi, [esp]
        lea     esi, [esi + (sdh_glue - .here) + SDH_GLUE_CALL + 4]
        mov     ecx, SDH_GLUE_LEN - SDH_GLUE_CALL - 4
        repe    cmpsb
        pop     edi
        pop     esi
        jne     .no
        cmp     byte [esi + SDH_GLUE_RVA], 0xa1 ; ...and the two filled in
        jne     .no
        lea     eax, [edi + (sdh_wait - .here)]
        lea     ecx, [esi + SDH_GLUE_RVA + SDH_GLUE_CALL + 4]
        add     ecx, [esi + SDH_GLUE_RVA + SDH_GLUE_CALL]
        cmp     eax, ecx
        jne     .no
%endif
        mov     eax, 1
        jmp     .out
.no:    xor     eax, eax
.out:   pop     edi
        pop     esi
        pop     ebx
        ret

; UINT32 mmw_patch_image(EFI_LOADED_IMAGE_PROTOCOL *Li): 1 if this is the stock MmcMediaDevice
; and both waits after CMD0 are now 1 ms. Keeps ebx, esi, edi.
mmw_patch_image:
%ifndef MMI_CMD0
        mov     eax, 1                          ; the CMD0 waits left as they are (see the top)
        ret
%endif
        push    ebx
        push    esi
        push    edi
        call    .here
.here:  pop     edi
        lea     eax, [edi + (cea_mmc_guid - .here)]
        push    MMW_END_RVA
        push    eax
        push    dword [esp + 24]                ; Li
        call    mmi_image_base
        add     esp, 12
        test    eax, eax
        jz      .no
        mov     esi, eax
        lea     eax, [edi + (mmw_sig1 - .here)]
        push    eax
        lea     eax, [esi + MMW_SIG1_RVA]
        push    eax
        call    mmc_sig_match
        add     esp, 8
        test    eax, eax
        jz      .no
        lea     eax, [edi + (mmw_sig2 - .here)]
        push    eax
        lea     eax, [esi + MMW_SIG2_RVA]
        push    eax
        call    mmc_sig_match
        add     esp, 8
        test    eax, eax
        jz      .no
        mov     dword [esi + MMW_SD_RVA], MMW_NEW_US
        mov     dword [esi + MMW_MMC_RVA], MMW_NEW_US
        cmp     dword [esi + MMW_SD_RVA], MMW_NEW_US
        jne     .no
        cmp     dword [esi + MMW_MMC_RVA], MMW_NEW_US
        jne     .no
        mov     eax, 1
        jmp     .out
.no:    xor     eax, eax
.out:   pop     edi
        pop     esi
        pop     ebx
        ret

; UINT8 *mmi_image_base(EFI_LOADED_IMAGE_PROTOCOL *Li, EFI_GUID *File, UINT32 MinSize): the image's
; base if it was loaded from the firmware volume file File (FilePath is that one node) and is at
; least MinSize bytes, else 0. Keeps ebx, esi, edi.
mmi_image_base:
        push    esi
        push    edi
        mov     edx, [esp + 12]                 ; Li
        mov     esi, [edx + 0x10]               ; FilePath: one MEDIA_FW_VOL_FILEPATH node
        test    esi, esi
        jz      .no
        cmp     dword [esi], 0x00140604         ; type 4, subtype 6, length 20
        jne     .no
        add     esi, 4                          ; its file GUID
        mov     edi, [esp + 16]
        mov     ecx, 16
        cld
        repe    cmpsb
        jne     .no
        mov     eax, [esp + 20]
        cmp     [edx + 0x28], eax               ; ImageSize (low half: IA32)
        jb      .no
        mov     eax, [edx + 0x20]               ; ImageBase
        jmp     .out
.no:    xor     eax, eax
.out:   pop     edi
        pop     esi
        ret

sdh_done:       db 0                            ; set once SdHost is changed
mmw_done:       db 0                            ; set once MmcMediaDevice's waits are changed
        align   4, db 0
sdh_guid:                                       ; 89C24DFA-A105-4E8D-AFE6-0BA7D40972AE SdHost
        dd      0x89c24dfa
        dw      0xa105, 0x4e8d
        db      0xaf, 0xe6, 0x0b, 0xa7, 0xd4, 0x09, 0x72, 0xae

%include "mmcinit_sig.inc"
%include "sdh_wait.inc"
