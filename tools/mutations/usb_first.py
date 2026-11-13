"""Mutations for usb_first.inc (a USB drive with startup.nsh is the shell's fs0). Each is
(label, old, new), or (label, [(old, new), ...]) for several changes at once:
    venv/bin/python -I tools/emu_usbfirst.py --mutate tools/mutations/usb_first.py
Not caught on purpose, so not listed: dropping the read-back of the patch (the emulated memory
always takes the write); usbf_scan going on without the want flag (it is only ever called, and its
notify only registered, once that is set); either of the two guards against
a zero-length device-path node alone (the length check and the 64-node limit each stop it), so one
mutation takes out both."""
MUTATIONS = [
    ('not once per boot', '        cmp     byte [edi + (usbf_done - .here)], 0\n        jne     .none', ''),
    ('plain USB node not recognised', '        cmp     byte [esi + 1], 0x05', '        cmp     byte [esi + 1], 0x55'),
    ('USB class node not recognised', '        cmp     byte [esi + 1], 0x0f', '        cmp     byte [esi + 1], 0x5f'),
    ('volumes opened on non-USB disks', '        jz      .fnext\n        UFCOUNT 1', '        nop\n        nop\n        UFCOUNT 1'),
    ('found although Open failed', '        jnz     .fclose\n        mov     eax, [ebp - 20]', '        nop\n        nop\n        mov     eax, [ebp - 20]'),
    ('found although Open gave no file', '        jz      .fclose\n        mov     dword [ebp - 24], 1', '        nop\n        nop\n        mov     dword [ebp - 24], 1'),
    ('file not closed', '        push    eax                             ; file->Close(file)\n        call    [eax + 0x0c]\n        add     esp, 4', ''),
    ('root not closed', '        mov     eax, [ebp - 16]                 ; root->Close(root)\n        push    eax\n        call    [eax + 0x0c]\n        add     esp, 4', ''),
    ('opened for writing', '        push    0\n        push    1\n        lea     ecx, [edi + (usbf_name - .here)]', '        push    0x80000000\n        push    3\n        lea     ecx, [edi + (usbf_name - .here)]'),
    ('shell changed without startup.nsh', '.found: cmp     dword [ebp - 24], 0\n        je      .free', '.found: cmp     dword [ebp - 24], 0\n        nop\n        nop'),
    ('notify not registered when the shell is not loaded yet', '        test    eax, eax\n        jz      .later', '        test    eax, eax\n        jmp     .free'),
    ('unregistered event not closed', '        push    dword [ebp - 36]                ; not registered: the event is no use\n        call    [ebx + 0x44]                    ; CloseEvent\n        add     esp, 4', ''),
    ('a failed CreateEvent ignored', '        call    [ebx + 0x34]\n        add     esp, 20\n        test    eax, eax\n        jnz     .free', '        call    [ebx + 0x34]\n        add     esp, 20\n        test    eax, eax\n        nop\n        nop'),
    ('FilePath node type not checked', '        cmp     dword [esi], 0x00140604         ; type 4, subtype 6, length 20\n        jne     .no', '        cmp     dword [esi], 0x00140604         ; type 4, subtype 6, length 20\n        nop\n        nop'),
    ('no FilePath not checked', '        mov     esi, [ebx + 0x10]               ; FilePath\n        test    esi, esi\n        jz      .no', '        mov     esi, [ebx + 0x10]               ; FilePath\n        test    esi, esi\n        nop\n        nop'),
    ('file GUID not checked', '        repe    cmpsb\n        pop     edi\n        jne     .no\n        UFSCOUNT 9', '        repe    cmpsb\n        pop     edi\n        nop\n        nop\n        UFSCOUNT 9'),
    ('ImageSize not checked', '        cmp     dword [ebx + 0x28], USBF_CMP_RVA + USBF_CMP_LEN   ; ImageSize (low half: IA32)\n        jb      .no', '        cmp     dword [ebx + 0x28], USBF_CMP_RVA + USBF_CMP_LEN   ; ImageSize (low half: IA32)\n        nop\n        nop'),
    ('function bytes not compared', '        pop     edi\n        pop     esi\n        jne     .no\n        UFSUNCOUNT 9', '        pop     edi\n        pop     esi\n        nop\n        nop\n        UFSUNCOUNT 9'),
    ('only half the function compared', '        mov     ecx, USBF_CMP_LEN\n        cld\n        repe    cmpsb\n        pop     edi\n        pop     esi', '        mov     ecx, USBF_CMP_LEN / 2\n        cld\n        repe    cmpsb\n        pop     edi\n        pop     esi'),
    ('first sequence not changed', '        mov     dword [esi + USBF_A], 0x940fd284        ; test dl, dl; sete dl; add edx, edx\n        mov     dword [esi + USBF_A + 3], 0xd201c294    ;  (84 D2 0F 94 C2 01 D2)', ''),
    ('second sequence not changed', '        mov     dword [esi + USBF_B], 0x940fc084        ; test al, al; sete al; add eax, eax\n        mov     dword [esi + USBF_B + 3], 0xc001c094    ;  (84 C0 0F 94 C0 01 C0)', ''),
    ('first sequence written one byte out', '        mov     dword [esi + USBF_A], 0x940fd284        ; test dl, dl; sete dl; add edx, edx\n        mov     dword [esi + USBF_A + 3], 0xd201c294', '        mov     dword [esi + USBF_A + 1], 0x940fd284        ; test dl, dl; sete dl; add edx, edx\n        mov     dword [esi + USBF_A + 4], 0xd201c294'),
    ('stock bytes wrong in the copy', '        db      0x14, 0x8b, 0x56, 0x14', '        db      0x14, 0x8b, 0x56, 0x15'),
    ('shell GUID wrong', '        dd      0xc57ad6b7', '        dd      0xc57ad6b8'),
    ('notify scans with the wrong context', '        push    dword [esp + 8]\n        call    usbf_scan', '        push    dword [esp + 4]\n        call    usbf_scan'),
    ('scan list not freed', '        call    [ebx + 0x30]                    ; FreePool\n        add     esp, 4\n.none:  movzx', '        add     esp, 0\n        add     esp, 4\n.none:  movzx'),
    ('file system list not freed', '        call    [ebx + 0x30]                    ; FreePool(buffer)', '        add     esp, 0'),
    ('zero-length node not stopped (length check and node limit both off)', [
        ('        cmp     edx, 4\n        jb      .wbad', '        cmp     edx, 0\n        jb      .wbad'),
        ('        dec     dword [esp]\n        jz      .wbad', '        dec     dword [esp]\n        nop\n        nop')]),
]
