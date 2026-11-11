; panel_clock: undoes the stock north-bridge driver's LVDS clock, so the OpenFrame 1's panel runs
; at 60 Hz in the firmware as it was made to, not 156 Hz.
;
; The display driver (Intel's IEGD GOP) programs pipe B's PLL correctly for the panel in its
; table (800x480, 33.264 MHz): FPB0 0x00021005 (N 2, M1 16, M2 5) and DPLL_B 0xd8100003 (96 MHz
; reference, P1 5, P2 14), 60 Hz. Then the stock north-bridge driver (NBDXE, E4ECD0B2-E277-4F2B-
; BECB-E4D75C9A812E), on a protocol notify (EF9A3971-C1A0-4A93-BD40-5AA165F2DC3A, a stage signal
; of the boot manager), unlocks the panel registers and writes a fixed DPLL_B of 0xd8027203: the
; spread-spectrum reference (about 100 MHz) with P1 2. With the GOP's dividers that is 86.5 MHz,
; 156 Hz, which some panels show as white, or blue with blotches. (Measured on an OF1 from the
; display pipe's frame counter: 156 frames a second before, 60 after the GOP's SetMode.)
;
; This puts the GOP's value back, and only when the PLL holds exactly NBDXE's value with exactly
; the GOP's dividers for this panel, so nothing else (another panel, a fixed NBDXE) is touched.
; Like the GOP and Linux's driver, it writes the PLL with the panel registers unlocked (PP_CONTROL
; upper half 0xABCD), then puts PP_CONTROL back as it was. Changing the PLL under a running pipe
; was glitch-free on hardware (flipped 156/103/60 Hz many times).
;
; From 26.11.24 NBDXE is also changed in memory to write the GOP's value itself (nb_patch_image):
; putting the clock back after NBDXE had changed it showed as a twitch of the screen, as the panel
; re-locked; writing the value already there shows nothing (both tried live on an OF1). The
; driver patches NBDXE's loaded image before its notify can run: at the driver's entry if NBDXE
; is loaded by then, else as it loads (img_patch.inc). Only the exact stock function is changed
; (nbdxe_sig.inc, the value included), and only its 4-byte value. The notify and ReadyToBoot fix
; above stay, for anything that writes the old value anyway.
;
; Linux: gma500 takes its panel mode from what the firmware left. With this value (96 MHz
; reference) an unpatched gma500 assumes 48 MHz and drives 30 Hz; openframe-kernel's patch 07
; (6.18.63-4 on) makes it 60 Hz whatever it reads.

PCLK_BAD_DPLL   equ     0xd8027203              ; NBDXE's fixed value
PCLK_FP         equ     0x00021005              ; the GOP's FPB0 for 33.264 MHz
PCLK_DPLL       equ     0xd8100003              ; the GOP's own DPLL_B for it: 60 Hz

; UINT32 panel_clock(EFI_BOOT_SERVICES *BS): 1 if it put the GOP's clock back, 0 if there was
; nothing to do (not a Poulsbo GMA at 00:02.0, its registers not decoded, or not NBDXE's value).
; Needs PCI enumerated (port I/O). cdecl. Keeps ebx, esi, edi.
panel_clock:
        push    ebx
        push    esi
        push    edi
        mov     eax, 0x80001000                 ; 00:02.0: vendor/device
        call    cfg_read32
        cmp     eax, 0x81088086                 ; Intel US15W (Poulsbo) graphics
        jne     .no
        mov     eax, 0x80001004                 ; memory decode on?
        call    cfg_read32
        test    al, 2
        jz      .no
        mov     eax, 0x80001010                 ; BAR0: the display registers
        call    cfg_read32
        test    al, 1
        jnz     .no                             ; not a memory BAR
        and     eax, 0xfffffff0
        jz      .no
        mov     esi, eax
        cmp     dword [esi + 0x6018], PCLK_BAD_DPLL
        jne     .no
        cmp     dword [esi + 0x6048], PCLK_FP
        jne     .no

        mov     ebx, [esi + 0x61204]            ; PP_CONTROL
        mov     eax, ebx
        and     eax, 0xffff
        or      eax, 0xabcd0000                 ; unlock the PLL and timing registers
        mov     [esi + 0x61204], eax
        mov     dword [esi + 0x6018], PCLK_DPLL
        mov     eax, [esp + 16]                 ; BS
        push    150
        call    [eax + 0x88]                    ; Stall(150 us) for the PLL, as gma500 does
        add     esp, 4
        mov     dword [esi + 0x6018], PCLK_DPLL
        mov     [esi + 0x61204], ebx            ; lock them again
        mov     eax, 1
        jmp     .out
.no:    xor     eax, eax
.out:   pop     edi
        pop     esi
        pop     ebx
        ret

; VOID EFIAPI panel_notify(EFI_EVENT Event, VOID *Context = BootServices)
panel_notify:
        push    dword [esp + 8]
        call    panel_clock
        add     esp, 4
        ret

pclk_nb_guid:                                   ; EF9A3971-C1A0-4A93-BD40-5AA165F2DC3A, which
        dd      0xef9a3971                      ; NBDXE's clock write waits for
        dw      0xc1a0, 0x4a93
        db      0xbd, 0x40, 0x5a, 0xa1, 0x65, 0xf2, 0xdc, 0x3a

; UINT32 nb_patch_image(EFI_LOADED_IMAGE_PROTOCOL *Li): 1 if this is the stock NBDXE and its value
; is now the GOP's. EFI_LOADED_IMAGE_PROTOCOL (IA32): +0x10 FilePath, +0x20 ImageBase,
; +0x28 ImageSize (UINT64; only its low half matters on IA32). Keeps ebx, esi, edi.
nb_patch_image:
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
        lea     edi, [edi + (nbp_nbdxe_guid - .here)]
        mov     ecx, 16
        cld
        repe    cmpsb
        pop     edi
        jne     .no
        cmp     dword [ebx + 0x28], NBD_END_RVA ; ImageSize (low half: IA32)
        jb      .no
        mov     esi, [ebx + 0x20]               ; ImageBase
        test    esi, esi
        jz      .no
        lea     eax, [edi + (nbd_sig - .here)]
        push    eax
        lea     eax, [esi + NBD_FN_RVA]
        push    eax
        call    mmc_sig_match
        add     esp, 8
        test    eax, eax
        jz      .no
        mov     dword [esi + NBD_IMM_RVA], PCLK_DPLL
        cmp     dword [esi + NBD_IMM_RVA], PCLK_DPLL
        jne     .no
        mov     eax, 1
        jmp     .out
.no:    xor     eax, eax
.out:   pop     edi
        pop     esi
        pop     ebx
        ret

nbp_done:       db 0                            ; set once NBDXE is patched
        align   4, db 0
nbp_li_guid:                                    ; 5B1B31A1-9562-11D2-8E3F-00A0C969723B LoadedImage
        dd      0x5b1b31a1
        dw      0x9562, 0x11d2
        db      0x8e, 0x3f, 0x00, 0xa0, 0xc9, 0x69, 0x72, 0x3b
nbp_nbdxe_guid:                                 ; E4ECD0B2-E277-4F2B-BECB-E4D75C9A812E NBDXE
        dd      0xe4ecd0b2
        dw      0xe277, 0x4f2b
        db      0xbe, 0xcb, 0xe4, 0xd7, 0x5c, 0x9a, 0x81, 0x2e

%include "nbdxe_sig.inc"

