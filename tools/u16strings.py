import sys, re
d = open(sys.argv[1],'rb').read()
pat = re.compile(rb'(?:[\x20-\x7e]\x00){%s,}' % sys.argv[2].encode() if len(sys.argv)>2 else rb'(?:[\x20-\x7e]\x00){5,}')
for m in pat.finditer(d):
    print('%06x %s' % (m.start(), m.group().decode('utf-16le')))
