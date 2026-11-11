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
%ifdef PROFILE
        mov     eax, edx
        call    tm_img_add
%endif
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
%ifdef PROFILE
        mov     eax, [ebp - 12]
        call    tm_img_add
%endif
%ifdef USBTRACE
        mov     eax, [ebp - 12]
        call    ut_image
%endif
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

%ifdef PROFILE
; ---- Sampling profiler (development build: --define TIMING --define PROFILE) ----
; A periodic timer event (10 ms, TPL_NOTIFY) samples what the CPU was doing when the timer
; interrupt came: the interrupted EIP, found as the interrupt frame (EIP, CS, EFLAGS) on the stack
; above the notify (the core dispatches TPL_NOTIFY events from the timer interrupt itself), and up
; to two return addresses from the interrupted stack that point just after a call instruction in
; a loaded image. Time spent in SMM shows as a longer gap before the next sample. The images'
; file GUIDs, bases and sizes are kept as they load (tm_pre, tm_li_notify), so the samples can be
; mapped to drivers. At ReadyToBoot the timer stops and everything goes into volatile variables:
; BlwProfImg (12 bytes an image) and BlwProf00, 01, ... (64 samples of 16 bytes each: TSC >> 10,
; EIP or 0 if no frame was found, return address, return address). Reads only, never dereferences
; anything outside the stack it was called on and the loaded images.

TP_MAX          equ     512
TP_IMAX         equ     128
TP_CHUNK        equ     64

; tm_img_add: eax = EFI_LOADED_IMAGE_PROTOCOL *. Appends (file GUID's first dword or 'LI??',
; ImageBase, ImageSize) to the image table. Keeps every register but eax.
tm_img_add:
        push    ebx
        push    ecx
        push    edx
        push    esi
        mov     esi, eax
        call    .here
.here:  pop     ebx
        mov     ecx, [ebx + (tp_nimg - .here)]
        cmp     ecx, TP_IMAX
        jae     .o
        mov     edx, 'LI??'
        mov     eax, [esi + 0x10]
        test    eax, eax
        jz      .t
        cmp     dword [eax], 0x00140604
        jne     .t
        mov     edx, [eax + 4]
.t:     lea     ecx, [ecx + ecx * 2]
        lea     ecx, [ebx + (tp_imgs - .here) + ecx * 4]
        mov     [ecx], edx
        mov     eax, [esi + 0x20]
        mov     [ecx + 4], eax
        mov     eax, [esi + 0x28]
        mov     [ecx + 8], eax
        inc     dword [ebx + (tp_nimg - .here)]
.o:     pop     esi
        pop     edx
        pop     ecx
        pop     ebx
        ret

; tm_prof_start: in edi = entry.here, [ebp+12] = SystemTable. Starts the 10 ms sampling timer.
; Keeps ebx, esi, edi.
tm_prof_start:
        push    ebx
        mov     ebx, [ebp + 12]
        mov     ebx, [ebx + 0x3c]
        lea     eax, [edi + (tp_ev - entry.here)]
        push    eax                             ; &Event
        push    0
        lea     eax, [edi + (tm_prof_tick - entry.here)]
        push    eax
        push    16                              ; TPL_NOTIFY
        push    0x80000200                      ; EVT_TIMER | EVT_NOTIFY_SIGNAL
        call    [ebx + 0x34]                    ; CreateEvent
        add     esp, 20
        test    eax, eax
        jnz     .o
        push    0                               ; TriggerTime, high
        push    100000                          ; low: 10 ms in 100 ns units
        push    1                               ; TimerPeriodic
        push    dword [edi + (tp_ev - entry.here)]
        call    [ebx + 0x38]                    ; SetTimer
        add     esp, 16
.o:     pop     ebx
        ret

; VOID EFIAPI tm_prof_tick(EFI_EVENT, VOID *)
tm_prof_tick:
        push    ebp
        mov     ebp, esp
        push    ebx
        push    esi
        push    edi
        call    .here
.here:  pop     edi
        mov     eax, [edi + (tp_n - .here)]
        cmp     eax, TP_MAX
        jae     .o
        xor     ebx, ebx                        ; the interrupted EIP, if a frame is found
        mov     ecx, cs
        lea     esi, [ebp + 16]                 ; above this call's arguments
        lea     edx, [ebp + 16 + 0x300]
.s:     cmp     esi, edx
        jae     .nf
        cmp     [esi + 4], ecx                  ; CS
        jne     .n
        mov     eax, [esi + 8]                  ; EFLAGS: bit 1 set, IF set, VM and the top 10 clear
        and     eax, 0xffc20202
        cmp     eax, 0x202
        jne     .n
        mov     eax, [esi]
        cmp     eax, 0x1000
        jb      .n
        cmp     eax, 0xc0000000
        jae     .n
        mov     ebx, eax
        add     esi, 12                         ; the interrupted stack
        jmp     .f
.n:     add     esi, 4
        jmp     .s
.nf:    xor     esi, esi
.f:     mov     eax, [edi + (tp_n - .here)]
        shl     eax, 4
        lea     ecx, [edi + (tp_buf - .here) + eax]
        push    ecx
        rdtsc
        shrd    eax, edx, 10
        pop     ecx
        mov     [ecx], eax
        mov     [ecx + 4], ebx
        mov     dword [ecx + 8], 0
        mov     dword [ecx + 12], 0
        test    esi, esi
        jz      .done
        lea     edx, [ecx + 8]                  ; where the next return address goes
        lea     ebx, [esi + 0x100]
.r:     cmp     esi, ebx
        jae     .done
        mov     eax, [esi]
        call    tp_is_ret
        test    eax, eax
        jz      .rn
        mov     eax, [esi]
        mov     [edx], eax
        add     edx, 4
        lea     eax, [ecx + 16]
        cmp     edx, eax
        jae     .done
.rn:    add     esi, 4
        jmp     .r
.done:  inc     dword [edi + (tp_n - .here)]
.o:     pop     edi
        pop     esi
        pop     ebx
        leave
        ret

; tp_is_ret: eax = a value from the stack. eax = 1 if it points into a loaded image, at least 8
; bytes in, just after a call (E8 rel32, or FF /2: register, [reg], [reg+disp8], [disp32] or
; [reg+disp32]); else 0. Keeps every other register.
tp_is_ret:
        push    ebx
        push    ecx
        push    edx
        push    esi
        call    .here
.here:  pop     esi
        mov     ecx, [esi + (tp_nimg - .here)]
        lea     esi, [esi + (tp_imgs - .here)]
.l:     test    ecx, ecx
        jz      .no
        mov     ebx, eax
        sub     ebx, [esi + 4]
        jb      .nx
        cmp     ebx, [esi + 8]
        jae     .nx
        cmp     ebx, 8
        jb      .nx
        cmp     byte [eax - 5], 0xe8
        je      .yes
        cmp     byte [eax - 6], 0xff
        jne     .c3
        mov     dl, [eax - 5]
        and     dl, 0x38
        cmp     dl, 0x10
        je      .yes
.c3:    cmp     byte [eax - 3], 0xff
        jne     .c2
        mov     dl, [eax - 2]
        and     dl, 0x38
        cmp     dl, 0x10
        je      .yes
.c2:    cmp     byte [eax - 2], 0xff
        jne     .nx
        mov     dl, [eax - 1]
        and     dl, 0x38
        cmp     dl, 0x10
        je      .yes
.nx:    add     esi, 12
        dec     ecx
        jmp     .l
.no:    xor     eax, eax
        jmp     .o
.yes:   mov     eax, 1
.o:     pop     esi
        pop     edx
        pop     ecx
        pop     ebx
        ret

; tp_publish(EFI_SYSTEM_TABLE *St): stops the timer and writes the image table and the samples
; into volatile variables (BS+RT, no NV bit). cdecl. Keeps ebx, esi, edi.
tp_publish:
        push    ebx
        push    esi
        push    edi
        call    .here
.here:  pop     edi
        mov     ebx, [esp + 16]                 ; St
        mov     eax, [ebx + 0x3c]
        mov     ecx, [edi + (tp_ev - .here)]
        test    ecx, ecx
        jz      .img
        push    0
        push    0
        push    0                               ; TimerCancel
        push    ecx
        call    [eax + 0x38]                    ; SetTimer
        add     esp, 16
.img:   mov     eax, [edi + (tp_nimg - .here)]
        lea     eax, [eax + eax * 2]
        shl     eax, 2
        lea     ecx, [edi + (tp_imgs - .here)]
        lea     edx, [edi + (tp_name_img - .here)]
        call    .set
        xor     esi, esi                        ; chunk number
.chunk: mov     eax, esi
        shl     eax, 6                          ; * TP_CHUNK
        mov     ecx, [edi + (tp_n - .here)]
        sub     ecx, eax
        jbe     .o
        cmp     ecx, TP_CHUNK
        jbe     .sz
        mov     ecx, TP_CHUNK
.sz:    shl     eax, 4
        lea     edx, [edi + (tp_buf - .here) + eax]
        mov     eax, ecx
        shl     eax, 4                          ; size
        mov     ecx, edx                        ; data
        mov     edx, esi                        ; name: BlwProf + two hex digits
        shr     edx, 4
        call    .hex
        mov     [edi + (tp_name_s - .here) + 14], dx
        mov     edx, esi
        call    .hex
        mov     [edi + (tp_name_s - .here) + 16], dx
        lea     edx, [edi + (tp_name_s - .here)]
        call    .set
        inc     esi
        jmp     .chunk
.o:     pop     edi
        pop     esi
        pop     ebx
        ret
; .hex: edx = value (low 4 bits used) -> dx = its hex digit
.hex:   and     edx, 0xf
        add     edx, '0'
        cmp     edx, '9'
        jbe     .h
        add     edx, 'A' - '9' - 1
.h:     ret
; .set: eax = size, ecx = data, edx = name; ebx = St, edi = .here. SetVariable, volatile.
.set:   push    ecx
        push    eax
        push    6                               ; BS | RT
        lea     eax, [edi + (blw_guid_t - .here)]
        push    eax
        push    edx
        mov     eax, [ebx + 0x38]               ; RuntimeServices
        call    [eax + 0x38]                    ; SetVariable
        add     esp, 20
        ret

        align   4, db 0
tp_ev:          dd 0
tp_n:           dd 0
tp_nimg:        dd 0
tp_name_img:    dw      __utf16__('BlwProfImg'), 0
tp_name_s:      dw      __utf16__('BlwProf00'), 0
        align   4, db 0
tp_imgs:        times TP_IMAX * 3 dd 0
tp_buf:         times TP_MAX * 4 dd 0
%endif

%ifdef USBTRACE
; ---- USB trace (development build: --define TIMING --define PROFILE --define USBTRACE) ----
; AMI's USB stack does its work in SMM (USBRT), entered from UHCD through SmmControl->Trigger
; (software SMI 0x31, a request block at gUsbData+0xdd9 whose first bytes are the function and
; subfunction). The timer can't sample that, so this records, in normal (DXE) code:
;   T: every SmmControl->Trigger: start, end, the UHCD function that asked, SMI value, request
;      function and subfunction, status;
;   S: every BS->Stall: start, end, caller, microseconds (repeats merged, with a count);
;   C: as each USBRT copy loads, and again at ReadyToBoot, whether its code reads back as the
;      stock bytes (read only: closed SMRAM reads as all ones), to see whether USBRT could be
;      patched in memory.
; Both wrappers are put back at ReadyToBoot (the boot-services table CRC fixed each time), then
; the records go into volatile variables BlwUsbTr00, 01, ... (48 records of 20 bytes each:
; start, end, caller, info, extra).

UT_MAX          equ     1024
UT_CHUNK        equ     48
UT_SMMC_GUID0   equ     0x8d12e231              ; EFI_SMM_CONTROL_PROTOCOL 8D12E231-C667-4FD1-98F2-2449A7E7B2E5
UT_UHCD         equ     0x580dd900              ; UHCD's file GUID, first dword
UT_USBRT        equ     0x04eaaaa1              ; USBRT's

; ut_ok_ptr: eax = a pointer. ZF set if it is plausibly in RAM (0x1000 to 512 MB), else clear.
ut_ok_ptr:
        cmp     eax, 0x1000
        jb      .no
        cmp     eax, 0x20000000
        jae     .no
        cmp     eax, eax                        ; ZF = 1
        ret
.no:    or      eax, eax                        ; eax >= 0x1000 or nonzero: ZF = 0
        jnz     .r
        inc     eax                             ; eax was 0: make it nonzero, ZF = 0
.r:     ret

; ut_bs_crc(EFI_BOOT_SERVICES *BS): recomputes the table header's CRC32. cdecl, keeps ebx/esi/edi.
ut_bs_crc:
        push    ebx
        mov     ebx, [esp + 8]
        push    dword [ebx + 0x10]              ; the old CRC, kept if it can't be computed
        mov     dword [ebx + 0x10], 0
        push    0
        mov     eax, esp
        push    eax
        push    dword [ebx + 0x0c]
        push    ebx
        call    [ebx + 0xb8]                    ; CalculateCrc32
        add     esp, 12
        pop     edx
        pop     ecx                             ; the old CRC
        test    eax, eax
        jz      .ok
        mov     edx, ecx
.ok:    mov     [ebx + 0x10], edx
        pop     ebx
        ret

; ut_add(start, end, caller, info, extra, merge): appends a record; with merge, a repeat of the
; last record (or of the one before it, past one T/S record) with the same caller and info has its
; end updated and its extra (the count) bumped instead. cdecl, keeps ebx/esi/edi.
ut_add:
        push    ebx
        push    esi
        push    edi
        pushfd
        cli
        call    .here
.here:  pop     edi
        mov     ecx, [edi + (ut_n - .here)]
        cmp     dword [esp + 40], 0             ; merge?
        je      .new
        mov     esi, 1
.try:   cmp     ecx, esi
        jb      .new
        mov     eax, ecx
        sub     eax, esi
        imul    eax, eax, 20
        lea     ebx, [edi + (ut_buf - .here) + eax]
        mov     edx, [esp + 28]                 ; caller
        cmp     [ebx + 8], edx
        jne     .nx
        mov     edx, [esp + 32]                 ; info
        cmp     [ebx + 12], edx
        jne     .nx
        mov     edx, [esp + 24]
        mov     [ebx + 4], edx                  ; end
        inc     dword [ebx + 16]
        jmp     .o
.nx:    cmp     esi, 2
        jae     .new
        inc     esi
        jmp     .try
.new:   cmp     ecx, UT_MAX
        jae     .o
        imul    eax, ecx, 20
        lea     ebx, [edi + (ut_buf - .here) + eax]
        mov     edx, [esp + 20]
        mov     [ebx], edx
        mov     edx, [esp + 24]
        mov     [ebx + 4], edx
        mov     edx, [esp + 28]
        mov     [ebx + 8], edx
        mov     edx, [esp + 32]
        mov     [ebx + 12], edx
        mov     edx, [esp + 36]
        mov     [ebx + 16], edx
        inc     dword [edi + (ut_n - .here)]
.o:     popfd
        pop     edi
        pop     esi
        pop     ebx
        ret

; ut_start: in edi = entry.here, [ebp+12] = SystemTable. Wraps BS->Stall, and SmmControl->Trigger
; now if it is there and as it is installed. Keeps ebx, esi, edi.
ut_start:
        push    ebx
        mov     ebx, [ebp + 12]
        mov     ebx, [ebx + 0x3c]               ; BS
        mov     [edi + (ut_bs - entry.here)], ebx
        mov     eax, [ebx + 0x10]
        mov     [edi + (ut_ocrc - entry.here)], eax ; the table's CRC as the firmware made it
        mov     eax, [ebx + 0x88]
        mov     [edi + (ut_ostall - entry.here)], eax
        lea     eax, [edi + (ut_stall - entry.here)]
        mov     [ebx + 0x88], eax
        push    ebx
        call    ut_bs_crc
        add     esp, 4
        push    0                               ; the interface
        mov     eax, esp
        push    eax
        push    0
        lea     eax, [edi + (ut_smmc_guid - entry.here)]
        push    eax
        call    [ebx + 0xac]                    ; LocateProtocol
        add     esp, 12
        pop     ecx
        test    eax, eax
        jnz     .ev
        push    ecx
        call    ut_patch_trig
        add     esp, 4
.ev:    push    0
        mov     eax, esp
        push    eax                             ; &Event
        push    ebx                             ; context: BS
        lea     eax, [edi + (ut_smmc_notify - entry.here)]
        push    eax
        push    16                              ; TPL_NOTIFY
        push    0x200
        call    [ebx + 0x34]                    ; CreateEvent
        add     esp, 20
        pop     ecx
        test    eax, eax
        jnz     .o
        mov     [edi + (ut_ev - entry.here)], ecx
        lea     eax, [edi + (ut_reg - entry.here)]
        push    eax
        push    ecx
        lea     eax, [edi + (ut_smmc_guid - entry.here)]
        push    eax
        call    [ebx + 0x60]                    ; RegisterProtocolNotify
        add     esp, 12
.o:     pop     ebx
        ret

; VOID EFIAPI ut_smmc_notify(EFI_EVENT, VOID *BS): wraps each SmmControl installed since last time.
ut_smmc_notify:
        push    ebp
        mov     ebp, esp
        sub     esp, 12                         ; -4 handle, -8 size, -12 interface
        push    ebx
        push    edi
        call    .here
.here:  pop     edi
        mov     ebx, [ebp + 12]
.next:  mov     dword [ebp - 8], 4
        lea     eax, [ebp - 4]
        push    eax
        lea     eax, [ebp - 8]
        push    eax
        push    dword [edi + (ut_reg - .here)]
        push    0
        push    1                               ; ByRegisterNotify
        call    [ebx + 0x64]                    ; LocateHandle
        add     esp, 20
        test    eax, eax
        jnz     .o
        lea     eax, [ebp - 12]
        push    eax
        lea     eax, [edi + (ut_smmc_guid - .here)]
        push    eax
        push    dword [ebp - 4]
        call    [ebx + 0x58]                    ; HandleProtocol
        add     esp, 12
        test    eax, eax
        jnz     .next
        push    dword [ebp - 12]
        call    ut_patch_trig
        add     esp, 4
        jmp     .next
.o:     pop     edi
        pop     ebx
        leave
        ret

; ut_patch_trig(EFI_SMM_CONTROL_PROTOCOL *): points its Trigger (offset 0) at ut_trig, once, if it
; is the same Trigger as any wrapped before and wrapping hasn't stopped. Keeps ebx/esi/edi.
ut_patch_trig:
        push    esi
        push    edi
        call    .here
.here:  pop     edi
        mov     esi, [esp + 12]
        mov     eax, esi
        call    ut_ok_ptr
        jnz     .o
        cmp     byte [edi + (ut_stopped - .here)], 0
        jne     .o
        lea     ecx, [edi + (ut_trig - .here)]
        mov     eax, [esi]
        cmp     eax, ecx
        je      .o
        cmp     dword [edi + (ut_otrig - .here)], 0
        jne     .chk
        mov     [edi + (ut_otrig - .here)], eax
.chk:   cmp     eax, [edi + (ut_otrig - .here)]
        jne     .o
        mov     edx, [edi + (ut_ninst - .here)]
        cmp     edx, 4
        jae     .o
        mov     [edi + (ut_inst - .here) + edx * 4], esi
        inc     dword [edi + (ut_ninst - .here)]
        mov     [esi], ecx
.o:     pop     edi
        pop     esi
        ret

; EFI_STATUS EFIAPI ut_trig(This, ArgumentBuffer, ArgumentBufferSize, Periodic, ActivationInterval)
ut_trig:
        mov     eax, ebp                        ; the caller's frame (UHCD's SMI helper keeps one)
        push    ebp
        mov     ebp, esp
        sub     esp, 12                         ; -4 start, -8 info, -12 caller
        push    ebx
        push    esi
        push    edi
        call    .here
.here:  pop     edi
        mov     ecx, [ebp + 4]                  ; by default the return address
        lea     edx, [ebp + 8]                  ; the caller's frame, if it is on this stack: its return
        cmp     eax, edx
        jb      .c1
        lea     edx, [ebp + 0x10000]
        cmp     eax, edx
        jae     .c1
        mov     ecx, [eax + 4]
.c1:    mov     [ebp - 12], ecx
        xor     ebx, ebx                        ; info: SMI value | function << 8 | subfunction << 16
        mov     eax, [ebp + 12]                 ; ArgumentBuffer
        call    ut_ok_ptr
        jnz     .c2
        movzx   ebx, byte [eax]
        cmp     bl, 0x31
        jne     .c2
        call    ut_urp                          ; eax = function | subfunction << 8, or 0
        shl     eax, 8
        or      ebx, eax
.c2:    or      ebx, 'T' << 24
        mov     [ebp - 8], ebx
        rdtsc
        shrd    eax, edx, 10
        mov     [ebp - 4], eax
        mov     ecx, 5
.cp:    push    dword [ebp + 4 + ecx * 4]
        loop    .cp
        call    [edi + (ut_otrig - .here)]
        add     esp, 20
        mov     ebx, eax                        ; status
        rdtsc
        shrd    eax, edx, 10
        push    0                               ; no merging
        push    ebx                             ; extra: status
        push    dword [ebp - 8]
        push    dword [ebp - 12]
        push    eax
        push    dword [ebp - 4]
        call    ut_add
        add     esp, 24
        mov     eax, ebx
        pop     edi
        pop     esi
        pop     ebx
        leave
        ret

; ut_urp: eax = the pending USB request's function | subfunction << 8, found through UHCD's
; globals: only if UHCD is in the image table and its SMI helper is the stock one (mov eax,
; [SmmControl] at +0x4fcd, the variable at +0x62b8), then gUsbData = [+0x62bc] and the request at
; [gUsbData+0xdd9]. 0 if anything doesn't fit. Keeps every other register.
ut_urp:
        push    ecx
        push    edx
        push    esi
        call    .here
.here:  pop     esi
        mov     ecx, [esi + (tp_nimg - .here)]
        lea     esi, [esi + (tp_imgs - .here)]
.l:     test    ecx, ecx
        jz      .no
        cmp     dword [esi], UT_UHCD
        jne     .nx
        cmp     dword [esi + 8], 0x62c0
        jb      .nx
        mov     edx, [esi + 4]                  ; UHCD's base
        cmp     byte [edx + 0x4fcd], 0xa1
        jne     .nx
        lea     eax, [edx + 0x62b8]
        cmp     [edx + 0x4fce], eax
        jne     .nx
        mov     eax, [edx + 0x62bc]             ; gUsbData
        call    ut_ok_ptr
        jnz     .no
        mov     eax, [eax + 0xdd9]              ; the request
        call    ut_ok_ptr
        jnz     .no
        movzx   eax, word [eax]
        jmp     .o
.nx:    add     esi, 12
        dec     ecx
        jmp     .l
.no:    xor     eax, eax
.o:     pop     esi
        pop     edx
        pop     ecx
        ret

; EFI_STATUS EFIAPI ut_stall(UINTN Microseconds)
ut_stall:
        push    ebx
        push    esi
        push    edi
        call    .here
.here:  pop     edi
        rdtsc
        shrd    eax, edx, 10
        mov     esi, eax
        push    dword [esp + 16]
        call    [edi + (ut_ostall - .here)]
        add     esp, 4
        mov     ebx, eax
        rdtsc
        shrd    eax, edx, 10
        push    1                               ; merge
        push    1                               ; extra: count
        mov     ecx, [esp + 24]                 ; Microseconds
        cmp     ecx, 0xffffff
        jbe     .us
        mov     ecx, 0xffffff
.us:    or      ecx, 'S' << 24
        push    ecx
        push    dword [esp + 24]                ; caller: our return address
        push    eax
        push    esi
        call    ut_add
        add     esp, 24
        mov     eax, ebx
        pop     edi
        pop     esi
        pop     ebx
        ret

; ut_check(base, when): a C record for a USBRT copy at base: bit 0 if its FixedDelay (+0x1381)
; reads as the stock bytes, bit 1 if its 1 s wait (+0x530e, push 0x1046a) does; bit 7 = at
; ReadyToBoot. Reads only. cdecl, keeps ebx/esi/edi.
ut_check:
        push    ebx
        mov     edx, [esp + 8]
        xor     ebx, ebx
        cmp     dword [edx + 0x1381], 0x616a5653        ; push ebx; push esi; push 0x61
        jne     .b1
        cmp     byte [edx + 0x1385], 0xe8
        jne     .b1
        or      ebx, 1
.b1:    cmp     dword [edx + 0x530e], 0x01046a68        ; push 0x1046a
        jne     .b2
        cmp     byte [edx + 0x5312], 0
        jne     .b2
        or      ebx, 2
.b2:    or      ebx, [esp + 12]
        or      ebx, 'C' << 24
        rdtsc
        shrd    eax, edx, 10
        push    0
        push    0
        push    ebx
        push    0
        push    dword [esp + 24]                ; end field: the base
        push    eax
        call    ut_add
        add     esp, 24
        pop     ebx
        ret

; ut_image: eax = EFI_LOADED_IMAGE_PROTOCOL * (from tm_li_notify). If it is a USBRT copy big
; enough, checks it and remembers its base. Keeps every register but eax.
ut_image:
        push    ecx
        push    edx
        push    esi
        mov     esi, eax
        mov     eax, [esi + 0x10]               ; FilePath (from the core: only null is odd)
        test    eax, eax
        jz      .o
        cmp     dword [eax], 0x00140604
        jne     .o
        cmp     dword [eax + 4], UT_USBRT
        jne     .o
        cmp     dword [esi + 0x28], 0x5320
        jb      .o
        mov     eax, [esi + 0x20]
        test    eax, eax
        jz      .o
        call    .here
.here:  pop     ecx
        mov     edx, [ecx + (ut_nrt - .here)]
        cmp     edx, 4
        jae     .chk
        mov     [ecx + (ut_rt - .here) + edx * 4], eax
        inc     dword [ecx + (ut_nrt - .here)]
.chk:   push    0
        push    eax
        call    ut_check
        add     esp, 8
.o:     pop     esi
        pop     edx
        pop     ecx
        ret

; ut_stop: puts BS->Stall and each wrapped Trigger back, then checks the USBRT copies again.
; Keeps ebx/esi/edi.
ut_stop:
        push    ebx
        push    esi
        push    edi
        call    .here
.here:  pop     edi
        mov     byte [edi + (ut_stopped - .here)], 1
        mov     ebx, [edi + (ut_bs - .here)]
        test    ebx, ebx
        jz      .t
        lea     eax, [edi + (ut_stall - .here)]
        cmp     [ebx + 0x88], eax
        jne     .t
        mov     eax, [edi + (ut_ostall - .here)]
        mov     [ebx + 0x88], eax
        mov     eax, [edi + (ut_ocrc - .here)]  ; the table is the firmware's again: so is its CRC
        mov     [ebx + 0x10], eax
.t:     xor     esi, esi
.tl:    cmp     esi, [edi + (ut_ninst - .here)]
        jae     .c
        mov     ecx, [edi + (ut_inst - .here) + esi * 4]
        lea     eax, [edi + (ut_trig - .here)]
        cmp     [ecx], eax
        jne     .tn
        mov     eax, [edi + (ut_otrig - .here)]
        mov     [ecx], eax
.tn:    inc     esi
        jmp     .tl
.c:     xor     esi, esi
.cl:    cmp     esi, [edi + (ut_nrt - .here)]
        jae     .o
        push    0x80
        push    dword [edi + (ut_rt - .here) + esi * 4]
        call    ut_check
        add     esp, 8
        inc     esi
        jmp     .cl
.o:     pop     edi
        pop     esi
        pop     ebx
        ret

; ut_publish(EFI_SYSTEM_TABLE *St): the records into BlwUsbTr00, 01, ... cdecl, keeps ebx/esi/edi.
ut_publish:
        push    ebx
        push    esi
        push    edi
        call    .here
.here:  pop     edi
        mov     ebx, [esp + 16]
        xor     esi, esi
.chunk: imul    eax, esi, UT_CHUNK
        mov     ecx, [edi + (ut_n - .here)]
        sub     ecx, eax
        jbe     .o
        cmp     ecx, UT_CHUNK
        jbe     .sz
        mov     ecx, UT_CHUNK
.sz:    imul    eax, eax, 20
        lea     edx, [edi + (ut_buf - .here) + eax]
        imul    eax, ecx, 20
        push    edx                             ; Data
        push    eax                             ; DataSize
        mov     edx, esi
        shr     edx, 4
        call    .hex
        mov     [edi + (ut_name - .here) + 16], dx
        mov     edx, esi
        call    .hex
        mov     [edi + (ut_name - .here) + 18], dx
        push    6
        lea     eax, [edi + (blw_guid_t - .here)]
        push    eax
        lea     eax, [edi + (ut_name - .here)]
        push    eax
        mov     eax, [ebx + 0x38]
        call    [eax + 0x38]                    ; SetVariable
        add     esp, 20
        inc     esi
        jmp     .chunk
.o:     pop     edi
        pop     esi
        pop     ebx
        ret
.hex:   and     edx, 0xf
        add     edx, '0'
        cmp     edx, '9'
        jbe     .h
        add     edx, 'A' - '9' - 1
.h:     ret

        align   4, db 0
ut_bs:          dd 0
ut_ostall:      dd 0
ut_ocrc:        dd 0
ut_otrig:       dd 0
ut_ev:          dd 0
ut_reg:         dd 0
ut_n:           dd 0
ut_ninst:       dd 0
ut_inst:        times 4 dd 0
ut_nrt:         dd 0
ut_rt:          times 4 dd 0
ut_stopped:     db 0
        align   4, db 0
ut_smmc_guid:   dd      UT_SMMC_GUID0
                dw      0xc667, 0x4fd1
                db      0x98, 0xf2, 0x24, 0x49, 0xa7, 0xe7, 0xb2, 0xe5
ut_name:        dw      __utf16__('BlwUsbTr00'), 0
        align   4, db 0
ut_buf:         times UT_MAX * 5 dd 0
%endif
