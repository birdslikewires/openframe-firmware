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
; The prompt has to be made visible. The display driver's (IEGD's) Blt, which the firmware's text
; console draws with, reports success but draws nothing on the panel while its splash is up
; (measured 2026-11-17: a fill through Blt never showed; the same fill written straight into the
; framebuffer did). That keeps every boot's console text (the shell prints a device table as it
; starts) off the bird, so it is left alone on a normal boot. Only once Esc has been caught does
; ew_show put ew_blt in its place, which draws straight into the framebuffer (all four Blt
; operations), and move the console's cursor to the top left: the prompt appears at the top of
; the bird screen, the bird left where it is. Only for the framebuffer seen on the OpenFrames (one
; Graphics Output, 32-bit Blue-Green-Red-Reserved pixels, a linear framebuffer below 4 GB that
; holds the whole screen); otherwise Esc still stops at the shell, unseen, as before.
;
; Not in the keyboard-timing build (kbdtime.inc wraps the same call) or the app build.

ESC_WINDOW      equ     10000000                ; 1 s, in 100 ns units (SetTimer)
ESC_POLL_US     equ     10000                   ; Stall between polls
ESC_FALLBACK    equ     100                     ; polls when there is no timer (1 s)
ESC_MAXPOLLS    equ     300                     ; at most 3 s, whatever the timer says
ESC_MAXREAD     equ     64                      ; keys read in one go, at most
ESC_SCAN        equ     0x17                    ; EFI scan code SCAN_ESC
ESC_BGR8        equ     1                       ; PixelBlueGreenRedReserved8BitPerColor

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
.got:   call    ew_show                         ; the prompt, made visible
        mov     eax, [ebp + 12]                 ; Esc for the shell
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

; ew_show: Esc caught. Puts ew_blt over the display's Graphics Output Blt (once, and only for the
; framebuffer described at the top) and the console's cursor at the top left. Keeps every register.
ew_show:
        pushad
        call    .here
.here:  pop     edi
        cmp     dword [edi + (ew_fb - .here)], 0
        jne     .cur                            ; done already
        mov     ebx, [edi + (ew_st - .here)]
        mov     ebx, [ebx + 0x3c]               ; BootServices
        push    0                               ; local: Interface
        mov     eax, esp
        push    eax
        push    0
        lea     eax, [edi + (ew_gop_guid - .here)]
        push    eax
        call    [ebx + 0xac]                    ; BS->LocateProtocol(&GraphicsOutput, NULL, &gop)
        add     esp, 12
        pop     esi                             ; esi = GOP
        test    eax, eax
        jnz     .out
        test    esi, esi
        jz      .out
        mov     edx, [esi + 0x0c]               ; ->Mode
        test    edx, edx
        jz      .out
        cmp     dword [edx + 0x14], 0           ; FrameBufferBase below 4 GB
        jne     .out
        mov     eax, [edx + 0x10]
        test    eax, eax
        jz      .out
        mov     ecx, [edx + 8]                  ; ->Info
        test    ecx, ecx
        jz      .out
        cmp     dword [ecx + 0x0c], ESC_BGR8
        jne     .out
        mov     eax, [ecx + 4]                  ; HorizontalResolution
        test    eax, eax
        jz      .out
        cmp     eax, 4096
        ja      .out
        cmp     eax, [ecx + 0x20]               ; not wider than a scan line
        ja      .out
        mov     eax, [ecx + 8]                  ; VerticalResolution
        test    eax, eax
        jz      .out
        cmp     eax, 4096
        ja      .out
        imul    eax, [ecx + 0x20]               ; the whole screen in the framebuffer
        shl     eax, 2
        cmp     eax, [edx + 0x18]               ; FrameBufferSize (UINTN: 32 bits here)
        ja      .out
        mov     eax, [ecx + 4]
        mov     [edi + (ew_w - .here)], eax
        mov     eax, [ecx + 8]
        mov     [edi + (ew_h - .here)], eax
        mov     eax, [ecx + 0x20]
        shl     eax, 2
        mov     [edi + (ew_pitch - .here)], eax
        mov     eax, [edx + 0x10]
        mov     [edi + (ew_fb - .here)], eax
        lea     eax, [edi + (ew_blt - .here)]
        mov     [esi + 8], eax                  ; GOP->Blt: ours from now on
.cur:   mov     eax, [edi + (ew_st - .here)]
        mov     eax, [eax + 0x2c]               ; ST->ConOut
        test    eax, eax
        jz      .out
        push    0                               ; Row
        push    0                               ; Column
        push    eax
        call    [eax + 0x1c]                    ; ConOut->SetCursorPosition(ConOut, 0, 0)
        add     esp, 12
.out:   popad
        ret

; EFI_STATUS EFIAPI ew_blt(GOP *This, PIXEL *BltBuffer, UINTN Operation, UINTN SourceX, SourceY,
; DestinationX, DestinationY, Width, Height, Delta): Graphics Output Blt, straight on the
; framebuffer ew_show found. Operations: 0 VideoFill (BltBuffer[0] over the destination), 1
; VideoToBltBuffer, 2 BufferToVideo, 3 VideoToVideo (either way round: overlapping copies keep
; their source). Delta is the BltBuffer's bytes a row (0: Width * 4). EFI_INVALID_PARAMETER for any
; other operation, an empty rectangle, or a rectangle not wholly on the screen.
ew_blt:
        push    ebp
        mov     ebp, esp
        push    ebx
        push    esi
        push    edi
        call    .here
.here:  pop     ebx                             ; ebx = .here
        cld
        mov     ecx, [ebp + 36]                 ; Width
        test    ecx, ecx
        jz      .inval
        cmp     dword [ebp + 40], 0             ; Height
        je      .inval
        cmp     dword [ebp + 44], 0             ; Delta
        jne     .op
        lea     eax, [ecx * 4]
        mov     [ebp + 44], eax
.op:    mov     eax, [ebp + 16]
        cmp     eax, 3
        ja      .inval
        cmp     eax, 1
        je      .tobuf
        ; 0, 2, 3: the destination must be on the screen
        mov     eax, [ebp + 28]
        mov     edx, [ebp + 32]
        call    .onscr
        jc      .inval
        cmp     dword [ebp + 16], 0
        je      .fill
        cmp     dword [ebp + 16], 2
        je      .tovid
        ; 3: VideoToVideo, the source too
        mov     eax, [ebp + 20]
        mov     edx, [ebp + 24]
        call    .onscr
        jc      .inval
        mov     eax, [ebp + 20]                 ; esi = source, edi = destination (top-left)
        mov     edx, [ebp + 24]
        call    .fbat
        mov     esi, eax
        mov     eax, [ebp + 28]
        mov     edx, [ebp + 32]
        call    .fbat
        mov     edi, eax
        mov     edx, [ebx + (ew_pitch - .here)] ; edx = row step
        mov     ecx, [ebp + 40]                 ; rows
        cmp     edi, esi
        jbe     .v2vd                           ; destination before the source: forwards
        mov     eax, ecx                        ; else backwards: from the last row's last pixel
        dec     eax
        imul    eax, edx
        add     esi, eax
        add     edi, eax
        mov     eax, [ebp + 36]
        lea     esi, [esi + eax * 4 - 4]
        lea     edi, [edi + eax * 4 - 4]
        neg     edx
        std
.v2vd:  push    ecx
        push    esi
        push    edi
        mov     ecx, [ebp + 36]
        rep     movsd
        pop     edi
        pop     esi
        pop     ecx
        add     esi, edx
        add     edi, edx
        dec     ecx
        jnz     .v2vd
        cld
        jmp     .ok

.fill:  mov     eax, [ebp + 28]
        mov     edx, [ebp + 32]
        call    .fbat
        mov     edi, eax
        mov     esi, [ebp + 12]                 ; BltBuffer: the colour
        test    esi, esi
        jz      .inval
        mov     eax, [esi]
        mov     edx, [ebp + 40]
.frow:  push    edi
        mov     ecx, [ebp + 36]
        rep     stosd
        pop     edi
        add     edi, [ebx + (ew_pitch - .here)]
        dec     edx
        jnz     .frow
        jmp     .ok

.tovid: mov     esi, [ebp + 12]                 ; BltBuffer at (SourceX, SourceY)
        test    esi, esi
        jz      .inval
        mov     eax, [ebp + 24]
        imul    eax, [ebp + 44]
        add     esi, eax
        mov     eax, [ebp + 20]
        lea     esi, [esi + eax * 4]
        mov     eax, [ebp + 28]
        mov     edx, [ebp + 32]
        call    .fbat
        mov     edi, eax
        mov     edx, [ebp + 40]
.vrow:  push    esi
        push    edi
        mov     ecx, [ebp + 36]
        rep     movsd
        pop     edi
        pop     esi
        add     esi, [ebp + 44]
        add     edi, [ebx + (ew_pitch - .here)]
        dec     edx
        jnz     .vrow
        jmp     .ok

.tobuf: mov     eax, [ebp + 20]                 ; the source must be on the screen
        mov     edx, [ebp + 24]
        call    .onscr
        jc      .inval
        mov     edi, [ebp + 12]                 ; BltBuffer at (DestinationX, DestinationY)
        test    edi, edi
        jz      .inval
        mov     eax, [ebp + 32]
        imul    eax, [ebp + 44]
        add     edi, eax
        mov     eax, [ebp + 28]
        lea     edi, [edi + eax * 4]
        mov     eax, [ebp + 20]
        mov     edx, [ebp + 24]
        call    .fbat
        mov     esi, eax
        mov     edx, [ebp + 40]
.brow:  push    esi
        push    edi
        mov     ecx, [ebp + 36]
        rep     movsd
        pop     edi
        pop     esi
        add     esi, [ebx + (ew_pitch - .here)]
        add     edi, [ebp + 44]
        dec     edx
        jnz     .brow

.ok:    xor     eax, eax
        jmp     .out
.inval: mov     eax, 0x80000002                 ; EFI_INVALID_PARAMETER
.out:   cld
        pop     edi
        pop     esi
        pop     ebx
        pop     ebp
        ret

; .onscr: eax = x, edx = y: CF set unless (x, y) + (Width, Height) is wholly on the screen.
.onscr: cmp     eax, [ebx + (ew_w - .here)]
        jae     .off
        cmp     edx, [ebx + (ew_h - .here)]
        jae     .off
        push    eax
        mov     eax, [ebx + (ew_w - .here)]
        sub     eax, [esp]
        cmp     [ebp + 36], eax                 ; Width <= screen width - x
        pop     eax
        ja      .off
        push    eax
        mov     eax, [ebx + (ew_h - .here)]
        sub     eax, edx
        cmp     [ebp + 40], eax                 ; Height <= screen height - y
        pop     eax
        ja      .off
        clc
        ret
.off:   stc
        ret

; .fbat: eax = x, edx = y: eax = the framebuffer address of that pixel. Clobbers edx.
.fbat:  imul    edx, [ebx + (ew_pitch - .here)]
        lea     eax, [edx + eax * 4]
        add     eax, [ebx + (ew_fb - .here)]
        ret

ew_fb:          dd 0                            ; the framebuffer, once ew_blt is in place
ew_pitch:       dd 0                            ; its bytes a row
ew_w:           dd 0
ew_h:           dd 0
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
ew_gop_guid:                                    ; 9042A9DE-23DC-4A38-96FB-7ADED080516A GraphicsOutput
        dd      0x9042a9de
        dw      0x23dc, 0x4a38
        db      0x96, 0xfb, 0x7a, 0xde, 0xd0, 0x80, 0x51, 0x6a
ew_dp_guid:                                     ; 09576E91-6D3F-11D2-8E39-00A0C969723B DevicePath
        dd      0x09576e91
        dw      0x6d3f, 0x11d2
        db      0x8e, 0x39, 0x00, 0xa0, 0xc9, 0x69, 0x72, 0x3b
