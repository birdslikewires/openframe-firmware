; countdown.inc: no startup.nsh countdown on any OpenFrame, made in the shell's loaded image as it
; loads, from 26.11.31.
;
; The built-in shell waits 5 s before running startup.nsh, counting down in tenths of a second from
; 50 (`push 0x32; pop esi`, and again as the fallback if a delay setting won't parse). Our OF1
; releases change both operands to 0 in flash (build_patched.py's shell patch); an OpenFrame 2's
; shell (another build, with the same function elsewhere) is in blocks our updates never write, so
; the change is made in memory instead, here, for either build. The shell loads at BDS, long after
; this driver, so through the LoadedImage notify (img_patch.inc), before it runs. Only a shell we
; know is changed: shell_sig.inc (from tools/shell_sig.py) has, for each build, the function from its
; start to the countdown loop, less its relocated dwords and the two operands, which are what this
; writes (so on our OF1 releases, where they are already 0, it writes 0 again). A delay setting, if
; one were ever set, is still honoured, as before.

; UINT32 cd_patch_image(EFI_LOADED_IMAGE_PROTOCOL *Li): 1 if Li is a shell we know and its countdown
; now starts at 0, else 0 (nothing changed). cdecl. Keeps ebx, esi, edi.
cd_patch_image:
        push    ebx
        push    esi
        push    edi
        call    .here
.here:  pop     edi
        mov     ebx, [esp + 16]                 ; Li
        mov     esi, [ebx + 0x10]               ; FilePath: one MEDIA_FW_VOL_FILEPATH node
        test    esi, esi
        jz      .no
        cmp     dword [esi], 0x00140604         ; type 4, subtype 6, length 20
        jne     .no
        add     esi, 4                          ; its file GUID
        push    edi
        lea     edi, [edi + (usbf_shell_guid - .here)]
        mov     ecx, 16
        cld
        repe    cmpsb
        pop     edi
        jne     .no
        mov     esi, [ebx + 0x20]               ; ImageBase
        test    esi, esi
        jz      .no
        lea     edx, [edi + (shl_cds - .here)]  ; each shell build we know
.try:   mov     eax, [edx]                      ; its countdown's RVA (0: no more)
        test    eax, eax
        jz      .no
        mov     ecx, [edx + 4]                  ; the ImageSize it needs
        cmp     dword [ebx + 0x28], ecx         ; ImageSize (low half: IA32)
        jb      .next
        push    edx
        lea     ecx, [edx + 8]
        push    ecx
        add     eax, esi
        push    eax
        call    mmc_sig_match
        add     esp, 8
        pop     edx
        test    eax, eax
        jnz     .found
.next:  add     edx, 8                          ; past its runs, to the next build
.skip:  movzx   ecx, word [edx + 2]
        add     edx, 4
        jecxz   .try
        add     edx, ecx
        jmp     .skip

.found: mov     eax, [edx]
        add     eax, esi                        ; the countdown function
        mov     byte [eax + SHL_CD_A], 0        ; push 0 (was 0x32): no countdown
        mov     byte [eax + SHL_CD_B], 0        ; and the same for the fallback
        cmp     byte [eax + SHL_CD_A], 0
        jne     .no
        cmp     byte [eax + SHL_CD_B], 0
        jne     .no
        mov     eax, 1
        jmp     .out
.no:    xor     eax, eax
.out:   pop     edi
        pop     esi
        pop     ebx
        ret

cd_done:        db 0                            ; set once the shell's countdown is 0
        align   4, db 0
%include "shell_sig.inc"
