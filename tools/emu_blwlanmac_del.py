#!/usr/bin/env python3
"""
Run BlwLanMacDel.efi (driver/blwlanmac_del.asm) under emu_blwmac.py's fake OpenFrame: it must delete
BlwLanMac after locking blocks 1-15 down, leave BlwMac alone, and write nothing if the variable is
already gone or the lock-down doesn't stick.

Usage: emu_blwlanmac_del.py <BlwLanMacDel.efi>
"""
import sys, struct
sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.abspath(__file__)))
import emu_blwmac as E
pe = open(sys.argv[1], 'rb').read()
MAC = bytes.fromhex('020e8e123456'); NEW = bytes.fromhex('000e8e123456')
both = {('BlwLanMac', E.BLW): (7, MAC), ('BlwMac', E.BLW): (7, NEW)}
bad = 0
for name, kw, want in [
    ('BlwLanMac there: deleted, BlwMac kept', dict(store=both), (0, 0, 0, 0x8000000E)),
    ('already gone: nothing written', dict(store={('BlwMac', E.BLW): (7, NEW)}), (0x8000000E, 0, 0, 0)),
    ('lock-down does not stick: nothing written', dict(store=both, lock_ignores=True), (0, 1, 0, 0)),
]:
    r = E.run(pe, loadopts='fs1:\\BlwLanMacDel.efi', **kw)
    p = list(r['errors'])
    st = r['store'].get(('BlwLanMacDel', E.BLW))
    got = struct.unpack('<4I', st[1]) if st else None
    if got != want: p.append('results %s, expected %s' % (got and [hex(x) for x in got], [hex(x) for x in want]))
    if st and st[0] != 6: p.append('status not volatile')
    sets = [e for e in r['events'] if e.startswith('SetVariable(') and 'BlwLanMacDel' not in e]
    locks = [e for e in r['events'] if e.startswith('lock[')]
    if want[0] == 0 and want[1] == 0:
        if sets != ['SetVariable(BlwLanMac, BLW, attr=0x7, )']: p.append('writes %s' % sets)
        if locks != ['lock[%d] <- 0x3' % b for b in range(1, 16)]: p.append('locks %s' % locks)
        if ('BlwLanMac', E.BLW) in r['store']: p.append('BlwLanMac still there')
    elif sets: p.append('unexpected writes %s' % sets)
    if r['store'].get(('BlwMac', E.BLW)) != (7, NEW): p.append('BlwMac touched')
    if r['eax'] != 0: p.append('returned %#x' % r['eax'])
    print('%-4s %s' % ('ok' if not p else 'FAIL', name)); [print('      ' + x) for x in p]; bad += bool(p)
sys.exit(bad)
