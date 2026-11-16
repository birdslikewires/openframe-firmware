"""Mutations for kbdtime.inc (the keyboard-timing development build):
    venv/bin/python -I tools/mutate.py tools/mutations/kbdtime.py --base /tmp/base.bin --version 26.11.31-devN \\
        --define KBDTIME --mode kbdtime
Not listed, as no scenario can reach it: kt_scan's check that the count isn't 0 before stamping I
(LocateHandleBuffer returns EFI_NOT_FOUND, not an empty buffer, when there are none)."""
K, U = 'driver/kbdtime.inc', 'driver/ucode_late.asm'
MUTATIONS = [
    ('waiting keys not drained', K, '        cmp     esi, 1\n        jne     .read', '        cmp     esi, 1\n        jne     .done'),
    ('the shell told of the key', K, '        mov     eax, 0x80000006                 ; EFI_NOT_READY', '        xor     eax, eax                        ; EFI_NOT_READY'),
    ('later calls not passed through', K, '.pass:  jmp     [eax + (kt_orig - .here)]', '.pass:  mov     eax, 0x80000006\n        ret'),
    ('USB node not recognised', K, '        cmp     al, 5\n        je      .usb', '        cmp     al, 5\n        jmp     .adv'),
    ('any messaging node taken for USB', K, '        cmp     al, 3\n        jne     .adv', '        cmp     al, 3\n        jmp     .usb'),
    ('fields stamped again', K, '        cmp     byte [eax + edx], \'-\'\n        jne     .out', '        cmp     byte [eax + edx], \'-\'\n        nop\n        nop'),
    ('ConIn not checked', K, '        mov     eax, [eax + 0x24]               ; ST->ConIn\n        test    eax, eax\n        jz      .out',
     '        mov     eax, [eax + 0x24]               ; ST->ConIn'),
    ('no cap on keys read', K, '.read:  cmp     esi, 200', '.read:  cmp     esi, 300'),
    ('scan code and character swapped', K, '        movzx   eax, word [ebp - 4]             ; the first key', '        movzx   eax, word [ebp - 2]             ; the first key'),
    ('shell not stamped', K, '        mov     edx, KT_S\n        call    kt_stamp', '        nop'),
    ('ReadyToBoot not stamped', U, '        mov     edx, KT_R\n        call    kt_stamp', '        nop'),
    ('not hooked at the shell', 'driver/img_patch.inc', '        call    kt_shell', '        nop'),
    ('count not capped at 99', K, '        dec     eax                             ; the largest that fits', '        add     eax, 1000'),
]
