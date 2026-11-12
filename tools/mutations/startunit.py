# Mutations for the StartUnit experiment (driver/usbrt.inc under STARTUNIT).
# venv/bin/python -I tools/mutate.py tools/mutations/startunit.py --base <base> --version 26.11.26-dev1 \
#     --define TIMING --define PROFILE --define USBTRACE --define SCREEN --define STARTUNIT --mode screen,startunit
U = 'driver/usbrt.inc'
MUTATIONS = [
    ('StartUnit signature unchecked', U, "        call    mmc_sig_match\n        add     esp, 8\n        test    eax, eax\n        jz      .wait",
     "        call    mmc_sig_match\n        add     esp, 8\n        test    eax, eax\n        nop\n        nop"),
    ('wrong device pushed', U, "        mov     byte [esi + 0x56c3], 0x56           ; push esi", "        mov     byte [esi + 0x56c3], 0x57           ; push esi"),
    ('call aimed one byte off', U, "        mov     dword [esi + 0x56cc], URT_START_RVA - 0x56d0   ; call StartUnit",
     "        mov     dword [esi + 0x56cc], URT_START_RVA - 0x56cf   ; call StartUnit"),
    ('old push imm left in place', U, "        mov     dword [esi + 0x56c4], 0x90909090    ; nop x4 (the old push's imm32)", "        nop"),
    ('A signature unchecked', U, "        mov     edx, 18\n        call    .cmp\n        jne     .bc", "        mov     edx, 18\n        call    .cmp\n        nop\n        nop"),
]
