; splash.inc: our boot splash on any OpenFrame, put into the stock display driver in memory as it
; loads, from 26.11.31.
;
; The splash is a raw RGB bitmap in the configuration text of Intel's IEGD display driver (GOP,
; 2B13E5F0-E82E-4209-898A-8A53E8570DB9): `$sspc01` holds its background, x, y, width and height as
; hex numbers, and `$ssd` is followed by the pixels and `ENDSSDATA`. On an OpenFrame 1 our releases
; put the bird there in flash (build_patched.py --logo); an OpenFrame 2's display driver is in
; blocks no update of ours writes, so there it has to be done in memory. That works because the
; driver's entry only installs its driver binding: the text is read in its Start, through the
; `ssp` and `ssd` handlers, when the console connects the screen, long after this driver's entry
; and the LoadedImage notifies.
;
; Only a display driver we know is changed: the IEGD build most OpenFrames have, with either of
; its two panel configurations (OpenFrame-Sharp: the O2 OF1s and most OF2s; OpenFrame-Samsung: the
; Cisco OF2), and from 26.11.32 two older builds, whose handlers are the same code 0x50 bytes
; earlier, with their own Sharp configurations (the OpenPeak OF2; the Verizon and Telstra OF1s).
; splash_sig.inc (from tools/splash_sig.py) has the code of both handlers and each place they can
; be and, for each configuration, all of its text from its start to the first pixel, less the
; digits of x, y, width and height, plus the reserve's closing `CEDCED`. On a match the pixels, `ENDSSDATA`
; and zeros to the reserve's end are written (the handlers read at most up to it), then the
; numbers, and both are read back. The panel settings are left as they were. On our OpenFrame 1
; releases this writes what is already there.

%ifndef SPLASH_FILE
%error "SPLASH_FILE: the numbers (24 bytes), pixels and ENDSSDATA (build_ucode_driver.py --splash)"
%endif

; UINT32 gsp_patch_image(EFI_LOADED_IMAGE_PROTOCOL *Li): 1 if Li is a display driver we know and its
; splash is now ours, else 0 (nothing changed). cdecl. Keeps ebx, esi, edi, ebp.
gsp_patch_image:
        push    ebx
        push    esi
        push    edi
        push    ebp
        call    .here
.here:  pop     edi
        push    edi                             ; [esp] = .here
        mov     ebx, [esp + 24]                 ; Li
        mov     esi, [ebx + 0x10]               ; FilePath: one MEDIA_FW_VOL_FILEPATH node
        test    esi, esi
        jz      .no
        cmp     dword [esi], 0x00140604         ; type 4, subtype 6, length 20
        jne     .no
        add     esi, 4                          ; its file GUID
        lea     edi, [edi + (gsp_gop_guid - .here)]
        mov     ecx, 16
        cld
        repe    cmpsb
        jne     .no
        cmp     dword [ebx + 0x28], GSP_MIN_SIZE ; ImageSize (low half: IA32)
        jb      .no
        mov     ebp, [ebx + 0x20]               ; ImageBase
        test    ebp, ebp
        jz      .no
        call    pid_note_display                ; which panel it's set up for (panel_id.inc), from
        mov     edi, [esp]                      ; any build, before the code checks below
        lea     ebx, [edi + (gsp_code_rvas - .here)]
.code:  mov     eax, [ebx]                      ; each place the handlers can be (0 ends the list)
        test    eax, eax
        jz      .no
        add     ebx, 4
        lea     ecx, [edi + (gsp_code_sig - .here)]
        push    ecx
        add     eax, ebp
        push    eax
        call    mmc_sig_match                   ; the handlers that read the splash
        add     esp, 8
        test    eax, eax
        jz      .code
        lea     ebx, [edi + (gsp_cfgs - .here)] ; then which configuration, if any
.cfg:   movzx   eax, word [ebx]
        test    eax, eax
        jz      .no
        lea     eax, [ebx + 8]
        push    eax
        lea     eax, [ebp + GSP_CFG_RVA]
        push    eax
        call    mmc_sig_match
        add     esp, 8
        test    eax, eax
        jnz     .found
        movzx   eax, word [ebx]
        add     ebx, eax
        jmp     .cfg

.found: mov     edx, [esp]                      ; the pixels and ENDSSDATA
        lea     esi, [edx + (gsp_data - .here) + 24]
        movzx   eax, word [ebx + 4]
        lea     edi, [ebp + GSP_CFG_RVA + eax]
        mov     ecx, GSP_DATA_LEN - 24
        cld
        rep     movsb
        lea     ecx, [ebp + GSP_END_RVA]        ; zeros to the reserve's end
        sub     ecx, edi
        xor     eax, eax
        rep     stosb
        lea     esi, [edx + (gsp_data - .here)] ; then the numbers
        movzx   eax, word [ebx + 2]
        lea     edi, [ebp + GSP_CFG_RVA + eax]
        mov     ecx, 24
        rep     movsb

        lea     esi, [edx + (gsp_data - .here)] ; read both back
        movzx   eax, word [ebx + 2]
        lea     edi, [ebp + GSP_CFG_RVA + eax]
        mov     ecx, 24
        repe    cmpsb
        jne     .no
        movzx   eax, word [ebx + 4]
        lea     edi, [ebp + GSP_CFG_RVA + eax]
        mov     ecx, GSP_DATA_LEN - 24
        repe    cmpsb
        jne     .no
        mov     eax, 1
        jmp     .out
.no:    xor     eax, eax
.out:   pop     edx
        pop     ebp
        pop     edi
        pop     esi
        pop     ebx
        ret

gsp_done:       db 0                            ; set once the display driver has our splash
        align   4, db 0
gsp_gop_guid:                                   ; 2B13E5F0-E82E-4209-898A-8A53E8570DB9 the IEGD GOP
        dd      0x2b13e5f0
        dw      0xe82e, 0x4209
        db      0x89, 0x8a, 0x8a, 0x53, 0xe8, 0x57, 0x0d, 0xb9

%include "splash_sig.inc"

gsp_data:                                       ; "0x0,0xXXX,0xYY,0xWW,0xHH", RGB pixels, ENDSSDATA
        incbin  SPLASH_FILE
GSP_DATA_LEN    equ     $ - gsp_data
        times   -(GSP_DATA_LEN < 24 + 9) db 0  ; (nasm refuses a negative count: SPLASH_FILE too short,
        times   -(GSP_CFG_RVA + GSP_PX_MAX + GSP_DATA_LEN - 24 > GSP_END_RVA) db 0 ; or too big for the reserve)
