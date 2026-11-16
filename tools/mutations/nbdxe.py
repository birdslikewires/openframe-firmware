"""Mutations for the NBDXE change (panel_clock.inc, nb_patch_image: NBDXE's clock function made to
return at once, from 26.11.31) and the splash's place in the image patches (img_patch.inc):
    venv/bin/python -I tools/mutate.py tools/mutations/nbdxe.py --base BASE --version 26.11.31-dev2
Not caught on purpose, so not listed: dropping the read-back of the ret (the emulated memory always
takes the write); trying the splash again after it is done (the display driver still matches, and
gets the same bytes written again: nothing to see, only a little time)."""
P, I = 'driver/panel_clock.inc', 'driver/img_patch.inc'
MUTATIONS = [
    ('NBDXE function not changed', P, '        mov     byte [esi + NBD_FN_RVA], 0xc3   ; ret: no clock written',
     '        nop'),
    ('NBDXE given the Sharp value again', P, '        mov     byte [esi + NBD_FN_RVA], 0xc3   ; ret: no clock written\n        cmp     byte [esi + NBD_FN_RVA], 0xc3',
     '        mov     dword [esi + NBD_IMM_RVA], PCLK_DPLL\n        cmp     dword [esi + NBD_IMM_RVA], PCLK_DPLL'),
    ('ret one byte late', P, '        mov     byte [esi + NBD_FN_RVA], 0xc3   ; ret: no clock written\n        cmp     byte [esi + NBD_FN_RVA], 0xc3',
     '        mov     byte [esi + NBD_FN_RVA + 1], 0xc3\n        cmp     byte [esi + NBD_FN_RVA + 1], 0xc3'),
    ('NBDXE signature not checked', P, '        call    mmc_sig_match\n        add     esp, 8\n        test    eax, eax\n        jz      .no\n        mov     byte [esi + NBD_FN_RVA]',
     '        call    mmc_sig_match\n        add     esp, 8\n        test    eax, eax\n        nop\n        nop\n        mov     byte [esi + NBD_FN_RVA]'),
    ('splash not tried on each image', I, '        call    gsp_patch_image\n        add     esp, 4\n        mov     [edi + (gsp_done - .here)], al',
     '        add     esp, 0\n        mov     [edi + (gsp_done - .here)], al'),
    ('splash done not counted in all-done', I, '        cmp     byte [edi + (gsp_done - .here)], 0\n        je      .b0', '        nop'),
]
