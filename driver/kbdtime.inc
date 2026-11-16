; kbdtime.inc: only in the keyboard-timing development build (-DKBDTIME). For the Esc console
; (an emergency prompt at boot): when, during a boot, a USB keyboard can first be read, set
; against when the shell asks for its one key, and whether keys pressed before that wait for it.
;
; It fills a second SMBIOS Type 11 string, "KbdTime: " and 48 characters (build_ucode_driver.py
; with --define KBDTIME), as things happen, so Linux reads it with `dmidecode -t 11` (this kernel
; has no efivarfs). Times are milliseconds since reset (the timestamp counter, 1329.993 MHz on the
; OF1), 5 digits; a field still '-' wasn't reached:
;   I-----  the first Simple Text Input (console input) of any kind appears
;   U-----  the first on a USB device path (a USB keyboard)
;   R-----  ReadyToBoot
;   S-----  the shell loads (its countdown made 0: countdown.inc)
;   F-----  the shell's first ReadKeyStroke
;   N--     keys waiting at that call (decimal, 99 at most)
;   K--------  the first of them: ScanCode, UnicodeChar (hex; Esc is 0017 0000)
;   C--u--  Simple Text Inputs, and those on USB, at the last count
;   P-----  when the first key was read (from the shell's first ReadKeyStroke on)
;   W--     keys already waiting at that call (the rest of N came in the window)
; At the shell's first ReadKeyStroke every waiting key is read and counted here; then, for a window
; of KT_WINDOW ms, any that come in (a poll every 10 ms, Stall between: the keyboard's own timer
; events run meanwhile). The shell is then told there was none (EFI_NOT_READY), so it runs
; startup.nsh and the system starts: Esc pressed at boot does not stop at the shell in this
; build, and a boot with it takes KT_WINDOW longer. Later calls go straight through.
;
; Reads only, apart from the string and, once, the console's ReadKeyStroke pointer. The release
; build has none of this.

KT_TSC_KHZ      equ     1329993
KT_I            equ     1                       ; digit offsets in the string
KT_U            equ     7
KT_R            equ     13
KT_S            equ     19
KT_F            equ     25
KT_N            equ     31
KT_KS           equ     34
KT_KU           equ     38
KT_C            equ     43
KT_UN           equ     46
KT_P            equ     49
KT_W            equ     55
KT_WINDOW       equ     2000                    ; ms the shell's first ReadKeyStroke waits for keys
KT_POLL_US      equ     10000                   ; Stall between polls
KT_MAXPOLLS     equ     1000                    ; whatever the clock says
KT_MAXKEYS      equ     200

; kt_start: in edi = entry.here, [ebp+12] = SystemTable. Keeps the system table for the shell's
; hook, watches console inputs appearing, and counts any already there. Keeps ebx, esi, edi.
kt_start:
        mov     eax, [ebp + 12]
        mov     [edi + (kt_st - entry.here)], eax
        lea     eax, [edi + (kt_in_guid - entry.here)]
        lea     ecx, [edi + (kt_in_notify - entry.here)]
        call    notify_on
        mov     eax, [ebp + 12]
        push    dword [eax + 0x3c]
        call    kt_scan
        add     esp, 4
        ret

; VOID EFIAPI kt_in_notify(EFI_EVENT Event, VOID *Context = BootServices)
kt_in_notify:
        push    dword [esp + 8]
        call    kt_scan
        add     esp, 4
        ret

; kt_scan(EFI_BOOT_SERVICES *BS): counts the Simple Text Inputs, and those whose device path has
; a USB node (messaging 3, subtype 5 USB, 0x0f USB class or 0x10 USB WWID); stamps I and U the
; first time each is non-zero. cdecl. Keeps ebx, esi, edi.
kt_scan:
        push    ebp
        mov     ebp, esp
        sub     esp, 20                         ; -4 count, -8 buffer, -12 path, -16 index, -20 USB
        push    ebx
        push    esi
        push    edi
        call    .here
.here:  pop     edi
        xor     eax, eax
        mov     [ebp - 4], eax
        mov     [ebp - 8], eax
        mov     [ebp - 16], eax
        mov     [ebp - 20], eax
        mov     ebx, [ebp + 8]                  ; BS
        lea     eax, [ebp - 8]                  ; LocateHandleBuffer(ByProtocol, &SimpleTextIn, ...)
        push    eax
        lea     eax, [ebp - 4]
        push    eax
        push    0
        lea     eax, [edi + (kt_in_guid - .here)]
        push    eax
        push    2
        call    [ebx + 0xa8]
        add     esp, 20
        test    eax, eax
        jnz     .out
.loop:  mov     eax, [ebp - 16]
        cmp     eax, [ebp - 4]
        jae     .free
        mov     edx, [ebp - 8]
        mov     edx, [edx + eax * 4]
        mov     dword [ebp - 12], 0
        lea     eax, [ebp - 12]                 ; HandleProtocol(handle, &DevicePath, &path)
        push    eax
        lea     eax, [edi + (kt_dp_guid - .here)]
        push    eax
        push    edx
        call    [ebx + 0x58]
        add     esp, 12
        test    eax, eax
        jnz     .next
        mov     esi, [ebp - 12]
        mov     ecx, 32                         ; nodes looked at, at most
.node:  test    esi, esi
        jz      .next
        mov     al, [esi]
        cmp     al, 0x7f                        ; end of the path
        je      .next
        cmp     al, 3
        jne     .adv
        mov     al, [esi + 1]
        cmp     al, 5
        je      .usb
        cmp     al, 0x0f
        je      .usb
        cmp     al, 0x10
        je      .usb
.adv:   movzx   eax, word [esi + 2]
        cmp     eax, 4
        jb      .next
        add     esi, eax
        loop    .node
        jmp     .next
.usb:   inc     dword [ebp - 20]
.next:  inc     dword [ebp - 16]
        jmp     .loop
.free:  push    dword [ebp - 8]
        call    [ebx + 0x30]                    ; FreePool
        add     esp, 4
        cmp     dword [ebp - 4], 0
        je      .c
        mov     edx, KT_I
        call    kt_stamp
.c:     mov     eax, [ebp - 4]
        mov     ecx, 2
        mov     edx, KT_C
        call    kt_dec
        cmp     dword [ebp - 20], 0
        je      .u
        mov     edx, KT_U
        call    kt_stamp
.u:     mov     eax, [ebp - 20]
        mov     ecx, 2
        mov     edx, KT_UN
        call    kt_dec
.out:   pop     edi
        pop     esi
        pop     ebx
        leave
        ret

; kt_shell: the shell has loaded (img_patch, once its countdown is made 0). Stamps S and puts
; kt_rks over the console's ReadKeyStroke (ST->ConIn), once. Keeps every register.
kt_shell:
        pushad
        call    .here
.here:  pop     edi
        mov     edx, KT_S
        call    kt_stamp
        cmp     dword [edi + (kt_orig - .here)], 0
        jne     .out
        mov     eax, [edi + (kt_st - .here)]
        test    eax, eax
        jz      .out
        mov     eax, [eax + 0x24]               ; ST->ConIn
        test    eax, eax
        jz      .out
        mov     ecx, [eax + 4]                  ; ->ReadKeyStroke
        test    ecx, ecx
        jz      .out
        mov     [edi + (kt_orig - .here)], ecx
        lea     ecx, [edi + (kt_rks - .here)]
        mov     [eax + 4], ecx
.out:   popad
        ret

; EFI_STATUS EFIAPI kt_rks(EFI_SIMPLE_TEXT_INPUT_PROTOCOL *This, EFI_INPUT_KEY *Key): the first
; call (the shell's) reads and counts the waiting keys, then those that come in for KT_WINDOW ms,
; and says there was none; later calls go to the console's own.
kt_rks:
        call    .here
.here:  pop     eax
        cmp     byte [eax + (kt_done - .here)], 0
        jne     .pass
        push    ebp
        mov     ebp, esp
        sub     esp, 12                         ; -4 EFI_INPUT_KEY, -8 polls, -12 window start (ms)
        push    ebx
        push    esi
        push    edi
        mov     edi, eax                        ; edi = .here
        mov     byte [edi + (kt_done - .here)], 1
        mov     edx, KT_F
        call    kt_stamp
        call    kt_ms
        mov     [ebp - 12], eax
        mov     dword [ebp - 8], 0
        xor     esi, esi                        ; esi = keys read
        call    .drain
        mov     eax, esi                        ; W: those already waiting
        mov     ecx, 2
        mov     edx, KT_W
        call    kt_dec
.wait:  cmp     esi, KT_MAXKEYS
        jae     .done
        cmp     dword [ebp - 8], KT_MAXPOLLS
        jae     .done
        call    kt_ms
        sub     eax, [ebp - 12]
        cmp     eax, KT_WINDOW
        jae     .done
        mov     eax, [edi + (kt_st - .here)]
        mov     eax, [eax + 0x3c]               ; BootServices
        push    KT_POLL_US
        call    [eax + 0x88]                    ; BS->Stall
        add     esp, 4
        inc     dword [ebp - 8]
        call    .drain
        jmp     .wait
.done:  mov     eax, esi
        mov     ecx, 2
        mov     edx, KT_N
        call    kt_dec
        mov     eax, 0x80000006                 ; EFI_NOT_READY: the shell goes on to startup.nsh
        pop     edi
        pop     esi
        pop     ebx
        leave
        ret
.pass:  jmp     [eax + (kt_orig - .here)]

; .drain (kt_rks's frame): reads keys until none is waiting or KT_MAXKEYS are read; the first one's
; code and time are recorded.
.drain:
.read:  cmp     esi, KT_MAXKEYS
        jae     .dret
        lea     eax, [ebp - 4]
        push    eax
        push    dword [ebp + 8]                 ; This
        call    [edi + (kt_orig - .here)]
        add     esp, 8
        test    eax, eax
        jnz     .dret
        inc     esi
        cmp     esi, 1
        jne     .read
        movzx   eax, word [ebp - 4]             ; the first key
        mov     ecx, 4
        mov     edx, KT_KS
        call    kt_hex
        movzx   eax, word [ebp - 2]
        mov     ecx, 4
        mov     edx, KT_KU
        call    kt_hex
        mov     edx, KT_P
        call    kt_stamp
        jmp     .read
.dret:  ret

; kt_stamp: edx = a time field's digit offset. Writes the milliseconds since reset there if it
; is still '-' (each is stamped once). Clobbers eax, ecx, edx.
kt_stamp:
        call    .here
.here:  pop     eax
        mov     eax, [eax + (kt_ptr - .here)]
        test    eax, eax
        jz      .out
        cmp     byte [eax + edx], '-'
        jne     .out
        push    edx
        call    kt_ms
        pop     edx
        mov     ecx, 5
        call    kt_dec
.out:   ret

; kt_ms: eax = milliseconds since reset. Clobbers edx.
kt_ms:
        push    ecx
        rdtsc
        mov     ecx, KT_TSC_KHZ
        cmp     edx, ecx
        jae     .big
        div     ecx
        pop     ecx
        ret
.big:   mov     eax, 99999
        pop     ecx
        ret

; kt_dec: eax = value, ecx = digits (1-9), edx = offset. Writes it in decimal there, all nines if
; it doesn't fit, if the string is there. Clobbers eax, ecx, edx.
kt_dec:
        push    ebx
        push    edi
        call    .here
.here:  pop     edi
        mov     edi, [edi + (kt_ptr - .here)]
        test    edi, edi
        jz      .out
        add     edi, edx
        push    eax
        mov     eax, 1
        mov     ebx, ecx
.pow:   imul    eax, eax, 10
        dec     ebx
        jnz     .pow
        dec     eax                             ; the largest that fits
        mov     ebx, eax
        pop     eax
        cmp     eax, ebx
        jbe     .fits
        mov     eax, ebx
.fits:  lea     edi, [edi + ecx - 1]
        mov     ebx, 10
.dig:   xor     edx, edx
        div     ebx
        add     dl, '0'
        mov     [edi], dl
        dec     edi
        dec     ecx
        jnz     .dig
.out:   pop     edi
        pop     ebx
        ret

; kt_hex: eax = value, ecx = digits, edx = offset. Writes it in upper-case hex there, if the
; string is there. Clobbers eax, ecx, edx.
kt_hex:
        push    ebx
        push    edi
        call    .here
.here:  pop     edi
        mov     edi, [edi + (kt_ptr - .here)]
        test    edi, edi
        jz      .out
        add     edi, edx
        lea     edi, [edi + ecx - 1]
.dig:   mov     ebx, eax
        and     ebx, 0x0f
        add     bl, '0'
        cmp     bl, '9'
        jbe     .put
        add     bl, 7
.put:   mov     [edi], bl
        shr     eax, 4
        dec     edi
        dec     ecx
        jnz     .dig
.out:   pop     edi
        pop     ebx
        ret

kt_ptr:         dd 0                            ; set by smbios_install: the string's first field, or 0
kt_st:          dd 0
kt_orig:        dd 0                            ; the console's ReadKeyStroke, once replaced
kt_done:        db 0
                align 4
kt_in_guid:                                     ; 387477C1-69C7-11D2-8E39-00A0C969723B SimpleTextIn
        dd      0x387477c1
        dw      0x69c7, 0x11d2
        db      0x8e, 0x39, 0x00, 0xa0, 0xc9, 0x69, 0x72, 0x3b
kt_dp_guid:                                     ; 09576E91-6D3F-11D2-8E39-00A0C969723B DevicePath
        dd      0x09576e91
        dw      0x6d3f, 0x11d2
        db      0x8e, 0x39, 0x00, 0xa0, 0xc9, 0x69, 0x72, 0x3b
