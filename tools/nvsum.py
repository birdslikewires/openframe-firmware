import sys, os, glob, hashlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nvar import parse
def bootdesc(d):
    attr = int.from_bytes(d[0:4],'little'); fpl = int.from_bytes(d[4:6],'little')
    e = 6
    while d[e:e+2] != b'\0\0': e += 2
    return '%s attr=%x' % (d[6:e].decode('utf-16le'), attr)
for img in sys.argv[1:]:
    f = glob.glob(img + '/volume-0/*/file.obj')[0]
    buf = open(f,'rb').read()
    v, n, used = parse(buf)
    print('==== %s  entries=%d used=0x%x' % (os.path.basename(img), n, used))
    for name, guid, data, hops in v:
        extra = ''
        if name.startswith('Boot0') : extra = bootdesc(data)
        elif name == 'BootOrder': extra = ' '.join('%04x' % int.from_bytes(data[i:i+2],'little') for i in range(0,len(data),2))
        elif name == 'StdDefaults':
            sv, _, _ = parse(data + b'\xff'*16)
            extra = '; '.join('%s(%d) md5=%s' % (a, len(c), hashlib.md5(c).hexdigest()[:8]) for a,b,c,h in sv)
        elif name == 'MonotonicCounter': extra = 'count=%d' % int.from_bytes(data,'little')
        elif len(data) <= 8: extra = data.hex()
        print('  %-20s upd=%-4d %s' % (name, hops, extra))
