; panel_id.inc: which panel the firmware drives, in SMBIOS (Type 11 "Panel: Sharp"), from dev2.
;
; From the panel configuration of the stock display driver (IEGD GOP): its label in the
; configuration text, "OpenFrame-Sharp" or "OpenFrame-Samsung", at RVA 0x13FB in every build we
; have (OF1, and the OF2's Cisco, Telio, Avaya and OpenPeak builds). Noted by splash.inc's
; LoadedImage path, before its code checks, so a build it won't give the bird still says what it's
; set up for. Written at ReadyToBoot: "Panel: Sharp", "Panel: Samsung", or "Panel: unknown" when
; no label was seen (the app build, which runs after the display driver has loaded, says that).
; Space-padded to the placeholder's width, so the table's length never changes.
;
; The panel actually fitted can be told from its USB touchscreen (04B4:1974), whose product string
; ends in a version: "S01_02" with the Samsung panel, "Hyup05_3" with the Sharp. Linux reads that
; itself (lsusb), so the firmware doesn't. Should firmware ever need it: AMI's UsbGetStringDescriptor
; can't read it. Its search of the device's languages is inverted (it stops at the first language
; that DOESN'T match, then requires that one to match), so it fails with EFI_NOT_FOUND for a
; device's only language. A GET_DESCRIPTOR request through UsbControlTransfer works (tried in
; dev2 on the Da Vinci OF2, firmware commit 46e7a78).

PID_LABEL_RVA   equ     0x13fb
PID_WIDTH       equ     7                       ; the placeholder's characters (build_ucode_driver.py)

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

; VOID pid_fill(EFI_BOOT_SERVICES *BS): if this driver installed the SMBIOS table, the panel over
; the Type 11 placeholder, space-padded; once. BS is unused. cdecl. Keeps ebx, esi, edi.
pid_fill:
        push    esi
        push    edi
        call    .here
.here:  pop     edx
        mov     edi, [edx + (panel_ptr - .here)]
        test    edi, edi
        jz      .out                            ; not our table
        cmp     byte [edx + (pid_filled - .here)], 0
        jne     .out
        mov     byte [edx + (pid_filled - .here)], 1
        lea     esi, [edx + (pid_s_unknown - .here)]
        mov     al, [edx + (pid_display - .here)]
        cmp     al, 1
        jne     .sam
        lea     esi, [edx + (pid_s_sharp - .here)]
.sam:   cmp     al, 2
        jne     .copy
        lea     esi, [edx + (pid_s_samsung - .here)]
.copy:  mov     ecx, PID_WIDTH
.c:     lodsb
        test    al, al
        jz      .pad
        stosb
        loop    .c
        jmp     .out
.pad:   mov     al, ' '
        rep     stosb
.out:   pop     edi
        pop     esi
        ret

pid_s_samsung:  db 'Samsung', 0
pid_s_sharp:    db 'Sharp', 0
pid_s_unknown:  db 'unknown', 0
pid_filled:     db 0
pid_display:    db 0                            ; 1 Sharp, 2 Samsung (pid_note_display), else 0
        align   4, db 0
panel_ptr:      dd 0                            ; set by smbios_install: the Type 11 line, or 0
