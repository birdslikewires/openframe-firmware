; timing.inc: only in the boot-timing development build (-DTIMING). It records when things
; happen during boot, to find where the firmware's time goes, and hands the records to the OS.
;
; A record is the event's tag (a dword: four characters, or the first dword of a driver's file
; GUID) and the CPU's timestamp counter at that moment, shifted right by 10. The counter starts
; at zero at power-on and runs at a constant rate (1330 MHz on the OF1), so one unit is 0.77 us
; and 32 bits last over 50 minutes. Watched: this driver's entry, each driver loaded after it (by
; file GUID), protocols appearing (graphics, BlockIo, file systems, USB devices, console in and
; out, PCI devices), the boot manager's stage signals, ReadyToBoot. The notifies run at
; TPL_NOTIFY, ahead of ordinary callbacks, so each stamp is close to its event.
;
; At ReadyToBoot the records go into the volatile variable BlwBootTimes (AMI's GUID EC87D643, as
; BlwMac's, BS+RT, no NV bit: RAM only, nothing reaches the flash). Linux reads it with efivarfs.
; The release build has none of this, and no SetVariable at all.

TM_MAX          equ     192

; tm_rec: eax = tag. Records it with the current timestamp. Keeps every register but eax.
tm_rec:
        push    ebx
        push    ecx
        push    edx
        push    esi
        pushfd                                  ; no notify between reading and bumping the count
        cli
        mov     esi, eax
        call    .here
.here:  pop     ebx
        mov     ecx, [ebx + (tm_count - .here)]
        cmp     ecx, TM_MAX
        jae     .full
        rdtsc
        shrd    eax, edx, 10
        mov     [ebx + (tm_buf - .here) + ecx * 8], esi
        mov     [ebx + (tm_buf - .here) + ecx * 8 + 4], eax
        inc     dword [ebx + (tm_count - .here)]
.full:  popfd
        pop     esi
        pop     edx
        pop     ecx
        pop     ebx
        ret

; tm_watch: in edi = entry.here, [ebp+12] = SystemTable, eax = protocol GUID, ecx = tag. A notify
; that records the tag each time the protocol is installed. Keeps ebx, esi, edi.
tm_watch:
        push    ebx
        push    esi
        mov     esi, eax
        mov     ebx, [ebp + 12]
        mov     ebx, [ebx + 0x3c]
        push    0                               ; Event, Registration
        push    0
        lea     eax, [esp + 4]
        push    eax                             ; &Event
        push    ecx                             ; NotifyContext = tag
        lea     eax, [edi + (tm_notify - entry.here)]
        push    eax
        push    16                              ; TPL_NOTIFY
        push    0x200                           ; EVT_NOTIFY_SIGNAL
        call    [ebx + 0x34]                    ; CreateEvent
        add     esp, 20
        test    eax, eax
        jnz     .out
        mov     eax, esp
        push    eax
        push    dword [esp + 8]
        push    esi
        call    [ebx + 0x60]                    ; RegisterProtocolNotify
        add     esp, 12
        test    eax, eax
        jz      .out
        push    dword [esp + 4]
        call    [ebx + 0x44]                    ; CloseEvent
        add     esp, 4
.out:   add     esp, 8
        pop     esi
        pop     ebx
        ret

; VOID EFIAPI tm_notify(EFI_EVENT Event, VOID *Context = tag)
tm_notify:
        mov     eax, [esp + 8]
        call    tm_rec
        ret

; tm_watch_images: in edi = entry.here, [ebp+12] = SystemTable. A LoadedImage notify that records
; each newly loaded driver by the first dword of its FV file GUID ('LI??' if it has no such path).
; Keeps ebx, esi, edi.
tm_watch_images:
        push    ebx
        push    esi
        mov     ebx, [ebp + 12]
        mov     ebx, [ebx + 0x3c]
        push    0                               ; Event
        mov     eax, esp
        push    eax
        push    ebx                             ; NotifyContext = BS
        lea     eax, [edi + (tm_li_notify - entry.here)]
        push    eax
        push    16
        push    0x200
        call    [ebx + 0x34]
        add     esp, 20
        test    eax, eax
        jnz     .out
        lea     eax, [edi + (tm_li_reg - entry.here)]
        push    eax                             ; &Registration (kept: LocateHandle needs it)
        push    dword [esp + 4]
        lea     eax, [edi + (nbp_li_guid - entry.here)]
        push    eax
        call    [ebx + 0x60]
        add     esp, 12
.out:   add     esp, 4
        pop     esi
        pop     ebx
        ret

; tm_pre: in edi = entry.here, [ebp+12] = SystemTable. Records 'PRE ', then the file GUID of each
; image loaded before this driver (in handle order, which is load order; all with about the
; entry's time: the order is what counts), then 'PREE'. Keeps ebx, esi, edi.
tm_pre:
        push    ebx
        push    esi
        push    edi
        mov     eax, 'PRE '
        call    tm_rec
        mov     ebx, [ebp + 12]
        mov     ebx, [ebx + 0x3c]
        lea     esi, [edi + (nbp_li_guid - entry.here)]
        push    0                               ; -> [esp+8] buffer, [esp+4] count, [esp] index
        push    0
        push    0
        lea     eax, [esp + 8]
        push    eax                             ; &Buffer
        lea     eax, [esp + 8]
        push    eax                             ; &Count
        push    0
        push    esi
        push    2                               ; ByProtocol
        call    [ebx + 0xa8]                    ; LocateHandleBuffer
        add     esp, 20
        test    eax, eax
        jnz     .end
.loop:  mov     eax, [esp]
        cmp     eax, [esp + 4]
        jae     .free
        mov     edx, [esp + 8]
        push    0                               ; LoadedImage
        mov     ecx, esp
        push    ecx
        push    esi
        push    dword [edx + eax * 4]
        call    [ebx + 0x58]                    ; HandleProtocol
        add     esp, 12
        pop     edx
        mov     ecx, 'LI??'
        test    eax, eax
        jnz     .rec
        mov     eax, [edx + 0x10]               ; FilePath
        test    eax, eax
        jz      .rec
        cmp     dword [eax], 0x00140604
        jne     .rec
        mov     ecx, [eax + 4]
.rec:   mov     eax, ecx
        call    tm_rec
        inc     dword [esp]
        jmp     .loop
.free:  push    dword [esp + 8]
        call    [ebx + 0x30]                    ; FreePool
        add     esp, 4
.end:   add     esp, 12
        mov     eax, 'PREE'
        call    tm_rec
        pop     edi
        pop     esi
        pop     ebx
        ret

; VOID EFIAPI tm_li_notify(EFI_EVENT Event, VOID *Context = BS): every image installed since the
; last call, through LocateHandle(ByRegisterNotify).
tm_li_notify:
        push    ebp
        mov     ebp, esp
        sub     esp, 12                         ; -4 handle, -8 size, -12 LoadedImage
        push    ebx
        push    esi
        push    edi
        call    .here
.here:  pop     edi
        mov     ebx, [ebp + 12]                 ; BS
.next:  mov     dword [ebp - 8], 4
        lea     eax, [ebp - 4]
        push    eax                             ; Buffer
        lea     eax, [ebp - 8]
        push    eax                             ; &BufferSize
        push    dword [edi + (tm_li_reg - .here)]   ; SearchKey
        push    0                               ; Protocol
        push    1                               ; ByRegisterNotify
        call    [ebx + 0x64]                    ; LocateHandle
        add     esp, 20
        test    eax, eax
        jnz     .done
        lea     eax, [ebp - 12]
        push    eax
        lea     eax, [edi + (nbp_li_guid - .here)]
        push    eax
        push    dword [ebp - 4]
        call    [ebx + 0x58]                    ; HandleProtocol
        add     esp, 12
        mov     ecx, 'LI??'
        test    eax, eax
        jnz     .rec
        mov     eax, [ebp - 12]
        mov     eax, [eax + 0x10]               ; FilePath
        test    eax, eax
        jz      .rec
        cmp     dword [eax], 0x00140604
        jne     .rec
        mov     ecx, [eax + 4]                  ; the file GUID's first dword
.rec:   mov     eax, ecx
        call    tm_rec
        jmp     .next
.done:  pop     edi
        pop     esi
        pop     ebx
        leave
        ret

; tm_publish(EFI_SYSTEM_TABLE *St): writes the records into the volatile variable BlwBootTimes.
; cdecl. Keeps ebx, esi, edi.
tm_publish:
        push    ebx
        call    .here
.here:  pop     ebx
        mov     ecx, [ebx + (tm_count - .here)]
        shl     ecx, 3
        lea     eax, [ebx + (tm_buf - .here)]
        push    eax                             ; Data
        push    ecx                             ; DataSize
        push    6                               ; BS | RT: volatile
        lea     eax, [ebx + (blw_guid_t - .here)]
        push    eax
        lea     eax, [ebx + (tm_name - .here)]
        push    eax
        mov     eax, [esp + 28]                 ; St
        mov     eax, [eax + 0x38]               ; RuntimeServices
        call    [eax + 0x38]                    ; SetVariable
        add     esp, 20
        pop     ebx
        ret

tm_li_reg:      dd 0
tm_count:       dd 0
blw_guid_t:                                     ; EC87D643-EBA4-4BB5-A1E5-3F3E36B20DA9 (AMI's)
        dd      0xec87d643
        dw      0xeba4, 0x4bb5
        db      0xa1, 0xe5, 0x3f, 0x3e, 0x36, 0xb2, 0x0d, 0xa9
tm_name:        dw      __utf16__('BlwBootTimes'), 0
tm_g_gop:       dd 0x9042a9de
                dw 0x23dc, 0x4a38
                db 0x96, 0xfb, 0x7a, 0xde, 0xd0, 0x80, 0x51, 0x6a
tm_g_sfs:       dd 0x964e5b22
                dw 0x6459, 0x11d2
                db 0x8e, 0x39, 0x00, 0xa0, 0xc9, 0x69, 0x72, 0x3b
tm_g_usb:       dd 0x2b2f68d6
                dw 0x0cd2, 0x44cf
                db 0x8e, 0x8b, 0xbb, 0xa2, 0x0b, 0x1b, 0x5b, 0x75
tm_g_out:       dd 0x387477c2
                dw 0x69c7, 0x11d2
                db 0x8e, 0x39, 0x00, 0xa0, 0xc9, 0x69, 0x72, 0x3b
tm_g_in:        dd 0x387477c1
                dw 0x69c7, 0x11d2
                db 0x8e, 0x39, 0x00, 0xa0, 0xc9, 0x69, 0x72, 0x3b
tm_g_pci:       dd 0x4cf5b200
                dw 0x68b8, 0x4ca5
                db 0x9e, 0xec, 0xb2, 0x3e, 0x3f, 0x50, 0x02, 0x9a
tm_g_stg1:      dd 0x2df1e051                   ; the boot manager's stage signals (CORE_DXE's table)
                dw 0x906d, 0x4eff
                db 0x86, 0x9d, 0x24, 0xe6, 0x53, 0x78, 0xfb, 0x9e
tm_g_stg3:      dd 0xdbc9fd21
                dw 0xfad8, 0x45b0
                db 0x9e, 0x78, 0x27, 0x15, 0x88, 0x67, 0xcc, 0x93
tm_g_stg4:      dd 0x8ff925f1
                dw 0x8624, 0x4d38
                db 0x9e, 0xd2, 0xf8, 0xf5, 0xaa, 0x94, 0xf8, 0x4a
        align   4, db 0
tm_buf:         times TM_MAX * 2 dd 0
