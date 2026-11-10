; nic_mac0.inc: write a MAC into the OpenFrame RTL8111C's MAC0 registers. Shared by blwmac.asm (BlwMac.efi)
; and ucode_late.asm (BlwPlatformDxe). The includer defines F_NIC_DONE, F_NIC_VERIFY and F_NIC_MISSING.
; Uses only port I/O: PCI configuration mechanism 1 and the NIC's I/O BAR.

; lan_mac: esi = the MAC BlwMac.efi stored, edi = 6 bytes for the LAN MAC. A card's burned-in
; address (bit 1 of the first octet clear) gives 02 then its last five octets, as of-netplan
; makes it; a locally administered one is the LAN MAC already and is copied as it is.
; Keeps every register.
lan_mac:
        push    esi
        push    edi
        push    ecx
        mov     ecx, 6
        rep movsb
        pop     ecx
        pop     edi
        pop     esi
        test    byte [esi], 2
        jnz     .r
        mov     byte [edi], 0x02
.r:     ret

; program_nic: esi = MAC (6 bytes). Writes it into the RTL8111's MAC0 through its I/O BAR and
; reads it back. eax = F_NIC_DONE, F_NIC_VERIFY or F_NIC_MISSING.
program_nic:
        push    esi
        push    edi
        push    ebx
        mov     eax, 0x8000E019                 ; 00:1c.0 offset 0x19: secondary bus number
        call    cfg_read32_aligned_byte         ; al = byte
        movzx   ebx, al                         ; ebx = NIC bus
        test    ebx, ebx
        jz      .missing
        mov     eax, ebx
        shl     eax, 16
        or      eax, 0x80000000                 ; bus:00.0 offset 0
        mov     edi, eax                        ; edi = NIC config address base
        call    cfg_read32
        cmp     eax, 0x816810ec
        jne     .missing
        lea     eax, [edi + 0x10]               ; BAR0
        call    cfg_read32
        test    al, 1
        jz      .missing                        ; not an I/O BAR
        and     eax, 0xfffc
        jz      .missing
        mov     ebx, eax                        ; ebx = I/O base (bus no longer needed)

        mov     eax, 0x8000E004                 ; save both command registers, enable I/O decode
        call    cfg_read32
        push    eax                             ; [esp+4] root port command/status
        lea     eax, [edi + 4]
        call    cfg_read32
        push    eax                             ; [esp] NIC command/status
        mov     eax, 0x8000E004
        mov     ecx, [esp + 4]
        or      ecx, 1
        call    cfg_write16
        lea     eax, [edi + 4]
        mov     ecx, [esp]
        or      ecx, 1
        call    cfg_write16

        lea     edx, [ebx + 0x50]               ; Cfg9346: unlock
        mov     al, 0xc0
        out     dx, al
        mov     edx, ebx                        ; MAC0..3
        mov     eax, [esi]
        out     dx, eax
        lea     edx, [ebx + 4]                  ; MAC4..5
        movzx   eax, word [esi + 4]
        out     dx, eax
        lea     edx, [ebx + 0x50]               ; lock
        xor     eax, eax
        out     dx, al

        mov     ecx, 6                          ; read back byte by byte
        mov     edx, ebx
        push    esi
.cmp:   in      al, dx
        cmp     al, [esi]
        jne     .diff
        inc     esi
        inc     edx
        dec     ecx
        jnz     .cmp
        mov     esi, F_NIC_DONE
        jmp     .restore
.diff:  mov     esi, F_NIC_VERIFY
.restore:
        lea     eax, [edi + 4]                  ; put both command registers back
        mov     ecx, [esp + 4]                  ; (stack: saved esi, NIC cmd, root port cmd)
        call    cfg_write16
        mov     eax, 0x8000E004
        mov     ecx, [esp + 8]
        call    cfg_write16
        mov     eax, esi
        pop     esi
        add     esp, 8
        jmp     .out
.missing:
        mov     eax, F_NIC_MISSING
.out:   pop     ebx
        pop     edi
        pop     esi
        ret

; PCI configuration mechanism 1. eax = 0x80000000 | bus << 16 | dev << 11 | fn << 8 | reg.
cfg_read32:                                     ; eax = dword (reg dword-aligned)
        mov     edx, 0xcf8
        out     dx, eax
        mov     edx, 0xcfc
        in      eax, dx
        ret
cfg_read32_aligned_byte:                        ; al = byte at reg (any alignment)
        push    ecx
        mov     ecx, eax
        and     eax, 0xfffffffc
        call    cfg_read32
        and     ecx, 3
        shl     ecx, 3
        shr     eax, cl
        pop     ecx
        ret
cfg_write16:                                    ; eax = address (reg word-aligned), cx = value
        push    eax
        and     eax, 0xfffffffc
        mov     edx, 0xcf8
        out     dx, eax
        pop     eax
        and     eax, 2
        lea     edx, [eax + 0xcfc]
        mov     eax, ecx
        out     dx, ax
        ret
