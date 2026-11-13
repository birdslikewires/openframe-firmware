; usb_first: a USB drive with boot files on it is the shell's fs0, ahead of the internal memory.
;
; The built-in shell (EDK1, FV file C57AD6B7) names devices with its "consistent mapping": it
; gathers every file system and block device and sorts them (comparison function at RVA 0xB7C5).
; The sort's first key puts any device whose path has a USB or SCSI node after every other, so
; with the internal memory (MMC) holding a file system the MMC is always fs0 and a USB drive fs1
; or later, whatever order the core installed them in. (A stick was only ever fs0 when the MMC
; wasn't connected yet when the shell mapped.) At start-up the shell runs the first startup.nsh
; along its path, which lists the devices in that order (the current directory, unset at start-up,
; then the first device's \efi\tools, \efi\boot and root, then the next device's); a file named
; without a volume in a script is looked for the same way. So the MMC's startup.nsh ran instead of
; a USB drive's, and its files were found first.
;
; At ReadyToBoot, just before the boot manager loads and starts the shell, this looks for
; \startup.nsh on every file system on a USB disk (its device path has a USB node). If one has it,
; the shell's sort is turned round for this boot: in the shell's code in memory (the flash copy is
; untouched), the two 7-byte sequences that turn "USB or SCSI" into the key's "later" bit become
; ones that set it for every other device instead, so USB drives sort first and the drive is fs0,
; the source of truth, with the MMC after it. That is made as the shell loads (a LoadedImage
; notify, registered only then), or at once if it is already loaded, and only to the exact stock
; function: the shell's file GUID, an image big enough, and all 120 bytes of the function as they
; are in every OF1 firmware we have. Without such a drive nothing is changed: the MMC is fs0, and
; the shell sorts as it always has. Two drives with it, or a drive with it and one without, are
; all ahead of the MMC, in the shell's own order among themselves.
;
; Reads only, apart from those 14 bytes of the shell's code: every file is opened read-only, and
; nothing is written to any disk or variable. Once per boot.
;
; UINT32 usb_first(EFI_BOOT_SERVICES *BS): 1 if the shell was changed now, 2 if it will be as it
; loads, 0 if not (no such drive, or the shell isn't one we know). cdecl. Keeps ebx, esi, edi.

USBF_CMP_RVA    equ     0xb7c5                  ; the shell's mapping sort comparison
USBF_CMP_LEN    equ     120
USBF_A          equ     0x28                    ; its two "USB or SCSI: later" sequences
USBF_B          equ     0x4d


; The boot-timing development build (-DTIMING) records what it finds (usbf_rec: two characters,
; then a byte in hex): UE LocateHandleBuffer's status; UH file systems; per file system its device
; path's nodes (N + type digit, subtype) and UK (00 no path, 10 not USB, 11 USB); on USB drives UV
; (OpenVolume's status) and UO (Open's); UP the drive's place among file systems; US the result.
;
; It also leaves a summary where Linux can read it without efivarfs: over the SMBIOS Type 11 boot
; count (count_ptr, 10 characters, `dmidecode -t 11`), usbf_diag below, one character each:
;   0 file systems (hex, x if LocateHandleBuffer failed)   5 the drive's place among file systems
;   1 file systems on USB disks                            6 1 the shell changed at ReadyToBoot,
;   2 1 if a USB one has \startup.nsh                          2 a notify waits for it
;   3 OpenVolume's last status, 4 Open's last status       7 1 once the shell is changed
;   8 loaded images looked at, last scan (hex, capped F)   9 images that were the shell but not stock
; (statuses: the low digit of the EFI status, 0 success, E not found; - not reached)
%macro  UFDIAG 2                                ; position, value (low nibble as a hex digit)
%ifdef TIMING
        push    eax
        mov     eax, %2
        and     eax, 0x0f
        add     al, '0'
        cmp     al, '9'
        jbe     %%d
        add     al, 7
%%d:    mov     [edi + (usbf_diag - usb_first.here) + %1], al
        pop     eax
%endif
%endmacro
%macro  UFSDIAG 2                               ; the same, in usbf_scan (edi = usbf_scan.here)
%ifdef TIMING
        push    eax
        mov     eax, %2
        cmp     eax, 15
        jbe     %%c
        mov     eax, 15
%%c:    add     al, '0'
        cmp     al, '9'
        jbe     %%d
        add     al, 7
%%d:    mov     [edi + (usbf_diag - usbf_scan.here) + %1], al
        pop     eax
%endif
%endmacro
%macro  UFSCOUNT 1                              ; in usbf_patch (edi = usbf_patch.here): one more
%ifdef TIMING
        inc     byte [edi + (usbf_diag - usbf_patch.here) + %1]
%endif
%endmacro
%macro  UFSUNCOUNT 1                            ; in usbf_patch: one fewer
%ifdef TIMING
        dec     byte [edi + (usbf_diag - usbf_patch.here) + %1]
%endif
%endmacro
%macro  UFCOUNT 1                               ; position: one more
%ifdef TIMING
        inc     byte [edi + (usbf_diag - usb_first.here) + %1]
%endif
%endmacro
%macro  UFREC 2                                 ; prefix, value (register or memory)
%ifdef TIMING
        push    eax
        push    ecx
        mov     eax, %2
        mov     ecx, %1
        call    usbf_rec
        pop     ecx
        pop     eax
%endif
%endmacro

usb_first:
        push    ebp
        mov     ebp, esp
        sub     esp, 56                         ; -4 count, -8 handle buffer, -12 interface,
        push    ebx                             ; -16 root, -20 file, -24 found, -28 handle,
        push    esi                             ; -32 the drive, -36 event, -40 registration,
        push    edi                             ; -48 result
        call    .here
.here:  pop     edi
        cmp     byte [edi + (usbf_done - .here)], 0
        jne     .none
        mov     byte [edi + (usbf_done - .here)], 1
        mov     ebx, [ebp + 8]                  ; ebx = BS
        xor     eax, eax
        mov     [ebp - 4], eax
        mov     [ebp - 8], eax
        mov     [ebp - 24], eax
        mov     [ebp - 32], eax
        mov     [ebp - 48], eax
        lea     eax, [ebp - 8]                  ; LocateHandleBuffer(ByProtocol, &SimpleFileSystem,
        push    eax                             ;                    NULL, &count, &buffer)
        lea     eax, [ebp - 4]
        push    eax
        push    0
        lea     eax, [edi + (usbf_sfs_guid - .here)]
        push    eax
        push    2
        call    [ebx + 0xa8]
        add     esp, 20
        UFREC   'UE', eax
%ifdef TIMING
        test    eax, eax
        jz      .lhok
        mov     byte [edi + (usbf_diag - .here)], 'x'
.lhok:
%endif
        test    eax, eax
        jnz     .none
        cmp     dword [ebp - 8], 0
        je      .none
        UFREC   'UH', [ebp - 4]
        UFDIAG  0, [ebp - 4]

        ; 1. The first USB file system with \startup.nsh.
        xor     esi, esi
.find:  cmp     esi, [ebp - 4]
        jae     .found
        mov     eax, [ebp - 8]
        mov     eax, [eax + esi * 4]
        mov     [ebp - 28], eax
        call    .path
        UFREC   'UK', 0
        test    eax, eax
        jz      .fnext
%ifdef TIMING
        call    usbf_nodes
%endif
        call    .walk
%ifdef TIMING
        push    eax
        add     eax, 0x10
        UFREC   'UK', eax
        pop     eax
%endif
        test    eax, eax                        ; not on a USB disk
        jz      .fnext
        UFCOUNT 1
        mov     dword [ebp - 12], 0             ; HandleProtocol(handle, &SimpleFileSystem, &fs)
        lea     eax, [ebp - 12]
        push    eax
        lea     eax, [edi + (usbf_sfs_guid - .here)]
        push    eax
        push    dword [ebp - 28]
        call    [ebx + 0x58]
        add     esp, 12
        test    eax, eax
        jnz     .fnext
        mov     eax, [ebp - 12]
        test    eax, eax
        jz      .fnext
        mov     dword [ebp - 16], 0             ; fs->OpenVolume(fs, &root)
        lea     ecx, [ebp - 16]
        push    ecx
        push    eax
        call    [eax + 8]
        add     esp, 8
        UFREC   'UV', eax
        UFDIAG  3, eax
        test    eax, eax
        jnz     .fnext
        mov     eax, [ebp - 16]
        test    eax, eax
        jz      .fnext
        mov     dword [ebp - 20], 0             ; root->Open(root, &file, L"\startup.nsh",
        push    0                               ;            EFI_FILE_MODE_READ, 0): two UINT64s
        push    0
        push    0
        push    1
        lea     ecx, [edi + (usbf_name - .here)]
        push    ecx
        lea     ecx, [ebp - 20]
        push    ecx
        push    eax
        call    [eax + 8]
        add     esp, 28
        UFREC   'UO', eax
        UFDIAG  4, eax
        test    eax, eax
        jnz     .fclose
        mov     eax, [ebp - 20]
        test    eax, eax
        jz      .fclose
        mov     dword [ebp - 24], 1
        mov     ecx, [ebp - 28]
        mov     [ebp - 32], ecx                 ; the drive
        UFDIAG  2, 1
        UFREC   'UP', esi
        UFDIAG  5, esi
        push    eax                             ; file->Close(file)
        call    [eax + 0x0c]
        add     esp, 4
.fclose:
        mov     eax, [ebp - 16]                 ; root->Close(root)
        push    eax
        call    [eax + 0x0c]
        add     esp, 4
        cmp     dword [ebp - 24], 0
        jne     .found
.fnext: inc     esi
        jmp     .find
.found: cmp     dword [ebp - 24], 0
        je      .free

        ; 2. Turn the shell's sort round: now, if it is loaded, or as it loads.
        mov     byte [edi + (usbf_want - .here)], 1
        push    ebx
        call    usbf_scan                       ; 1 if changed now
        add     esp, 4
        test    eax, eax
        jz      .later
        mov     dword [ebp - 48], 1
        UFDIAG  6, 1
        jmp     .free
.later: mov     dword [ebp - 36], 0             ; CreateEvent(EVT_NOTIFY_SIGNAL, TPL_CALLBACK,
        lea     eax, [ebp - 36]                 ;             usbf_notify, BS, &event)
        push    eax
        push    ebx
        lea     eax, [edi + (usbf_notify - .here)]
        push    eax
        push    8
        push    0x200
        call    [ebx + 0x34]
        add     esp, 20
        test    eax, eax
        jnz     .free
        lea     eax, [ebp - 40]                 ; RegisterProtocolNotify(&LoadedImage, event, &reg)
        push    eax
        push    dword [ebp - 36]
        lea     eax, [edi + (usbf_li_guid - .here)]
        push    eax
        call    [ebx + 0x60]
        add     esp, 12
        test    eax, eax
        jz      .reg
        push    dword [ebp - 36]                ; not registered: the event is no use
        call    [ebx + 0x44]                    ; CloseEvent
        add     esp, 4
        jmp     .free
.reg:   mov     dword [ebp - 48], 2
        UFDIAG  6, 2

.free:  push    dword [ebp - 8]
        call    [ebx + 0x30]                    ; FreePool(buffer)
        add     esp, 4
        mov     eax, [ebp - 48]
        jmp     .ret
.none:  xor     eax, eax
.ret:
%ifdef TIMING
        call    usbf_summary
%endif
        pop     edi
        pop     esi
        pop     ebx
        leave
        ret

; .path: [ebp - 28] = handle. eax = its device path, or 0. Keeps ebx, esi, edi.
.path:  mov     dword [ebp - 12], 0             ; HandleProtocol(handle, &DevicePath, &path)
        lea     eax, [ebp - 12]
        push    eax
        lea     eax, [edi + (usbf_dp_guid - .here)]
        push    eax
        push    dword [ebp - 28]
        call    [ebx + 0x58]
        add     esp, 12
        test    eax, eax
        jnz     .pnone
        mov     eax, [ebp - 12]
        ret
.pnone: xor     eax, eax
        ret

; .walk: eax = a device path. eax = 1 if it has a USB node (messaging 03, subtype 05 USB, 0F USB
; class or 10 USB WWID), else 0 (also for a node shorter than its header, or more than 64 nodes).
; Keeps every other register.
.walk:  push    esi
        push    edx
        push    65                              ; [esp]: nodes left, plus one
        mov     esi, eax
        xor     eax, eax
.wnode: cmp     word [esi], 0xff7f              ; end of the whole path (type 7F, subtype FF)
        je      .wend
        dec     dword [esp]
        jz      .wbad
        cmp     byte [esi], 3
        jne     .wnext
        cmp     byte [esi + 1], 0x05
        je      .wusb
        cmp     byte [esi + 1], 0x0f
        je      .wusb
        cmp     byte [esi + 1], 0x10
        jne     .wnext
.wusb:  mov     eax, 1
.wnext: movzx   edx, word [esi + 2]             ; its length
        cmp     edx, 4
        jb      .wbad
        add     esi, edx
        jmp     .wnode
.wbad:  xor     eax, eax
.wend:  add     esp, 4
        pop     edx
        pop     esi
        ret

; VOID EFIAPI usbf_notify(EFI_EVENT Event, VOID *Context = BS): an image has loaded.
usbf_notify:
        push    dword [esp + 8]
        call    usbf_scan
        add     esp, 4
        ret

; UINT32 usbf_scan(EFI_BOOT_SERVICES *BS): if the shell is to be changed and isn't yet, looks
; through every loaded image for it and changes it. 1 if it is changed (now or before), else 0.
; cdecl. Keeps ebx, esi, edi.
usbf_scan:
        push    ebp
        mov     ebp, esp
        sub     esp, 12                         ; -4 count, -8 buffer, -12 LoadedImage
        push    ebx
        push    esi
        push    edi
        call    .here
.here:  pop     edi
        xor     eax, eax
        cmp     byte [edi + (usbf_want - .here)], 0
        je      .out
        inc     eax
        cmp     byte [edi + (usbf_shell - .here)], 0
        jne     .out
        mov     ebx, [ebp + 8]
        xor     eax, eax
        mov     [ebp - 4], eax
        mov     [ebp - 8], eax
        lea     eax, [ebp - 8]                  ; LocateHandleBuffer(ByProtocol, &LoadedImage, ...)
        push    eax
        lea     eax, [ebp - 4]
        push    eax
        push    0
        lea     eax, [edi + (usbf_li_guid - .here)]
        push    eax
        push    2
        call    [ebx + 0xa8]
        add     esp, 20
        test    eax, eax
        jnz     .none
        cmp     dword [ebp - 8], 0
        je      .none
        UFSDIAG 8, [ebp - 4]
        xor     esi, esi
.loop:  cmp     esi, [ebp - 4]
        jae     .free
        mov     eax, [ebp - 8]
        mov     dword [ebp - 12], 0             ; HandleProtocol(handle, &LoadedImage, &li)
        lea     ecx, [ebp - 12]
        push    ecx
        lea     ecx, [edi + (usbf_li_guid - .here)]
        push    ecx
        push    dword [eax + esi * 4]
        call    [ebx + 0x58]
        add     esp, 12
        test    eax, eax
        jnz     .next
        mov     eax, [ebp - 12]
        test    eax, eax
        jz      .next
        call    usbf_patch
        test    eax, eax
        jz      .next
        mov     byte [edi + (usbf_shell - .here)], 1
        UFSDIAG 7, 1
%ifdef TIMING
        call    usbf_summary
%endif
        jmp     .free
.next:  inc     esi
        jmp     .loop
.free:  push    dword [ebp - 8]
        call    [ebx + 0x30]                    ; FreePool
        add     esp, 4
.none:  movzx   eax, byte [edi + (usbf_shell - .here)]
.out:   pop     edi
        pop     esi
        pop     ebx
        leave
        ret

; usbf_patch: eax = an EFI_LOADED_IMAGE_PROTOCOL. If it is the stock shell (FilePath one
; MEDIA_FW_VOL_FILEPATH node naming its file, ImageSize past the function, the function's 120 bytes
; exactly the stock ones), turns its sort round and reads the change back. eax = 1 if done, else 0.
; Keeps ebx, esi, edi.
usbf_patch:
        push    ebx
        push    esi
        push    edi
        call    .here
.here:  pop     edi
        mov     ebx, eax
        mov     esi, [ebx + 0x10]               ; FilePath
        test    esi, esi
        jz      .no
        cmp     dword [esi], 0x00140604         ; type 4, subtype 6, length 20
        jne     .no
        add     esi, 4                          ; its file GUID
        push    edi
        lea     edi, [edi + (usbf_shell_guid - .here)]
        mov     ecx, 16
        cld
        repe    cmpsb
        pop     edi
        jne     .no
        UFSCOUNT 9                              ; the shell: one more, until it proves stock
        cmp     dword [ebx + 0x28], USBF_CMP_RVA + USBF_CMP_LEN   ; ImageSize (low half: IA32)
        jb      .no
        mov     esi, [ebx + 0x20]               ; ImageBase
        test    esi, esi
        jz      .no
        add     esi, USBF_CMP_RVA               ; esi = the function
        push    esi
        push    edi
        lea     edi, [edi + (usbf_cmp_stock - .here)]
        mov     ecx, USBF_CMP_LEN
        cld
        repe    cmpsb
        pop     edi
        pop     esi
        jne     .no
        UFSUNCOUNT 9
        mov     dword [esi + USBF_A], 0x940fd284        ; test dl, dl; sete dl; add edx, edx
        mov     dword [esi + USBF_A + 3], 0xd201c294    ;  (84 D2 0F 94 C2 01 D2)
        mov     dword [esi + USBF_B], 0x940fc084        ; test al, al; sete al; add eax, eax
        mov     dword [esi + USBF_B + 3], 0xc001c094    ;  (84 C0 0F 94 C0 01 C0)
        cmp     dword [esi + USBF_A], 0x940fd284
        jne     .no
        cmp     dword [esi + USBF_A + 3], 0xd201c294
        jne     .no
        cmp     dword [esi + USBF_B], 0x940fc084
        jne     .no
        cmp     dword [esi + USBF_B + 3], 0xc001c094
        jne     .no
        mov     eax, 1
        jmp     .out
.no:    xor     eax, eax
.out:   pop     edi
        pop     esi
        pop     ebx
        ret

usbf_want:      db 0                            ; set at ReadyToBoot: a USB drive has startup.nsh
usbf_shell:     db 0                            ; set once the shell is changed
        align   4, db 0
usbf_li_guid:                                   ; 5B1B31A1-9562-11D2-8E3F-00A0C969723B LoadedImage
        dd      0x5b1b31a1
        dw      0x9562, 0x11d2
        db      0x8e, 0x3f, 0x00, 0xa0, 0xc9, 0x69, 0x72, 0x3b
usbf_shell_guid:                                ; C57AD6B7-0515-40A8-9D21-551652854E37 the EFI shell
        dd      0xc57ad6b7
        dw      0x0515, 0x40a8
        db      0x9d, 0x21, 0x55, 0x16, 0x52, 0x85, 0x4e, 0x37
usbf_cmp_stock:                                 ; the shell's sort comparison, RVA 0xB7C5, as stock
        db      0x8b, 0x44, 0x24, 0x04, 0x8b, 0x08, 0x8b, 0x44, 0x24, 0x08, 0x56, 0x8b, 0x30, 0x8a, 0x41, 0x1a
        db      0xf6, 0xd8, 0x57, 0x1b, 0xc0, 0x33, 0xd2, 0x83, 0xe0, 0xfc, 0x83, 0xc0, 0x04, 0x38, 0x51, 0x19
        db      0x0f, 0x94, 0xc2, 0x0b, 0xc2, 0x8a, 0x51, 0x18, 0xf6, 0xda, 0x1b, 0xd2, 0x83, 0xe2, 0x02, 0x0b
        db      0xc2, 0x8a, 0x56, 0x1a, 0xf6, 0xda, 0x8b, 0xf8, 0x1b, 0xd2, 0x33, 0xc0, 0x83, 0xe2, 0xfc, 0x83
        db      0xc2, 0x04, 0x38, 0x46, 0x19, 0x0f, 0x94, 0xc0, 0x0b, 0xd0, 0x8a, 0x46, 0x18, 0xf6, 0xd8, 0x1b
        db      0xc0, 0x83, 0xe0, 0x02, 0x0b, 0xd0, 0x3b, 0xfa, 0x74, 0x04, 0x8b, 0xc7, 0xeb, 0x0a, 0x8b, 0x41
        db      0x14, 0x8b, 0x56, 0x14, 0x3b, 0xc2, 0x74, 0x04, 0x2b, 0xc2, 0xeb, 0x09, 0x56, 0x51, 0xe8, 0x64
        db      0xac, 0x00, 0x00, 0x59, 0x59, 0x5f, 0x5e, 0xc3

%ifdef TIMING
; usbf_summary: copies usbf_diag over the SMBIOS boot count, if this driver's table has one.
; Keeps every register.
usbf_summary:
        push    eax
        push    ecx
        push    esi
        call    .here
.here:  pop     esi
        mov     ecx, [esi + (count_ptr - .here)]
        test    ecx, ecx
        jz      .out
        add     esi, usbf_diag - .here
        mov     eax, [esi]
        mov     [ecx], eax
        mov     eax, [esi + 4]
        mov     [ecx + 4], eax
        mov     ax, [esi + 8]
        mov     [ecx + 8], ax
.out:   pop     esi
        pop     ecx
        pop     eax
        ret

usbf_diag:      db      '-00---0-00'
; usbf_rec: ecx = two characters (low word), eax = a byte. Records them with the byte in hex.
; Keeps every register but eax.
usbf_rec:
        push    ecx
        push    edx
        movzx   edx, al
        and     dl, 0x0f
        call    .hex
        shl     edx, 24
        and     ecx, 0xffff
        or      ecx, edx
        movzx   edx, al
        shr     dl, 4
        call    .hex
        shl     edx, 16
        or      ecx, edx
        mov     eax, ecx
        call    tm_rec
        pop     edx
        pop     ecx
        ret
.hex:   add     dl, '0'
        cmp     dl, '9'
        jbe     .hd
        add     dl, 7
.hd:    ret

; usbf_nodes: eax = a device path. Records each node: N, its type as a digit, its subtype. Keeps
; every register.
usbf_nodes:
        push    eax
        push    ecx
        push    esi
        mov     esi, eax
        mov     ecx, 64
.n:     cmp     word [esi], 0xff7f
        je      .nd
        push    ecx
        movzx   ecx, byte [esi]
        and     ecx, 0x0f
        add     ecx, '0'
        shl     ecx, 8
        or      ecx, 'N'
        movzx   eax, byte [esi + 1]
        call    usbf_rec
        pop     ecx
        movzx   eax, word [esi + 2]
        cmp     eax, 4
        jb      .nd
        add     esi, eax
        loop    .n
.nd:    pop     esi
        pop     ecx
        pop     eax
        ret
%endif

usbf_done:      db 0
        align   4, db 0
usbf_sfs_guid:                                  ; 964E5B22-6459-11D2-8E39-00A0C969723B SimpleFileSystem
        dd      0x964e5b22
        dw      0x6459, 0x11d2
        db      0x8e, 0x39, 0x00, 0xa0, 0xc9, 0x69, 0x72, 0x3b
usbf_dp_guid:                                   ; 09576E91-6D3F-11D2-8E39-00A0C969723B DevicePath
        dd      0x09576e91
        dw      0x6d3f, 0x11d2
        db      0x8e, 0x39, 0x00, 0xa0, 0xc9, 0x69, 0x72, 0x3b
usbf_name:
        dw      __utf16__('\startup.nsh'), 0
