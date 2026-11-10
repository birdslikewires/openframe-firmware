; Console output for the shell-run tools. IA32 EFIAPI (cdecl). Keep ebx, esi, edi.

; void con_out(EFI_SYSTEM_TABLE *ST, CHAR16 *String)
con_out:
        mov     eax, [esp + 4]
        mov     eax, [eax + 0x2c]               ; ST->ConOut
        push    dword [esp + 8]
        push    eax
        call    [eax + 4]                       ; ConOut->OutputString
        add     esp, 8
        ret

; void con_hex(EFI_SYSTEM_TABLE *ST, UINT32 Value): eight hex digits.
con_hex:
        push    ebp
        mov     ebp, esp
        sub     esp, 20
        push    esi
        mov     esi, [ebp + 12]
        xor     ecx, ecx
.digit: rol     esi, 4
        mov     eax, esi
        and     eax, 0xf
        cmp     al, 10
        jb      .num
        add     al, 'a' - '0' - 10
.num:   add     al, '0'
        mov     [ebp - 20 + ecx * 2], ax
        inc     ecx
        cmp     ecx, 8
        jb      .digit
        mov     word [ebp - 20 + 16], 0
        lea     eax, [ebp - 20]
        push    eax
        push    dword [ebp + 8]
        call    con_out
        add     esp, 8
        pop     esi
        leave
        ret
