; ScreenTest.efi: a shell app that lists every Graphics Output protocol instance (pixel format,
; colour masks, resolution, pixels a scan line, framebuffer base and size), then draws a test row
; of hex digits with st_hex (the screen-trace build's drawing code) at the top left of the first
; instance with a framebuffer, and waits 5 s so it can be seen. Reads only, apart from the pixels.
;
; Each instance prints as:
;   G <handle> <MaxMode> <Mode> <PixelFormat> <Hres> <Vres> <PixelsPerScanLine>
;     <FrameBufferBase lo> <hi> <FrameBufferSize> <red mask> <green mask> <blue mask>
; then "D <base used>" if it drew.

bits 32

%define COUNT   ebp - 4
%define HANDLES ebp - 8
%define INDEX   ebp - 12
%define GOP     ebp - 16
%define DREW    ebp - 20

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
        PRINT   s_sp
%endmacro

entry:
        push    ebp
        mov     ebp, esp
        sub     esp, 20
        push    ebx
        push    esi
        push    edi
        call    .here
.here:  pop     edi
        xor     eax, eax
        mov     [COUNT], eax
        mov     [HANDLES], eax
        mov     [INDEX], eax
        mov     [DREW], eax
        mov     esi, [ebp + 12]                 ; SystemTable
        mov     ebx, [esi + 0x3c]               ; BootServices
        lea     eax, [HANDLES]
        push    eax
        lea     eax, [COUNT]
        push    eax
        push    0
        lea     eax, [edi + (gop_guid - .here)]
        push    eax
        push    2
        call    [ebx + 0xa8]                    ; LocateHandleBuffer(ByProtocol, GOP)
        add     esp, 20
        test    eax, eax
        jz      .loop
        PRINT   s_none
        jmp     .ret
.loop:  mov     eax, [INDEX]
        cmp     eax, [COUNT]
        jae     .free
        mov     edx, [HANDLES]
        lea     ecx, [GOP]
        push    ecx
        lea     ecx, [edi + (gop_guid - .here)]
        push    ecx
        push    dword [edx + eax * 4]
        call    [ebx + 0x58]                    ; HandleProtocol
        add     esp, 12
        test    eax, eax
        jnz     .next
        PRINT   s_g
        mov     eax, [HANDLES]
        mov     ecx, [INDEX]
        HEX     dword [eax + ecx * 4]
        mov     eax, [GOP]
        mov     eax, [eax + 0x0c]               ; Mode
        test    eax, eax
        jz      .nl
        HEX     dword [eax]                     ; MaxMode
        mov     eax, [GOP]
        mov     eax, [eax + 0x0c]
        HEX     dword [eax + 4]                 ; Mode
        mov     eax, [GOP]
        mov     eax, [eax + 0x0c]
        mov     eax, [eax + 8]                  ; Info
        test    eax, eax
        jz      .nl
        HEX     dword [eax + 0x0c]              ; PixelFormat
        mov     eax, [GOP]
        mov     eax, [eax + 0x0c]
        mov     eax, [eax + 8]
        HEX     dword [eax + 4]                 ; Hres
        mov     eax, [GOP]
        mov     eax, [eax + 0x0c]
        mov     eax, [eax + 8]
        HEX     dword [eax + 8]                 ; Vres
        mov     eax, [GOP]
        mov     eax, [eax + 0x0c]
        mov     eax, [eax + 8]
        HEX     dword [eax + 0x20]              ; PixelsPerScanLine
        mov     eax, [GOP]
        mov     eax, [eax + 0x0c]
        HEX     dword [eax + 0x10]              ; FrameBufferBase lo
        mov     eax, [GOP]
        mov     eax, [eax + 0x0c]
        HEX     dword [eax + 0x14]              ; hi
        mov     eax, [GOP]
        mov     eax, [eax + 0x0c]
        HEX     dword [eax + 0x18]              ; FrameBufferSize
        mov     eax, [GOP]
        mov     eax, [eax + 0x0c]
        mov     eax, [eax + 8]
        HEX     dword [eax + 0x10]              ; red mask
        mov     eax, [GOP]
        mov     eax, [eax + 0x0c]
        mov     eax, [eax + 8]
        HEX     dword [eax + 0x14]              ; green mask
        mov     eax, [GOP]
        mov     eax, [eax + 0x0c]
        mov     eax, [eax + 8]
        HEX     dword [eax + 0x18]              ; blue mask
        ; draw with the first instance that has a framebuffer below 4 GB
        cmp     dword [DREW], 0
        jne     .nl
        mov     eax, [GOP]
        mov     ecx, [eax + 0x0c]               ; Mode
        cmp     dword [ecx + 0x14], 0
        jne     .nl
        mov     edx, [ecx + 0x10]
        test    edx, edx
        jz      .nl
        mov     ecx, [ecx + 8]                  ; Info
        lea     eax, [edi + (fb - .here)]
        mov     dword [eax], 'BLWF'
        mov     [eax + 4], edx
        mov     edx, [ecx + 0x20]
        mov     [eax + 8], edx
        mov     edx, [ecx + 4]
        mov     [eax + 12], edx
        mov     edx, [ecx + 8]
        mov     [eax + 16], edx
        push    8
        push    0x01234567
        push    8
        push    8
        push    eax
        call    st_hex
        add     esp, 20
        lea     eax, [edi + (fb - .here)]
        push    8
        push    0x89abcdef
        push    32
        push    8
        push    eax
        call    st_hex
        add     esp, 20
        mov     dword [DREW], 1
        PRINT   s_d
        lea     eax, [edi + (fb - .here)]
        HEX     dword [eax + 4]
.nl:    PRINT   s_nl
.next:  inc     dword [INDEX]
        jmp     .loop
.free:  push    dword [HANDLES]
        call    [ebx + 0x30]
        add     esp, 4
        cmp     dword [DREW], 0
        je      .ret
        push    5000000
        call    [ebx + 0x88]                    ; Stall 5 s: look at the top left
        add     esp, 4
.ret:   xor     eax, eax
        pop     edi
        pop     esi
        pop     ebx
        leave
        ret

%include "sthex.inc"
%include "con.inc"

        align   4, db 0
fb:             times 5 dd 0
gop_guid:                                       ; 9042A9DE-23DC-4A38-96FB-7ADED080516A
        dd      0x9042a9de
        dw      0x23dc, 0x4a38
        db      0x96, 0xfb, 0x7a, 0xde, 0xd0, 0x80, 0x51, 0x6a
s_g:    dw      __utf16__('G '), 0
s_d:    dw      __utf16__('D '), 0
s_sp:   dw      __utf16__(' '), 0
s_nl:   dw      13, 10, 0
s_none: dw      __utf16__('ScreenTest: no Graphics Output'), 13, 10, 0
