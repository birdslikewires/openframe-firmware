"""Mutations for countdown.inc (the shell's startup.nsh countdown made 0 in memory):
    venv/bin/python -I tools/emu_countdown.py --mutate tools/mutations/countdown.py of1_efi_o2.img of2_efi_cisco.img
Not caught on purpose, so not listed: dropping the read-back (the emulated memory always takes the
write); dropping the cld (the direction flag is clear whenever the firmware calls a notify)."""
MUTATIONS = [
    ('no FilePath check', '        test    esi, esi\n        jz      .no\n        cmp     dword [esi], 0x00140604',
     '        test    esi, esi\n        jmp     .no\n        cmp     dword [esi], 0x00140604'),
    ('file GUID not checked', '        repe    cmpsb\n        pop     edi\n        jne     .no', '        repe    cmpsb\n        pop     edi\n        nop\n        nop'),
    ('ImageSize not checked', '        jb      .next', '        nop\n        nop'),
    ('function not checked', '        test    eax, eax\n        jnz     .found', '        test    eax, eax\n        jmp     .found'),
    ('second build never tried', '.next:  add     edx, 8', '.next:  jmp     .no'),
    ('runs skipped wrongly', '        add     edx, ecx\n        jmp     .skip', '        add     edx, ecx\n        inc     edx\n        jmp     .skip'),
    ('only the first operand', '        mov     byte [eax + SHL_CD_B], 0        ; and the same for the fallback', '        nop'),
    ('only the fallback', '        mov     byte [eax + SHL_CD_A], 0        ; push 0 (was 0x32): no countdown', '        nop'),
    ('operand one byte out', '        mov     byte [eax + SHL_CD_A], 0        ; push 0 (was 0x32): no countdown\n        mov     byte [eax + SHL_CD_B], 0',
     '        mov     byte [eax + SHL_CD_A - 1], 0\n        mov     byte [eax + SHL_CD_B], 0'),
    ('success reported for a shell left alone', '.no:    xor     eax, eax', '.no:    mov     eax, 1'),
    ('esi not kept', '.out:   pop     edi\n        pop     esi', '.out:   pop     edi\n        pop     ecx'),
]
