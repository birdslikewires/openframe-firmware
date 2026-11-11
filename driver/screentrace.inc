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

; st_hex(fb, x, y, value, digits): draws value's low digits in hex at (x, y), 16x16 pixels a digit,
; white on black, if it fits. fb: +0 magic, +4 base, +8 pixels a scan line, +12 width, +16 height
; (32 bits a pixel). cdecl, keeps ebx, esi, edi. Position-independent.
st_hex:
        push    ebp
        mov     ebp, esp
        sub     esp, 16                         ; -4 x0, -8 row, -12 glyph, -16 digit
        push    ebx
        push    esi
        push    edi
        call    .h
.h:     pop     edi
        mov     ebx, [ebp + 8]
        mov     eax, [ebp + 24]
        shl     eax, 4
        add     eax, [ebp + 12]
        cmp     eax, [ebx + 12]
        ja      .out
        mov     eax, [ebp + 16]
        add     eax, 16
        cmp     eax, [ebx + 16]
        ja      .out
        mov     dword [ebp - 16], 0
.dig:   mov     eax, [ebp - 16]
        cmp     eax, [ebp + 24]
        jae     .out
        mov     ecx, [ebp + 24]
        sub     ecx, eax
        dec     ecx
        shl     ecx, 2
        mov     edx, [ebp + 20]
        shr     edx, cl
        and     edx, 0xf
        lea     edx, [edi + (hexfont - .h) + edx * 8]
        mov     [ebp - 12], edx
        shl     eax, 4
        add     eax, [ebp + 12]
        mov     [ebp - 4], eax
        mov     dword [ebp - 8], 0
.row:   mov     eax, [ebp - 8]
        cmp     eax, 16
        jae     .nd
        mov     ecx, eax
        shr     ecx, 1
        mov     edx, [ebp - 12]
        movzx   edx, byte [edx + ecx]           ; this row's glyph bits
        add     eax, [ebp + 16]
        imul    eax, [ebx + 8]
        add     eax, [ebp - 4]
        shl     eax, 2
        add     eax, [ebx + 4]                  ; the row's first pixel
        xor     esi, esi
.col:   cmp     esi, 16
        jae     .rn
        mov     ecx, esi
        shr     ecx, 1
        push    edx
        shl     edx, cl
        test    dl, 0x80
        pop     edx
        jz      .bk
        mov     dword [eax + esi * 4], 0x00ffffff
        jmp     .cn
.bk:    mov     dword [eax + esi * 4], 0
.cn:    inc     esi
        jmp     .col
.rn:    inc     dword [ebp - 8]
        jmp     .row
.nd:    inc     dword [ebp - 16]
        jmp     .dig
.out:   pop     edi
        pop     esi
        pop     ebx
        leave
        ret

%include "hexfont.inc"
st_blob_end:
ST_BLOB_LEN     equ     st_blob_end - st_blob
%if ST_BLOB_LEN > ST_AREA_LEN
%error "screentrace blob too big for USBRT's .reloc area"
%endif

; ---- in this driver ----
        align   4, db 0
st_fb:          times 5 dd 0                    ; magic, base, pixels a line, width, height
st_nreq:        dd 0                            ; USB requests (SMIs) so far

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

; VOID EFIAPI st_gop_notify(EFI_EVENT, VOID *BS): the Graphics Output protocol is there: fill the
; framebuffer block if it's a plain 32-bit framebuffer.
st_gop_notify:
        push    ebx
        push    esi
        push    edi
        call    .h
.h:     pop     edi
        mov     ebx, [esp + 20]                 ; BS
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
        mov     ecx, [ecx + 0x0c]               ; Mode
        test    ecx, ecx
        jz      .o
        mov     edx, [ecx + 8]                  ; Info
        test    edx, edx
        jz      .o
        cmp     dword [edx + 0x0c], 1           ; PixelFormat: RGB or BGR, 8 bits each, 32 a pixel
        ja      .o
        cmp     dword [ecx + 0x14], 0           ; FrameBufferBase below 4 GB
        jne     .o
        lea     esi, [edi + (st_fb - .h)]
        mov     eax, [ecx + 0x10]
        mov     [esi + 4], eax
        mov     eax, [edx + 0x20]               ; PixelsPerScanLine
        mov     [esi + 8], eax
        mov     eax, [edx + 4]
        mov     [esi + 12], eax
        mov     eax, [edx + 8]
        mov     [esi + 16], eax
        mov     dword [esi], ST_MAGIC
.o:     pop     edi
        pop     esi
        pop     ebx
        ret

; st_draw_req(before, info, start, end, n): line 1 from ut_trig. cdecl, keeps ebx/esi/edi.
st_draw_req:
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
