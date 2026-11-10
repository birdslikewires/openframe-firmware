; FvDiag: EFI application (IA32) that lists what the Framework firmware-volume protocol sees
; and tries the section reads the DXE dispatcher needs for one file. Read-only.
;
; For every handle with the Framework EFI_FIRMWARE_VOLUME_PROTOCOL (389F751F-1838-4388-8390-CD8154BD27F8).
; NOT DE28BC59-6228-41BD-BDF6-A3B9ADB58DA1, which is the Firmware Volume BLOCK protocol: its +0x14 is
; Write(). The first run used that GUID by mistake and called it with junk; the firmware refused
; (EFI_UNSUPPORTED) and the chip was verified unchanged. Guard: an FV interface must have a small
; KeySize at +0x18 (an FVB interface has a function pointer there); otherwise the app stops ("X").
;   "FV hh @iiiiiiii" then one line per file from GetNextFile(FileType = ALL):
;   "  gggggggg tt ssssssss aaaaaaaa"  (first GUID dword, type, size, attributes)
;   ending with "  end ssssssss" (GetNextFile's final status).
; Then, for TARGET_GUID on each FV: ReadSection(DXE_DEPEX), ReadSection(PE32) and
; ReadFile(size only), printing "  rs13 status size", "  rs10 status size", "  rf status size type".
;
; Position-independent (call/pop). Output via ConOut; run it from the shell with "> file".

bits 32

%define BS_ALLOCATEPOOL      0x2c
%define BS_FREEPOOL          0x30
%define BS_HANDLEPROTOCOL    0x58
%define BS_LOCATEHANDLEBUF   0xa8
%define FV_READFILE          0x08
%define FV_READSECTION       0x0c
%define FV_GETNEXTFILE       0x14
%define FV_KEYSIZE           0x18

; frame locals (ebp-relative)
%define L_BS        -4
%define L_COUT      -8
%define L_NHANDLES  -12
%define L_HANDLES   -16
%define L_IDX       -20
%define L_FV        -24
%define L_TYPE      -28
%define L_ATTR      -32
%define L_SIZE      -36
%define L_BUF       -40
%define L_BUFSZ     -44
%define L_AUTH      -48
%define L_KEY       -112        ; 64-byte key buffer
%define L_GUID      -128        ; 16-byte name GUID
%define L_TEXT      -200        ; UCS-2 line buffer (36 chars)
%define FRAME       200

entry:
        push    ebp
        mov     ebp, esp
        sub     esp, FRAME
        push    ebx
        push    esi
        push    edi
        call    pic_base
pic_base:
        pop     ebx                             ; ebx = runtime pic_base (PIC base, kept)

        mov     eax, [ebp + 12]                 ; SystemTable
        mov     ecx, [eax + 0x2c]
        mov     [ebp + L_COUT], ecx             ; ConOut
        mov     eax, [eax + 0x3c]
        mov     [ebp + L_BS], eax               ; BootServices

        mov     dword [ebp + L_NHANDLES], 0
        mov     dword [ebp + L_HANDLES], 0
        lea     eax, [ebp + L_HANDLES]
        push    eax                             ; &Buffer
        lea     eax, [ebp + L_NHANDLES]
        push    eax                             ; &NoHandles
        push    0                               ; SearchKey
        lea     eax, [ebx + (fv_guid - pic_base)]
        push    eax                             ; Protocol
        push    2                               ; ByProtocol
        mov     eax, [ebp + L_BS]
        call    [eax + BS_LOCATEHANDLEBUF]
        add     esp, 20
        mov     ecx, eax
        mov     al, 'L'
        call    put_tag_hex                     ; "L <status>"
        mov     ecx, [ebp + L_NHANDLES]
        mov     al, 'N'
        call    put_tag_hex                     ; "N <count>"

        mov     dword [ebp + L_IDX], 0
.fv_loop:
        mov     eax, [ebp + L_IDX]
        cmp     eax, [ebp + L_NHANDLES]
        jae     .done
        mov     ecx, [ebp + L_HANDLES]
        mov     ecx, [ecx + eax * 4]            ; handle
        lea     eax, [ebp + L_FV]
        push    eax
        lea     eax, [ebx + (fv_guid - pic_base)]
        push    eax
        push    ecx
        mov     eax, [ebp + L_BS]
        call    [eax + BS_HANDLEPROTOCOL]
        add     esp, 12
        mov     ecx, eax
        mov     al, 'H'
        call    put_tag_hex                     ; "H <HandleProtocol status>"
        mov     ecx, [ebp + L_IDX]
        mov     al, 'F'
        call    put_tag_hex                     ; "F <fv index>"
        mov     eax, [ebp + L_FV]
        mov     ecx, [eax + FV_KEYSIZE]
        mov     al, 'K'
        call    put_tag_hex                     ; "K <KeySize>"
        mov     eax, [ebp + L_FV]
        mov     ecx, [eax + FV_KEYSIZE]
        cmp     ecx, 64                         ; must fit our key buffer, and an FVB would
        ja      .not_fv                         ; have a pointer here
        test    ecx, ecx
        jz      .not_fv

        ; zero the key
        lea     edi, [ebp + L_KEY]
        mov     ecx, 16
        xor     eax, eax
        rep stosd
.file_loop:
        mov     dword [ebp + L_TYPE], 0         ; EFI_FV_FILETYPE_ALL
        lea     eax, [ebp + L_SIZE]
        push    eax                             ; &Size
        lea     eax, [ebp + L_ATTR]
        push    eax                             ; &Attributes
        lea     eax, [ebp + L_GUID]
        push    eax                             ; NameGuid
        lea     eax, [ebp + L_TYPE]
        push    eax                             ; &FileType
        lea     eax, [ebp + L_KEY]
        push    eax                             ; Key
        mov     eax, [ebp + L_FV]
        push    eax                             ; This
        call    [eax + FV_GETNEXTFILE]
        add     esp, 24
        test    eax, eax
        jnz     .file_end
        ; "  <guid0> <type> <size> <attr>"
        lea     edi, [ebp + L_TEXT]
        mov     word [edi], ' '
        mov     word [edi + 2], ' '
        add     edi, 4
        mov     eax, [ebp + L_GUID]
        call    hex32
        mov     word [edi], ' '
        add     edi, 2
        mov     eax, [ebp + L_TYPE]
        and     eax, 0xff
        call    hex32
        mov     word [edi], ' '
        add     edi, 2
        mov     eax, [ebp + L_SIZE]
        call    hex32
        mov     word [edi], ' '
        add     edi, 2
        mov     eax, [ebp + L_ATTR]
        call    hex32
        call    put_line
        jmp     .file_loop
.file_end:
        mov     ecx, eax
        mov     al, 'E'
        call    put_tag_hex                     ; "E <final GetNextFile status>"

        ; ReadSection(DXE_DEPEX = 0x13) and (PE32 = 0x10) on the target file
        mov     edx, 0x13
        call    read_sec
        mov     edx, 0x10
        call    read_sec
        ; ReadFile with a NULL buffer pointer -> size and type only
        mov     dword [ebp + L_BUF], 0
        mov     dword [ebp + L_BUFSZ], 0
        lea     eax, [ebp + L_AUTH]
        push    eax                             ; &AuthStatus
        lea     eax, [ebp + L_ATTR]
        push    eax                             ; &FileAttributes
        lea     eax, [ebp + L_TYPE]
        push    eax                             ; &FoundType
        lea     eax, [ebp + L_BUFSZ]
        push    eax                             ; &BufferSize
        push    0                               ; Buffer = NULL: size, type, attributes only
        lea     eax, [ebx + (target_guid - pic_base)]
        push    eax                             ; NameGuid
        mov     eax, [ebp + L_FV]
        push    eax
        call    [eax + FV_READFILE]
        add     esp, 28
        mov     ecx, eax
        mov     al, 'R'
        call    put_tag_hex                     ; "R <ReadFile status>"
        mov     ecx, [ebp + L_BUFSZ]
        mov     al, 'S'
        call    put_tag_hex                     ; "S <file size>"
        mov     ecx, [ebp + L_TYPE]
        and     ecx, 0xff
        mov     al, 'T'
        call    put_tag_hex                     ; "T <file type>"

        inc     dword [ebp + L_IDX]
        jmp     .fv_loop

.not_fv:
        mov     ecx, [ebp + L_IDX]
        mov     al, 'X'
        call    put_tag_hex                     ; "X <fv index>": not an FV interface, stop
.done:  xor     eax, eax
        pop     edi
        pop     esi
        pop     ebx
        leave
        ret

; read_sec: edx = section type. ReadSection(target, type, instance 0) into a firmware-allocated
; buffer; prints "D/P <status>" and "Z <size>", frees the buffer.
read_sec:
        push    edx
        mov     dword [ebp + L_BUF], 0
        mov     dword [ebp + L_BUFSZ], 0
        lea     eax, [ebp + L_AUTH]
        push    eax                             ; &AuthStatus
        lea     eax, [ebp + L_BUFSZ]
        push    eax                             ; &BufferSize
        lea     eax, [ebp + L_BUF]
        push    eax                             ; &Buffer (NULL -> allocated)
        push    0                               ; SectionInstance
        push    edx                             ; SectionType
        lea     eax, [ebx + (target_guid - pic_base)]
        push    eax                             ; NameGuid
        mov     eax, [ebp + L_FV]
        push    eax
        call    [eax + FV_READSECTION]
        add     esp, 28
        pop     edx
        mov     ecx, eax
        mov     al, 'D'
        cmp     edx, 0x13
        je      .tag
        mov     al, 'P'
.tag:   call    put_tag_hex
        mov     ecx, [ebp + L_BUFSZ]
        mov     al, 'Z'
        call    put_tag_hex
        mov     eax, [ebp + L_BUF]
        test    eax, eax
        jz      .nofree
        push    eax
        mov     eax, [ebp + L_BS]
        call    [eax + BS_FREEPOOL]
        add     esp, 4
.nofree:
        ret

; put_tag_hex: al = ASCII tag, ecx = value. Prints "<tag> <8 hex>\r\n".
put_tag_hex:
        push    edi
        lea     edi, [ebp + L_TEXT]
        movzx   eax, al
        mov     [edi], ax
        mov     word [edi + 2], ' '
        add     edi, 4
        mov     eax, ecx
        call    hex32
        call    put_line
        pop     edi
        ret

; hex32: eax -> 8 UCS-2 hex digits at edi, edi advanced by 16.
hex32:
        push    ecx
        push    edx
        mov     ecx, 8
.h:     rol     eax, 4
        mov     edx, eax
        and     edx, 0xf
        add     edx, '0'
        cmp     edx, '9'
        jbe     .d
        add     edx, 'A' - '0' - 10
.d:     mov     [edi], dx
        add     edi, 2
        loop    .h
        pop     edx
        pop     ecx
        ret

; put_line: terminate the text at edi with CR LF NUL and print the line buffer.
put_line:
        mov     word [edi], 13
        mov     word [edi + 2], 10
        mov     word [edi + 4], 0
        lea     eax, [ebp + L_TEXT]
        push    eax
        mov     eax, [ebp + L_COUT]
        push    eax
        call    [eax + 4]                       ; ConOut->OutputString
        add     esp, 8
        ret

        align   16, db 0
fv_guid:                                        ; 389F751F-1838-4388-8390-CD8154BD27F8
        dd      0x389f751f
        dw      0x1838, 0x4388
        db      0x83, 0x90, 0xcd, 0x81, 0x54, 0xbd, 0x27, 0xf8
target_guid:                                    ; A5D5B0C1-7D2F-4C3E-9B1A-26B1E0F17217 (BlwUcodeLate)
        dd      0xa5d5b0c1
        dw      0x7d2f, 0x4c3e
        db      0x9b, 0x1a, 0x26, 0xb1, 0xe0, 0xf1, 0x72, 0x17
