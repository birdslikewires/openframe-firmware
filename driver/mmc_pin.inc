; mmc_pin.inc: the internal memory (MMC) always the shell's fs0.
;
; The EFI shell numbers file systems (fs0, fs1, ...) in the order their handles were made, and
; startup.nsh and boot.nsh name volumes by those numbers. Which comes first, the MMC or a USB stick,
; used to depend on which driver finished first: up to 26.11.24 the MMC's 2.8 s CE-ATA probe put
; any stick first; since then the MMC has been first, by luck. This makes it deliberate: at the
; boot manager's first stage signal (EF9A3971, about 1.96 s from reset on an OF1, once every driver
; is loaded and PCI is set up, about 1.5 s before USB has a device) it connects every SD host
; controller (PCI class 08, subclass 05) with everything below it, so the MMC's partitions and file
; systems are made before any USB disk's. The boot manager's own connect finds them done later; the
; work is the same, only earlier. The OF1's two empty SD controllers cost about 11 ms each, as they
; already did. A dead or missing MMC leaves nothing to number first, and a stick is fs0 as before.

; VOID EFIAPI mmc_pin_notify(EFI_EVENT Event, VOID *Context = BootServices): the stage signal.
; Once only (the signal can come more than once).
mmc_pin_notify:
        push    ebp
        mov     ebp, esp
        sub     esp, 20                         ; -4 count, -8 buffer, -12 PciIo, -16 handle, -20 class
        push    ebx
        push    esi
        push    edi
        call    .here
.here:  pop     edi
        cmp     byte [edi + (pin_done - .here)], 0
        jne     .out
        mov     byte [edi + (pin_done - .here)], 1
        mov     ebx, [ebp + 12]                 ; BootServices
        xor     eax, eax
        mov     [ebp - 4], eax
        mov     [ebp - 8], eax
        lea     eax, [ebp - 8]                  ; LocateHandleBuffer(ByProtocol, &PciIo, NULL, &n, &buf)
        push    eax
        lea     eax, [ebp - 4]
        push    eax
        push    0
        lea     eax, [edi + (pin_pciio_guid - .here)]
        push    eax
        push    2
        call    [ebx + 0xa8]
        add     esp, 20
        test    eax, eax
        jnz     .out
        xor     esi, esi                        ; esi = index
.loop:  cmp     esi, [ebp - 4]
        jae     .free
        mov     edx, [ebp - 8]
        mov     edx, [edx + esi * 4]
        mov     [ebp - 16], edx
        lea     eax, [ebp - 12]                 ; HandleProtocol(handle, &PciIo, &PciIo)
        push    eax
        lea     eax, [edi + (pin_pciio_guid - .here)]
        push    eax
        push    edx
        call    [ebx + 0x58]
        add     esp, 12
        test    eax, eax
        jnz     .next
        mov     dword [ebp - 20], 0
        mov     eax, [ebp - 12]
        lea     ecx, [ebp - 20]                 ; Pci.Read(PciIo, Uint32, 0x08, 1, &class): revision,
        push    ecx                             ; programming interface, subclass, class
        push    1
        push    0x08
        push    2
        push    eax
        call    [eax + 0x18]
        add     esp, 20
        test    eax, eax
        jnz     .next
        mov     eax, [ebp - 20]
        shr     eax, 16
        cmp     eax, 0x0805                     ; class 08 (base system peripheral), subclass 05 (SD host)
        jne     .next
        push    1                               ; ConnectController(handle, NULL, NULL, Recursive)
        push    0
        push    0
        push    dword [ebp - 16]
        call    [ebx + 0x90]
        add     esp, 16
.next:  inc     esi
        jmp     .loop
.free:  push    dword [ebp - 8]
        call    [ebx + 0x30]                    ; FreePool
        add     esp, 4
.out:   pop     edi
        pop     esi
        pop     ebx
        leave
        ret

pin_done:       db 0                            ; set at the first stage signal
        align   4, db 0
pin_pciio_guid:                                 ; 4CF5B200-68B8-4CA5-9EEC-B23E3F50029A EFI_PCI_IO_PROTOCOL
        dd      0x4cf5b200
        dw      0x68b8, 0x4ca5
        db      0x9e, 0xec, 0xb2, 0x3e, 0x3f, 0x50, 0x02, 0x9a
