; escwin.inc: Esc reliably stops at the shell, from 26.11.31, with nothing on the screen for it.
;
; The built-in shell looks for Esc once, at its start (with no countdown, countdown.inc), and Esc
; there skips startup.nsh and leaves it at its prompt. Measured on an OF1 with a USB keyboard (the
; keyboard-timing build, kbdtime.inc, 2026-11-16): the keyboard can first be read about 0.24 s
; before that look, so an Esc pressed at boot was only seen if it landed in that quarter second; a
; key held down from power-on is never reported at all (the firmware's keyboard support only
; reports keys pressed after it starts). Keys pressed after the keyboard appears come through at
; once, each one.
;
; So: when the first Simple Text Input on a USB device path appears (a USB keyboard), a timer is
; started for ESC_WINDOW (1 s). At the shell's first ReadKeyStroke (ST->ConIn's, wrapped once the
; shell is ours: img_patch), keys are read until an Esc, then every 10 ms (Stall between: the
; keyboard's own timer events run meanwhile) until the timer has gone off. Esc is handed to the
; shell; any other key is dropped; with no Esc the shell is told there was no key and starts as
; it always has. With no USB keyboard there is no wait at all: one read, as before. If the timer
; couldn't be made, the window is ESC_FALLBACK polls (1 s) from the shell's look; and it is never
; more than ESC_MAXPOLLS polls (3 s) whatever the timer does. Later calls go straight through.
; The screen is never touched: the bird stays, keyboard or not, until Esc brings the prompt.
;
; Not in the keyboard-timing build (kbdtime.inc wraps the same call) or the app build.

ESC_WINDOW      equ     10000000                ; 1 s, in 100 ns units (SetTimer)
ESC_POLL_US     equ     10000                   ; Stall between polls
ESC_FALLBACK    equ     100                     ; polls when there is no timer (1 s)
ESC_MAXPOLLS    equ     300                     ; at most 3 s, whatever the timer says
ESC_MAXREAD     equ     64                      ; keys read in one go, at most
ESC_SCAN        equ     0x17                    ; EFI scan code SCAN_ESC

; ew_start: in edi = entry.here, [ebp+12] = SystemTable. Keeps the system table for the shell's
; hook, watches console inputs appearing, and looks at any already there. Keeps ebx, esi, edi.
ew_start:
        mov     eax, [ebp + 12]
        mov     [edi + (ew_st - entry.here)], eax
        lea     eax, [edi + (ew_in_guid - entry.here)]
        lea     ecx, [edi + (ew_in_notify - entry.here)]
        call    notify_on
        mov     eax, [ebp + 12]
        push    dword [eax + 0x3c]
        call    ew_scan
        add     esp, 4
        ret

; VOID EFIAPI ew_in_notify(EFI_EVENT Event, VOID *Context = BootServices)
ew_in_notify:
        push    dword [esp + 8]
        call    ew_scan
        add     esp, 4
        ret

; ew_scan(EFI_BOOT_SERVICES *BS): the first time a Simple Text Input has a USB node in its device
; path (messaging 3, subtype 5 USB, 0x0f USB class or 0x10 USB WWID), notes the keyboard and starts
; the window's timer. cdecl. Keeps ebx, esi, edi.
ew_scan:
        push    ebp
        mov     ebp, esp
        sub     esp, 20                         ; -4 count, -8 buffer, -12 path, -16 index, -20 event
        push    ebx
        push    esi
        push    edi
        call    .here
.here:  pop     edi
        cmp     byte [edi + (ew_usb - .here)], 0
        jne     .out
        xor     eax, eax
        mov     [ebp - 4], eax
        mov     [ebp - 8], eax
        mov     [ebp - 16], eax
        mov     ebx, [ebp + 8]                  ; BS
        lea     eax, [ebp - 8]                  ; LocateHandleBuffer(ByProtocol, &SimpleTextIn, ...)
        push    eax
        lea     eax, [ebp - 4]
        push    eax
        push    0
        lea     eax, [edi + (ew_in_guid - .here)]
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
        lea     eax, [edi + (ew_dp_guid - .here)]
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
.next:  inc     dword [ebp - 16]
        jmp     .loop
.usb:   mov     byte [edi + (ew_usb - .here)], 1
.free:  push    dword [ebp - 8]
        call    [ebx + 0x30]                    ; FreePool
        add     esp, 4
        cmp     byte [edi + (ew_usb - .here)], 0
        je      .out
        mov     dword [ebp - 20], 0             ; the window starts now: a one-shot timer
        lea     eax, [ebp - 20]
        push    eax                             ; &Event
        push    0                               ; NotifyContext
        push    0                               ; NotifyFunction
        push    0                               ; NotifyTpl
        push    0x80000000                      ; EVT_TIMER
        call    [ebx + 0x34]                    ; BS->CreateEvent
        add     esp, 20
        test    eax, eax
        jnz     .out
        push    0                               ; TriggerTime (UINT64): ESC_WINDOW
        push    ESC_WINDOW
        push    2                               ; TimerRelative
        push    dword [ebp - 20]
        call    [ebx + 0x38]                    ; BS->SetTimer
        add     esp, 16
        test    eax, eax
        jz      .keep
        push    dword [ebp - 20]                ; not set: no timer (ESC_FALLBACK instead)
        call    [ebx + 0x44]                    ; BS->CloseEvent
        add     esp, 4
        jmp     .out
.keep:  mov     eax, [ebp - 20]
        mov     [edi + (ew_timer - .here)], eax
.out:   pop     edi
        pop     esi
        pop     ebx
        leave
        ret

; ew_shell: the shell has loaded and its countdown is made 0 (img_patch). Puts ew_rks over the
; console's ReadKeyStroke (ST->ConIn), once. Keeps every register.
ew_shell:
        pushad
        call    .here
.here:  pop     edi
        cmp     dword [edi + (ew_orig - .here)], 0
        jne     .out
        mov     eax, [edi + (ew_st - .here)]
        test    eax, eax
        jz      .out
        mov     eax, [eax + 0x24]               ; ST->ConIn
        test    eax, eax
        jz      .out
        mov     ecx, [eax + 4]                  ; ->ReadKeyStroke
        test    ecx, ecx
        jz      .out
        mov     [edi + (ew_orig - .here)], ecx
        lea     ecx, [edi + (ew_rks - .here)]
        mov     [eax + 4], ecx
.out:   popad
        ret

; EFI_STATUS EFIAPI ew_rks(EFI_SIMPLE_TEXT_INPUT_PROTOCOL *This, EFI_INPUT_KEY *Key): the shell's
; first call: Esc if one comes in the window (see the top), else EFI_NOT_READY. Later calls go to
; the console's own.
ew_rks:
        call    .here
.here:  pop     eax
        cmp     byte [eax + (ew_done - .here)], 0
        jne     .pass
        push    ebp
        mov     ebp, esp
        sub     esp, 4                          ; -4 EFI_INPUT_KEY (ScanCode, UnicodeChar)
        push    ebx
        push    esi
        push    edi
        mov     edi, eax                        ; edi = .here
        mov     byte [edi + (ew_done - .here)], 1
        mov     ebx, [edi + (ew_st - .here)]
        mov     ebx, [ebx + 0x3c]               ; ebx = BootServices
        xor     esi, esi                        ; esi = polls
.try:   call    .esc                            ; any Esc waiting?
        jz      .got
        cmp     byte [edi + (ew_usb - .here)], 0
        je      .none                           ; no USB keyboard: no window
        cmp     esi, ESC_MAXPOLLS
        jae     .none
        mov     eax, [edi + (ew_timer - .here)]
        test    eax, eax
        jz      .fixed
        push    eax
        call    [ebx + 0x48]                    ; BS->CheckEvent: EFI_SUCCESS once it has gone off
        add     esp, 4
        test    eax, eax
        jz      .none
        jmp     .poll
.fixed: cmp     esi, ESC_FALLBACK
        jae     .none
.poll:  push    ESC_POLL_US
        call    [ebx + 0x88]                    ; BS->Stall
        add     esp, 4
        inc     esi
        jmp     .try
.got:   mov     eax, [ebp + 12]                 ; Esc for the shell
        mov     dword [eax], ESC_SCAN           ; ScanCode SCAN_ESC, UnicodeChar 0
        xor     eax, eax
        jmp     .ret
.none:  mov     eax, 0x80000006                 ; EFI_NOT_READY: the shell starts as it always has
.ret:   push    eax
        mov     eax, [edi + (ew_timer - .here)] ; the window is over: its timer goes
        test    eax, eax
        jz      .nt
        push    eax
        call    [ebx + 0x44]                    ; BS->CloseEvent
        add     esp, 4
        mov     dword [edi + (ew_timer - .here)], 0
.nt:    pop     eax
        pop     edi
        pop     esi
        pop     ebx
        leave
        ret
.pass:  jmp     [eax + (ew_orig - .here)]

; .esc (ew_rks's frame): reads up to ESC_MAXREAD waiting keys; ZF set as soon as one is Esc, clear
; if none was (the others are dropped).
.esc:   push    ecx
        mov     ecx, ESC_MAXREAD
.read:  push    ecx
        lea     eax, [ebp - 4]
        push    eax
        push    dword [ebp + 8]                 ; This
        call    [edi + (ew_orig - .here)]
        add     esp, 8
        pop     ecx
        test    eax, eax
        jnz     .noesc
        cmp     word [ebp - 4], ESC_SCAN
        je      .yes
        loop    .read
.noesc: or      eax, 1                          ; ZF clear: no Esc
        pop     ecx
        ret
.yes:   pop     ecx                             ; ZF set (from the cmp)
        ret

ew_st:          dd 0
ew_orig:        dd 0                            ; the console's ReadKeyStroke, once replaced
ew_timer:       dd 0                            ; the window's timer, while it runs
ew_usb:         db 0                            ; a USB keyboard has appeared
ew_done:        db 0
                align 4
ew_in_guid:                                     ; 387477C1-69C7-11D2-8E39-00A0C969723B SimpleTextIn
        dd      0x387477c1
        dw      0x69c7, 0x11d2
        db      0x8e, 0x39, 0x00, 0xa0, 0xc9, 0x69, 0x72, 0x3b
ew_dp_guid:                                     ; 09576E91-6D3F-11D2-8E39-00A0C969723B DevicePath
        dd      0x09576e91
        dw      0x6d3f, 0x11d2
        db      0x8e, 0x39, 0x00, 0xa0, 0xc9, 0x69, 0x72, 0x3b
