# Intel microcode for the Atom Z5xx (CPUID 06-1C-02)

`06-1c-02` is Intel's microcode file for family 6, model 0x1C, stepping 2, from
[Intel-Linux-Processor-Microcode-Data-Files](https://github.com/intel/Intel-Linux-Processor-Microcode-Data-Files)
(`intel-ucode/06-1c-02`). It is redistributed unmodified under Intel's licence, copied here as
[LICENSE](LICENSE); the release images carry the same file's 0x217 entry, unmodified, inside
BlwPlatformDxe. `build_ucode_driver.py --microcode` picks the entry for the image's own CPU signature and
platform flags (revision 0x217 for the OpenFrame 1's Z520) and embeds it in BlwPlatformDxe.
