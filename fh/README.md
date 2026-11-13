# fh has moved

The firmware hub kernel module (`/dev/fh`, `/dev/fhp`) now lives in
[openframe-kernel](https://github.com/birdslikewires/openframe-kernel/tree/main/fh), with its
history (from the 2019 `fh` repository, through this one, to 2026-11-13). OpenFrame kernel packages
build it as a module; its README says how to build it for any other kernel.

The flash scripts here use only its interface: `/dev/fh` (erase and program whole 64 KB blocks),
`/dev/fhp` (program without erasing, from the first kernel built after 2026-11-13), and the load-time
options `writable=1` and `unprotect=1`. `FH_KO=/path/to/fh.ko` makes a script load a module that
isn't installed.
