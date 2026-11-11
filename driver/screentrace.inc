; screentrace.inc: development build only (--define TIMING --define PROFILE --define USBTRACE
; --define SCREEN). Shows on the screen, live, what the firmware's USB code is doing, so a boot that
; hangs there (a 12 TB drive attached) can be read off the panel. Three lines of hex digits above
; the boot logo (8x8 glyphs drawn 2x: 16 pixels a digit), straight into the framebuffer (safe with
; interrupts off, which the console isn't):
;   y=8   USB request (DXE, ut_trig): function/subfunction, start, time it took (both TSC >> 20,
;         0.79 ms units; the time stays 00000 while a request hasn't returned), how many so far
;   y=32  USBRT FixedDelay (SMM): caller (RVA in USBRT), its argument (15 us ticks), calls so far
;   y=56  USBRT mass-storage command (SMM): SCSI opcode, data length, commands so far
;   y=80  the last 16 SCSI opcodes, oldest first (from 26.11.99)
;   y=104 USBRT's REQUEST SENSE (SMM): the sense code it returned (ASCQ ASC key), how many so far
;   y=128..200  the last 4 control transfers (setup requests) on either USB controller, oldest
;         first (from 26.11.96): controller (30 EHCI, 10 UHCI) and USBRT's transfer status byte
;         (gUsbData+0xe14: 1 bulk stall, 2 bulk timeout, 4 control stall) after it, as 4 digits;
;         the device's address; wRequest (bRequest bmRequestType), wValue, wIndex, wLength; what
;         the transfer function returned (bytes, 0 = failed); how long it took (TSC >> 20)
;   (y=224 showed USBRT's error log in 26.11.96 and .89; dropped in .88 for F's stub's room)
;   (y=248 showed the last root-port status check in 26.11.96 and .89; also dropped in .88: the
;   ring shows each transfer's port. The port-status hooks stay, recording only.)
; The control-transfer and port-status lines come from the controllers' function tables: USBRT
; fills them when USB starts (long after the hooks go in), from relocated addresses in four
; instructions, which then point at the blob instead.
; The SMM lines come from two hooks put into each USBRT copy as it loads (while SMRAM is open):
; FixedDelay's first instructions jump to a stub, and the call that sends a command block goes
; to a stub first. The stub (st_blob, position-independent) is copied into USBRT's relocation
; section, which nothing uses once the image is loaded: only if the image's relocations are
; already applied (a relocated dword checked) and both hook sites are the stock bytes. The SMM
; stubs only draw while the framebuffer block (st_fb, in this driver) carries its magic and the
; TSC says less than about 5 minutes since power-on; ReadyToBoot clears the magic, so after a
; normal boot they never touch it again (the image is freed later; the hooks stay, inert).

ST_MAGIC        equ     'BLWF'
ST_AREA         equ     0x9920                  ; USBRT's .reloc section (0x64c bytes)
ST_AREA_LEN     equ     F_RVA - ST_AREA         ; (usbrt.inc's F stub has the rest of the section)
ST_IMAGE_SIZE   equ     0x9f80

; ---- the blob: copied into each USBRT copy; position-independent ----
st_blob:
st_fbblk:       dd 0                            ; -> st_fb (patched)
st_rtbase:      dd 0                            ; this USBRT copy's base (patched)
st_nfd:         dd 0                            ; FixedDelay calls
st_ncbw:        dd 0                            ; command blocks sent
st_nsense:      dd 0                            ; REQUEST SENSEs
st_ops:         times 16 db 0                   ; the last 16 opcodes (a ring)
st_orig:        times 8 dd 0                    ; the real functions, in st_tab's order (patched)
st_ring:        times 4 * 8 dw 0                ; the last 4 transfers (words): kind | status << 8, port << 8 | address, arguments 3, 5, 4, 7, result, time
st_fdlast:      dd 0                            ; the FixedDelay caller last drawn
st_rhs:         dd 0, 0, 0                      ; the last port status check: controller, port, result
st_nrhs:        dd 0                            ; (st_rhs + 12) port status checks so far
st_nctl:        dd 0                            ; (st_rhs + 16) transfers so far

; st_gate: eax = the framebuffer block if drawing is allowed, else 0. In ebp = a .h label inside
; the blob: [ebp + st_fbblk - .h] must be addressable; callers pass the offset in ecx. Clobbers
; eax, edx. (Used by the SMM entries only.)
; Given: ecx = address of st_fbblk in this copy.
st_gate:
        rdtsc
        cmp     edx, 0x5d                       ; about 300 s at 1330 MHz
        jae     .no
        mov     eax, [ecx]
        test    eax, eax
        jz      .no
        cmp     dword [eax], ST_MAGIC
        jne     .no
        ret
.no:    xor     eax, eax
        ret

; FixedDelay's entry jumps here (stack as at its entry: [esp] caller, [esp+4] count).
st_fd_entry:
        pushad
        pushfd
        call    .h
.h:     pop     ebp
        inc     dword [ebp + (st_nfd - .h)]
        mov     eax, [esp + 36]                 ; drawn for a new caller or every 256th call (drawing
        cmp     eax, [ebp + (st_fdlast - .h)]   ;  each one makes a polling loop ~20x slower)
        je      .same
.new:   lea     ecx, [ebp + (st_fbblk - .h)]
        call    st_gate
        test    eax, eax
        jz      .skip
        mov     ebx, eax
        mov     eax, [esp + 36]                 ; the caller
        mov     [ebp + (st_fdlast - .h)], eax
        sub     eax, [ebp + (st_rtbase - .h)]
        push    5
        push    eax
        push    32
        push    8
        push    ebx
        call    st_hex
        add     esp, 20
        mov     eax, [esp + 40]                 ; the count
        push    5
        push    eax
        push    32
        push    88
        push    ebx
        call    st_hex
        add     esp, 20
        push    6
        push    dword [ebp + (st_nfd - .h)]
        push    32
        push    184
        push    ebx
        call    st_hex
        add     esp, 20
        jmp     .skip
.same:  test    byte [ebp + (st_nfd - .h)], 0xff
        jz      .new
.skip:  popfd
        popad
        push    ebx                             ; FixedDelay's own first instructions
        push    esi
        push    0x61
st_fd_call:
        db      0xe8                            ; call IoRead8 (rel32 patched)
        dd      0
st_fd_jmp:
        db      0xe9                            ; jmp back into FixedDelay (rel32 patched)
        dd      0

; The command-block send calls here instead of the transfer function (stack: [esp] its return,
; [esp+4] device, [esp+8] 0, [esp+12] the command block, [esp+16] 0x1f).
st_cbw_entry:
        pushad
        pushfd
        call    .h
.h:     pop     ebp
        inc     dword [ebp + (st_ncbw - .h)]
        lea     ecx, [ebp + (st_fbblk - .h)]
        call    st_gate
        test    eax, eax
        jz      .skip
        mov     ebx, eax
        mov     esi, [esp + 48]                 ; the command block (CBW)
        mov     ecx, [ebp + (st_ncbw - .h)]     ; the ring: opcode n-1 at (n-1) mod 16
        dec     ecx
        and     ecx, 15
        mov     al, [esi + 15]
        mov     [ebp + (st_ops - .h) + ecx], al
        xor     edi, edi                        ; line 4: oldest first
.ops:   mov     ecx, [ebp + (st_ncbw - .h)]
        add     ecx, edi
        and     ecx, 15
        movzx   eax, byte [ebp + (st_ops - .h) + ecx]
        push    2
        push    eax
        push    80
        imul    eax, edi, 48
        add     eax, 8
        push    eax
        push    ebx
        call    st_hex
        add     esp, 20
        inc     edi
        cmp     edi, 16
        jb      .ops
        movzx   eax, byte [esi + 15]            ; its first CDB byte: the SCSI opcode
        push    2
        push    eax
        push    56
        push    8
        push    ebx
        call    st_hex
        add     esp, 20
        push    8
        push    dword [esi + 8]                 ; data transfer length
        push    56
        push    56
        push    ebx
        call    st_hex
        add     esp, 20
        push    6
        push    dword [ebp + (st_ncbw - .h)]
        push    56
        push    200
        push    ebx
        call    st_hex
        add     esp, 20
.skip:  popfd
        popad
st_cbw_jmp:
        db      0xe9                            ; jmp to the transfer function (rel32 patched)
        dd      0

; USBRT's REQUEST SENSE function jumps here (stack: [esp] its caller, [esp+4] the device). Runs
; it (its displaced first instructions below), then shows the sense code it returns.
st_sense_entry:
        push    dword [esp + 4]
        call    .orig
        add     esp, 4
        pushad
        pushfd
        call    .h
.h:     pop     ebp
        inc     dword [ebp + (st_nsense - .h)]
        lea     ecx, [ebp + (st_fbblk - .h)]
        call    st_gate
        test    eax, eax
        jz      .skip
        mov     ebx, eax
        push    6
        push    dword [esp + 36]                ; the returned code: pushad's eax at +32, +4 for the push
        push    104
        push    8
        push    ebx
        call    st_hex
        add     esp, 20
        push    6
        push    dword [ebp + (st_nsense - .h)]
        push    104
        push    120
        push    ebx
        call    st_hex
        add     esp, 20
.skip:  popfd
        popad
        ret
.orig:  push    edi                             ; its own first instructions
        push    1
st_sense_call:
        db      0xe8                            ; call (+0x2a12) (rel32 patched)
        dd      0
st_sense_jmp:
        db      0xe9                            ; jmp back into it (+0x4c69) (rel32 patched)
        dd      0

; The controllers' function tables point here: control transfer (HC, dev, wRequest, wIndex, wValue,
; buffer, length), root-port status (HC, port), bulk (HC, dev, direction, buffer, length) and
; interrupt transfers, cdecl. Each pushes which one it is (bit 0: UHCI; bits 1-2: control, port
; status, bulk, interrupt).
st_ctl_e:
        push    0
        jmp     st_wrap
st_ctl_u:
        push    1
        jmp     st_wrap
st_rhs_e:
        push    2
        jmp     st_wrap
st_rhs_u:
        push    3
        jmp     st_wrap
st_blk_e:
        push    4
        jmp     st_wrap
st_blk_u:
        push    5
        jmp     st_wrap
st_int_e:
        push    6
        jmp     st_wrap
st_int_u:
        push    7
st_wrap:
        pushad                                  ; [esp+32] which, [esp+36] return, [esp+40] arguments
        call    .h
.h:     pop     ebp
        mov     ecx, [esp + 32]
        movzx   eax, byte [ebp + (st_kinds - .h) + ecx]
        mov     edx, ecx
        and     edx, 6
        cmp     edx, 2
        je      .port
        ; a transfer: recorded (and drawn) as it starts, result EEEE, then again when it returns
        call    .g                              ; gUsbData, if there is one yet: its status byte
        jz      .ns
        mov     ah, [edx + 0xe14]
.ns:    mov     edx, [ebp + (st_nctl - .h)]
        inc     dword [ebp + (st_nctl - .h)]
        and     edx, 3
        shl     edx, 4
        lea     edx, [ebp + (st_ring - .h) + edx]
        mov     [edx], ax
        xor     eax, eax
        mov     ecx, [esp + 44]                 ; the device: port << 8 | address
        test    ecx, ecx
        jz      .na
        mov     al, [ecx + 6]
        mov     ah, [ecx + 9]
.na:    mov     [edx + 2], ax
        mov     ax, [esp + 48]                  ; arguments 3, 5, 4, 7 (control: wRequest, wValue,
        mov     [edx + 4], ax                   ;  wIndex, wLength; bulk: direction, length, buffer, -)
        mov     ax, [esp + 56]
        mov     [edx + 6], ax
        mov     ax, [esp + 52]
        mov     [edx + 8], ax
        mov     ax, [esp + 64]
        mov     [edx + 10], ax
        mov     dword [edx + 12], 0xeeee        ; not returned yet; time 0
        push    edx                             ; (the entry: arguments now at [esp+44])
        call    .draw
        rdtsc
        shrd    eax, edx, 10
        mov     edi, eax                        ; start (TSC >> 10)
        call    .orig
        mov     esi, eax
        rdtsc
        shrd    eax, edx, 10
        sub     eax, edi
        shr     eax, 10                         ; time it took (TSC >> 20)
        pop     ebx                             ; the entry
        mov     [ebx + 12], si
        mov     [ebx + 14], ax
        call    .g
        jz      .end
        mov     cl, [edx + 0xe14]               ; the status byte after it
        mov     [ebx + 1], cl
.end:   call    .draw
        jmp     .out
.port:  push    eax                             ; root-port status: shown when it returns
        call    .orig
        movzx   esi, ax
        pop     eax
        mov     [ebp + (st_rhs - .h)], eax
        movzx   eax, byte [esp + 44]
        mov     [ebp + (st_rhs - .h) + 4], eax
        mov     [ebp + (st_rhs - .h) + 8], esi
        inc     dword [ebp + (st_nrhs - .h)]
        call    .draw
.out:   popad
        add     esp, 4
        ret
; .orig: calls the real function with the 7 arguments again (not all used by every function) and
; puts what it returns in the caller's eax (pushad's copy). Called with one dword pushed since
; pushad: which is at [esp+40], the arguments at [esp+48]. Keeps ebp, edi.
.orig:  mov     ecx, [esp + 40]
        mov     esi, [ebp + (st_orig - .h) + ecx * 4]
        lea     ebx, [esp + 48 + 24]
        push    7
        pop     ecx
.cp:    push    dword [ebx]
        sub     ebx, 4
        loop    .cp
        call    esi
        add     esp, 28
        mov     [esp + 8 + 28], eax
        ret
; .g: edx = gUsbData (0 if not set up yet; ZF then set).
.g:     mov     edx, [ebp + (st_rtbase - .h)]
        mov     edx, [edx + 0x98f0]
        test    edx, edx
        ret
; .draw: the ring (oldest first), if drawing is allowed.
; Clobbers everything but ebp.
.draw:  lea     ecx, [ebp + (st_fbblk - .h)]
        call    st_gate
        test    eax, eax
        jz      .dr
        mov     ebx, eax
        xor     edi, edi
.line:  mov     esi, [ebp + (st_nctl - .h)]
        add     esi, edi
        and     esi, 3
        shl     esi, 4
        lea     esi, [ebp + (st_ring - .h) + esi]
        xor     ecx, ecx
.fld:   push    ecx                             ; (st_hex keeps ebx, esi, edi, not ecx)
        movzx   eax, byte [ebp + (st_fields - .h) + ecx * 4 + 3]
        push    eax                             ; digits
        movzx   eax, byte [ebp + (st_fields - .h) + ecx * 4]
        movzx   eax, word [esi + eax]
        push    eax                             ; the field
        imul    eax, edi, 24
        add     eax, 128
        push    eax                             ; y
        movzx   eax, word [ebp + (st_fields - .h) + ecx * 4 + 1]
        push    eax                             ; x
        push    ebx
        call    st_hex
        add     esp, 20
        pop     ecx
        inc     ecx
        cmp     ecx, 8
        jb      .fld
        inc     edi
        cmp     edi, 4
        jb      .line
.dr:    ret
st_kinds:       db      0x31, 0x11, 0x30, 0x10, 0x32, 0x12, 0x33, 0x13  ; controller | 1 control, 2 bulk, 3 interrupt
st_fields:                                      ; offset in the entry (words), x, digits
        db      0
        dw      8
        db      4
        db      2
        dw      88
        db      4
        db      4
        dw      168
        db      4
        db      6
        dw      248
        db      4
        db      8
        dw      328
        db      4
        db      10
        dw      408
        db      4
        db      12
        dw      488
        db      4
        db      14
        dw      568
        db      4
%include "sthex.inc"
st_blob_end:
ST_BLOB_LEN     equ     st_blob_end - st_blob
%if ST_BLOB_LEN > ST_AREA_LEN
%error "screentrace blob too big for USBRT's .reloc area"
%endif

; ---- in this driver ----
        align   4, db 0
st_fb:          times 5 dd 0                    ; magic, base, pixels a line, width, height
st_nreq:        dd 0                            ; USB requests (SMIs) so far
st_gop:         dd 0                            ; the Graphics Output protocol
st_g_info:      dd 0                            ; what st_refresh saw (a G record's info)
st_g_last:      dd 0                            ; ...and what it last recorded
st_tab:         ; the table fills: instruction RVA, the function it stores, its slot, the stub
        dd      0x7401, 0x6ee4, 0x25, st_ctl_e - st_blob     ; EHCI control transfer
        dd      0x2765, 0x20a7, 0x25, st_ctl_u - st_blob     ; UHCI control transfer
        dd      0x73ec, 0x639e, 0x19, st_rhs_e - st_blob     ; EHCI root-port status
        dd      0x2750, 0x151b, 0x19, st_rhs_u - st_blob     ; UHCI root-port status
        dd      0x7408, 0x70b2, 0x29, st_blk_e - st_blob     ; EHCI bulk transfer
        dd      0x276c, 0x26c1, 0x29, st_blk_u - st_blob     ; UHCI bulk transfer
        dd      0x740f, 0x72b7, 0x2d, st_int_e - st_blob     ; EHCI interrupt transfer
        dd      0x2773, 0x2329, 0x2d, st_int_u - st_blob     ; UHCI interrupt transfer

; st_install(base, size): puts the hooks and the blob into a USBRT copy. 1 if done (2 = not
; relocated yet, 3 = a site not stock, 4 = not this USBRT: nothing done; 5 = all but the function
; table hooks, their eight fill instructions not all stock). cdecl, keeps ebx/esi/edi.
st_install:
        push    ebx
        push    esi
        push    edi
        call    .h
.h:     pop     edi
        mov     esi, [esp + 16]
        mov     eax, 4
        cmp     dword [esp + 20], ST_IMAGE_SIZE
        jne     .out
        mov     eax, 2
        lea     ecx, [esi + 0x98f0]             ; mov eax, [gUsbData] at +0x52ac: relocated yet?
        cmp     [esi + 0x52ad], ecx
        jne     .out
        mov     eax, 3
        cmp     dword [esi + 0x1381], 0x616a5653        ; push ebx; push esi; push 0x61
        jne     .out
        cmp     dword [esi + 0x1385], 0xfffeaae8        ; call IoRead8
        jne     .out
        cmp     byte [esi + 0x1389], 0xff
        jne     .out
        cmp     byte [esi + 0x4811], 0xe8               ; call the transfer function (+0x452d)
        jne     .out
        cmp     dword [esi + 0x4812], 0xfffffd17
        jne     .out
        cmp     dword [esi + 0x4c61], 0xe8016a57        ; push edi; push 1; call (+0x2a12)
        jne     .out
        cmp     dword [esi + 0x4c65], 0xffffdda9
        jne     .out
        push    esi                             ; copy the blob
        push    edi
        lea     ebx, [esi + ST_AREA]
        lea     esi, [edi + (st_blob - .h)]
        mov     edi, ebx
        mov     ecx, ST_BLOB_LEN
        cld
        rep     movsb
        pop     edi
        pop     esi
        lea     eax, [edi + (st_fb - .h)]
        mov     [ebx + (st_fbblk - st_blob)], eax
        mov     [ebx + (st_rtbase - st_blob)], esi
        lea     eax, [esi + 0x1234]             ; IoRead8
        lea     ecx, [ebx + (st_fd_call - st_blob) + 5]
        sub     eax, ecx
        mov     [ebx + (st_fd_call - st_blob) + 1], eax
        lea     eax, [esi + 0x138a]             ; back into FixedDelay
        lea     ecx, [ebx + (st_fd_jmp - st_blob) + 5]
        sub     eax, ecx
        mov     [ebx + (st_fd_jmp - st_blob) + 1], eax
        lea     eax, [esi + 0x452d]             ; the transfer function
        lea     ecx, [ebx + (st_cbw_jmp - st_blob) + 5]
        sub     eax, ecx
        mov     [ebx + (st_cbw_jmp - st_blob) + 1], eax
        lea     eax, [esi + 0x2a12]             ; the sense function's own call
        lea     ecx, [ebx + (st_sense_call - st_blob) + 5]
        sub     eax, ecx
        mov     [ebx + (st_sense_call - st_blob) + 1], eax
        lea     eax, [esi + 0x4c69]             ; back into it
        lea     ecx, [ebx + (st_sense_jmp - st_blob) + 5]
        sub     eax, ecx
        mov     [ebx + (st_sense_jmp - st_blob) + 1], eax
        lea     eax, [ebx + (st_sense_entry - st_blob)] ; its hook
        lea     ecx, [esi + 0x4c66]
        sub     eax, ecx
        mov     word [esi + 0x4c66], 0x9090
        mov     byte [esi + 0x4c68], 0x90
        mov     [esi + 0x4c62], eax
        mov     byte [esi + 0x4c61], 0xe9
        lea     eax, [ebx + (st_fd_entry - st_blob)]    ; the hooks, last
        lea     ecx, [esi + 0x1386]
        sub     eax, ecx
        mov     dword [esi + 0x1386], 0x90909090
        mov     [esi + 0x1382], eax
        mov     byte [esi + 0x1381], 0xe9
        lea     eax, [ebx + (st_cbw_entry - st_blob)]
        lea     ecx, [esi + 0x4816]
        sub     eax, ecx
        mov     [esi + 0x4812], eax
        xor     ecx, ecx                        ; the function tables: all eight fills stock, or none changed
.tc:    mov     edx, ecx
        shl     edx, 4
        lea     edx, [edi + (st_tab - .h) + edx]
        mov     eax, [edx]
        cmp     word [esi + eax], 0x40c7        ; mov dword [eax + slot], imm32 (relocated)
        jne     .tno
        push    ecx
        mov     cl, [edx + 8]
        cmp     [esi + eax + 2], cl
        pop     ecx
        jne     .tno
        mov     eax, [esi + eax + 3]
        sub     eax, esi
        cmp     eax, [edx + 4]
        jne     .tno
        inc     ecx
        cmp     ecx, 8
        jb      .tc
        xor     ecx, ecx
.ts:    mov     edx, ecx
        shl     edx, 4
        lea     edx, [edi + (st_tab - .h) + edx]
        mov     eax, [edx + 4]                  ; the real function, for the stub
        add     eax, esi
        mov     [ebx + (st_orig - st_blob) + ecx * 4], eax
        mov     eax, [edx + 12]                 ; the table gets the stub instead
        add     eax, ebx
        push    eax
        mov     eax, [edx]
        pop     dword [esi + eax + 3]
        inc     ecx
        cmp     ecx, 8
        jb      .ts
        mov     eax, 1
        jmp     .out
.tno:   mov     eax, 5                          ; the other hooks are in; the tables are not
.out:   pop     edi
        pop     esi
        pop     ebx
        ret

; VOID EFIAPI st_gop_notify(EFI_EVENT, VOID *BS): the Graphics Output protocol is there: keep it.
; Its framebuffer details are read again before every draw (st_refresh): when it is installed the
; display driver may not have set its mode yet (the console sets it later).
st_gop_notify:
        push    ebx
        push    edi
        call    .h
.h:     pop     edi
        mov     ebx, [esp + 16]                 ; BS
        push    0
        mov     eax, esp
        push    eax
        push    0
        lea     eax, [edi + (tm_g_gop - .h)]
        push    eax
        call    [ebx + 0xac]                    ; LocateProtocol(GOP)
        add     esp, 12
        pop     ecx
        test    eax, eax
        jnz     .o
        mov     [edi + (st_gop - .h)], ecx
        call    st_refresh
.o:     pop     edi
        pop     ebx
        ret

; st_refresh: fills the framebuffer block from the Graphics Output protocol's current mode, magic
; last, if that is a plain 32-bit framebuffer (RGB or BGR, 8 bits each) below 4 GB with a
; non-zero base and size and a scan line at least as wide as the screen; otherwise, or once the
; trace has stopped (ReadyToBoot), the magic is cleared. Only memory reads of the protocol's
; data: fine at any TPL. A G record whenever what it sees changes (info: G | format | valid << 8;
; end: base; caller: pixels a line; extra: width << 16 | height). Keeps every register but eax.
st_refresh:
        push    ebx
        push    ecx
        push    edx
        push    esi
        push    edi
        call    .h
.h:     pop     edi
        lea     esi, [edi + (st_fb - .h)]
        xor     ebx, ebx                        ; valid?
        xor     eax, eax                        ; format
        xor     ecx, ecx                        ; base
        xor     edx, edx                        ; info
        cmp     byte [edi + (ut_stopped - .h)], 0
        jne     .rec
        mov     edx, [edi + (st_gop - .h)]
        test    edx, edx
        jz      .rec
        mov     edx, [edx + 0x0c]               ; Mode
        test    edx, edx
        jz      .rec
        cmp     dword [edx + 0x14], 0           ; FrameBufferBase below 4 GB
        jne     .rec
        mov     ecx, [edx + 0x10]
        mov     edx, [edx + 8]                  ; Info
        test    edx, edx
        jz      .rec
        mov     eax, [edx + 0x0c]               ; PixelFormat
        test    ecx, ecx
        jz      .rec
        cmp     eax, 1
        ja      .rec
        cmp     dword [edx + 4], 0              ; width
        je      .rec
        cmp     dword [edx + 8], 0              ; height
        je      .rec
        push    eax
        mov     eax, [edx + 0x20]               ; PixelsPerScanLine
        cmp     eax, [edx + 4]
        pop     eax
        jb      .rec
        inc     ebx
.rec:   ; a G record if validity, format, base, line, width or height changed
        push    eax
        mov     eax, ebx
        shl     eax, 8
        or      eax, [esp]                      ; valid << 8 | format
        or      eax, 'G' << 24
        mov     [edi + (st_g_info - .h)], eax
        pop     eax
        push    eax
        test    ebx, ebx
        jz      .inv
        cmp     dword [esi], ST_MAGIC           ; same as already in the block?
        jne     .new
        cmp     [esi + 4], ecx
        jne     .new
        mov     eax, [edx + 0x20]
        cmp     [esi + 8], eax
        jne     .new
        mov     eax, [edx + 4]
        cmp     [esi + 12], eax
        jne     .new
        mov     eax, [edx + 8]
        cmp     [esi + 16], eax
        je      .same
.new:   mov     dword [esi], 0
        mov     [esi + 4], ecx
        mov     eax, [edx + 0x20]
        mov     [esi + 8], eax
        mov     eax, [edx + 4]
        mov     [esi + 12], eax
        mov     eax, [edx + 8]
        mov     [esi + 16], eax
        mov     dword [esi], ST_MAGIC           ; last
        jmp     .log
.inv:   mov     dword [esi], 0                  ; invalid: no drawing; recorded if that is news
.log:   mov     eax, [edi + (st_g_info - .h)]
        cmp     eax, [edi + (st_g_last - .h)]
        jne     .put
        test    ebx, ebx
        jz      .same
.put:   mov     [edi + (st_g_last - .h)], eax
        rdtsc
        shrd    eax, edx, 10
        push    0                               ; no merging
        test    ebx, ebx
        jz      .z
        mov     edx, [esi + 12]
        shl     edx, 16
        or      edx, [esi + 16]
        push    edx                             ; extra: width << 16 | height
        push    dword [edi + (st_g_info - .h)]
        push    dword [esi + 8]                 ; caller: pixels a line
        push    ecx                             ; end: base
        jmp     .p
.z:     push    0
        push    dword [edi + (st_g_info - .h)]
        push    0
        push    ecx
.p:     push    eax
        call    ut_add
        add     esp, 24
.same:  pop     eax
        mov     eax, ebx
        pop     edi
        pop     esi
        pop     edx
        pop     ecx
        pop     ebx
        ret

; st_draw_req(before, info, start, end, n): line 1 from ut_trig. cdecl, keeps ebx/esi/edi.
st_draw_req:
        call    st_refresh                      ; the framebuffer as it is now
        push    ebx
        call    .h
.h:     pop     ebx
        lea     ebx, [ebx + (st_fb - .h)]
        cmp     dword [ebx], ST_MAGIC
        jne     .o
        mov     eax, [esp + 12]                 ; info: function | subfunction << 8 (in bits 8-23)
        shr     eax, 8
        and     eax, 0xffff
        xchg    al, ah                          ; function first
        push    4
        push    eax
        push    8
        push    8
        push    ebx
        call    st_hex
        add     esp, 20
        mov     eax, [esp + 16]                 ; start (TSC >> 10) >> 10
        shr     eax, 10
        push    5
        push    eax
        push    8
        push    88
        push    ebx
        call    st_hex
        add     esp, 20
        xor     eax, eax
        cmp     dword [esp + 8], 0              ; before: duration 0 for now
        jne     .d
        mov     eax, [esp + 20]
        sub     eax, [esp + 16]
        shr     eax, 10
.d:     push    5
        push    eax
        push    8
        push    184
        push    ebx
        call    st_hex
        add     esp, 20
        push    4
        push    dword [esp + 28]
        push    8
        push    280
        push    ebx
        call    st_hex
        add     esp, 20
.o:     pop     ebx
        ret
