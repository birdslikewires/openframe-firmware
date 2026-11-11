# Mutations for the on-screen USB trace (driver/screentrace.inc, timing.inc's SCREEN parts).
# venv/bin/python -I tools/mutate.py tools/mutations/screen.py --base <26.11.9x base> --version 26.11.99 \
#     --define TIMING --define PROFILE --define USBTRACE --define SCREEN --mode screen
S = 'driver/screentrace.inc'
T = 'driver/timing.inc'
MUTATIONS = [
    ('relocation unchecked', S, "        cmp     [esi + 0x52ad], ecx\n        jne     .out", "        cmp     [esi + 0x52ad], ecx\n        nop"),
    ('TSC gate gone', S, "        cmp     edx, 0x5d                       ; about 300 s at 1330 MHz\n        jae     .no",
     "        cmp     edx, 0x5d                       ; about 300 s at 1330 MHz\n        nop"),
    ('magic not cleared at ReadyToBoot', T, "        mov     dword [edi + (st_fb - .here)], 0 ; the SMM stubs stop drawing from now on", "        nop"),
    ('displaced push wrong', S, "        push    esi\n        push    0x61\nst_fd_call:", "        push    esi\n        push    0x60\nst_fd_call:"),
    ('opcode offset wrong', S, "        movzx   eax, byte [esi + 15]            ; its first CDB byte: the SCSI opcode",
     "        movzx   eax, byte [esi + 14]            ; its first CDB byte: the SCSI opcode"),
    ('FixedDelay jump back wrong', S, "        lea     eax, [esi + 0x138a]             ; back into FixedDelay", "        lea     eax, [esi + 0x138b]             ; back into FixedDelay"),
    ('count argument wrong', S, "        mov     eax, [esp + 40]                 ; the count", "        mov     eax, [esp + 44]                 ; the count"),
    ('zero framebuffer accepted', S, "        mov     eax, [edx + 0x0c]               ; PixelFormat\n        test    ecx, ecx\n        jz      .rec",
     "        mov     eax, [edx + 0x0c]               ; PixelFormat\n        nop\n        nop"),
    ('no refresh before drawing', S, "st_draw_req:\n        call    st_refresh                      ; the framebuffer as it is now",
     "st_draw_req:\n        nop                                     ; the framebuffer as it is now"),
    ('any pixel format', S, "        cmp     eax, 1\n        ja      .rec", "        nop\n        nop"),
    ('sense offset as first written', S, "        push    dword [esp + 36]                ; the returned code: pushad's eax at +32, +4 for the push",
     "        push    dword [esp + 32]                ; the returned code: pushad's eax at +32, +4 for the push"),
    ('sense call target wrong', S, "        lea     eax, [esi + 0x2a12]             ; the sense function's own call", "        lea     eax, [esi + 0x2a13]             ; the sense function's own call"),
    ('sense jump back wrong', S, "        lea     eax, [esi + 0x4c69]             ; back into it", "        lea     eax, [esi + 0x4c6a]             ; back into it"),
    ('ring drawn in slot order', S, ".ops:   mov     ecx, [ebp + (st_ncbw - .h)]\n        add     ecx, edi", ".ops:   mov     ecx, edi\n        nop\n        nop"),
    ('sense site unchecked', S, "        cmp     dword [esi + 0x4c61], 0xe8016a57        ; push edi; push 1; call (+0x2a12)\n        jne     .out",
     "        cmp     dword [esi + 0x4c61], 0xe8016a57        ; push edi; push 1; call (+0x2a12)\n        nop"),
]
