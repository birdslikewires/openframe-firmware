; ConDiag.efi: a shell app that says how the console is put together, for finding why the shell's
; text never reaches the screen (the Esc console, 2026-11-17). Run with its output redirected to a
; file (`condiag.efi > con.txt`): the shell then swaps ST->ConOut for the file, so ST->ConOut and
; ConsoleOutHandle as printed are the redirect's; the handles below are the firmware's own.
; It prints, in hex:
;   - ST->ConsoleInHandle, ConIn, ConsoleOutHandle, ConOut, StandardErrorHandle, StdErr;
;   - every handle with Simple Text Output: the handle, the interface, its Mode (MaxMode, Mode,
;     Attribute, CursorColumn, CursorRow), and GOP if the handle has Graphics Output too;
;   - the EFI global variables ConOut, ConOutDev, ConIn, ConInDev, ErrOut: their size and up to
;     128 bytes (device paths);
;   - that handle's Graphics Output: its mode (MaxMode, Mode, width, height, pixel format, pixels a
;     line) and framebuffer (base, size);
; then (v2) a red bar at the top left through GOP->Blt (EfiBltVideoFill, as the graphics console
; draws), a green bar under it written straight into the framebuffer (as screentrace.inc drew),
; the graphics console's SetMode(0) and a line of text, and a 5 s Stall so it can all be seen.
; Reads only, apart from that screen. Touches no flash, writes no variable.
;
; Entry: EFI_STATUS EFIAPI Entry(EFI_HANDLE ImageHandle, EFI_SYSTEM_TABLE *SystemTable)

bits 32

%define COUNT   ebp - 4
%define HANDLES ebp - 8
%define INDEX   ebp - 12
%define IFACE   ebp - 16
%define GC      ebp - 20
%define VSIZE   ebp - 24
%define VATTR   ebp - 28
%define VBUF    ebp - 28 - 512
%define GOP     ebp - 28 - 516

%macro PRINT 1
        lea     eax, [edi + (%1 - entry.here)]
        push    eax
        push    esi
        call    con_out
        add     esp, 8
%endmacro
%macro HEX 1
        push    %1
        push    esi
        call    con_hex
        add     esp, 8
%endmacro

entry:
        push    ebp
        mov     ebp, esp
        sub     esp, 28 + 516
        push    ebx
        push    esi
        push    edi
        call    .here
.here:  pop     edi
        mov     esi, [ebp + 12]                 ; esi = SystemTable
        mov     ebx, [esi + 0x3c]               ; ebx = BootServices
        xor     eax, eax
        mov     [COUNT], eax
        mov     [HANDLES], eax
        mov     [INDEX], eax
        mov     [GC], eax
        mov     [GOP], eax

        PRINT   s_st
        HEX     dword [esi + 0x20]
        PRINT   s_sp
        HEX     dword [esi + 0x24]
        PRINT   s_sp
        HEX     dword [esi + 0x28]
        PRINT   s_sp
        HEX     dword [esi + 0x2c]
        PRINT   s_sp
        HEX     dword [esi + 0x30]
        PRINT   s_sp
        HEX     dword [esi + 0x34]
        PRINT   s_nl

        lea     eax, [HANDLES]                  ; LocateHandleBuffer(ByProtocol, &SimpleTextOut, ...)
        push    eax
        lea     eax, [COUNT]
        push    eax
        push    0
        lea     eax, [edi + (g_txtout - entry.here)]
        push    eax
        push    2
        call    [ebx + 0xa8]
        add     esp, 20
        test    eax, eax
        jnz     .vars
.loop:  mov     eax, [INDEX]
        cmp     eax, [COUNT]
        jae     .free
        mov     edx, [HANDLES]
        mov     edx, [edx + eax * 4]
        push    edx
        PRINT   s_to
        pop     edx
        push    edx
        HEX     edx
        pop     edx
        push    edx
        mov     dword [IFACE], 0
        lea     eax, [IFACE]                    ; HandleProtocol(handle, &SimpleTextOut, &iface)
        push    eax
        lea     eax, [edi + (g_txtout - entry.here)]
        push    eax
        push    edx
        call    [ebx + 0x58]
        add     esp, 12
        pop     edx
        test    eax, eax
        jnz     .nl
        push    edx
        PRINT   s_sp
        HEX     dword [IFACE]
        mov     ecx, [IFACE]
        mov     ecx, [ecx + 0x24]               ; ->Mode
        test    ecx, ecx
        jz      .gop
        push    ecx
        PRINT   s_mode
        mov     ecx, [esp]
        HEX     dword [ecx]                     ; MaxMode
        PRINT   s_sp
        mov     ecx, [esp]
        HEX     dword [ecx + 4]                 ; Mode
        PRINT   s_sp
        mov     ecx, [esp]
        HEX     dword [ecx + 8]                 ; Attribute
        PRINT   s_sp
        mov     ecx, [esp]
        HEX     dword [ecx + 0x0c]              ; CursorColumn
        PRINT   s_sp
        mov     ecx, [esp]
        HEX     dword [ecx + 0x10]              ; CursorRow
        pop     ecx
.gop:   pop     edx
        push    0                               ; HandleProtocol(handle, &GraphicsOutput, &gop)
        mov     eax, esp
        push    eax
        lea     eax, [edi + (g_gop - entry.here)]
        push    eax
        push    edx
        call    [ebx + 0x58]
        add     esp, 12
        pop     ecx                             ; the GOP interface HandleProtocol gave
        test    eax, eax
        jnz     .nl
        mov     [GOP], ecx
        mov     eax, [IFACE]
        mov     [GC], eax
        PRINT   s_gop
.nl:    PRINT   s_nl
        inc     dword [INDEX]
        jmp     .loop
.free:  push    dword [HANDLES]
        call    [ebx + 0x30]                    ; FreePool
        add     esp, 4

.vars:  lea     eax, [edi + (v_conout - entry.here)]
        call    dumpvar
        lea     eax, [edi + (v_conoutdev - entry.here)]
        call    dumpvar
        lea     eax, [edi + (v_conin - entry.here)]
        call    dumpvar
        lea     eax, [edi + (v_conindev - entry.here)]
        call    dumpvar
        lea     eax, [edi + (v_errout - entry.here)]
        call    dumpvar

        mov     eax, [GOP]                      ; the display's Graphics Output
        test    eax, eax
        jz      .gcon
        PRINT   s_gm
        mov     eax, [GOP]
        mov     ecx, [eax + 0x0c]               ; ->Mode
        push    ecx
        HEX     dword [ecx]                     ; MaxMode
        PRINT   s_sp
        mov     ecx, [esp]
        HEX     dword [ecx + 4]                 ; Mode
        PRINT   s_sp
        mov     ecx, [esp]
        mov     ecx, [ecx + 8]                  ; ->Info
        push    ecx
        HEX     dword [ecx + 4]                 ; HorizontalResolution
        PRINT   s_sp
        mov     ecx, [esp]
        HEX     dword [ecx + 8]                 ; VerticalResolution
        PRINT   s_sp
        mov     ecx, [esp]
        HEX     dword [ecx + 0x0c]              ; PixelFormat
        PRINT   s_sp
        mov     ecx, [esp]
        HEX     dword [ecx + 0x20]              ; PixelsPerScanLine
        pop     ecx
        PRINT   s_fb
        mov     ecx, [esp]
        HEX     dword [ecx + 0x10]              ; FrameBufferBase (low)
        PRINT   s_sp
        mov     ecx, [esp]
        HEX     dword [ecx + 0x18]              ; FrameBufferSize
        pop     ecx
        PRINT   s_nl
        ; red bar through Blt: VideoFill (300 x 40 at 0,0)
        mov     dword [VBUF], 0x00ff0000        ; BGRR: red
        mov     eax, [GOP]
        push    0                               ; Delta
        push    40                              ; Height
        push    300                             ; Width
        push    0                               ; DestY
        push    0                               ; DestX
        push    0                               ; SourceY
        push    0                               ; SourceX
        push    0                               ; EfiBltVideoFill
        lea     ecx, [VBUF]
        push    ecx                             ; BltBuffer: one pixel
        push    eax                             ; This
        call    [eax + 8]                       ; GOP->Blt
        add     esp, 40
        push    eax
        PRINT   s_blt
        pop     eax
        HEX     eax
        PRINT   s_nl
        ; green bar straight into the framebuffer (300 x 40 at 0,60), if it is 32-bit with a base
        mov     eax, [GOP]
        mov     eax, [eax + 0x0c]               ; Mode
        mov     edx, [eax + 0x10]               ; FrameBufferBase
        test    edx, edx
        jz      .gcon
        mov     ecx, [eax + 8]
        mov     ecx, [ecx + 0x20]               ; PixelsPerScanLine
        cmp     ecx, 300
        jb      .gcon
        push    ebx
        push    esi
        mov     ebx, 60                         ; y
.gy:    mov     eax, ebx
        imul    eax, ecx
        lea     eax, [edx + eax * 4]
        mov     esi, 300
.gx:    mov     dword [eax], 0x0000ff00         ; green
        add     eax, 4
        dec     esi
        jnz     .gx
        inc     ebx
        cmp     ebx, 100
        jb      .gy
        pop     esi
        pop     ebx
.gcon:
        mov     eax, [GC]                       ; the graphics console, directly
        test    eax, eax
        jz      .done
        PRINT   s_gc
        mov     eax, [GC]
        push    0
        push    eax
        call    [eax + 0x10]                    ; SetMode(This, 0)
        add     esp, 8
        HEX     eax
        PRINT   s_nl
        mov     eax, [GC]
        lea     ecx, [edi + (s_hello - entry.here)]
        push    ecx
        push    eax
        call    [eax + 4]                       ; OutputString(This, s_hello)
        add     esp, 8
        push    5000000
        call    [ebx + 0x88]                    ; Stall(5 s)
        add     esp, 4

.done:  PRINT   s_end
        xor     eax, eax
        pop     edi
        pop     esi
        pop     ebx
        leave
        ret

; dumpvar: eax = the variable's name (CHAR16, EFI global GUID). Prints "V name size: bytes..." (up
; to 128 bytes as dwords). Uses the entry's frame. Keeps ebx, esi, edi.
dumpvar:
        push    eax
        PRINT   s_var
        mov     eax, [esp]
        push    eax
        push    esi
        call    con_out
        add     esp, 8
        mov     dword [VSIZE], 512
        lea     eax, [VBUF]
        push    eax                             ; Data
        lea     eax, [VSIZE]
        push    eax                             ; &DataSize
        lea     eax, [VATTR]
        push    eax                             ; &Attributes
        lea     eax, [edi + (g_global - entry.here)]
        push    eax
        push    dword [esp + 16]                ; VariableName
        mov     eax, [esi + 0x38]               ; RuntimeServices
        call    [eax + 0x30]                    ; GetVariable
        add     esp, 20
        pop     ecx
        push    eax
        PRINT   s_sp
        pop     eax
        push    eax
        HEX     eax                             ; status
        pop     eax
        test    eax, eax
        jnz     .out
        PRINT   s_sp
        HEX     dword [VSIZE]
        PRINT   s_colon
        push    ebx
        xor     ebx, ebx
.d:     cmp     ebx, [VSIZE]
        jae     .dd
        cmp     ebx, 128
        jae     .dd
        PRINT   s_sp
        HEX     dword [VBUF + ebx]
        add     ebx, 4
        jmp     .d
.dd:    pop     ebx
.out:   PRINT   s_nl
        ret

%include "con.inc"

s_st:   dw      __utf16__('ConDiag ST ConInH ConIn ConOutH ConOut ErrH Err: '), 0
s_to:   dw      __utf16__('TO '), 0
s_mode: dw      __utf16__(' mode(max cur attr col row)'), 0
s_gop:  dw      __utf16__(' GOP'), 0
s_gm:   dw      __utf16__('GOP mode(max cur w h fmt line) '), 0
s_fb:   dw      __utf16__(' fb(base size) '), 0
s_blt:  dw      __utf16__('Blt VideoFill: '), 0
s_var:  dw      __utf16__('V '), 0
s_gc:   dw      __utf16__('GC SetMode(0): '), 0
s_end:  dw      __utf16__('ConDiag done'), 13, 10, 0
s_sp:   dw      ' ', 0
s_colon: dw     ':', 0
s_nl:   dw      13, 10, 0
s_hello: dw     __utf16__('GRAPHICS CONSOLE DIRECT: HELLO FROM CONDIAG'), 13, 10, 0
v_conout:       dw      __utf16__('ConOut'), 0
v_conoutdev:    dw      __utf16__('ConOutDev'), 0
v_conin:        dw      __utf16__('ConIn'), 0
v_conindev:     dw      __utf16__('ConInDev'), 0
v_errout:       dw      __utf16__('ErrOut'), 0
        align 4
g_txtout:                                       ; 387477C2-69C7-11D2-8E39-00A0C969723B
        dd      0x387477c2
        dw      0x69c7, 0x11d2
        db      0x8e, 0x39, 0x00, 0xa0, 0xc9, 0x69, 0x72, 0x3b
g_gop:                                          ; 9042A9DE-23DC-4A38-96FB-7ADED080516A
        dd      0x9042a9de
        dw      0x23dc, 0x4a38
        db      0x96, 0xfb, 0x7a, 0xde, 0xd0, 0x80, 0x51, 0x6a
g_global:                                       ; 8BE4DF61-93CA-11D2-AA0D-00E098032B8C
        dd      0x8be4df61
        dw      0x93ca, 0x11d2
        db      0xaa, 0x0d, 0x00, 0xe0, 0x98, 0x03, 0x2b, 0x8c
