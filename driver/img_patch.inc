; img_patch.inc: small changes to two stock drivers' code, made in memory as they load (the flash
; copies are untouched). Each is made only to the exact stock function, found by its FV file GUID
; and a byte signature, and only once:
;   - NBDXE: the panel clock value it writes (panel_clock.inc, nb_patch_image), from 26.11.24;
;   - MmcMediaDevice: its CE-ATA check answers "no" at once (ceata.inc, cea_patch_image), from
;     26.11.25;
;   - USBRT, both copies (the SMM one while SMRAM is still open): two shorter waits and more
;     tries (usbrt.inc, urt_patch_image), from 26.11.26.
; NBDXE is loaded before this driver, so it is patched at the entry; the others after, so
; through a LoadedImage protocol notify, before anything uses them.

; UINT32 img_patch(EFI_BOOT_SERVICES *BS): looks through every loaded image (EFI_LOADED_IMAGE)
; for the ones to change. 1 once all are made (now or before: NBDXE, MmcMediaDevice and two
; USBRT copies), else 0. cdecl.
; Keeps ebx, esi, edi.
img_patch:
        push    ebp
        mov     ebp, esp
        sub     esp, 16                         ; -4 count, -8 buffer, -12 LoadedImage, -16 index
        push    ebx
        push    esi
        push    edi
        call    .here
.here:  pop     edi
        call    .both
        jnz     .out
        xor     eax, eax
        mov     [ebp - 4], eax
        mov     [ebp - 8], eax
        mov     [ebp - 16], eax
        mov     ebx, [ebp + 8]                  ; BS
        lea     eax, [ebp - 8]                  ; LocateHandleBuffer(ByProtocol, &LoadedImage, ...)
        push    eax
        lea     eax, [ebp - 4]
        push    eax
        push    0
        lea     eax, [edi + (nbp_li_guid - .here)]
        push    eax
        push    2
        call    [ebx + 0xa8]
        add     esp, 20
        test    eax, eax
        jnz     .none
.loop:  mov     eax, [ebp - 16]
        cmp     eax, [ebp - 4]
        jae     .free
        mov     edx, [ebp - 8]
        mov     edx, [edx + eax * 4]
        lea     eax, [ebp - 12]                 ; HandleProtocol(handle, &LoadedImage, &li)
        push    eax
        lea     eax, [edi + (nbp_li_guid - .here)]
        push    eax
        push    edx
        call    [ebx + 0x58]
        add     esp, 12
        test    eax, eax
        jnz     .next
        cmp     byte [edi + (nbp_done - .here)], 0
        jne     .mmc
        push    dword [ebp - 12]
        call    nb_patch_image
        add     esp, 4
        test    eax, eax
        jz      .mmc
        mov     byte [edi + (nbp_done - .here)], 1
.mmc:   cmp     byte [edi + (cea_done - .here)], 0
        jne     .chk
        push    dword [ebp - 12]
        call    cea_patch_image
        add     esp, 4
        test    eax, eax
        jz      .chk
        mov     byte [edi + (cea_done - .here)], 1
.chk:   cmp     byte [edi + (urt_n - .here)], 2 ; USBRT: any copy not changed yet (a changed one
        jae     .all                            ; no longer matches, so isn't counted twice)
        push    dword [ebp - 12]
        call    urt_patch_image
        add     esp, 4
        add     [edi + (urt_n - .here)], al
.all:   call    .both
        jnz     .free
.next:  inc     dword [ebp - 16]
        jmp     .loop
.free:  push    dword [ebp - 8]
        call    [ebx + 0x30]                    ; FreePool
        add     esp, 4
.none:  call    .both
.out:   pop     edi
        pop     esi
        pop     ebx
        leave
        ret

; .both: in edi = img_patch.here. eax = 1 and ZF clear if all changes are made, else eax = 0, ZF set.
.both:  xor     eax, eax
        cmp     byte [edi + (nbp_done - .here)], 0
        je      .b0
        cmp     byte [edi + (cea_done - .here)], 0
        je      .b0
        cmp     byte [edi + (urt_n - .here)], 2
        jb      .b0
        inc     eax
.b0:    test    eax, eax
        ret

; VOID EFIAPI img_patch_notify(EFI_EVENT Event, VOID *Context = BootServices): an image has loaded
img_patch_notify:
        push    dword [esp + 8]
        call    img_patch
        add     esp, 4
        ret
