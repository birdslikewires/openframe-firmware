; usbrt.inc: shortens two fixed waits in the firmware's USB code, which cost 1 s on every boot with
; a USB drive attached (from 26.11.26).
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
; Each is made only where the whole instruction sequence around it is the stock one (none has a
; relocated byte), and each value is read back. FixedDelay (RVA 0x1381) counts 15 us refresh
; ticks: 0x29a = 10 ms, 0x3415 = 200 ms.

URT_GUID0       equ     0x04eaaaa1              ; USBRT's file GUID, first dword

; UINT32 urt_patch_image(EFI_LOADED_IMAGE_PROTOCOL *Li): 1 if this is a stock USBRT copy and now
; has the shorter waits (A, or B and C, or all three), else 0. Keeps ebx, esi, edi.
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
        cmp     dword [ebx + 0x28], 0x5800      ; ImageSize: past the last site
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
.r:     xor     eax, eax
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
urt_sig_c:      db      0x8a, 0x81, 0x43, 0x0e, 0x00, 0x00, 0xfe, 0xc0, 0xb2, 0x0a, 0xf6, 0xea
                db      0x88, 0x81, 0xe3, 0x0d, 0x00, 0x00, 0xc3 ; mov al, [ecx+0xe43]; inc al; mov dl, 10; imul dl; mov [ecx+0xde3], al; ret
