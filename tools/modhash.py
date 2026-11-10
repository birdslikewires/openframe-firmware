import sys, os, hashlib, glob, re
# map (fv, guid) -> md5 of decompressed PE/TE/raw payloads concatenated
def mods(img):
    out = {}
    for fdir in sorted(glob.glob(img + '/volume-*/file-*')):
        fv = fdir.split('/')[1]
        guid = fdir.split('file-')[1]
        h = hashlib.md5()
        for p in sorted(glob.glob(fdir + '/**/*', recursive=True)):
            if os.path.isfile(p) and re.search(r'\.(pe|te|raw|ui|bin|dxe|pei|guid|vars)$|section\d+$', p) and 'compressed' not in p:
                h.update(open(p,'rb').read())
        out[(fv, guid)] = h.hexdigest()[:8]
    return out
imgs = sys.argv[1:]
m = [mods(i) for i in imgs]
keys = sorted(set().union(*m))
print('%-14s %-10s ' % ('fv','guid') + ' '.join('%9s' % os.path.basename(i)[:9] for i in imgs))
for k in keys:
    vals = [x.get(k, '-') for x in m]
    if len(set(vals)) > 1:
        print('%-14s %-10s ' % (k[0], k[1][:8]) + ' '.join('%9s' % v for v in vals))
