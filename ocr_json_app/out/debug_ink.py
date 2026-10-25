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
y0, y1 = int(h * 0.03), int(h * 0.86)
body = ink[y0:y1]
for x in [843, 859, 875, 899, 906, 914, 1398, 1401, 1405, 1618, 1634, 1650, 1674, 1681, 1688, 1900, 1950]:
    band = body[:, x - 3:x + 4]
    print(x, int(band.sum()), int((band.sum(axis=1) > 0).sum()))
