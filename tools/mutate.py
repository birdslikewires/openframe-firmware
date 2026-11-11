"""Mutation testing for BlwPlatformDxe: breaks one thing at a time and checks the emulator notices.

    venv/bin/python -I tools/mutate.py MUTATIONS.py --base /tmp/base.bin --version 26.11.99 \\
        [--define TIMING ...] [--mode screen] [-j 24]

MUTATIONS.py (any path) defines MUTATIONS = [(label, file, old, new), ...]: in `file` (relative to
the repository), `old` (which must occur exactly once) becomes `new`. Each mutation gets its own
copy of driver/ and tools/ in a temporary directory, so the source tree is never touched and the
mutations run in parallel. Each is built with build_ucode_driver.py and run through
emu_platform.py --first-fail (with --<mode>, if given). The unmutated source runs first: if it
fails, nothing below means anything. A mutation is CAUGHT if the emulator fails, SURVIVED if it
passes (a check nothing tests, or one that changes nothing), BUILD FAILED if it doesn't assemble.
"""
import argparse
import concurrent.futures
import os
import runpy
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run_one(label, file, old, new, a, tmp_root):
    tmp = tempfile.mkdtemp(prefix='mut-', dir=tmp_root)
    try:
        for d in ('driver', 'tools'):
            shutil.copytree(os.path.join(ROOT, d), os.path.join(tmp, d))
        if file:
            p = os.path.join(tmp, file)
            s = open(p).read()
            if s.count(old) != 1:
                return label, 'PATTERN (%d matches)' % s.count(old), ''
            open(p, 'w').write(s.replace(old, new))
        defs = sum((['--define', d] for d in a.define), [])
        b = subprocess.run([sys.executable, '-I', os.path.join(tmp, 'tools', 'build_ucode_driver.py'), a.base,
                            os.path.join(tmp, 'img.bin'), '--microcode', os.path.join(ROOT, 'third_party', 'intel-ucode', '06-1c-02'),
                            '--bios-version', a.version, '--bios-date', a.date] + defs +
                           ['--pe', os.path.join(tmp, 'd.efi'), '--app', os.path.join(tmp, 'a.efi')],
                           capture_output=True, text=True)
        if b.returncode:
            return label, 'BUILD FAILED', (b.stdout + b.stderr).strip().splitlines()[-1:]
        e = subprocess.run([sys.executable, '-I', os.path.join(tmp, 'tools', 'emu_platform.py'),
                            os.path.join(tmp, 'd.efi'), os.path.join(tmp, 'a.efi'), '--first-fail'] +
                           (['--' + a.mode] if a.mode else []), capture_output=True, text=True)
        lines = [l for l in (e.stdout + e.stderr).splitlines() if l.startswith('FAIL') or l.startswith('Traceback')]
        return label, ('CAUGHT' if e.returncode else 'SURVIVED'), lines[:1]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('mutations')
    ap.add_argument('--base', required=True)
    ap.add_argument('--version', required=True)
    ap.add_argument('--date', default='11/11/2026')
    ap.add_argument('--define', action='append', default=[])
    ap.add_argument('--mode', default='', help='emu_platform mode: timing, profile, usbtrace, screen')
    ap.add_argument('-j', type=int, default=os.cpu_count())
    a = ap.parse_args()
    muts = runpy.run_path(a.mutations)['MUTATIONS']
    tmp_root = tempfile.mkdtemp(prefix='mutate-')
    try:
        base = run_one('(unmutated)', None, None, None, a, tmp_root)
        print('%-40s %s %s' % (base[0], 'ok' if base[1] == 'SURVIVED' else base[1], ' '.join(base[2])))
        if base[1] != 'SURVIVED':
            sys.exit('the unmutated source fails: fix that first')
        with concurrent.futures.ThreadPoolExecutor(a.j) as ex:
            futs = [ex.submit(run_one, *m, a, tmp_root) for m in muts]
            results = [f.result() for f in futs]
        for label, verdict, why in results:
            print('%-40s %-14s %s' % (label, verdict, (' '.join(why))[:110]))
        survived = [r for r in results if r[1] != 'CAUGHT']
        print('%d of %d caught' % (len(results) - len(survived), len(results)))
        sys.exit(1 if survived else 0)
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


if __name__ == '__main__':
    main()
