; textmode.inc: the firmware's text console fills the panel's width, from 26.11.31.
;
; The text console (the graphics console in CORE_DXE, 5AE3F37E) takes its text modes from a table
; in the core (RVA 0x163AC, 20 bytes a mode: columns, rows, width, height, number): 80x25 at
; 800x480, 80x50 at 1280x1024 (never offered), 100x31 at 800x600. On the OpenFrames' 800x480 panel
; it uses the first: 80 columns of its 8x19 glyphs are 640 pixels, so it centres them, 80 pixels in
; from each side. When the console starts it works out that margin, (width - columns * 8) / 2, and
; sizes its line buffer, columns * 608 bytes (8 x 19 pixels of 4 bytes), from the table; so with
; the first mode's columns made 100 before it starts, its text fills the width (100 x 8 = 800) and
; everything that asks (the console splitter, the shell's line wrapping) is told 100 columns.
; Seen only after Esc (escwin.inc): on a normal boot the display driver shows none of it.
;
; cw_patch_image changes that one dword in the core's loaded image (it is loaded long before this
; driver, so at the entry), and only in the core we know: its ImageSize, the two relocated
; references to the table in its QueryMode (they hold ImageBase + the table's RVA), and the whole
; table but the first dword as stock; that dword 80 (or already 100). If the image's FilePath has
; a firmware-volume file node, it must be the core's.

CW_TABLE        equ     0x163ac                 ; the mode table's RVA
CW_REF1         equ     0x5338                  ; QueryMode: mov ecx, [eax + table] (its disp32)
CW_REF2         equ     0x5340                  ;            mov eax, [eax + table + 4]
CW_SIZE         equ     0x5be80                 ; the core's ImageSize
CW_COLS         equ     100                     ; 800 / 8

; UINT32 cw_patch_image(EFI_LOADED_IMAGE_PROTOCOL *Li): 1 if Li is the core we know and its first
; text mode now has CW_COLS columns, else 0 (nothing changed). cdecl. Keeps ebx, esi, edi.
cw_patch_image:
        push    ebx
        push    esi
        push    edi
        call    .here
.here:  pop     edi
        mov     ebx, [esp + 16]                 ; Li
        mov     esi, [ebx + 0x10]               ; FilePath
        test    esi, esi
        jz      .sig
        mov     ecx, 8                          ; nodes looked at, at most
.node:  mov     eax, [esi]
        cmp     al, 0x7f                        ; the end: no file node
        je      .sig
        cmp     ax, 0x0604                      ; MEDIA_FW_VOL_FILEPATH: must be the core's
        jne     .adv
        push    ecx
        push    esi
        push    edi
        add     esi, 4
        lea     edi, [edi + (cw_core_guid - .here)]
        mov     ecx, 16
        cld
        repe    cmpsb
        pop     edi
        pop     esi
        pop     ecx
        jne     .no
        jmp     .sig
.adv:   movzx   eax, word [esi + 2]
        cmp     eax, 4
        jb      .no
        add     esi, eax
        loop    .node
.sig:   cmp     dword [ebx + 0x28], CW_SIZE     ; ImageSize (UINT64)
        jne     .no
        cmp     dword [ebx + 0x2c], 0
        jne     .no
        mov     esi, [ebx + 0x20]               ; ImageBase
        test    esi, esi
        jz      .no
        lea     eax, [esi + CW_TABLE]           ; QueryMode's two references to the table
        cmp     [esi + CW_REF1], eax
        jne     .no
        add     eax, 4
        cmp     [esi + CW_REF2], eax
        jne     .no
        push    esi                             ; the table, all but the first dword
        push    edi
        lea     esi, [esi + CW_TABLE + 4]
        lea     edi, [edi + (cw_table - .here) + 4]
        mov     ecx, 56
        cld
        repe    cmpsb
        pop     edi
        pop     esi
        jne     .no
        mov     eax, [esi + CW_TABLE]
        cmp     eax, CW_COLS
        je      .yes                            ; already
        cmp     eax, 80
        jne     .no
        mov     dword [esi + CW_TABLE], CW_COLS
        cmp     dword [esi + CW_TABLE], CW_COLS
        jne     .no
.yes:   mov     eax, 1
        jmp     .out
.no:    xor     eax, eax
.out:   pop     edi
        pop     esi
        pop     ebx
        ret

cw_done:        db 0
                align 4
cw_core_guid:                                   ; 5AE3F37E-4EAE-41AE-8240-35465B5E81EB CORE_DXE
        dd      0x5ae3f37e
        dw      0x4eae, 0x41ae
        db      0x82, 0x40, 0x35, 0x46, 0x5b, 0x5e, 0x81, 0xeb
cw_table:                                       ; the core's text modes, as stock
        dd      80, 25, 800, 480, 1
        dd      80, 50, 1280, 1024, 2
        dd      100, 31, 800, 600, 3
