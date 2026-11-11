; usbrt.inc: changes to the firmware's USB code: two fixed waits shortened, which cost 1 s on every
; boot with a USB drive attached (A-C, from 26.11.26); and a drive that is slow to answer while it
; spins up no longer stops the boot (D, E).
;
; AMI's USB stack does its work in SMM (USBRT, file 04EAAAA1-29A1-11D7-8838-00500473D4EB): one copy
; is loaded as a normal driver, which loads a second into SMRAM; the SMM one runs everything.
; Measured on an OF1 (development builds 26.11.93-95: SMM entries timed from outside, the SMM copy
; read and then changed as it loaded, while SMRAM is still open):
;   A  Before a mass-storage device's first command USBRT always waits 500 ms (FixedDelay at RVA
;      0x56cb, in the identify step), and it does that twice per drive (as the device is
;      enumerated and again as it is set up). Now 10 ms: the identify step then polls the drive,
;      and the ready loop below waits for a drive that needs time. (Linux on these images sends
;      its first command at once too: usb-storage.delay_use=0.) This is the 1 s saved.
;   B  The ready loop (RVA 0x52a8) waits 1 s before asking again when a drive answers "power-on
;      reset occurred" (sense 6/29/00) or "becoming ready" (2/04/01, 2/04/02), as a disk spinning
;      up does. Now 200 ms, so a drive is used as soon as it's ready...
;   C  ...and the number of tries goes from (setting + 1) x 10 to (setting + 1) x 50 (the setter
;      at RVA 0x3f95, run when USB starts, long after this patch), so a drive that's genuinely
;      not ready still gets the same 10 to 40 s. B and C are made together or not at all.
;   D  When a transfer to a drive fails, USBRT sends what is meant to be a Bulk-Only Mass Storage
;      Reset, but with the request in the wrong field: 21 00 FF 00 (bRequest 0, wValue 0xFF), not
;      21 FF 00 00. An ASMedia ASM1053 caddy rejects it and then refuses every command until the
;      host resets the port, which USBRT never does: the boot hangs. Now the real reset (the
;      11 bytes at RVA 0x4388 that push its arguments, re-encoded; same length), after which the
;      caddy answers again at once (replayed from Linux through usbfs, 2026-11-11).
;   E  USBRT gives each phase of a transfer 5 s (the default at RVA 0x3382) and the data of READ,
;      WRITE, READ CAPACITY and START STOP UNIT 10 s (RVAs 0x4a0e, 0x4eba, 0x57fa). That caddy
;      holds a reply for up to 7 s while its drive spins up, and the first read for 12 s; Linux
;      waits up to 30 s and sees nothing wrong. Now 20 s for all of them. The waits end as soon
;      as the device answers; only a device that never answers at all costs more (20 s, not 5,
;      per attempt: USBRT's transfers never give up on their own, error counter CErr = 0).
; Each is made only where the whole instruction sequence around it is the stock one (none has a
; relocated byte), and each value is read back. FixedDelay (RVA 0x1381) counts 15 us refresh
; ticks: 0x29a = 10 ms, 0x3415 = 200 ms.

URT_GUID0       equ     0x04eaaaa1              ; USBRT's file GUID, first dword

; UINT32 urt_patch_image(EFI_LOADED_IMAGE_PROTOCOL *Li): 1 if this is a stock USBRT copy and now
; has any of the changes (A; B and C; D; E), else 0. Keeps ebx, esi, edi.
urt_patch_image:
        push    ebx
        push    esi
        push    edi
        call    .here
.here:  pop     edi
        mov     ebx, [esp + 16]                 ; Li
        mov     esi, [ebx + 0x10]               ; FilePath: one MEDIA_FW_VOL_FILEPATH node
        test    esi, esi
        jz      .no
        cmp     dword [esi], 0x00140604
        jne     .no
        add     esi, 4
        push    edi
        lea     edi, [edi + (urt_guid - .here)]
        mov     ecx, 16
        cld
        repe    cmpsb
        pop     edi
        jne     .no
        cmp     dword [ebx + 0x28], 0x5803      ; ImageSize: past the last site
        jb      .no
        mov     esi, [ebx + 0x20]               ; ImageBase
        test    esi, esi
        jz      .no
        xor     ebx, ebx                        ; what got done
        lea     eax, [esi + 0x56be]             ; A
        lea     ecx, [edi + (urt_sig_a - .here)]
        mov     edx, 18
        call    .cmp
        jne     .bc
%ifdef STARTUNIT
        ; EXPERIMENT: instead of waiting, wake the drive: identify's push 0x8235 / call FixedDelay
        ; becomes push esi (the device) / call StartUnit (START STOP UNIT, start; 10 s timeout),
        ; same length, same pop after it. Only if StartUnit is the stock function.
        lea     eax, [edi + (urt_sig_start - .here)]
        push    eax
        lea     eax, [esi + URT_START_RVA]
        push    eax
        call    mmc_sig_match
        add     esp, 8
        test    eax, eax
        jz      .wait
        mov     dword [esi + 0x56c4], 0x90909090    ; nop x4 (the old push's imm32)
        mov     byte [esi + 0x56c3], 0x56           ; push esi
        mov     dword [esi + 0x56cc], URT_START_RVA - 0x56d0   ; call StartUnit
        cmp     byte [esi + 0x56c3], 0x56
        jne     .bc
        cmp     dword [esi + 0x56cc], URT_START_RVA - 0x56d0
        jne     .bc
        or      ebx, 1
        jmp     .bc
.wait:
%endif
        mov     dword [esi + 0x56c4], 0x29a
        cmp     dword [esi + 0x56c4], 0x29a
        jne     .bc
        or      ebx, 1
.bc:    lea     eax, [esi + 0x5307]             ; B and C: both stock, or neither is changed
        lea     ecx, [edi + (urt_sig_b - .here)]
        mov     edx, 17
        call    .cmp
        jne     .r
        lea     eax, [esi + 0x3f9b]
        lea     ecx, [edi + (urt_sig_c - .here)]
        mov     edx, 19
        call    .cmp
        jne     .r
        mov     byte [esi + 0x3fa4], 50         ; C first: more tries...
        mov     dword [esi + 0x530f], 0x3415    ; ...then the shorter wait
        cmp     byte [esi + 0x3fa4], 50
        jne     .r
        cmp     dword [esi + 0x530f], 0x3415
        jne     .r
        or      ebx, 2
.r:     lea     eax, [esi + 0x437e]             ; D: the reset's arguments
        lea     ecx, [edi + (urt_sig_d - .here)]
        mov     edx, 29
        call    .cmp
        jne     .e
        push    esi
        push    edi
        lea     eax, [esi + 0x4388]
        lea     esi, [edi + (urt_new_d - .here)]
        mov     edi, eax
        mov     ecx, 11
        cld
        rep     movsb
        pop     edi
        pop     esi
        lea     eax, [esi + 0x4388]
        lea     ecx, [edi + (urt_new_d - .here)]
        mov     edx, 11
        call    .cmp
        jne     .e
        or      ebx, 4
.e:     xor     ecx, ecx                        ; E: all four stock, or none is changed
.e_chk: mov     eax, [edi + (urt_e_rva - .here) + ecx * 4]
        add     eax, esi
        push    ecx
        test    ecx, ecx                        ; the first is the default (e12), the rest overrides (de4)
        lea     ecx, [edi + (urt_sig_e12 - .here)]
        jz      .e_one
        lea     ecx, [edi + (urt_sig_de4 - .here)]
.e_one: mov     edx, 9
        call    .cmp
        pop     ecx
        jne     .x
        inc     ecx
        cmp     ecx, 4
        jb      .e_chk
        xor     ecx, ecx
.e_set: mov     eax, [edi + (urt_e_rva - .here) + ecx * 4]
        mov     word [esi + eax + 7], URT_TIMEOUT_MS
        cmp     word [esi + eax + 7], URT_TIMEOUT_MS
        jne     .x
        inc     ecx
        cmp     ecx, 4
        jb      .e_set
        or      ebx, 8
.x:     xor     eax, eax
        test    ebx, ebx
        setnz   al
        jmp     .out
.no:    xor     eax, eax
.out:   pop     edi
        pop     esi
        pop     ebx
        ret
; .cmp: eax = memory, ecx = signature, edx = length. ZF set if they are the same.
.cmp:   push    esi
        push    edi
        mov     esi, eax
        mov     edi, ecx
        mov     ecx, edx
        cld
        repe    cmpsb
        pop     edi
        pop     esi
        ret

URT_TIMEOUT_MS  equ     20000                   ; E: every transfer phase may take this long

urt_n:          db 0                            ; USBRT copies changed so far
        align   4, db 0
urt_guid:                                       ; 04EAAAA1-29A1-11D7-8838-00500473D4EB USBRT
        dd      URT_GUID0
        dw      0x29a1, 0x11d7
        db      0x88, 0x38, 0x00, 0x50, 0x04, 0x73, 0xd4, 0xeb
urt_sig_a:      db      0xbb, 0x03, 0x01, 0x00, 0x00, 0x68, 0x35, 0x82, 0x00, 0x00, 0x89, 0x5d, 0x08
                db      0xe8, 0xb1, 0xbc, 0xff, 0xff    ; mov ebx, 0x103; push 0x8235; mov [ebp+8], ebx; call FixedDelay
urt_sig_b:      db      0x3d, 0x06, 0x29, 0x00, 0x00, 0x75, 0x2a, 0x68, 0x6a, 0x04, 0x01, 0x00
                db      0xe8, 0x69, 0xc0, 0xff, 0xff    ; cmp eax, 0x2906; jne; push 0x1046a; call FixedDelay
urt_sig_d:      db      0x6a, 0x00, 0x83, 0xea, 0x10, 0x6a, 0x00, 0xc1, 0xfa, 0x04 ; push 0; sub edx, 0x10; push 0; sar edx, 4
                db      0x68, 0xff, 0x00, 0x00, 0x00, 0x6b, 0xd2, 0x41, 0x57, 0x6a, 0x21 ; push 0xff; imul edx, edx, 0x41; push edi; push 0x21
                db      0x81, 0xc1, 0xf4, 0x01, 0x00, 0x00, 0x56, 0x51 ; add ecx, 0x1f4; push esi; push ecx
urt_new_d:      db      0x6a, 0x00, 0x6b, 0xd2, 0x41, 0x57, 0x68, 0x21, 0xff, 0x00, 0x00 ; push 0 (wValue); imul; push edi (wIndex); push 0xff21 (21 FF)
urt_sig_e12:    db      0x66, 0xc7, 0x80, 0x12, 0x0e, 0x00, 0x00, 0x88, 0x13 ; mov word [eax+0xe12], 5000
urt_sig_de4:    db      0x66, 0xc7, 0x80, 0xe4, 0x0d, 0x00, 0x00, 0x10, 0x27 ; mov word [eax+0xde4], 10000
        align   4, db 0
urt_e_rva:      dd      0x3382, 0x4a0e, 0x4eba, 0x57fa ; the default, then the three overrides
%ifdef STARTUNIT
%include "usbrt_sig.inc"
%endif
urt_sig_c:      db      0x8a, 0x81, 0x43, 0x0e, 0x00, 0x00, 0xfe, 0xc0, 0xb2, 0x0a, 0xf6, 0xea
                db      0x88, 0x81, 0xe3, 0x0d, 0x00, 0x00, 0xc3 ; mov al, [ecx+0xe43]; inc al; mov dl, 10; imul dl; mov [ecx+0xde3], al; ret
