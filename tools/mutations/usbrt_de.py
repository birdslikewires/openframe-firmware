"""Mutations for usbrt.inc's D (the real Bulk-Only reset) and E (20 s transfer timeouts):
    venv/bin/python -I tools/mutate.py tools/mutations/usbrt_de.py --base BASE --version 26.11.92"""
F = 'driver/usbrt.inc'
MUTATIONS = [
    ('D: shorter signature check', F, '        mov     edx, 29\n', '        mov     edx, 28\n'),
    ('D: signature result ignored', F, '        mov     edx, 29\n        call    .cmp\n        jne     .e\n',
     '        mov     edx, 29\n        call    .cmp\n        nop\n        nop\n'),
    ('D: wrong request type in the reset', F, '0x57, 0x68, 0x21, 0xff', '0x57, 0x68, 0x22, 0xff'),
    ('D: copies 10 of 11 bytes', F, '        mov     ecx, 11\n', '        mov     ecx, 10\n'),
    ('D: patched at the wrong place', F, '        lea     eax, [esi + 0x4388]\n        lea     esi,', '        lea     eax, [esi + 0x4389]\n        lea     esi,'),
    ('E: only three sites checked', F, '        cmp     ecx, 4\n        jb      .e_chk', '        cmp     ecx, 3\n        jb      .e_chk'),
    ('E: only three sites changed', F, '        cmp     ecx, 4\n        jb      .e_set', '        cmp     ecx, 3\n        jb      .e_set'),
    ('E: wrong timeout', F, 'URT_TIMEOUT_MS  equ     20000', 'URT_TIMEOUT_MS  equ     20001'),
    ('E: wrong site address', F, 'dd      0x3382, 0x4a0e, 0x4eba, 0x57fa', 'dd      0x3382, 0x4a0e, 0x4eba, 0x57fb'),
    ('E: default checked against the override sig', F, '        jz      .e_one\n', '        jnz     .e_one\n'),
    ('E: value written one byte off', F, '        mov     word [esi + eax + 7], URT_TIMEOUT_MS\n', '        mov     word [esi + eax + 6], URT_TIMEOUT_MS\n'),
    ('image-size guard too low', F, '0x28], URT_IMAGE_SIZE', '0x28], 0x5000'),
]
