; panel_id.inc: which panel this OpenFrame has, in SMBIOS Type 11, from 26.11.32:
;   "Panel: Samsung" or "Panel: Sharp", and "Touch: Samsung 1.2" or "Touch: Hyup 5.3".
;
; The panel comes from the USB touchscreen fitted with it (04B4:1974, a Cypress PSoC), whose
; product string ends in a version: "AmSC OpenPeak Touchscreen S01_02" with the Samsung panel,
; "...Hyup05_3" with the Sharp (OF1 and OF2; its board is printed "PSoC HYUP BOARD"). OpenPeak's own
; /sbin/sysinfo read it the same way: SH* samsung-hyup, S* samsung, H* sharp. It is
; read at ReadyToBoot through USB I/O (device descriptor, then the product string in US English by
; GET_DESCRIPTOR control transfers: the firmware's own UsbGetStringDescriptor can't be used, see
; pid_read_string), read only. The display driver can't tell: its configuration is whatever its
; build was set up for (the Da Vinci OF2 drives its Samsung panel with the Telio's Sharp one).
;
; Without a touchscreen version, the panel comes from that configuration instead: the stock display
; driver's (IEGD GOP) label "OpenFrame-Sharp" or "OpenFrame-Samsung" at RVA 0x13FB in every build
; we have, noted by splash.inc's LoadedImage path (the app build, run after the display driver has
; loaded, never sees it). Then "unknown".
;
; The touch line names the touch board as sysinfo did, then the version with its numbers tidied
; (pid_decode): S01_02 is "Samsung 1.2" (the board OpenPeak paired with Samsung panels), Hyup05_3
; "Hyup 5.3", SH01_02 or SHyup01_02 "Hyup 1.2" (a Samsung panel on the Hyup board). A version that
; doesn't fit that pattern is shown as read; "unknown" when none was read.
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
        lea     esi, [ebx + (pid_s_unknown - .here)]
        cmp     byte [ebx + (pid_touch - .here)], 0
        je      .t
        call    pid_decode
        lea     esi, [ebx + (pid_tdisp - .here)]
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

; pid_decode: the touch version (pid_touch, not empty) for the touch line, into pid_tdisp: the
; board sysinfo's prefixes name, a space, and the version's digit groups without leading zeros,
; joined by dots ("Samsung 1.2"); else the version as read. Keeps ebx, esi, edi, ebp.
pid_decode:
        push    ebx
        push    esi
        push    edi
        push    ebp
        call    .here
.here:  pop     ebx
        lea     ebp, [ebx + (pid_boards - .here)]
.entry: cmp     byte [ebp], 0
        je      .raw                            ; no prefix matched
        lea     esi, [ebx + (pid_touch - .here)]
        mov     edx, ebp
.cmp:   mov     al, [edx]                       ; the prefix
        test    al, al
        jz      .match                          ; matched (.group wants a digit next)
        cmp     al, [esi]
        jne     .skip
        inc     edx
        inc     esi
        jmp     .cmp
.skip:  mov     edx, ebp                        ; the next entry: past the prefix and the board
.s1:    inc     edx
        cmp     byte [edx - 1], 0
        jne     .s1
.s2:    inc     edx
        cmp     byte [edx - 1], 0
        jne     .s2
        mov     ebp, edx
        jmp     .entry
.match: lea     edi, [ebx + (pid_tdisp - .here)]
        lea     ecx, [edi + PID_TOUCH_WIDTH]    ; ecx = the end
.board: inc     edx                             ; the board's name (short: always fits)
        mov     al, [edx]
        test    al, al
        jz      .sp
        mov     [edi], al
        inc     edi
        jmp     .board
.sp:    mov     byte [edi], ' '
        inc     edi
.group: mov     al, [esi]                       ; a group: at least one digit
        sub     al, '0'
        cmp     al, 9
        ja      .raw
.zero:  cmp     byte [esi], '0'                 ; leading zeros, but not the group's last digit
        jne     .dig
        mov     al, [esi + 1]
        sub     al, '0'
        cmp     al, 9
        ja      .dig
        inc     esi
        jmp     .zero
.dig:   mov     al, [esi]
        mov     ah, al
        sub     ah, '0'
        cmp     ah, 9
        ja      .sep
        cmp     edi, ecx
        jae     .raw                            ; too long for the line
        mov     [edi], al
        inc     edi
        inc     esi
        jmp     .dig
.sep:   test    al, al
        jz      .end
        cmp     al, '_'
        je      .dot
        cmp     al, '.'
        jne     .raw
.dot:   cmp     edi, ecx
        jae     .raw
        mov     byte [edi], '.'
        inc     edi
        inc     esi
        jmp     .group
.end:   mov     byte [edi], 0
        jmp     .r
.raw:   lea     esi, [ebx + (pid_touch - .here)]    ; as read (at most PID_TOUCH_MAX)
        lea     edi, [ebx + (pid_tdisp - .here)]
.rc:    lodsb
        stosb
        test    al, al
        jnz     .rc
.r:     pop     ebp
        pop     edi
        pop     esi
        pop     ebx
        ret

; sysinfo's prefixes and the board each names; the first that matches is used, so longest first.
pid_boards:     db 'SHyup', 0, 'Hyup', 0
                db 'SH', 0, 'Hyup', 0
                db 'Hyup', 0, 'Hyup', 0
                db 'S', 0, 'Samsung', 0
                db 0
pid_s_samsung:  db 'Samsung', 0
pid_s_sharp:    db 'Sharp', 0
pid_s_unknown:  db 'unknown', 0
pid_filled:     db 0
pid_touch:      times PID_TOUCH_MAX + 1 db 0
pid_tdisp:      times PID_TOUCH_WIDTH + 1 db 0  ; the touch line's text (pid_decode)
        align   4, db 0
pid_display:    dd 0                            ; 1 Sharp, 2 Samsung (pid_note_display), else 0
panel_ptr:      dd 0                            ; set by smbios_install: the Type 11 panel line, or 0
touch_ptr:      dd 0                            ; ... and the touch line
pid_usbio_guid:                                 ; 2B2F68D6-0CD2-44CF-8E8B-BBA20B1B5B75 EFI_USB_IO_PROTOCOL
        dd      0x2b2f68d6
        dw      0x0cd2, 0x44cf
        db      0x8e, 0x8b, 0xbb, 0xa2, 0x0b, 0x1b, 0x5b, 0x75
