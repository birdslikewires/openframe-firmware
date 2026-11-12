"""Mutations for screentrace.inc's transfer hook (the function tables' control, bulk and interrupt entries):
    venv/bin/python -I tools/mutate.py tools/mutations/ctl.py --base BASE --version 26.11.26-dev1 \\
        --define TIMING --define PROFILE --define USBTRACE --define SCREEN --mode timing,profile,usbtrace,screen"""
F = 'driver/screentrace.inc'
MUTATIONS = [
    ('6 arguments passed on, not 7', F, '        push    7\n        pop     ecx', '        push    6\n        pop     ecx'),
    ('result not returned to the caller', F, '        mov     [esp + 8 + 28], eax', '        nop'),
    ('wValue and wIndex swapped', F, '        mov     ax, [esp + 56]\n        mov     [edx + 6], ax', '        mov     ax, [esp + 52]\n        mov     [edx + 6], ax'),
    ('time in the wrong unit', F, '        sub     eax, edi\n        shr     eax, 10', '        sub     eax, edi\n        shr     eax, 9'),
    ('status byte at the start not read', F, '        mov     ah, [edx + 0xe14]', '        mov     ah, [edx + 0xe15]'),
    ('status byte not updated at the end', F, '        mov     [ebx + 1], cl', '        nop'),
    ('ring of 8, not 4', F, '        and     edx, 3\n        shl     edx, 4', '        and     edx, 7\n        shl     edx, 4'),
    ('ring drawn newest first', F, '        add     esi, edi\n        and     esi, 3', '        sub     esi, edi\n        and     esi, 3'),
    ('no not-returned marker', F, '        mov     dword [edx + 12], 0xeeee', '        mov     dword [edx + 12], 0xeeef'),
    ('not drawn as it starts', F, '        push    edx                             ; (the entry: arguments now at [esp+44])\n        call    .draw',
     '        push    edx                             ; (the entry: arguments now at [esp+44])\n        nop'),
    ('device port not recorded', F, '        mov     ah, [ecx + 9]', '        mov     ah, [ecx + 8]'),
    ('bulk shown as control', F, 'st_kinds:       db      0x31, 0x11, 0x32', 'st_kinds:       db      0x31, 0x11, 0x31'),
    ('install: fill target not checked', F, '        cmp     eax, [edx + 4]\n        jne     .tno', '        cmp     eax, [edx + 4]\n        nop\n        nop'),
    ('install: stub address written one byte early', F, '        pop     dword [esi + eax + 3]', '        pop     dword [esi + eax + 2]'),
    ('install: EHCI and UHCI control swapped', F, '        dd      0x7401, 0x6ee4, 0x25, st_ctl_e - st_blob', '        dd      0x7401, 0x6ee4, 0x25, st_ctl_u - st_blob'),
    ('install: only 5 tables checked', F, '        cmp     ecx, 6\n        jb      .tc', '        cmp     ecx, 5\n        jb      .tc'),
    ('install: interrupt hooks swapped', F, '        dd      0x740f, 0x72b7, 0x2d, st_int_e - st_blob', '        dd      0x740f, 0x72b7, 0x2d, st_int_u - st_blob'),
    ('EHCI control shown as UHCI control', F, 'st_kinds:       db      0x31, 0x11', 'st_kinds:       db      0x11, 0x11'),
]
