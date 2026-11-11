"""Mutations for screentrace.inc's control-transfer / port-status hook (26.11.96):
    venv/bin/python -I tools/mutate.py tools/mutations/ctl.py --base BASE --version 26.11.96 \\
        --define TIMING --define PROFILE --define USBTRACE --define SCREEN --mode timing,profile,usbtrace,screen"""
F = 'driver/screentrace.inc'
MUTATIONS = [
    ('6 arguments passed on, not 7', F, '        mov     ecx, 7\n.cp:', '        mov     ecx, 6\n.cp:'),
    ('result not returned to the caller', F, '        mov     [esp + 28], eax                 ; returned', '        nop                                     ; returned'),
    ('wValue and wIndex swapped', F, '        movzx   eax, word [esp + 56]\n        mov     [edx + 12], eax', '        movzx   eax, word [esp + 52]\n        mov     [edx + 12], eax'),
    ('time in the wrong unit', F, '        sub     eax, edi\n        shr     eax, 10', '        sub     eax, edi\n        shr     eax, 9'),
    ('status byte not read', F, '        mov     ah, [edx + 0xe14]', '        mov     ah, [edx + 0xe15]'),
    ('ring of 8, not 4', F, '        inc     dword [ebp + (st_nctl - .h)]\n        and     edx, 3', '        inc     dword [ebp + (st_nctl - .h)]\n        and     edx, 7'),
    ('ring drawn newest first', F, '        add     esi, edi\n        and     esi, 3', '        sub     esi, edi\n        and     esi, 3'),
    ('error log: the last 7, not 8', F, '        sub     edi, 8                          ; the last 8', '        sub     edi, 7                          ; the last 8'),
    ('wrong timeout field', F, '        movzx   eax, word [esi + 0xe12]', '        movzx   eax, word [esi + 0xe10]'),
    ('port read from the wrong argument', F, '        movzx   eax, byte [esp + 44]\n        mov     [ebp + (st_rhs - .h) + 4], eax', '        movzx   eax, byte [esp + 48]\n        mov     [ebp + (st_rhs - .h) + 4], eax'),
    ('install: fill target not checked', F, '        cmp     eax, [edx + 4]\n        jne     .tno', '        cmp     eax, [edx + 4]\n        nop\n        nop'),
    ('install: stub address written one byte early', F, '        pop     dword [esi + eax + 3]', '        pop     dword [esi + eax + 2]'),
    ('install: EHCI and UHCI control swapped', F, '        dd      0x7401, 0x6ee4, 0x25, st_ctl_e - st_blob', '        dd      0x7401, 0x6ee4, 0x25, st_ctl_u - st_blob'),
    ('install: only 3 tables checked', F, '        cmp     ecx, 4\n        jb      .tc', '        cmp     ecx, 3\n        jb      .tc'),
    ('UHCI controller drawn as EHCI', F, '        mov     al, 0x10\n.t:', '        mov     al, 0x30\n.t:'),
]
