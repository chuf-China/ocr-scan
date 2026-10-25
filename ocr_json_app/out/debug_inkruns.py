import sys
sys.path.insert(0, 'src')
import numpy as np
import pypdfium2 as pdfium
import ocr_pipeline_layout as m

pdf = r'data\exams\2023北京卷.pdf'
doc = pdfium.PdfDocument(pdf)
pil = doc[1].render(scale=150 / 72).to_pil().convert('L')
g = np.asarray(pil)
h, w = g.shape
ink = g < m.INK_THRESHOLD
body = ink[int(h * .03):int(h * .86)]
col = body.sum(axis=0)
for th in [0, 1, 2, 5]:
    q = col <= th
    runs = []
    st = None
    for i, v in enumerate(q):
        if v and st is None:
            st = i
        elif not v and st is not None:
            runs.append((st, i - 1))
            st = None
    if st is not None:
        runs.append((st, w - 1))
    print('th', th, [r for r in runs if r[1] - r[0] + 1 >= 8 and r[0] > 25 and r[1] < w - 25])
