# Release images

The images themselves are published on this repository's
[Releases](https://github.com/birdslikewires/openframe-firmware/releases) page, one per version, and
are not kept in git (`.gitignore` ignores `releases/*.bin`). This folder is where they go when
downloaded or built, since `tools/mkbase.py` builds each release from the previous one:

    gh release download 26.11.29 -R birdslikewires/openframe-firmware -D releases/
    md5sum -c releases/MD5SUMS --ignore-missing

`MD5SUMS` lists every published release's md5 (26.11.29 on; 26.11.15 to 26.11.28 were withdrawn
on 2026-11-13, and their md5s stay in docs/releases.md).
