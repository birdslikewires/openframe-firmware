; sthex.inc: st_hex and its font, shared by screentrace.inc (inside the blob copied into SMM)
; and screentest.asm. Position-independent.

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
