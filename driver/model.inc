; model.inc: which OpenFrame this is, for SMBIOS Type 1, from 26.11.31.
;
; One block 10 serves the OpenFrame 1 and the OpenFrame 2, so the driver can't be told at build
; time. Their firmware differs in the boot block (blocks 14-15), which no update of ours ever
; writes: AMI's PEI core there carries the ROM's name at 0xFFFEA268 (image offset 0xEA268), in
; every image we have: "0ABPW400.rom" on every OF1 (O2, Verizon, Telstra, OpenPeak; all our
; releases and dumps), "0ABPW005.rom" on every good OF2 (Cisco, Telio, Avaya, Da Vinci, OpenPeak;
; the do-not-use Swisscom and Telio builds say 006 and 007). The firmware hub is mapped there
; (it is where the CPU fetched its reset vector); this only reads it.
;
; The table is built with the OF1's names ("OpenFrame 1", "OF1"); on 0ABPW005 their digits become 2.
; Anything else is left as built.

; model_fill: in edi = the structure table as copied. Keeps every register but eax.
model_fill:
        cmp     dword [0xfffea268], 0x50424130  ; "0ABP"
        jne     .r
        cmp     dword [0xfffea26c], 0x35303057  ; "W005"
        jne     .r
        mov     byte [edi + SMBIOS_MODEL1_OFF], '2'   ; "OpenFrame 2"
        mov     byte [edi + SMBIOS_MODEL2_OFF], '2'   ; "OF2"
.r:     ret
