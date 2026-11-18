"""Mutations for splash.inc (our splash put into the display driver in memory). Each is (label, old,
new), or (label, [(old, new), ...]) for several changes at once:
    venv/bin/python -I tools/emu_splash.py --mutate tools/mutations/splash.py of1_efi_o2.img of2_efi_cisco.img \
        of2_openpeak.img of1_efi_openpeak_verizon.img of1_efi_telstra.img
Not caught on purpose, so not listed: dropping either read-back (the emulated memory always takes
the write); dropping a cld (the direction flag is clear whenever the firmware calls a notify)."""
MUTATIONS = [
    ('no FilePath check', '        test    esi, esi\n        jz      .no\n        cmp     dword [esi], 0x00140604',
     '        test    esi, esi\n        jmp     .no\n        cmp     dword [esi], 0x00140604'),
    ('file GUID not checked', '        repe    cmpsb\n        jne     .no\n        cmp     dword [ebx + 0x28]',
     '        repe    cmpsb\n        nop\n        nop\n        cmp     dword [ebx + 0x28]'),
    ('ImageSize not checked', '        jb      .no\n', '        nop\n        nop\n'),
    ('handlers not checked', '        call    mmc_sig_match                   ; the handlers that read the splash\n        add     esp, 8\n        test    eax, eax\n        jz      .code',
     '        call    mmc_sig_match                   ; the handlers that read the splash\n        add     esp, 8\n        test    eax, eax\n        nop\n        nop'),
    ('the older builds\' place never tried', '        test    eax, eax\n        jz      .code', '        test    eax, eax\n        jz      .no'),
    ('only the older builds\' place tried', '        lea     ebx, [edi + (gsp_code_rvas - .here)]', '        lea     ebx, [edi + (gsp_code_rvas - .here) + 4]'),
    ('the handlers\' places not stepped through', '        add     ebx, 4\n        lea     ecx, [edi + (gsp_code_sig - .here)]', '        lea     ecx, [edi + (gsp_code_sig - .here)]'),
    ('the end of the places not checked', '.code:  mov     eax, [ebx]                      ; each place the handlers can be (0 ends the list)\n        test    eax, eax\n        jz      .no',
     '.code:  mov     eax, [ebx]                      ; each place the handlers can be (0 ends the list)\n        test    eax, eax\n        nop\n        nop'),
    ('configuration not checked', '        jnz     .found', '        jmp     .found'),
    ('second configuration never tried', '        add     ebx, eax\n        jmp     .cfg', '        jmp     .no'),
    ('pixels where the numbers go', '        lea     esi, [edx + (gsp_data - .here) + 24]\n        movzx   eax, word [ebx + 4]',
     '        lea     esi, [edx + (gsp_data - .here) + 24]\n        movzx   eax, word [ebx + 2]'),
    ('numbers where the pixels go', '        lea     esi, [edx + (gsp_data - .here)] ; then the numbers\n        movzx   eax, word [ebx + 2]',
     '        lea     esi, [edx + (gsp_data - .here)] ; then the numbers\n        movzx   eax, word [ebx + 4]'),
    ('one byte of the splash short', '        mov     ecx, GSP_DATA_LEN - 24\n        cld\n        rep     movsb',
     '        mov     ecx, GSP_DATA_LEN - 25\n        cld\n        rep     movsb'),
    ('the old splash left after ours', '        xor     eax, eax\n        rep     stosb', '        xor     eax, eax'),
    ('zeros over the end marker', '        lea     ecx, [ebp + GSP_END_RVA]        ; zeros to the reserve\'s end',
     '        lea     ecx, [ebp + GSP_END_RVA + 6]    ; zeros to the reserve\'s end'),
    ('the numbers not written', '        mov     ecx, 24\n        rep     movsb', '        mov     ecx, 0\n        rep     movsb'),
    ('ebp not kept', '.out:   pop     edx\n        pop     ebp', '.out:   pop     edx\n        mov     ebp, edx'),
    ('success reported for a driver left alone', '.no:    xor     eax, eax', '.no:    mov     eax, 1'),
]
