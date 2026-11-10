import sys, struct, uuid
def parse(buf):
    # NVRAM file body: region 0x48..0x10000 in volume 0; GUID store at end
    start = buf.find(b'NVAR')
    end = len(buf)
    guids = []
    p = end - 16
    while p > start:
        g = buf[p:p+16]
        if g == b'\xff'*16: break
        guids.append(str(uuid.UUID(bytes_le=g)))
        p -= 16
    out = []
    off = start
    entries = {}
    while off < end - 10 and buf[off:off+4] == b'NVAR':
        size, = struct.unpack_from('<H', buf, off+4)
        nxt = int.from_bytes(buf[off+6:off+9], 'little')
        attr = buf[off+9]
        q = off + 10
        name = None; guid = None
        if attr & 0x10:  # ext header -> skip parse of ext at end
            pass
        if not (attr & 0x08):
            if attr & 0x04:
                guid = str(uuid.UUID(bytes_le=buf[q:q+16])); q += 16
            else:
                gi = buf[q]; q += 1
                guid = guids[gi] if gi < len(guids) else 'guid#%d' % gi
            if attr & 0x02:
                e = buf.index(b'\0', q); name = buf[q:e].decode('ascii', 'replace'); q = e + 1
            else:
                e = q
                while buf[e:e+2] != b'\0\0': e += 2
                name = buf[q:e].decode('utf-16le', 'replace'); q = e + 2
        data = buf[q:off+size]
        entries[off] = dict(off=off, size=size, nxt=nxt, attr=attr, name=name, guid=guid, data=data)
        off += size
        if size == 0 or size == 0xffff: break
    # resolve chains: head entries (have name), follow nxt to last
    vars_ = []
    for o, e in entries.items():
        if e['name'] is None: continue
        if not (e['attr'] & 0x80): continue
        cur = e; hops = 0
        while cur['nxt'] != 0xffffff and (cur['off'] + cur['nxt']) in entries:
            cur = entries[cur['off'] + cur['nxt']]; hops += 1
        if not (cur['attr'] & 0x80): continue  # deleted
        vars_.append((e['name'], e['guid'], cur['data'], hops))
    return vars_, len(entries), off
if __name__ == '__main__':
    buf = open(sys.argv[1], 'rb').read()
    v, n, used = parse(buf)
    print('# entries %d, used to 0x%x of 0x%x, live vars %d' % (n, used, len(buf), len(v)))
    for name, guid, data, hops in v:
        print('%-28s %s len=%-5d upd=%-3d %s' % (name, guid[:8], len(data), hops, data[:24].hex()))
