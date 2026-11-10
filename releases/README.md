# Release images

The images themselves are published on this repository's
[Releases](https://github.com/birdslikewires/openframe-firmware/releases) page, one per version, and
are not kept in git (`.gitignore` ignores `releases/*.bin`). This folder is where they go when
downloaded or built, since `tools/mkbase.py` builds each release from the previous one:

    gh release download 26.11.21 -R birdslikewires/openframe-firmware -D releases/
    md5sum -c releases/MD5SUMS --ignore-missing

`MD5SUMS` lists every release's md5.
