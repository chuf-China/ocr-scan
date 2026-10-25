import sys
sys.path.insert(0, 'src')
import numpy as np
import pypdfium2 as pdfium
import ocr_pipeline_layout as m

pdf = r'data\exams\2023北京卷.pdf'
doc = pdfium.PdfDocument(pdf)
pil = doc[1].render(scale=150 / 72).to_pil().convert('L')
g = np.asarray(pil)
probe = m.PdfFontProbe(pdf)
cs = probe.char_boxes(2)
h, w = g.shape
scale = 150 / 72
y0, y1 = int(h * .03), int(h * .86)
ink = g < m.INK_THRESHOLD
punct = set('、，。；：！？,.;:!?·…—')
boxes = []
for c in cs:
    if (c.get('text') or '').strip() in punct:
        continue
    x0, x1 = int(c['x0'] * scale), int(c['x1'] * scale) + 1
    ty0, ty1 = int(c['top'] * scale), int(c['bottom'] * scale) + 1
    if x1 <= x0 or ty1 <= y0 or ty0 >= y1:
        continue
    x0, x1 = max(0, x0), min(w, x1)
    ty0, ty1 = max(0, ty0), min(h, ty1)
    if x1 <= x0 or ty1 <= ty0:
        continue
    if ink[ty0:ty1, x0:x1].sum() == 0:
        continue
    boxes.append((x0, x1))
print('boxes', len(boxes), 'range', min(b[0] for b in boxes), max(b[1] for b in boxes))
cov = np.zeros(w, bool)
for a, b in boxes:
    cov[a:b] = True
runs = []
st = None
for i, has in enumerate(~cov):
    if has and st is None:
        st = i
    elif not has and st is not None:
        runs.append((st, i - 1))
        st = None
if st is not None:
    runs.append((st, w - 1))
print('quiet', [r for r in runs if r[1] - r[0] + 1 >= 8 and r[0] > 25 and r[1] < w - 25])
for left, right in runs:
    if right - left + 1 < 8:
        continue
    lc = sum(1 for a, b in boxes if b <= left)
    rc = sum(1 for a, b in boxes if a >= right)
    if lc >= 5 and rc >= 5:
        print('cand', left, right, 'counts', lc, rc, 'center', (left + right) // 2)
