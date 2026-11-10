import sys, re, os
rows = {}
imgs = []
for p in sys.argv[1:]:
    n = os.path.basename(p).replace('.txt','')
    imgs.append(n)
    fv = None
    for line in open(p, errors='replace'):
        m = re.match(r'\| Firmware Volume : (\d+)', line)
        if m: fv = m.group(1)
        m = re.match(r'\|(\d{3})\|(.{15})\|([0-9A-F-]{36})\|([0-9A-F]{8})\|([0-9A-F]{6})\|(\w+)', line)
        if m:
            key = (fv, m.group(3))
            rows.setdefault(key, {'name': m.group(2).strip(), 'type': m.group(6)})[n] = int(m.group(5), 16)
short = [i.replace('of1_efi_','1:').replace('of2_efi_','2:').replace('of2_','2:') for i in imgs]
print('FV %-16s %-5s ' % ('name', 'type') + ' '.join('%10s' % s[:10] for s in short))
for (fv, g), r in sorted(rows.items()):
    vals = [r.get(i) for i in imgs]
    if len(set(vals)) == 1: continue  # identical everywhere
    print('%s  %-16s %-5s ' % (fv, (r['name'] or g[:8]), r['type']) + ' '.join('%10s' % (('%x' % v) if v is not None else '-') for v in vals))
