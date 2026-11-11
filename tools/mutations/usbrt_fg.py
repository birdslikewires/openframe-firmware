"""Mutations for usbrt.inc's F (READ CAPACITY(16) after an FFFFFFFF answer) and G (CErr = 3):
    venv/bin/python -I tools/mutate.py tools/mutations/usbrt_fg.py --base BASE --version 26.11.88"""
F = 'driver/usbrt.inc'
MUTATIONS = [
    ('F: condition inverted', F, '        cmp     dword [edx], 0xffffffff         ; last block "FFFFFFFF": over 2 TiB\n        jne     .out',
     '        cmp     dword [edx], 0xffffffff         ; last block "FFFFFFFF": over 2 TiB\n        je      .out'),
    ('F: a failed READ CAPACITY(10) not checked', F, '        test    eax, eax\n        jz      .out\n        mov     esi, [ebx + 0x98f0]             ; gUsbData',
     '        test    eax, eax\n        nop\n        nop\n        mov     esi, [ebx + 0x98f0]             ; gUsbData'),
    ('F: wrong opcode', F, '        mov     word [ecx], 0x109e', '        mov     word [ecx], 0x109f'),
    ('F: wrong service action', F, '        mov     word [ecx], 0x109e', '        mov     word [ecx], 0x119e'),
    ('F: allocation length 31', F, '        mov     byte [ecx + 13], 32', '        mov     byte [ecx + 13], 31'),
    ('F: command length 12', F, '        mov     byte [esi + 0xdea], 16', '        mov     byte [esi + 0xdea], 12'),
    ('F: data out', F, '        mov     byte [esi + 0xdeb], 0x80        ; data in', '        mov     byte [esi + 0xdeb], 0x00        ; data in'),
    ('F: data length 8', F, '        mov     dword [esi + 0xdf0], 32', '        mov     dword [esi + 0xdf0], 8'),
    ('F: answer not put back', F, "        pop     dword [edx]                     ; READ CAPACITY(10)'s answer back", "        pop     ecx                             ; READ CAPACITY(10)'s answer back"),
    ('F: READ CAPACITY(16) result returned', F, '        pop     eax                             ; and its result', '        pop     ecx                             ; and its result'),
    ('F: command buffer not set again', F, '        mov     [esi + 0xde6], ecx\n', '        nop\n'),
    ('F: old command bytes not cleared', F, '        mov     [ecx + 8], eax\n', '        nop\n'),
    ('F: signature 2 one byte short', F, '        lea     ecx, [edi + (urt_sig_f2 - .here)]\n        mov     edx, 21', '        lea     ecx, [edi + (urt_sig_f2 - .here)]\n        mov     edx, 20'),
    ('F: call to the stub one byte off', F, '        mov     dword [esi + 0x4a5c], F_RVA + (f_entry - f_blob) - 0x4a60\n', '        mov     dword [esi + 0x4a5c], F_RVA + (f_entry - f_blob) - 0x4a5f\n'),
    ('F: stub base not stored', F, '        mov     [esi + F_RVA + (f_base - f_blob)], esi\n', '        nop\n'),
    ('G: only four checked', F, '        cmp     ecx, 5\n        jb      .g_chk', '        cmp     ecx, 4\n        jb      .g_chk'),
    ('G: only four changed', F, '        cmp     ecx, 5\n        jb      .g_set', '        cmp     ecx, 4\n        jb      .g_set'),
    ('G: CErr 2, not 3', F, '        or      byte [esi + eax + 4], 0x0c      ; the token', '        or      byte [esi + eax + 4], 0x08      ; the token'),
    ('G: wrong site', F, 'urt_g_rva:      dd      0x6f95, 0x6fc4, 0x6fcd, 0x6ffa, 0x7003', 'urt_g_rva:      dd      0x6f95, 0x6fc4, 0x6fcd, 0x6ffa, 0x7004'),
]
