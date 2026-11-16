"""Mutations for kbdtime.inc (the keyboard-timing development build):
    venv/bin/python -I tools/mutate.py tools/mutations/kbdtime.py --base /tmp/base.bin --version 26.11.31-devN \\
        --define KBDTIME --mode kbdtime
Not listed, as no scenario can reach it: kt_scan's check that the count isn't 0 before stamping I
(LocateHandleBuffer returns EFI_NOT_FOUND, not an empty buffer, when there are none); and the
KT_MAXPOLLS limit, which only matters if the clock stops (the emulated one moves with each Stall)."""
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
    ('no cap on keys read', K, 'KT_MAXKEYS      equ     200', 'KT_MAXKEYS      equ     300'),
    ('scan code and character swapped', K, '        movzx   eax, word [ebp - 4]             ; the first key', '        movzx   eax, word [ebp - 2]             ; the first key'),
    ('shell not stamped', K, '        mov     edx, KT_S\n        call    kt_stamp', '        nop'),
    ('ReadyToBoot not stamped', U, '        mov     edx, KT_R\n        call    kt_stamp', '        nop'),
    ('not hooked at the shell', 'driver/img_patch.inc', '        call    kt_shell', '        nop'),
    ('no window: straight on after the waiting keys', K, '        cmp     eax, KT_WINDOW\n        jae     .done', '        cmp     eax, KT_WINDOW\n        jmp     .done'),
    ('window 3 s', K, 'KT_WINDOW       equ     2000', 'KT_WINDOW       equ     3000'),
    ('window 1 s', K, 'KT_WINDOW       equ     2000', 'KT_WINDOW       equ     1000'),
    ('no Stall between polls', K, '        call    [eax + 0x88]                    ; BS->Stall', '        nop'),
    ('first key not timed', K, '        mov     edx, KT_P\n        call    kt_stamp', '        nop'),
    ('W counts nothing', K, '        mov     eax, esi                        ; W: those already waiting', '        xor     eax, eax'),
    ('count not capped at 99', K, '        dec     eax                             ; the largest that fits', '        add     eax, 1000'),
]
