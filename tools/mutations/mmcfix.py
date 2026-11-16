"""Mutations for BlwMmcFix.efi's already-fixed check (driver/blwmmcfix.asm):
    venv/bin/python -I tools/emu_mmcfix.py --mutate tools/mutations/mmcfix.py of1_efi_o2.img MmcTest.efi [RELEASE.bin ...]
Not listed, as no scenario can catch them: the near-jmp (e9) form, which no release's wrapper uses."""
MUTATIONS = [
    ('success whatever it found', '        or      esi, [ebp - 8]', '        or      esi, 1'),
    ('already fixed ignored', '        or      esi, [ebp - 8]', '        or      esi, esi'),
    ('original not checked', '        call    mmc_sig_match                   ; the buggy driver\'s ReadBlocks',
     '        mov     eax, 1'),
    ('empty slot not refused', '        mov     eax, [esi + 5 + eax]            ; the original, from the wrapper\'s slot\n        test    eax, eax\n        jz      .no',
     '        mov     eax, [esi + 5 + eax]            ; the original, from the wrapper\'s slot'),
    ('split not compared', '        mov     ecx, SPLIT_HEAD', '        xor     ecx, ecx'),
    ('piece size not compared', 'SPLIT_HEAD      equ     21', 'SPLIT_HEAD      equ     15'),
    ('short jmp target one out', '        lea     esi, [esi + 14 + eax]', '        lea     esi, [esi + 15 + eax]'),
    ('call prefix not checked', '        cmp     dword [esi], 0x000000e8         ; e8 00 00 00 00: call $+5\n        jne     .no',
     '        cmp     dword [esi], 0x000000e8         ; e8 00 00 00 00: call $+5\n        nop\n        nop'),
    ('load prefix not checked', '        cmp     dword [esi + 4], 0x808b5800     ; 58: pop eax; 8b 80: mov eax, [eax + disp32]\n        jne     .no',
     '        cmp     dword [esi + 4], 0x808b5800     ; 58: pop eax; 8b 80: mov eax, [eax + disp32]\n        nop\n        nop'),
    ('slot one dword out', '        mov     eax, [esi + 5 + eax]            ; the original', '        mov     eax, [esi + 9 + eax]            ; the original'),
    ('fixed counted only for the first handle', '.next:  inc     dword [ebp - 16]\n        jmp     .loop\n\n.free:  push    dword [ebp - 8]\n        call    [ebx + 0x30]                    ; BS->FreePool\n        add     esp, 4\n.out:   mov     eax, esi\n        pop     edi\n        pop     esi\n        pop     ebx\n        leave\n        ret\n\n; UINT32 is_fixed',
     '.next:  jmp     .free\n\n.free:  push    dword [ebp - 8]\n        call    [ebx + 0x30]                    ; BS->FreePool\n        add     esp, 4\n.out:   mov     eax, esi\n        pop     edi\n        pop     esi\n        pop     ebx\n        leave\n        ret\n\n; UINT32 is_fixed'),
    ('esi not kept by is_fixed', '.out:   pop     edi\n        pop     esi\n        pop     ebx\n        ret\n', '.out:   pop     edi\n        pop     ecx\n        pop     ebx\n        ret\n'),
]
