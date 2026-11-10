; MmcTest.efi: a shell app that shows whether large BlockIo reads come back right. For every
; BlockIo instance it prints the media and the ReadBlocks/WriteBlocks addresses, and whether
; ReadBlocks is the buggy MMC driver's (mmc_sig.inc). Then for each whole disk (not a partition)
; it reads 3 MB from LBA 2048 in one call, and the same 3 MB again in six 512 KB calls, and prints
; the CRC32 of each 512 KB of both. With the bug, the one-call read's third to sixth pieces repeat
; its second. Compare with Linux reading the same sectors. Reads only.
;
; Entry: EFI_STATUS EFIAPI Entry(EFI_HANDLE ImageHandle, EFI_SYSTEM_TABLE *SystemTable)

bits 32

TEST_LBA        equ     2048
TEST_SIZE       equ     0x300000                ; 3 MB: six 512 KB pieces
PIECE           equ     0x80000

; Locals (ebp-relative)
%define COUNT   ebp - 4
%define HANDLES ebp - 8
%define BIO     ebp - 12
%define INDEX   ebp - 16
%define BUFA    ebp - 20
%define BUFB    ebp - 24
%define STAT    ebp - 28
%define CRC     ebp - 32
%define K       ebp - 36

; PRINT label / HEX value: through con_out / con_hex with esi = SystemTable, edi = entry.here.
; They clobber eax, ecx, edx.
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

entry:
        push    ebp
        mov     ebp, esp
        sub     esp, 36
        push    ebx
        push    esi
        push    edi
        call    .here
.here:  pop     edi
        xor     eax, eax
        mov     [COUNT], eax
        mov     [HANDLES], eax
        mov     [INDEX], eax
        mov     esi, [ebp + 12]                 ; esi = SystemTable
        mov     ebx, [esi + 0x3c]               ; ebx = BootServices

        lea     eax, [HANDLES]
        push    eax
        lea     eax, [COUNT]
        push    eax
        push    0
        lea     eax, [edi + (mmc_bio_guid - .here)]
        push    eax
        push    2
        call    [ebx + 0xa8]                    ; LocateHandleBuffer
        add     esp, 20
        test    eax, eax
        jnz     .ret

.loop:  mov     eax, [INDEX]
        cmp     eax, [COUNT]
        jae     .free
        mov     edx, [HANDLES]
        mov     edx, [edx + eax * 4]
        lea     eax, [BIO]
        push    eax
        lea     eax, [edi + (mmc_bio_guid - .here)]
        push    eax
        push    edx
        call    [ebx + 0x58]                    ; HandleProtocol
        add     esp, 12
        test    eax, eax
        jnz     .next

        ; "bio N last L bs B flags F rd R wr W sig S"
        mov     edx, [BIO]
        mov     edx, [edx + 8]                  ; Media
        PRINT   s_bio
        HEX     dword [INDEX]
        PRINT   s_last
        mov     edx, [BIO]
        mov     edx, [edx + 8]
        HEX     dword [edx + 0x18]
        PRINT   s_bs
        mov     edx, [BIO]
        mov     edx, [edx + 8]
        HEX     dword [edx + 0x0c]
        PRINT   s_flags
        mov     edx, [BIO]
        mov     edx, [edx + 8]
        HEX     dword [edx + 4]                 ; Removable, Present, LogicalPartition, ReadOnly
        PRINT   s_rd
        mov     edx, [BIO]
        HEX     dword [edx + 0x10]
        PRINT   s_wr
        mov     edx, [BIO]
        HEX     dword [edx + 0x14]
        PRINT   s_sig
        lea     eax, [edi + (mmc_sig_read - .here)]
        push    eax
        mov     edx, [BIO]
        push    dword [edx + 0x10]
        call    mmc_sig_match
        add     esp, 8
        HEX     eax
        PRINT   s_crlf

        mov     edx, [BIO]
        mov     edx, [edx + 8]
        cmp     byte [edx + 6], 0               ; a partition: skip
        jne     .next
        cmp     dword [edx + 0x0c], 512
        jne     .next
        cmp     dword [edx + 0x1c], 0           ; LastBlock high
        jne     .big
        cmp     dword [edx + 0x18], TEST_LBA + TEST_SIZE / 512
        jb      .next
.big:   call    test_disk
.next:  inc     dword [INDEX]
        jmp     .loop

.free:  push    dword [HANDLES]
        call    [ebx + 0x30]                    ; FreePool
        add     esp, 4
        xor     eax, eax
.ret:   pop     edi
        pop     esi
        pop     ebx
        leave
        ret


; test_disk: runs in entry's frame (ebp, ebx = BS, esi = ST, edi = entry.here, [BIO]).
test_disk:
        xor     eax, eax
        mov     [BUFA], eax
        mov     [BUFB], eax
        lea     eax, [BUFA]
        push    eax
        push    TEST_SIZE
        push    4                               ; EfiBootServicesData
        call    [ebx + 0x2c]                    ; AllocatePool
        add     esp, 12
        test    eax, eax
        jnz     .done
        lea     eax, [BUFB]
        push    eax
        push    TEST_SIZE
        push    4
        call    [ebx + 0x2c]
        add     esp, 12
        test    eax, eax
        jnz     .done

        mov     edx, [BIO]                      ; one call
        push    dword [BUFA]
        push    TEST_SIZE
        push    0
        push    TEST_LBA
        mov     eax, [edx + 8]
        push    dword [eax]                     ; MediaId
        push    edx
        call    [edx + 0x10]
        add     esp, 24
        mov     [STAT], eax
        PRINT   s_one
        HEX     dword [STAT]
        PRINT   s_colon
        mov     eax, [BUFA]
        call    crcs

        mov     dword [STAT], 0                 ; six calls
        mov     dword [K], 0
.pc:    mov     edx, [BIO]
        mov     eax, [K]
        shl     eax, 19                         ; * 512 KB
        add     eax, [BUFB]
        push    eax
        push    PIECE
        push    0
        mov     eax, [K]
        shl     eax, 10                         ; * 1024 blocks
        add     eax, TEST_LBA
        push    eax
        mov     eax, [edx + 8]
        push    dword [eax]
        push    edx
        call    [edx + 0x10]
        add     esp, 24
        or      [STAT], eax
        inc     dword [K]
        cmp     dword [K], TEST_SIZE / PIECE
        jb      .pc
        PRINT   s_pcs
        HEX     dword [STAT]
        PRINT   s_colon
        mov     eax, [BUFB]
        call    crcs

.done:  mov     eax, [BUFA]
        test    eax, eax
        jz      .nb
        push    eax
        call    [ebx + 0x30]
        add     esp, 4
.nb:    mov     eax, [BUFB]
        test    eax, eax
        jz      .ret
        push    eax
        call    [ebx + 0x30]
        add     esp, 4
.ret:   ret

; crcs: eax = buffer. Prints the CRC32 of each 512 KB piece, then CRLF. Entry's frame.
crcs:
        push    eax                             ; [esp] = buffer
        mov     dword [K], 0
.c:     mov     eax, [K]
        shl     eax, 19
        add     eax, [esp]
        lea     edx, [CRC]
        push    edx
        push    PIECE
        push    eax
        call    [ebx + 0xb8]                    ; CalculateCrc32
        add     esp, 12
        PRINT   s_sp
        HEX     dword [CRC]
        inc     dword [K]
        cmp     dword [K], TEST_SIZE / PIECE
        jb      .c
        PRINT   s_crlf
        pop     eax
        ret

s_bio:   dw     __utf16__('bio '), 0
s_last:  dw     __utf16__(' last '), 0
s_bs:    dw     __utf16__(' bs '), 0
s_flags: dw     __utf16__(' flags '), 0
s_rd:    dw     __utf16__(' rd '), 0
s_wr:    dw     __utf16__(' wr '), 0
s_sig:   dw     __utf16__(' sig '), 0
s_one:   dw     __utf16__('  one '), 0
s_pcs:   dw     __utf16__('  pcs '), 0
s_colon: dw     __utf16__(':'), 0
s_sp:    dw     __utf16__(' '), 0
s_crlf:  dw     13, 10, 0

%include "con.inc"
%include "mmc_fix.inc"
