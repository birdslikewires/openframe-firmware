; panel_id.inc: which panel this OpenFrame has, in SMBIOS Type 11, from 26.11.32:
;   "Panel: Samsung" or "Panel: Sharp", and "Touch: S01_02" (the touchscreen's version).
;
; The panel comes from the USB touchscreen fitted with it (04B4:1974), whose product string ends in
; a version: "OpenPeak Touchscreen S01_02" with the Samsung panel, "...Hyup05_3" with the Sharp
; (OF1 and OF2). OpenPeak's own system read it the same way (sysinfo: S* Samsung, H* Sharp). It is
; read at ReadyToBoot through USB I/O (device descriptor, then the product string in US English by
; GET_DESCRIPTOR control transfers: the firmware's own UsbGetStringDescriptor can't be used, see
; pid_read_string), read only. The display driver can't tell: its configuration is whatever its
; build was set up for (the Da Vinci OF2 drives its Samsung panel with the Telio's Sharp one).
;
; Without a touchscreen version, the panel comes from that configuration instead: the stock display
; driver's (IEGD GOP) label "OpenFrame-Sharp" or "OpenFrame-Samsung" at RVA 0x13FB in every build
; we have, noted by splash.inc's LoadedImage path (the app build, run after the display driver has
; loaded, never sees it). Then "unknown". The touch line says "unknown" when no version was read.
; Both are space-padded to their placeholders' widths, so the table's length never changes.

PID_LABEL_RVA   equ     0x13fb
PID_TOUCH_MAX   equ     12                      ; the version's characters kept
PID_WIDTH       equ     7                       ; the panel placeholder's characters (build_ucode_driver.py)
PID_TOUCH_WIDTH equ     12                      ; the touch placeholder's (PID_TOUCH_MAX)
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

; VOID pid_fill(EFI_BOOT_SERVICES *BS): if this driver installed the SMBIOS table, read the
; touchscreen's version and write the panel and touch lines over their Type 11 placeholders, once.
; cdecl. Keeps ebx, esi, edi.
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
        jnz     .write                          ; no USB I/O: touch unknown
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

; pid_write: the panel over the placeholder at panel_ptr (PID_WIDTH characters) and the touch
; version over touch_ptr's (PID_TOUCH_WIDTH), space-padded. Keeps ebx, esi, edi, ebp.
pid_write:
        push    ebx
        push    esi
        push    edi
        call    .here
.here:  pop     ebx
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
.p:     mov     edi, [ebx + (panel_ptr - .here)]
        mov     ecx, PID_WIDTH
        call    .put
        mov     edi, [ebx + (touch_ptr - .here)]
        test    edi, edi
        jz      .done
        lea     esi, [ebx + (pid_touch - .here)]
        cmp     byte [esi], 0
        jne     .t
        lea     esi, [ebx + (pid_s_unknown - .here)]
.t:     mov     ecx, PID_TOUCH_WIDTH
        call    .put
.done:  pop     edi
        pop     esi
        pop     ebx
        ret
; .put: the NUL-terminated esi into edi, ecx characters, space-padded.
.put:   lodsb
        test    al, al
        jz      .pad
        stosb
        loop    .put
        ret
.pad:   mov     al, ' '
        rep     stosb
        ret

pid_s_samsung:  db 'Samsung', 0
pid_s_sharp:    db 'Sharp', 0
pid_s_unknown:  db 'unknown', 0
pid_filled:     db 0
pid_touch:      times PID_TOUCH_MAX + 1 db 0
        align   4, db 0
pid_display:    dd 0                            ; 1 Sharp, 2 Samsung (pid_note_display), else 0
panel_ptr:      dd 0                            ; set by smbios_install: the Type 11 panel line, or 0
touch_ptr:      dd 0                            ; ... and the touch line
pid_usbio_guid:                                 ; 2B2F68D6-0CD2-44CF-8E8B-BBA20B1B5B75 EFI_USB_IO_PROTOCOL
        dd      0x2b2f68d6
        dw      0x0cd2, 0x44cf
        db      0x8e, 0x8b, 0xbb, 0xa2, 0x0b, 0x1b, 0x5b, 0x75
