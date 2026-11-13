; sdh_wait.inc: the wait at the end of SdHost's SendCommand, in place of its own (mmcinit.inc).
;
; The stock loop reads the error and normal interrupt status, leaves when the command (and any
; data) is done or has failed, and otherwise stalls 1000 us and counts a millisecond off the
; caller's timeout. Nothing in the driver waits for the controller to be free again before the
; next command: the stock 1 ms stall happened to cover that. Polling every few microseconds
; without that wait (26.11.27-dev1) broke the internal memory's large reads and writes.
;
; This polls every SDW_POLL_US, keeping the caller's timeout (no shorter), and once the command is
; done waits until the controller's Present State shows neither command line nor data line in
; use (Command Inhibit (CMD) and (DAT), bits 0 and 1: SD Host Controller Simplified Specification
; 2.00, 2.2.9). That is how long the card really needs: a slower chip, or one busy finishing a
; write, is waited for. The wait is capped at SDW_SETTLE_MS, after which the command counts as done
; as before. The first time the cap is reached (a failing chip that holds a line), the cap drops
; to SDW_SICK_MS for the rest of the boot, about what the stock stall allowed: a sick chip costs at
; most one long wait, not one per command.
;
; Position independent and self-contained (tools/emu_mmcinit.py assembles it alone).

SDW_POLL_US     equ     10
SDW_PASSES      equ     100                     ; passes per millisecond: 100 x 10 us
SDW_SETTLE_MS   equ     20
SDW_SICK_MS     equ     1

; UINT32 sdh_wait(UINT8 *Frame, EFI_BOOT_SERVICES *BS), called from SendCommand with esi = its
; EFI_PCI_IO_PROTOCOL. Frame is SendCommand's ebp: [Frame + 0x14] = data direction (0 = no data),
; [Frame + 0x24] = timeout in ms. Returns 0 when done, 0x80000000 on timeout, or the error
; interrupt status (bits 0-8 not all clear) on an error. Keeps ebx, esi, edi, ebp.
sdh_wait:
        push    ebp
        mov     ebp, esp
        push    ebx
        push    esi
        push    edi
        sub     esp, 12                         ; -16 register value, -20 passes, -24 ms left
        mov     edi, [ebp + 8]                  ; SendCommand's frame
        mov     eax, [edi + 0x24]
        mov     [ebp - 24], eax                 ; timeout (0 waits ~forever, as the stock loop)
        mov     dword [ebp - 20], SDW_PASSES
.loop:  mov     edx, 0x32                       ; Error Interrupt Status
        mov     ecx, 1
        call    .rd
        test    eax, 0x1ff
        jnz     .out                            ; an error: its status
        mov     edx, 0x30                       ; Normal Interrupt Status
        mov     ecx, 1
        call    .rd
        test    al, 1                           ; Command Complete
        jz      .wait
        cmp     dword [edi + 0x14], 0           ; no data: done
        je      .settle
        test    al, 2                           ; Transfer Complete
        jnz     .settle
.wait:  call    .stall
        dec     dword [ebp - 20]
        jnz     .loop
        mov     dword [ebp - 20], SDW_PASSES
        dec     dword [ebp - 24]
        jnz     .loop
        mov     eax, 0x80000000                 ; timeout
        jmp     .out
.settle:
        call    .flag                           ; eax = sdw_sick
        mov     ebx, SDW_SETTLE_MS * SDW_PASSES
        cmp     byte [eax], 0
        je      .free
        mov     ebx, SDW_SICK_MS * SDW_PASSES
.free:  mov     edx, 0x24                       ; Present State
        mov     ecx, 2
        call    .rd
        test    al, 3                           ; Command Inhibit (CMD), (DAT)
        jz      .done
        call    .stall
        dec     ebx
        jnz     .free
        call    .flag                           ; the cap reached: shorter from now on
        mov     byte [eax], 1
.done:  xor     eax, eax
.out:   add     esp, 12
        pop     edi
        pop     esi
        pop     ebx
        pop     ebp
        ret

; .rd: eax = the register at offset edx, width ecx (1: 16 bits, 2: 32), read through
; PciIo->Mem.Read (BAR 0), zero-extended. Uses [ebp - 16].
.rd:    mov     dword [ebp - 16], 0
        lea     eax, [ebp - 16]
        push    eax                             ; Buffer
        push    1                               ; Count
        push    0                               ; Offset (UINT64): high
        push    edx                             ; low
        push    0                               ; BarIndex
        push    ecx                             ; Width
        push    esi                             ; This
        call    [esi + 8]                       ; Mem.Read
        add     esp, 28
        mov     eax, [ebp - 16]
        ret

; .flag: eax = &sdw_sick.
.flag:  call    .fh
.fh:    pop     eax
        add     eax, sdw_sick - .fh
        ret

; .stall: BS->Stall(SDW_POLL_US).
.stall: push    SDW_POLL_US
        mov     eax, [ebp + 12]                 ; BS
        call    [eax + 0x88]
        add     esp, 4
        ret

sdw_sick:       db 0                            ; set once a settle wait reached its cap
