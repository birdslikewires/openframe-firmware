import sys, re, subprocess
def funcs(pe):
    asm = subprocess.run(['objdump','-d','--x86-asm-syntax=intel',pe],capture_output=True,text=True).stdout
    lines = [l for l in asm.splitlines() if re.match(r'^[0-9a-f]+:', l)]
    addrs=[]; ins={}
    for l in lines:
        a=int(l.split(':')[0],16); addrs.append(a)
        parts=l.split('\t'); ins[a]=' '.join(x.strip() for x in parts[1:]).strip()
    targets=set()
    for a,t in ins.items():
        m=re.match(r'call\s+0x([0-9a-f]+)',t)
        if m: targets.add(int(m.group(1),16))
    # also entry
    targets=sorted(targets)
    end=addrs[-1]
    out=[]
    for i,t in enumerate(targets):
        nxt=targets[i+1] if i+1<len(targets) else end
        body=[ins[a] for a in addrs if t<=a<nxt]
        # normalise: strip absolute addresses/immediates
        sig=' '.join(re.sub(r'0x[0-9a-f]+','N',x.split()[0] if x else '') for x in body)
        out.append((t,nxt-t,sig,body))
    return out
a=funcs(sys.argv[1]); b=funcs(sys.argv[2])
print(len(a),'funcs in bad,',len(b),'in good')
import difflib
sa=[x[2] for x in a]; sb=[x[2] for x in b]
sm=difflib.SequenceMatcher(None,sa,sb,autojunk=False)
for op,i1,i2,j1,j2 in sm.get_opcodes():
    if op=='equal': continue
    print(op, 'bad:',[(hex(a[i][0]),a[i][1]) for i in range(i1,i2)], 'good:',[(hex(b[j][0]),b[j][1]) for j in range(j1,j2)])
