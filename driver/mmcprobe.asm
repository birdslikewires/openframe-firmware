; MmcProbe.efi: a shell app that times the firmware's own SD/MMC initialisation, to find where it
; spends its time at boot (3.3 s on an OF1, against 81 ms for Linux on the same card). For each
; controller with an SdHostIo (the OF1 has three: 1E.0, 1E.1, 1E.2) it disconnects everything
; from it and connects it again (recursively), as the boot manager does, while recording:
;   - every SdHostIo->SendCommand: the command, argument, low byte of the status, and the time
;     (repeats of the same command, argument and status merge into one record with a count);
;   - every BootServices->Stall outside SendCommand (merged the same way); stalls inside
;     SendCommand (its polling) are only counted and summed;
;   - markers: H (controller n), D (disconnected, status), E (connected, status).
; Timestamps are the CPU's TSC >> 10 (0.77 us at 1330 MHz). It prints one hex line per record:
;   R tag arg start end count
; for tools/mmcprobe.py to decode. Everything it changes (the Stall pointer, the SdHostIo
; SendCommand pointers, a protocol notify) is put back before it returns. Touches no flash.
;
; Entry: EFI_STATUS EFIAPI Entry(EFI_HANDLE ImageHandle, EFI_SYSTEM_TABLE *SystemTable)

bits 32

MAXREC          equ     16384
RECSZ           equ     20

%define COUNT   ebp - 4
%define HANDLES ebp - 8
%define INDEX   ebp - 12
%define EVENT   ebp - 16
%define HOST    ebp - 20

%macro PRINT 1
        lea     eax, [edi + (%1 - entry.here)]
        push    eax
        push    esi
        call    con_out
        add     esp, 8
%endmacro
%macro HEX 1
        push    %1
        push    esi
        call    con_hex
        add     esp, 8
%endmacro
%macro MARK 2                                   ; tag, arg (a register or constant; not eax)
        mov     ecx, %2
        mov     eax, %1
        call    mark
%endmacro

entry:
        push    ebp
        mov     ebp, esp
        sub     esp, 20
        push    ebx
        push    esi
        push    edi
        call    .here
.here:  pop     edi
        xor     eax, eax
        mov     [COUNT], eax
        mov     [HANDLES], eax
        mov     [EVENT], eax
        mov     esi, [ebp + 12]                 ; esi = SystemTable
        mov     ebx, [esi + 0x3c]               ; ebx = BootServices
        mov     [edi + (g_bs - .here)], ebx

        lea     eax, [edi + (g_buf - .here)]
        push    eax
        push    MAXREC * RECSZ
        push    4                               ; EfiBootServicesData
        call    [ebx + 0x2c]                    ; AllocatePool
        add     esp, 12
        test    eax, eax
        jnz     .ret

        lea     eax, [HANDLES]
        push    eax
        lea     eax, [COUNT]
        push    eax
        push    0
        lea     eax, [edi + (sdh_guid - .here)]
        push    eax
        push    2
        call    [ebx + 0xa8]                    ; LocateHandleBuffer(ByProtocol, SdHostIo)
        add     esp, 20
        test    eax, eax
        jnz     .nohandles

        call    patch_all                       ; the instances there now

        lea     eax, [EVENT]                    ; and any SdHostIo installed from now on
        push    eax
        push    0
        lea     eax, [edi + (sdh_notify - .here)]
        push    eax
        push    16                              ; TPL_NOTIFY
        push    0x200                           ; EVT_NOTIFY_SIGNAL
        call    [ebx + 0x34]                    ; CreateEvent
        add     esp, 20
        test    eax, eax
        jnz     .done
        lea     eax, [edi + (g_reg - .here)]
        push    eax
        push    dword [EVENT]
        lea     eax, [edi + (sdh_guid - .here)]
        push    eax
        call    [ebx + 0x60]                    ; RegisterProtocolNotify
        add     esp, 12
        test    eax, eax
        jnz     .done

        mov     eax, [ebx + 0x88]               ; hook Stall
        mov     [edi + (g_ostall - .here)], eax
        lea     eax, [edi + (wrap_stall - .here)]
        mov     [ebx + 0x88], eax

%ifdef SKIPCEATA
        call    skip_ceata                      ; P record: 1 if MmcMediaDevice was patched
        MARK    'P' << 24, eax
%endif
        mov     dword [INDEX], 0
.loop:  mov     eax, [INDEX]
        cmp     eax, [COUNT]
        jae     .unhook
        MARK    'H' << 24, [INDEX]
        mov     edx, [HANDLES]
        mov     eax, [INDEX]
        push    0
        push    0
        push    dword [edx + eax * 4]
        call    [ebx + 0x94]                    ; DisconnectController(h, NULL, NULL)
        add     esp, 12
        MARK    'D' << 24, eax
        mov     edx, [HANDLES]
        mov     eax, [INDEX]
        push    1                               ; Recursive
        push    0
        push    0
        push    dword [edx + eax * 4]
        call    [ebx + 0x90]                    ; ConnectController(h, NULL, NULL, TRUE)
        add     esp, 16
        MARK    'E' << 24, eax
        inc     dword [INDEX]
        jmp     .loop

.unhook:
        mov     eax, [edi + (g_ostall - .here)]
        mov     [ebx + 0x88], eax
.done:  cmp     dword [EVENT], 0
        je      .nev
        push    dword [EVENT]
        call    [ebx + 0x44]                    ; CloseEvent
        add     esp, 4
.nev:   push    dword [HANDLES]
        call    [ebx + 0x30]                    ; FreePool
        add     esp, 4
        mov     dword [HANDLES], 0              ; the instances now (new ones after the connect)
        lea     eax, [HANDLES]
        push    eax
        lea     eax, [COUNT]
        push    eax
        push    0
        lea     eax, [edi + (sdh_guid - .here)]
        push    eax
        push    2
        call    [ebx + 0xa8]
        add     esp, 20
        test    eax, eax
        jnz     .print
        mov     byte [edi + (g_restore - .here)], 1
        call    patch_all                       ; restore mode: put SendCommand back
        push    dword [HANDLES]
        call    [ebx + 0x30]
        add     esp, 4

.print: PRINT   s_head
        HEX     dword [edi + (g_nest_n - .here)]
        PRINT   s_sp
        HEX     dword [edi + (g_nest_us - .here)]
        PRINT   s_sp
        HEX     dword [edi + (g_osend - .here)]
        PRINT   s_nl
        mov     dword [INDEX], 0
.pl:    mov     eax, [INDEX]
        cmp     eax, [edi + (g_n - .here)]
        jae     .freebuf
        imul    eax, eax, RECSZ
        add     eax, [edi + (g_buf - .here)]
        mov     [HOST], eax
        PRINT   s_r
        xor     ecx, ecx
.pf:    push    ecx
        mov     eax, [HOST]
        HEX     dword [eax + ecx * 4]
        PRINT   s_sp
        pop     ecx
        inc     ecx
        cmp     ecx, 5
        jb      .pf
        PRINT   s_nl
        inc     dword [INDEX]
        jmp     .pl
.freebuf:
        push    dword [edi + (g_buf - .here)]
        call    [ebx + 0x30]
        add     esp, 4
        xor     eax, eax
        jmp     .ret
.nohandles:
        push    dword [edi + (g_buf - .here)]
        call    [ebx + 0x30]
        add     esp, 4
        PRINT   s_none
        mov     eax, 0x8000000e
.ret:   pop     edi
        pop     esi
        pop     ebx
        leave
        ret

; patch_all: in [HANDLES]/[COUNT] (caller's frame: ebp), ebx = BS, edi = entry.here. Patches (or,
; with g_restore set, restores) each handle's SdHostIo SendCommand.
patch_all:
        push    esi
        xor     esi, esi
.l:     cmp     esi, [COUNT]
        jae     .o
        mov     edx, [HANDLES]
        lea     eax, [HOST]
        push    eax
        lea     eax, [edi + (sdh_guid - entry.here)]
        push    eax
        push    dword [edx + esi * 4]
        call    [ebx + 0x58]                    ; HandleProtocol
        add     esp, 12
        test    eax, eax
        jnz     .n
        push    dword [HOST]
        call    patch_host
        add     esp, 4
.n:     inc     esi
        jmp     .l
.o:     pop     esi
        ret

; patch_host(SdHostIo *h): cdecl, keeps ebx, esi, edi.
patch_host:
        push    edi
        call    .here
.here:  pop     edi
        mov     edx, [esp + 8]
        lea     ecx, [edi + (wrap_send - .here)]
        mov     eax, [edx + 0x0c]
        cmp     byte [edi + (g_restore - .here)], 0
        jne     .rest
        cmp     eax, ecx
        je      .o                              ; already ours
        cmp     dword [edi + (g_osend - .here)], 0
        jne     .chk
        mov     [edi + (g_osend - .here)], eax
.chk:   cmp     eax, [edi + (g_osend - .here)]
        jne     .o                              ; a different SendCommand: leave it
        mov     [edx + 0x0c], ecx
        jmp     .o
.rest:  cmp     eax, ecx
        jne     .o
        mov     eax, [edi + (g_osend - .here)]
        mov     [edx + 0x0c], eax
.o:     pop     edi
        ret

; VOID EFIAPI sdh_notify(EFI_EVENT, VOID *): patch every SdHostIo installed since the last call.
sdh_notify:
        push    ebp
        mov     ebp, esp
        sub     esp, 12                         ; -4 handle, -8 size, -12 interface
        push    ebx
        push    edi
        call    .here
.here:  pop     edi
        mov     ebx, [edi + (g_bs - .here)]
.next:  mov     dword [ebp - 8], 4
        lea     eax, [ebp - 4]
        push    eax
        lea     eax, [ebp - 8]
        push    eax
        push    dword [edi + (g_reg - .here)]
        push    0
        push    1                               ; ByRegisterNotify
        call    [ebx + 0x64]                    ; LocateHandle
        add     esp, 20
        test    eax, eax
        jnz     .o
        lea     eax, [ebp - 12]
        push    eax
        lea     eax, [edi + (sdh_guid - .here)]
        push    eax
        push    dword [ebp - 4]
        call    [ebx + 0x58]
        add     esp, 12
        test    eax, eax
        jnz     .next
        push    dword [ebp - 12]
        call    patch_host
        add     esp, 4
        jmp     .next
.o:     pop     edi
        pop     ebx
        leave
        ret

; tsc: eax = TSC >> 10. Clobbers edx.
tsc:
        rdtsc
        shrd    eax, edx, 10
        ret

; mark: eax = tag, ecx = arg. A one-off record at the current time. Keeps all but eax, ecx, edx.
mark:
        push    eax
        call    tsc
        pop     edx
        push    1                               ; no merging
        push    eax                             ; end
        push    eax                             ; start
        push    ecx
        push    edx
        call    add_rec
        add     esp, 20
        ret

; add_rec(tag, arg, start, end, nomerge): cdecl, keeps ebx, esi, edi.
add_rec:
        push    ebx
        push    edi
        call    .here
.here:  pop     edi
        mov     ecx, [edi + (g_n - .here)]
        cmp     dword [esp + 28], 0
        jne     .new
        mov     ebx, 1                          ; the last record, then (past a C or S) the one before
.try:   cmp     ecx, ebx
        jb      .new
        mov     eax, ecx
        sub     eax, ebx
        imul    eax, eax, RECSZ
        add     eax, [edi + (g_buf - .here)]
        mov     edx, [esp + 12]
        cmp     [eax], edx
        jne     .nx
        mov     edx, [esp + 16]
        cmp     [eax + 4], edx
        jne     .nx
        mov     edx, [esp + 24]
        mov     [eax + 12], edx                 ; same again: extend it
        inc     dword [eax + 16]
        jmp     .o
.nx:    cmp     ebx, 2
        jae     .new
        mov     edx, [eax]
        shr     edx, 24
        cmp     edx, 'C'
        je      .back
        cmp     edx, 'S'
        jne     .new
.back:  inc     ebx
        jmp     .try
.new:   cmp     ecx, MAXREC
        jae     .o
        mov     eax, ecx
        imul    eax, eax, RECSZ
        add     eax, [edi + (g_buf - .here)]
        mov     edx, [esp + 12]
        mov     [eax], edx
        mov     edx, [esp + 16]
        mov     [eax + 4], edx
        mov     edx, [esp + 20]
        mov     [eax + 8], edx
        mov     edx, [esp + 24]
        mov     [eax + 12], edx
        mov     dword [eax + 16], 1
        inc     dword [edi + (g_n - .here)]
.o:     pop     edi
        pop     ebx
        ret

; EFI_STATUS EFIAPI wrap_send(This, CmdIndex, Argument, DataType, Buffer, BufferSize,
;                              ResponseType, TimeOut, ResponseData)
wrap_send:
        push    ebp
        mov     ebp, esp
        push    ebx
        push    esi
        push    edi
        call    .here
.here:  pop     edi
        call    tsc
        mov     esi, eax                        ; start
        inc     dword [edi + (g_incmd - .here)]
        mov     ecx, 9
.c:     push    dword [ebp + 4 + ecx * 4]
        loop    .c
        call    [edi + (g_osend - .here)]
        add     esp, 36
        dec     dword [edi + (g_incmd - .here)]
        mov     ebx, eax                        ; status
        call    tsc
        push    0
        push    eax                             ; end
        push    esi                             ; start
        push    dword [ebp + 16]                ; Argument
        mov     eax, ebx                        ; tag: 'C', status (bit 7 = error, low 7 bits), cmd
        shr     eax, 24
        and     eax, 0x80
        mov     edx, ebx
        and     edx, 0x7f
        or      eax, edx
        shl     eax, 16
        movzx   edx, word [ebp + 12]
        or      eax, edx
        or      eax, 'C' << 24
        push    eax
        call    add_rec
        add     esp, 20
        mov     eax, ebx
        pop     edi
        pop     esi
        pop     ebx
        pop     ebp
        ret

; EFI_STATUS EFIAPI wrap_stall(UINTN Microseconds)
wrap_stall:
        push    ebx
        push    esi
        push    edi
        call    .here
.here:  pop     edi
        cmp     dword [edi + (g_incmd - .here)], 0
        je      .out
        inc     dword [edi + (g_nest_n - .here)]
        mov     eax, [esp + 16]
        add     [edi + (g_nest_us - .here)], eax
        push    eax
        call    [edi + (g_ostall - .here)]
        add     esp, 4
        jmp     .o
.out:   call    tsc
        mov     esi, eax
        push    dword [esp + 16]
        call    [edi + (g_ostall - .here)]
        add     esp, 4
        mov     ebx, eax
        call    tsc
        push    0
        push    eax
        push    esi
        push    dword [esp + 28]                ; Microseconds (esp moved by 12)
        push    'S' << 24
        call    add_rec
        add     esp, 20
        mov     eax, ebx
.o:     pop     edi
        pop     esi
        pop     ebx
        ret

%ifdef SKIPCEATA
; skip_ceata: in ebx = BS, edi = entry.here. If the loaded MmcMediaDevice (FV file DD223EF8-6D1B-
; 490A-A53E-BA86FAAEB778) has exactly the stock CE-ATA check at RVA 0x72b (79 bytes, no relocated
; dwords), make it return FALSE at once: xor al, al; ret. Then an MMC card is set up as it is now,
; without the CMD60 (CE-ATA RW_MULTIPLE_REGISTER) that no MMC answers and that costs 2.8 s. In RAM
; only, until the next reset. eax = 1 if patched (now or before), else 0. Keeps ebx, esi, edi.
CEATA_RVA       equ     0x72b
CEATA_LEN       equ     79
skip_ceata:
        push    ebp
        mov     ebp, esp
        sub     esp, 16                         ; -4 count, -8 buffer, -12 LoadedImage, -16 index
        push    esi
        push    edi
        xor     eax, eax
        mov     [ebp - 4], eax
        mov     [ebp - 8], eax
        mov     [ebp - 16], eax
        mov     [edi + (g_patched - entry.here)], eax
        lea     eax, [ebp - 8]
        push    eax
        lea     eax, [ebp - 4]
        push    eax
        push    0
        lea     eax, [edi + (li_guid - entry.here)]
        push    eax
        push    2
        call    [ebx + 0xa8]                    ; LocateHandleBuffer(ByProtocol, LoadedImage)
        add     esp, 20
        test    eax, eax
        jnz     .none
.loop:  mov     eax, [ebp - 16]
        cmp     eax, [ebp - 4]
        jae     .free
        mov     edx, [ebp - 8]
        lea     ecx, [ebp - 12]
        push    ecx
        lea     ecx, [edi + (li_guid - entry.here)]
        push    ecx
        push    dword [edx + eax * 4]
        call    [ebx + 0x58]                    ; HandleProtocol
        add     esp, 12
        test    eax, eax
        jnz     .next
        mov     edx, [ebp - 12]
        mov     esi, [edx + 0x10]               ; FilePath: one MEDIA_FW_VOL_FILEPATH node
        test    esi, esi
        jz      .next
        cmp     dword [esi], 0x00140604
        jne     .next
        push    edi
        add     esi, 4
        lea     edi, [edi + (mmcdev_guid - entry.here)]
        mov     ecx, 16
        cld
        repe    cmpsb
        pop     edi
        jne     .next
        mov     edx, [ebp - 12]
        cmp     dword [edx + 0x28], CEATA_RVA + CEATA_LEN   ; ImageSize (low half)
        jb      .next
        mov     esi, [edx + 0x20]               ; ImageBase
        test    esi, esi
        jz      .next
        add     esi, CEATA_RVA
        cmp     word [esi], 0xc032              ; already done (xor al, al; ret)
        jne     .cmp
        cmp     byte [esi + 2], 0xc3
        je      .done
.cmp:   push    edi
        lea     edi, [edi + (ceata_sig - entry.here)]
        mov     ecx, CEATA_LEN
        repe    cmpsb
        pop     edi
        jne     .next
        sub     esi, CEATA_LEN
        mov     word [esi], 0xc032              ; xor al, al
        mov     byte [esi + 2], 0xc3            ; ret
.done:  mov     dword [edi + (g_patched - entry.here)], 1
        jmp     .free
.next:  inc     dword [ebp - 16]
        jmp     .loop
.free:  push    dword [ebp - 8]
        call    [ebx + 0x30]                    ; FreePool
        add     esp, 4
.none:  mov     eax, [edi + (g_patched - entry.here)]
        pop     edi
        pop     esi
        leave
        ret
g_patched:      dd 0
li_guid:                                        ; 5B1B31A1-9562-11D2-8E3F-00A0C969723B LoadedImage
        dd      0x5b1b31a1
        dw      0x9562, 0x11d2
        db      0x8e, 0x3f, 0x00, 0xa0, 0xc9, 0x69, 0x72, 0x3b
mmcdev_guid:                                    ; DD223EF8-6D1B-490A-A53E-BA86FAAEB778
        dd      0xdd223ef8
        dw      0x6d1b, 0x490a
        db      0xa5, 0x3e, 0xba, 0x86, 0xfa, 0xae, 0xb7, 0x78
ceata_sig:      ; MmcMediaDevice RVA 0x72b-0x779 (in every OF1 build checked)
        db      0x56, 0x8b, 0x74, 0x24, 0x08, 0x8d, 0x86, 0xa0, 0x02, 0x00, 0x00, 0x50, 0x6a, 0x00, 0x6a, 0x10
        db      0x6a, 0x00, 0x56, 0xe8, 0x1d, 0xfb, 0xff, 0xff, 0x83, 0xc4, 0x14, 0x85, 0xc0, 0x7d, 0x0f, 0x8b
        db      0x76, 0x48, 0x6a, 0x03, 0x56, 0xff, 0x56, 0x1c, 0x59, 0x59, 0x32, 0xc0, 0x5e, 0xc3, 0x80, 0xbe
        db      0xac, 0x02, 0x00, 0x00, 0xce, 0x75, 0xf3, 0x80, 0xbe, 0xad, 0x02, 0x00, 0x00, 0xaa, 0x75, 0xea
        db      0x8b, 0x76, 0x48, 0x6a, 0x00, 0x56, 0xff, 0x56, 0x20, 0x59, 0x59, 0xb0, 0x01, 0x5e, 0xc3
%endif

%include "con.inc"

        align   4, db 0
g_bs:           dd 0
g_buf:          dd 0
g_n:            dd 0
g_reg:          dd 0
g_osend:        dd 0
g_ostall:       dd 0
g_incmd:        dd 0
g_nest_n:       dd 0
g_nest_us:      dd 0
g_restore:      db 0
        align   4, db 0
sdh_guid:                                       ; B63F8EC7-A9C9-4472-A4C0-4D8BF365CC51 SdHostIo
        dd      0xb63f8ec7
        dw      0xa9c9, 0x4472
        db      0xa4, 0xc0, 0x4d, 0x8b, 0xf3, 0x65, 0xcc, 0x51
s_head: dw      __utf16__('MmcProbe: nested stalls, their us, SendCommand at '), 0
s_sp:   dw      __utf16__(' '), 0
s_nl:   dw      13, 10, 0
s_r:    dw      __utf16__('R '), 0
s_none: dw      __utf16__('MmcProbe: no SdHostIo'), 13, 10, 0
