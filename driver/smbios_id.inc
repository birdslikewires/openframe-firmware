; smbios_id.inc: make the SMBIOS Type 1 UUID, and for a card's MAC the serial number, from the MAC
; BlwMac.efi stored. Shared by ucode_late.asm (BlwPlatformDxe, at ReadyToBoot) and blwmac.asm
; (BlwMac.efi, which runs after ReadyToBoot and so fills in a MAC it has only just stored).
;
; Only BlwPlatformDxe's own table from 26.11.20 on is changed: its Type 1 ends with a string of 12
; hex digits ("000000000000" until filled) that no field refers to, and the serial number field
; stays 0 (none) until this points it there. Older tables (Type 1's last string is "OpenFrame")
; are left alone.
;
; Serial number: only for a WLAN card's burned-in address (bit 1 of the first octet clear), as 12
; upper-case hex digits, as on the Joggler's sticker (MAC ID). A locally administered MAC was
; generated, not the hardware's, so it gets no serial number (and any filled in earlier this boot
; is taken away again).
; UUID: from either kind: RFC 9562 version 8 (vendor-specific), variant 10, the MAC as its last six
; bytes, e.g. 00000000-0000-8000-8000-000E8E123456, stored in the order written, which is how
; SMBIOS before 2.6 is read: Linux and dmidecode only byte-swap the first three fields from 2.6 on,
; and this table is 2.4.

; smbios_set_id: esi = SMBIOS entry point, edx = the stored MAC (6 bytes, already checked by
; mac_ok). eax = 1 if Type 1 was filled in, else 0. Keeps ebx, esi, edi, ebp.
smbios_set_id:
        push    ebx
        push    esi
        push    edi
        push    ebp
        mov     ebp, edx                        ; ebp = the MAC
        cmp     dword [esi], '_SM_'
        jne     .fail
        movzx   ebx, word [esi + 0x16]          ; structure table length
        mov     edi, [esi + 0x18]               ; structure table address
        add     ebx, edi                        ; ebx = end of the table
.struct:
        lea     ecx, [edi + 4]
        cmp     ecx, ebx
        ja      .fail
        cmp     byte [edi], 127                 ; end of table: no Type 1
        je      .fail
        movzx   ecx, byte [edi + 1]
        lea     esi, [edi + ecx]                ; esi = its strings
        cmp     byte [edi], 1
        je      .type1
.skip:  lea     ecx, [esi + 2]                  ; on to the double zero after the strings
        cmp     ecx, ebx
        ja      .fail
        cmp     word [esi], 0
        je      .next
        inc     esi
        jmp     .skip
.next:  lea     edi, [esi + 2]
        jmp     .struct

.type1: cmp     byte [edi + 1], 0x19            ; long enough to have the UUID
        jb      .fail
        xor     edx, edx                        ; edx = strings counted
        xor     eax, eax                        ; eax = start of the last one
.str:   cmp     esi, ebx
        jae     .fail
        cmp     byte [esi], 0
        je      .last                           ; the empty string that ends the set
        inc     edx
        mov     eax, esi
.chr:   inc     esi
        cmp     esi, ebx
        jae     .fail
        cmp     byte [esi], 0
        jne     .chr
        inc     esi
        jmp     .str
.last:  test    eax, eax                        ; (ebx, the table end, isn't needed from here)
        jz      .fail
        mov     esi, eax                        ; exactly 12 of 0-9 A-F (a shorter string
        lea     ecx, [eax + 12]                 ; fails at its terminator, so this never
.chk:   cmp     esi, ecx                        ; reads past it)
        je      .len
        mov     bl, [esi]
        sub     bl, '0'
        cmp     bl, 9
        jbe     .dig
        sub     bl, 'A' - '0'
        cmp     bl, 5
        ja      .fail
.dig:   inc     esi
        jmp     .chk
.len:   cmp     byte [esi], 0
        jne     .fail

        test    byte [ebp], 2
        jz      .serial
        mov     byte [edi + 7], 0               ; generated: no serial number, placeholder back
        mov     dword [eax], '0000'
        mov     dword [eax + 4], '0000'
        mov     dword [eax + 8], '0000'
        jmp     .uuid
.serial:
        mov     [edi + 7], dl                   ; serial number: that string
        xor     ecx, ecx
.hex:   movzx   edx, byte [ebp + ecx]
        shr     edx, 4
        call    .nib
        mov     [eax + ecx * 2], dl
        movzx   edx, byte [ebp + ecx]
        and     edx, 15
        call    .nib
        mov     [eax + ecx * 2 + 1], dl
        inc     ecx
        cmp     ecx, 6
        jb      .hex
.uuid:  mov     dword [edi + 8], 0              ; UUID 00000000-0000-8000-8000-<MAC>
        mov     dword [edi + 12], 0x00800000
        mov     word [edi + 16], 0x0080
        mov     edx, [ebp]
        mov     [edi + 18], edx
        mov     dx, [ebp + 4]
        mov     [edi + 22], dx
        mov     eax, 1
        jmp     .out
.fail:  xor     eax, eax
.out:   pop     ebp
        pop     edi
        pop     esi
        pop     ebx
        ret

.nib:   add     dl, '0'                         ; dl = 0..15 -> '0'..'9', 'A'..'F'
        cmp     dl, '9'
        jbe     .nr
        add     dl, 'A' - '9' - 1
.nr:    ret
