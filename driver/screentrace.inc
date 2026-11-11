; screentrace.inc: development build only (--define TIMING --define PROFILE --define USBTRACE
; --define SCREEN). Shows on the screen, live, what the firmware's USB code is doing, so a boot that
; hangs there (a 12 TB drive attached) can be read off the panel. Three lines of hex digits above
; the boot logo (8x8 glyphs drawn 2x: 16 pixels a digit), straight into the framebuffer (safe with
; interrupts off, which the console isn't):
;   y=8   USB request (DXE, ut_trig): function/subfunction, start, time it took (both TSC >> 20,
;         0.79 ms units; the time stays 00000 while a request hasn't returned), how many so far
;   y=32  USBRT FixedDelay (SMM): caller (RVA in USBRT), its argument (15 us ticks), calls so far
;   y=56  USBRT mass-storage command (SMM): SCSI opcode, data length, commands so far
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
ST_AREA_LEN     equ     0x64c
ST_IMAGE_SIZE   equ     0x9f80

; ---- the blob: copied into each USBRT copy; position-independent ----
st_blob:
st_fbblk:       dd 0                            ; -> st_fb (patched)
st_rtbase:      dd 0                            ; this USBRT copy's base (patched)
st_nfd:         dd 0                            ; FixedDelay calls
st_ncbw:        dd 0                            ; command blocks sent

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
        lea     ecx, [ebp + (st_fbblk - .h)]
        call    st_gate
        test    eax, eax
        jz      .skip
        mov     ebx, eax
        mov     eax, [esp + 36]                 ; the caller
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

; st_install(base, size): puts the two hooks and the blob into a USBRT copy. 1 if done, 0 if not
; (2 = not relocated yet, 3 = a site not stock, 4 = not this USBRT). cdecl, keeps ebx/esi/edi.
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
        mov     eax, 1
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
