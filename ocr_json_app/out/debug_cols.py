import sys
sys.path.insert(0, 'src')
import numpy as np
import pypdfium2 as pdfium
import ocr_pipeline_layout as m
from PIL import Image

pdf = r'data\exams\2023北京卷.pdf'
doc = pdfium.PdfDocument(pdf)
page = doc[1]
pil = page.render(scale=150 / 72).to_pil().convert('RGB')
gray = np.asarray(pil.convert('L'))
probe = m.PdfFontProbe(pdf)
native_boxes = probe.figure_boxes(2)
gray_for_lines = m._mask_figure_areas(gray, native_boxes, 150 / 72)
chars = probe.char_boxes(2)
gutters = m.detect_column_gutters(gray, chars=chars, scale=150 / 72)
print('gutters', gutters)
bounds = [(0, gray.shape[1])]
if gutters:
    bounds = []
    st = 0
    for g in gutters:
        bounds.append((st, g))
        st = g
    bounds.append((st, gray.shape[1]))
print('bounds', bounds)
mask = m.text_coverage_mask(chars, gray_for_lines.shape, 150 / 72)
for left, right in bounds:
    col = gray_for_lines[:, left:right]
    lines, typ = m.detect_lines(col)
    print('col', left, right, 'lines', len(lines), 'typical', typ)
    for ln in lines[:8]:
        print('  ', ln['px_top'], ln['px_bottom'], ln['px_left'] + left, ln['px_right'] + left, ln['px_height'])
    ext, figs = m.split_and_classify_regions(col, typ, mask[:, left:right])
    print('  extra', len(ext), 'figs', len(figs))
    for f in figs[:5]:
        print('   fig', f.get('px'), f.get('source'), f.get('kind'))
