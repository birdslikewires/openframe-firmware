; acpi_rp2.inc: the OpenFrame 2's mini-PCIe slot interrupt routing, from dev2 (26.11.32).
;
; Every OF2 build's DSDT leaves out the Poulsbo's second PCIe root port (00:1C.1), behind which
; the mini-PCIe slot sits, and routes only device 0x1C's INTA (GSI 16). An OS then takes the
; slot's INTA to GSI 16 by the bridge swizzle, but the root port signals it on its own pin INTB,
; GSI 17: on the Cisco OF2 the BCM43225's interrupts went unclaimed on IRQ 17 ("nobody cared",
; disabled) and its Wi-Fi heard nothing. of2_rp2.asl describes the port (\_SB.PCI0.RP02, _ADR
; 0x001C0001) with its own _PRT; this installs it as an SSDT.
;
; Only on an OpenFrame 2 (the boot block's ROM name, as model.inc), and only through the
; firmware's own ACPI Support protocol (Intel framework, DBFF9D55), the way its own modules add
; theirs (the HPET table: SetAcpiTable(This, Table, Checksum TRUE, ACPI 1.0b and 2.0, &Handle 0),
; no PublishTables: the firmware publishes them itself). The table is copied by SetAcpiTable.
; Tried at the driver's entry, and at ReadyToBoot if it hasn't been done; once only.

ACPI_RP2_VERSIONS equ   6                       ; EFI_ACPI_TABLE_VERSION_1_0B | _2_0

; UINT32 acpi_rp2(EFI_BOOT_SERVICES *BS): 1 if the SSDT is in (now or before), else 0. cdecl.
; Keeps ebx, esi, edi.
acpi_rp2:
        push    ebx
        push    esi
        push    edi
        call    .here
.here:  pop     edi
        mov     eax, 1
        cmp     byte [edi + (rp2_done - .here)], 0
        jne     .out
        xor     eax, eax
        cmp     dword [0xfffea268], 0x50424130  ; "0ABP"
        jne     .out
        cmp     dword [0xfffea26c], 0x35303057  ; "W005": an OpenFrame 2
        jne     .out
        mov     ebx, [esp + 16]                 ; BS
        push    0                               ; local: Interface, then Handle
        mov     eax, esp
        push    eax                             ; &Interface
        push    0                               ; Registration
        lea     eax, [edi + (acpisup_guid - .here)]
        push    eax
        call    [ebx + 0xac]                    ; BS->LocateProtocol
        add     esp, 12
        pop     esi                             ; esi = ACPI Support
        test    eax, eax
        jnz     .no
        test    esi, esi
        jz      .no
        push    0                               ; local: Handle = 0, a new table
        mov     eax, esp
        push    eax                             ; &Handle
        push    ACPI_RP2_VERSIONS
        push    1                               ; Checksum: TRUE
        lea     eax, [edi + (rp2_ssdt - .here)]
        push    eax                             ; Table
        push    esi                             ; This
        call    [esi + 4]                       ; ->SetAcpiTable
        add     esp, 24
        test    eax, eax
        jnz     .no
        mov     byte [edi + (rp2_done - .here)], 1
        mov     eax, 1
        jmp     .out
.no:    xor     eax, eax
.out:   pop     edi
        pop     esi
        pop     ebx
        ret

rp2_done:       db 0
        align   4, db 0
acpisup_guid:                                   ; DBFF9D55-89B7-46DA-BDDF-677D3DC0241D EFI_ACPI_SUPPORT_PROTOCOL
        dd      0xdbff9d55
        dw      0x89b7, 0x46da
        db      0xbd, 0xdf, 0x67, 0x7d, 0x3d, 0xc0, 0x24, 0x1d
        align   4, db 0
rp2_ssdt:
        incbin  ACPI_RP2_FILE
