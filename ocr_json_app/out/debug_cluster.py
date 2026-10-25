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

covered = np.zeros(w, bool)
for x0, x1 in boxes:
    covered[x0:x1] = True
quiet = ~covered
runs = []
start = None
for i, has in enumerate(quiet):
    if has and start is None:
        start = i
    elif not has and start is not None:
        runs.append((start, i - 1))
        start = None
if start is not None:
    runs.append((start, w - 1))
content_left = min(b[0] for b in boxes)
content_right = max(b[1] for b in boxes)
min_width = max(8, int(w * 0.0025))
candidates = []
for left, right in runs:
    if right - left + 1 < min_width:
        continue
    if left <= content_left + min_width or right >= content_right - min_width:
        continue
    left_chars = sum(1 for x0, x1 in boxes if x1 <= left)
    right_chars = sum(1 for x0, x1 in boxes if x0 >= right)
    if left_chars >= 5 and right_chars >= 5:
        candidates.append((left, right, min(left_chars, right_chars)))
print('candidates', candidates)
merge_px = max(40, int(80 * scale))
print('merge_px', merge_px)
clusters = []
for cand in candidates:
    if clusters and cand[0] - clusters[-1][-1][1] <= merge_px:
        clusters[-1].append(cand)
    else:
        clusters.append([cand])
print('clusters', clusters)
best = [max(cluster, key=lambda item: item[2]) for cluster in clusters]
print('best', best)
threshold = max(8, int(len(boxes) * 0.01))
print('threshold', threshold)
best = [cand for cand in best if cand[2] >= threshold]
best.sort(key=lambda item: item[2], reverse=True)
print('sorted', best)
print('gutters', sorted(((left + right) // 2) for left, right, _ in best[:2]))
print('func', m.detect_column_gutters(g, chars=cs, scale=scale))
