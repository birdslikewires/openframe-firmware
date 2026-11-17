"""Mutations for textmode.inc (the text console 100 columns wide):
    venv/bin/python -I tools/mutate.py tools/mutations/textmode.py --base /tmp/base.bin --version 26.11.31-devN"""
T = 'driver/textmode.inc'
MUTATIONS = [
    ('never patched', T, '        mov     dword [esi + CW_TABLE], CW_COLS\n        cmp', '        nop\n        cmp'),
    ('file GUID not checked', T, '        repe    cmpsb\n        pop     edi\n        pop     esi\n        pop     ecx\n        jne     .no',
     '        repe    cmpsb\n        pop     edi\n        pop     esi\n        pop     ecx\n        nop\n        nop'),
    ('ImageSize not checked', T, '.sig:   cmp     dword [ebx + 0x28], CW_SIZE     ; ImageSize (UINT64)\n        jne     .no',
     '.sig:   cmp     dword [ebx + 0x28], CW_SIZE     ; ImageSize (UINT64)\n        nop\n        nop'),
    ('QueryMode reference not checked', T, '        cmp     [esi + CW_REF1], eax\n        jne     .no', '        cmp     [esi + CW_REF1], eax\n        nop\n        nop'),
    ('rest of the table not checked', T, '        repe    cmpsb\n        pop     edi\n        pop     esi\n        jne     .no', '        repe    cmpsb\n        pop     edi\n        pop     esi\n        nop\n        nop'),
    ('already 100 not taken as done', T, '        cmp     eax, CW_COLS\n        je      .yes                            ; already', '        cmp     eax, CW_COLS\n        nop\n        nop'),
    ('any first value patched', T, '        cmp     eax, 80\n        jne     .no', '        cmp     eax, 80\n        nop\n        nop'),
    ('wrong column count', T, 'CW_COLS         equ     100', 'CW_COLS         equ     99'),
    ('done not recorded', 'driver/img_patch.inc', '        mov     [edi + (cw_done - .here)], al', '        nop'),
    ('not counted in .both', 'driver/img_patch.inc', '        cmp     byte [edi + (cw_done - .here)], 0\n        je      .b0', '        nop'),
]
