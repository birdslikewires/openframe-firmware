import sys, struct, uuid
img = open(sys.argv[1],'rb').read()
fv0 = int(sys.argv[2],16) if len(sys.argv)>2 else 0x10000
hlen, = struct.unpack_from('<H', img, fv0+0x30)
fvlen, = struct.unpack_from('<Q', img, fv0+0x20)
attrs, = struct.unpack_from('<I', img, fv0+0x2c)
print('FV @0x%x len 0x%x hdr 0x%x attrs 0x%x sig %s' % (fv0, fvlen, hlen, attrs, img[fv0+0x28:fv0+0x2c]))
off = fv0 + hlen
while off < fv0 + fvlen:
    off = (off + 7) & ~7
    if img[off:off+24] == b'\xff'*24: break
    g = uuid.UUID(bytes_le=img[off:off+16])
    hck, fck, typ, att = img[off+16], img[off+17], img[off+18], img[off+19]
    size = int.from_bytes(img[off+20:off+23],'little'); state = img[off+23]
    hsum = (sum(img[off:off+24]) - fck - state) & 0xff
    dsum = sum(img[off+24:off+size]) & 0xff
    print('0x%06x %s type=%02x attr=%02x size=0x%06x state=%02x hdrsum=%02x datasum+fck=%02x end=0x%06x' % (off, str(g)[:8], typ, att, size, state, hsum, (dsum + fck) & 0xff, off+size))
    off += size
print('free from 0x%x to 0x%x (%d bytes)' % (off, fv0+fvlen, fv0+fvlen-off))
