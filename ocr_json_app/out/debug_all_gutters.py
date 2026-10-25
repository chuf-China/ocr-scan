import sys
from pathlib import Path
sys.path.insert(0, 'src')
import numpy as np
import pypdfium2 as pdfium
import ocr_pipeline_layout as m

roots = [Path('data/exams'), Path('data/exams/corpus')]
pdfs = sorted(p for root in roots if root.exists() for p in root.glob('*.pdf'))
for pdf in pdfs:
    try:
        doc = pdfium.PdfDocument(str(pdf))
        probe = m.PdfFontProbe(str(pdf))
        parts = []
        for i in range(len(doc)):
            page = doc[i]
            pil = page.render(scale=150 / 72).to_pil().convert('L')
            gray = np.asarray(pil)
            cs = probe.char_boxes(i + 1)
            gs = m.detect_column_gutters(gray, chars=cs, scale=150 / 72)
            parts.append(str(gs))
        print(f'{pdf.name}: {" | ".join(parts)}')
    except Exception as exc:
        print(f'{pdf.name}: ERROR {exc}')
