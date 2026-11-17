; panel_id.inc: which panel this OpenFrame has, in SMBIOS (Type 11 "Panel: ..."), from dev2.
;
; Two sources, as they can disagree (a unit given another build's display driver):
; - display: the panel configuration of the stock display driver (IEGD GOP), from its label in
;   the configuration text, "OpenFrame-Sharp" or "OpenFrame-Samsung", at RVA 0x13FB in every
;   build we have (OF1, and the OF2's Cisco, Telio, Avaya and OpenPeak builds). Noted by
;   splash.inc's LoadedImage path, before its code checks, so a build it won't give the bird
;   still says what it's set up for. This is what the firmware drives the panel with.
; - touch: the USB touchscreen fitted with the panel (04B4:1974), whose product string ends in a
;   version: "OpenPeak Touchscreen S01_02" on the Cisco's Samsung panel, "...Hyup05_3" with the
;   Sharp panel (OF1 and OF2). OpenPeak's own system read it the same way (sysinfo: S* Samsung,
;   H* Sharp). Read at ReadyToBoot through USB I/O (device descriptor, then the product string,
;   US English), read only. This is what is fitted.
; The line: the panel (from the touch version's first letter, else the display's), then both:
;   "Panel: Samsung (display Samsung, touch S01_02)", "Panel: Sharp (display ?, touch Hyup05_3)".
; "?" for a source that can't be read; space-padded to the placeholder's fixed width, so the
; table's length never changes.

PID_LABEL_RVA   equ     0x13fb
PID_TOUCH_MAX   equ     12                      ; the version's characters kept
PID_WIDTH       equ     48                      ; the placeholder's characters (build_ucode_driver.py)

; pid_note_display: in ebp = the display driver's ImageBase (ImageSize already checked to be well
; past the label). Notes Sharp (1) or Samsung (2) from the label, else nothing. Keeps all but eax
; and ecx.
pid_note_display:
        cmp     dword [ebp + PID_LABEL_RVA], 'Open'
        jne     .r
        cmp     dword [ebp + PID_LABEL_RVA + 4], 'Fram'
        jne     .r
        cmp     word [ebp + PID_LABEL_RVA + 8], 'e-'
        jne     .r
        mov     cl, 1
        cmp     word [ebp + PID_LABEL_RVA + 10], 'Sh'
        je      .set
        mov     cl, 2
        cmp     word [ebp + PID_LABEL_RVA + 10], 'Sa'
        jne     .r
.set:   call    .fh
.fh:    pop     eax
        mov     [eax + (pid_display - .fh)], cl
.r:     ret

; VOID pid_fill(EFI_BOOT_SERVICES *BS): if this driver installed the SMBIOS table, read the
; touchscreen's version and write the line over the Type 11 placeholder. cdecl. Keeps ebx, esi, edi.
pid_fill:
        push    ebp
        mov     ebp, esp
        sub     esp, 40                         ; -4 count, -8 buffer, -12 usbio, -16 string, -36 desc[18+2]
        push    ebx
        push    esi
        push    edi
        call    .here
.here:  pop     ebx                             ; ebx = .here
        cmp     dword [ebx + (panel_ptr - .here)], 0
        je      .out                            ; not our table
        cmp     byte [ebx + (pid_filled - .here)], 0
        jne     .out
        mov     byte [ebx + (pid_touch - .here)], 0

        mov     esi, [ebp + 8]                  ; BS
        mov     dword [ebp - 4], 0
        mov     dword [ebp - 8], 0
        lea     eax, [ebp - 8]
        push    eax                             ; &Buffer
        lea     eax, [ebp - 4]
        push    eax                             ; &NoHandles
        push    0                               ; SearchKey
        lea     eax, [ebx + (pid_usbio_guid - .here)]
        push    eax                             ; Protocol
        push    2                               ; ByProtocol
        call    [esi + 0xa8]                    ; BS->LocateHandleBuffer
        add     esp, 20
        test    eax, eax
        jnz     .write                          ; no USB I/O: touch "?"
        xor     edi, edi                        ; edi = index
.dev:   cmp     edi, [ebp - 4]
        jae     .free
        mov     eax, [ebp - 8]
        mov     dword [ebp - 12], 0
        lea     ecx, [ebp - 12]
        push    ecx                             ; &Interface
        lea     ecx, [ebx + (pid_usbio_guid - .here)]
        push    ecx
        push    dword [eax + edi * 4]           ; Handle
        call    [esi + 0x58]                    ; BS->HandleProtocol
        add     esp, 12
        test    eax, eax
        jnz     .next
        mov     ecx, [ebp - 12]
        test    ecx, ecx
        jz      .next
        lea     eax, [ebp - 36]
        push    eax                             ; &DeviceDescriptor (18 bytes)
        push    ecx                             ; This
        call    [ecx + 0x18]                    ; UsbIo->UsbGetDeviceDescriptor
        add     esp, 8
        test    eax, eax
        jnz     .next
        cmp     dword [ebp - 36 + 8], 0x197404b4    ; idVendor 04B4, idProduct 1974
        jne     .next
        movzx   eax, byte [ebp - 36 + 15]       ; iProduct
        test    eax, eax
        jz      .next
        mov     dword [ebp - 16], 0
        lea     ecx, [ebp - 16]
        push    ecx                             ; &String (allocated by the callee)
        push    eax                             ; StringId
        push    0x0409                          ; LangId: US English
        push    dword [ebp - 12]                ; This
        mov     ecx, [ebp - 12]
        call    [ecx + 0x28]                    ; UsbIo->UsbGetStringDescriptor
        add     esp, 16
        test    eax, eax
        jnz     .next
        mov     eax, [ebp - 16]
        test    eax, eax
        jz      .next
        push    eax
        call    pid_take_version                ; the last word, into pid_touch
        pop     eax
        push    eax
        call    [esi + 0x30]                    ; BS->FreePool(String)
        add     esp, 4
        jmp     .free                           ; one touchscreen
.next:  inc     edi
        jmp     .dev
.free:  mov     eax, [ebp - 8]
        test    eax, eax
        jz      .write
        push    eax
        call    [esi + 0x30]                    ; BS->FreePool(Buffer)
        add     esp, 4

.write: call    pid_write
        mov     byte [ebx + (pid_filled - .here)], 1
.out:   pop     edi
        pop     esi
        pop     ebx
        mov     esp, ebp
        pop     ebp
        ret

; pid_take_version: [esp + 4] = a CHAR16 string. Copies what follows its last space (printable
; ASCII only, at most PID_TOUCH_MAX characters) into pid_touch, NUL-terminated; nothing if that is
; empty or holds anything else. Keeps ebx, esi, edi, ebp.
pid_take_version:
        push    esi
        push    edi
        mov     esi, [esp + 12]
        mov     edx, esi                        ; edx = after the last space so far
        xor     ecx, ecx
.scan:  movzx   eax, word [esi + ecx * 2]
        test    eax, eax
        jz      .found
        inc     ecx
        cmp     ecx, 256
        ja      .none                           ; no end: not a string we want
        cmp     eax, ' '
        jne     .scan
        lea     edx, [esi + ecx * 2]
        jmp     .scan
.found: call    .fh
.fh:    pop     edi
        add     edi, pid_touch - .fh
        xor     ecx, ecx
.copy:  movzx   eax, word [edx + ecx * 2]
        test    eax, eax
        jz      .end
        cmp     ecx, PID_TOUCH_MAX
        jae     .none
        cmp     eax, 0x21
        jb      .none
        cmp     eax, 0x7e
        ja      .none
        mov     [edi + ecx], al
        inc     ecx
        jmp     .copy
.end:   test    ecx, ecx
        jz      .none
        mov     byte [edi + ecx], 0
        jmp     .r
.none:  call    .fh2
.fh2:   pop     edi
        mov     byte [edi + (pid_touch - .fh2)], 0
.r:     pop     edi
        pop     esi
        ret

; pid_write: the line over the placeholder at panel_ptr (PID_WIDTH characters), space-padded.
; Keeps ebx, esi, edi, ebp.
pid_write:
        push    ebx
        push    esi
        push    edi
        call    .here
.here:  pop     ebx
        mov     edi, [ebx + (panel_ptr - .here)]
        lea     edx, [edi + PID_WIDTH]          ; edx = the end
        ; the panel: the touch version's first letter, else the display
        movzx   eax, byte [ebx + (pid_touch - .here)]
        cmp     al, 'S'
        je      .p_sam
        cmp     al, 'H'
        je      .p_sha
        movzx   eax, byte [ebx + (pid_display - .here)]
        cmp     al, 2
        je      .p_sam
        cmp     al, 1
        je      .p_sha
        lea     esi, [ebx + (pid_s_unknown - .here)]
        jmp     .p
.p_sam: lea     esi, [ebx + (pid_s_samsung - .here)]
        jmp     .p
.p_sha: lea     esi, [ebx + (pid_s_sharp - .here)]
.p:     call    .put
        lea     esi, [ebx + (pid_s_display - .here)]
        call    .put
        movzx   eax, byte [ebx + (pid_display - .here)]
        lea     esi, [ebx + (pid_s_q - .here)]
        cmp     al, 1
        jne     .d2
        lea     esi, [ebx + (pid_s_sharp - .here)]
.d2:    cmp     al, 2
        jne     .d3
        lea     esi, [ebx + (pid_s_samsung - .here)]
.d3:    call    .put
        lea     esi, [ebx + (pid_s_touch - .here)]
        call    .put
        lea     esi, [ebx + (pid_touch - .here)]
        cmp     byte [esi], 0
        jne     .t
        lea     esi, [ebx + (pid_s_q - .here)]
.t:     call    .put
        lea     esi, [ebx + (pid_s_close - .here)]
        call    .put
.pad:   cmp     edi, edx                        ; spaces to the end
        jae     .done
        mov     byte [edi], ' '
        inc     edi
        jmp     .pad
.done:  pop     edi
        pop     esi
        pop     ebx
        ret
; .put: copies the NUL-terminated esi to edi, never past edx.
.put:   mov     al, [esi]
        test    al, al
        jz      .pr
        cmp     edi, edx
        jae     .pr
        mov     [edi], al
        inc     esi
        inc     edi
        jmp     .put
.pr:    ret

pid_s_samsung:  db 'Samsung', 0
pid_s_sharp:    db 'Sharp', 0
pid_s_unknown:  db 'unknown', 0
pid_s_display:  db ' (display ', 0
pid_s_touch:    db ', touch ', 0
pid_s_close:    db ')', 0
pid_s_q:        db '?', 0
pid_filled:     db 0
pid_touch:      times PID_TOUCH_MAX + 1 db 0
        align   4, db 0
pid_display:    dd 0                            ; 1 Sharp, 2 Samsung (pid_note_display), else 0
panel_ptr:      dd 0                            ; set by smbios_install: the Type 11 line, or 0
pid_usbio_guid:                                 ; 2B2F68D6-0CD2-44CF-8E8B-BBA20B1B5B75 EFI_USB_IO_PROTOCOL
        dd      0x2b2f68d6
        dw      0x0cd2, 0x44cf
        db      0x8e, 0x8b, 0xbb, 0xa2, 0x0b, 0x1b, 0x5b, 0x75
