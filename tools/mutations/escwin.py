"""Mutations for escwin.inc (Esc at boot reliably stops at the shell):
    venv/bin/python -I tools/mutate.py tools/mutations/escwin.py --base /tmp/base.bin --version 26.11.31-devN
Not listed, as no scenario can tell: the ESC_MAXREAD cap's exact value above 64 (a run of more than
64 non-Esc keys before an Esc is only read over two polls)."""
E = 'driver/escwin.inc'
MUTATIONS = [
    ('window waited without a keyboard', E, "        cmp     byte [edi + (ew_usb - .here)], 0\n        je      .none                           ; no USB keyboard: no window",
     "        cmp     byte [edi + (ew_usb - .here)], 0\n        nop\n        nop"),
    ('no window at all', E, '        cmp     esi, ESC_MAXPOLLS\n        jae     .none', '        cmp     esi, ESC_MAXPOLLS\n        jmp     .none'),
    ('timer ignored', E, '        call    [ebx + 0x48]                    ; BS->CheckEvent: EFI_SUCCESS once it has gone off\n        add     esp, 4\n        test    eax, eax\n        jz      .none',
     '        call    [ebx + 0x48]                    ; BS->CheckEvent: EFI_SUCCESS once it has gone off\n        add     esp, 4\n        test    eax, eax\n        nop\n        nop'),
    ('window 2 s', E, 'ESC_WINDOW      equ     10000000', 'ESC_WINDOW      equ     20000000'),
    ('window 0.5 s', E, 'ESC_WINDOW      equ     10000000', 'ESC_WINDOW      equ     5000000'),
    ('no cap when the timer never goes off', E, 'ESC_MAXPOLLS    equ     300', 'ESC_MAXPOLLS    equ     3000'),
    ('fallback window 2 s', E, 'ESC_FALLBACK    equ     100', 'ESC_FALLBACK    equ     200'),
    ('no Stall between polls', E, '        call    [ebx + 0x88]                    ; BS->Stall', '        nop'),
    ('Esc not handed over', E, '        mov     dword [eax], ESC_SCAN           ; ScanCode SCAN_ESC, UnicodeChar 0\n        xor     eax, eax',
     '        mov     dword [eax], ESC_SCAN           ; ScanCode SCAN_ESC, UnicodeChar 0\n        mov     eax, 0x80000006'),
    ('any key taken for Esc', E, '        cmp     word [ebp - 4], ESC_SCAN\n        je      .yes', '        cmp     word [ebp - 4], ESC_SCAN\n        jmp     .yes'),
    ('only the first waiting key looked at', E, '        je      .yes\n        loop    .read', '        je      .yes\n        nop\n        nop'),
    ('later calls not passed through', E, '.pass:  jmp     [eax + (ew_orig - .here)]', '.pass:  mov     eax, 0x80000006\n        ret'),
    ('timer not closed', E, '        call    [ebx + 0x44]                    ; BS->CloseEvent\n        add     esp, 4\n        mov     dword [edi + (ew_timer - .here)], 0',
     '        add     esp, 4\n        mov     dword [edi + (ew_timer - .here)], 0'),
    ('timer kept although SetTimer failed', E, '        jz      .keep\n        push    dword [ebp - 20]', '        jmp     .keep\n        push    dword [ebp - 20]'),
    ('USB node not recognised', E, '        cmp     al, 5\n        je      .usb', '        cmp     al, 5\n        jmp     .adv'),
    ('any messaging node taken for USB', E, '        cmp     al, 3\n        jne     .adv', '        cmp     al, 3\n        jmp     .usb'),
    ('ConIn not checked', E, '        mov     eax, [eax + 0x24]               ; ST->ConIn\n        test    eax, eax\n        jz      .out',
     '        mov     eax, [eax + 0x24]               ; ST->ConIn'),
    ('not hooked at the shell', 'driver/img_patch.inc', '        call    ew_shell                        ; Esc reliably stops at it (escwin.inc)', '        nop'),
    ('scan not started at the entry', 'driver/ucode_late.asm', '        call    ew_start                        ; Esc at boot: the shell, reliably (escwin.inc)', '        nop'),
]
