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
;   US English, by GET_DESCRIPTOR control transfers: the firmware's own UsbGetStringDescriptor
;   can't be used, see pid_read_string), read only. This is what is fitted.
; The line: the panel (from the touch version's first letter, else the display's), then both:
;   "Panel: Samsung (display Samsung, touch S01_02)", "Panel: Sharp (display ?, touch Hyup05_3)".
; "?" for a source that can't be read; space-padded to the placeholder's fixed width, so the
; table's length never changes.

PID_LABEL_RVA   equ     0x13fb
PID_TOUCH_MAX   equ     12                      ; the version's characters kept
PID_WIDTH       equ     48                      ; the placeholder's characters (build_ucode_driver.py)
PID_STR_MAX     equ     126                     ; the longest string descriptor read (62 characters)
PID_USB_TIMEOUT equ     500                     ; ms, each control transfer

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

;;; DEVELOPMENT ONLY (-DPIDDIAG): what USB I/O offers, written over the panel line instead.
; "nCC" the handle count (or "LSS" LocateHandleBuffer's status), then per handle: "hSS" or "dSS"
; for a failing HandleProtocol or GetDeviceDescriptor (SS: the status's low byte), else the
; product ID (4 hex digits), and for the touchscreen "[gSS LLLL cSSUU vC]": GetSupportedLanguages'
; status and first language, the last control transfer's status and USB status (pid_read_string),
; and the version's first character (pid_take_version's, "0" for none).
%ifdef PIDDIAG
pid_fill:
        push    ebp
        mov     ebp, esp
        sub     esp, 184                        ; -40/-44 langs, -48 the end, -52 out, -184 string[128]
        push    ebx
        push    esi
        push    edi
        call    .here
.here:  pop     ebx
        mov     edi, [ebx + (panel_ptr - .here)]
        test    edi, edi
        jz      .out
        cmp     byte [ebx + (pid_filled - .here)], 0
        jne     .out
        mov     byte [ebx + (pid_filled - .here)], 1
        mov     [ebp - 52], edi
        lea     eax, [edi + PID_WIDTH]
        mov     [ebp - 48], eax
        mov     ecx, PID_WIDTH
        mov     al, ' '
        rep     stosb
        mov     esi, [ebp + 8]
        mov     dword [ebp - 4], 0
        mov     dword [ebp - 8], 0
        lea     eax, [ebp - 8]
        push    eax
        lea     eax, [ebp - 4]
        push    eax
        push    0
        lea     eax, [ebx + (pid_usbio_guid - .here)]
        push    eax
        push    2
        call    [esi + 0xa8]                    ; BS->LocateHandleBuffer
        add     esp, 20
        test    eax, eax
        jz      .lok
        push    eax
        mov     al, 'L'
        call    .c
        pop     eax
        call    .hex
        jmp     .out
.lok:   mov     al, 'n'
        call    .c
        mov     al, [ebp - 4]
        call    .hex
        xor     edi, edi
.dev:   cmp     edi, [ebp - 4]
        jae     .free
        mov     al, ' '
        call    .c
        mov     eax, [ebp - 8]
        mov     dword [ebp - 12], 0
        lea     ecx, [ebp - 12]
        push    ecx
        lea     ecx, [ebx + (pid_usbio_guid - .here)]
        push    ecx
        push    dword [eax + edi * 4]
        call    [esi + 0x58]                    ; BS->HandleProtocol
        add     esp, 12
        test    eax, eax
        jz      .hok
        push    eax
        mov     al, 'h'
        call    .c
        pop     eax
        call    .hex
        jmp     .next
.hok:   mov     ecx, [ebp - 12]
        lea     eax, [ebp - 36]
        push    eax
        push    ecx
        call    [ecx + 0x18]                    ; UsbIo->UsbGetDeviceDescriptor
        add     esp, 8
        test    eax, eax
        jz      .dok
        push    eax
        mov     al, 'd'
        call    .c
        pop     eax
        call    .hex
        jmp     .next
.dok:   mov     al, [ebp - 36 + 11]
        call    .hex
        mov     al, [ebp - 36 + 10]
        call    .hex
        cmp     dword [ebp - 36 + 8], 0x197404b4
        jne     .next
        mov     al, '['
        call    .c
        mov     al, 'g'
        call    .c
        mov     dword [ebp - 40], 0
        mov     dword [ebp - 44], 0
        lea     eax, [ebp - 44]
        push    eax                             ; &TableSize (UINT16)
        lea     eax, [ebp - 40]
        push    eax                             ; &LangIDTable
        push    dword [ebp - 12]
        mov     ecx, [ebp - 12]
        call    [ecx + 0x2c]                    ; UsbIo->UsbGetSupportedLanguages
        add     esp, 12
        call    .hex
        mov     al, ' '
        call    .c
        mov     eax, [ebp - 40]
        test    eax, eax
        jz      .nolang
        cmp     word [ebp - 44], 0
        je      .nolang
        movzx   eax, word [eax]
        push    eax
        mov     al, ah
        call    .hex
        pop     eax
        call    .hex
        jmp     .str
.nolang: mov    al, '-'
        call    .c
.str:   mov     al, ' '
        call    .c
        mov     al, 'c'
        call    .c
        lea     ecx, [ebp - 184]
        push    ecx
        movzx   eax, byte [ebp - 36 + 15]
        push    eax
        push    dword [ebp - 12]
        call    pid_read_string
        add     esp, 12
        push    eax
        mov     al, [ebx + (pid_xfer_efi - .here)]
        call    .hex
        mov     al, [ebx + (pid_xfer_usb - .here)]
        call    .hex
        mov     al, ' '
        call    .c
        mov     al, 'v'
        call    .c
        mov     byte [ebx + (pid_touch - .here)], '0'
        pop     eax
        test    eax, eax
        jz      .v
        lea     eax, [ebp - 184 + 2]
        push    eax
        call    pid_take_version
        add     esp, 4
        cmp     byte [ebx + (pid_touch - .here)], 0
        jne     .v
        mov     byte [ebx + (pid_touch - .here)], '0'
.v:     mov     al, [ebx + (pid_touch - .here)]
        call    .c
        mov     al, ']'
        call    .c
.next:  inc     edi
        jmp     .dev
.free:  mov     eax, [ebp - 8]
        test    eax, eax
        jz      .out
        push    eax
        call    [esi + 0x30]                    ; BS->FreePool(Buffer)
        add     esp, 4
.out:   pop     edi
        pop     esi
        pop     ebx
        mov     esp, ebp
        pop     ebp
        ret
; .hex: al as two hex digits; .c: the character al. Both bounded by [ebp - 48]; clobber eax, ecx.
.hex:   push    eax
        shr     al, 4
        call    .nib
        pop     eax
.nib:   and     al, 0x0f
        add     al, '0'
        cmp     al, '9'
        jbe     .c
        add     al, 7
.c:     mov     ecx, [ebp - 52]
        cmp     ecx, [ebp - 48]
        jae     .cr
        mov     [ecx], al
        inc     dword [ebp - 52]
.cr:    ret
%else
; VOID pid_fill(EFI_BOOT_SERVICES *BS): if this driver installed the SMBIOS table, read the
; touchscreen's version and write the line over the Type 11 placeholder. cdecl. Keeps ebx, esi, edi.
pid_fill:
        push    ebp
        mov     ebp, esp
        sub     esp, 168                        ; -4 count, -8 buffer, -12 usbio, -36 desc[18+2], -168 string[128]
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
        lea     ecx, [ebp - 168]
        push    ecx                             ; Buffer
        push    eax                             ; Index
        push    dword [ebp - 12]                ; UsbIo
        call    pid_read_string
        add     esp, 12
        test    eax, eax
        jz      .next
        lea     eax, [ebp - 168 + 2]
        push    eax
        call    pid_take_version                ; the last word, into pid_touch
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
%endif

; UINT32 pid_read_string(EFI_USB_IO_PROTOCOL *UsbIo, UINT32 Index, UINT8 *Buffer): the device's
; string Index in US English, as its string descriptor in Buffer (PID_STR_MAX + 2 bytes) with the
; text NUL-terminated at Buffer + 2; 1 if read, else 0. By standard GET_DESCRIPTOR requests through
; UsbControlTransfer, its length and then all of it, as an OS reads one. Not through the firmware's
; UsbGetStringDescriptor: AMI's search of the device's languages is inverted (it stops at the first
; language that DOESN'T match, then requires that one to match), so it fails with EFI_NOT_FOUND for
; a device's only language. Seen on the Da Vinci OF2's touchscreen (languages 0409, NOT_FOUND).
; Read only. cdecl. Keeps ebx, esi, edi.
pid_read_string:
        push    ebp
        mov     ebp, esp
        push    ebx
        push    esi
        push    edi
        sub     esp, 12                         ; -24 the request (8 bytes), -16 the USB status
        mov     esi, [ebp + 8]                  ; UsbIo
        mov     edi, [ebp + 16]                 ; Buffer
        mov     ebx, 2                          ; first its length
.xfer:  mov     dword [ebp - 24], 0x03000680    ; device to host, GET_DESCRIPTOR, STRING
        mov     al, [ebp + 12]
        mov     [ebp - 22], al                  ; the string's index
        mov     word [ebp - 20], 0x0409         ; US English
        mov     [ebp - 18], bx                  ; wLength
        mov     dword [ebp - 16], 0
        lea     eax, [ebp - 16]
        push    eax                             ; &Status
        push    ebx                             ; DataLength
        push    edi                             ; Data
        push    PID_USB_TIMEOUT
        push    0                               ; EfiUsbDataIn
        lea     eax, [ebp - 24]
        push    eax                             ; Request
        push    esi                             ; This
        call    [esi]                           ; UsbIo->UsbControlTransfer
        add     esp, 28
        call    .fh
.fh:    pop     ecx
        mov     [ecx + (pid_xfer_efi - .fh)], al
        mov     dl, [ebp - 16]
        mov     [ecx + (pid_xfer_usb - .fh)], dl
        test    eax, eax
        jnz     .no
        movzx   eax, byte [edi]                 ; bLength
        cmp     byte [edi + 1], 3               ; a string descriptor
        jne     .no
        cmp     eax, 4                          ; at least one character
        jb      .no
        cmp     eax, PID_STR_MAX
        ja      .no
        cmp     ebx, 2
        jne     .got
        mov     ebx, eax                        ; then all of it
        jmp     .xfer
.got:   cmp     eax, ebx                        ; the same length both times
        jne     .no
        and     eax, ~1
        mov     word [edi + eax], 0
        mov     eax, 1
        jmp     .r
.no:    xor     eax, eax
.r:     lea     esp, [ebp - 12]
        pop     edi
        pop     esi
        pop     ebx
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
pid_xfer_efi:   db 0                            ; the last control transfer's status (low byte), for -DPIDDIAG
pid_xfer_usb:   db 0                            ; ... and its USB status
pid_touch:      times PID_TOUCH_MAX + 1 db 0
        align   4, db 0
pid_display:    dd 0                            ; 1 Sharp, 2 Samsung (pid_note_display), else 0
panel_ptr:      dd 0                            ; set by smbios_install: the Type 11 line, or 0
pid_usbio_guid:                                 ; 2B2F68D6-0CD2-44CF-8E8B-BBA20B1B5B75 EFI_USB_IO_PROTOCOL
        dd      0x2b2f68d6
        dw      0x0cd2, 0x44cf
        db      0x8e, 0x8b, 0xbb, 0xa2, 0x0b, 0x1b, 0x5b, 0x75
