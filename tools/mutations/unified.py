"""Mutations for the single block 10's model detection (model.inc) and the countdown's place in the
image patches (img_patch.inc):
    venv/bin/python -I tools/mutate.py tools/mutations/unified.py --base BASE --version 26.11.31-dev3
Not caught on purpose, so not listed: trying the countdown again after it is done (the shell still
matches and gets the same two zeros)."""
M, U, I = 'driver/model.inc', 'driver/ucode_late.asm', 'driver/img_patch.inc'
MUTATIONS = [
    ('model never filled', U, '        call    model_fill                      ; Type 1: OpenFrame 1 or 2 (model.inc)', '        nop'),
    ('only the product name changed', M, '        mov     byte [edi + SMBIOS_MODEL2_OFF], \'2\'   ; "OF2"', '        nop'),
    ('only the version changed', M, '        mov     byte [edi + SMBIOS_MODEL1_OFF], \'2\'   ; "OpenFrame 2"', '        nop'),
    ('second half of the ROM name not checked', M, '        cmp     dword [0xfffea26c], 0x35303057  ; "W005"\n        jne     .r',
     '        cmp     dword [0xfffea26c], 0x35303057  ; "W005"\n        nop\n        nop'),
    ('first half of the ROM name not checked', M, '        cmp     dword [0xfffea268], 0x50424130  ; "0ABP"\n        jne     .r',
     '        cmp     dword [0xfffea268], 0x50424130  ; "0ABP"\n        nop\n        nop'),
    ('the OF1 taken for an OF2', M, '0x35303057  ; "W005"', '0x34303057  ; "W004"'),
    ('countdown not tried on each image', I, '        call    cd_patch_image\n        add     esp, 4', '        add     esp, 4\n        xor     eax, eax'),
    ('countdown not counted in all-done', I, '        cmp     byte [edi + (cd_done - .here)], 0\n        je      .b0', '        nop'),
]
