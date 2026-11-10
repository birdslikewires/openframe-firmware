import sys
from PIL import Image, ImageDraw
K = 10                      # supersample factor
W, H = 80 * K, 81 * K
def s(*v): return [x * K for x in v]
im = Image.new('L', (W, H), 0)
d = ImageDraw.Draw(im)
wire_y = 60
# wire, edge to edge
d.rectangle(s(0, wire_y, 80, wire_y + 1.6), fill=255)
# body: a plump teardrop, leaning slightly forward
d.ellipse(s(22, 26, 56, 54), fill=255)
# head
d.ellipse(s(44, 15, 62, 33), fill=255)
# beak
d.polygon(s(60.5, 21, 69, 24.5, 60.5, 27.5), fill=255)
# neck: fill the notch between head and body
d.polygon(s(45, 27, 58, 27, 56, 40, 40, 42), fill=255)
# tail, a tapered sweep down and back
d.polygon(s(28, 36, 6, 50, 8, 53, 33, 49), fill=255)
# legs to the wire
d.rectangle(s(36, 52, 37.6, wire_y), fill=255)
d.rectangle(s(42, 52, 43.6, wire_y), fill=255)
# eye
d.ellipse(s(54, 20.5, 57.5, 24), fill=0)
out = im.resize((80, 81), Image.LANCZOS).convert('RGB')
out.save(sys.argv[1])
out.resize((400, 405), Image.NEAREST).save(sys.argv[2])
# panel-scale preview: 800x480 black with the logo at the splash position
panel = Image.new('RGB', (800, 480), 0); panel.paste(out, (360, 191)); panel.save(sys.argv[3])
