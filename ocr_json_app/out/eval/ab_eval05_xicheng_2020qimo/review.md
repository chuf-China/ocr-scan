# 公式内容级 A/B 复核表

- layout：`C:\Users\Administrator\Desktop\ocr\ocr scan\ocr_json_app\out\eval\eval05_xicheng_2020qimo.formula.json`
- 公式证据：`eval05_xicheng_2020qimo.formula.json`
- 总证据 65 条，抽样 16 条（配额 differs/partial/normalized/exact）

判定口径：对照裁片，比较「现有 OCR 片段（current）」与「模型 LaTeX（model）」，
逐条填 h = 模型更好 / o = 现有更好 / b = 相同（都好或都错）。

| # | 页/区域 | 一致性 | current（现有 OCR） | model（公式模型） | score | 判定 |
|---|---|---|---|---|---|---|
| 1 | p3/p3-r4 | differs | `\boldsymbol{a} = (-4,6); \boldsymbol{b} = (2,x); \boldsymbol{a} \parallel \boldsymbol{b}; x \in \mathbb{R}` | `a\mathbin{//}b` | 0.64 |  |
| 2 | p3/p3-r5 | partial | `a_2, a_4, a_{12}` | `a_{\mathrm{~12~}},` | 0.55 |  |
| 3 | p4/p4-r5 | partial | `[-\frac{\pi}{2}, 0]` | `[-\frac{\pi}{2}` | 0.59 |  |
| 4 | p4/p4-r18 | differs | `A_1B \parallel; AC_1D` | `A_{1}B\not\cup.` | 0.57 |  |
| 5 | p5/p5-r8 | differs | `f(x)=\mathrm{e}^{x}-ax+\frac{1}{2}x^{2}` | `a>-1.` | 0.70 |  |
| 6 | p5/p5-r9 | differs | `y=f(x); (0,f(0))` | `y=\operatorname{ 在 }(x)` | 0.67 |  |
| 7 | p5/p5-r9 | differs | `y=f(x); (0,f(0))` | `a=0` | 0.75 |  |
| 8 | p5/p5-r13 | normalized | `1 \leqslant a_1 < a_2 < \cdots < a_{100} \leqslant 205` | `1\leqslant a_{1}<a_{2}<\cdots<a_{100}\leqslant205` | 0.87 |  |
| 9 | p5/p5-r13 | differs | `A = \{a_1, a_2, \cdots, a_{100}\}; 1 \leqslant a_1 < a_2 < \cdots < a_{100} \leqslant 205` | `A=\left\{a_{1},a_{2},\cdots,a_{100}\right\}` | 0.87 |  |
| 10 | p5/p5-r14 | exact | `i+j\in A` | `i+j\in A` | 0.78 |  |
| 11 | p8/p8-r3 | partial | `\theta` | `\left\|\cos\theta\right\|=\left\|\frac{\boldsymbol{n}_{1}\cdot\boldsymbol{n}_{2}}{\left\|\boldsymbol{n}_{1}\right\|\cdot\left\|\boldsymbol{n}_{2}\right\|}\right\|=\frac{\sqrt{15}}{5}` | 0.94 |  |
| 12 | p9/p9-r1 | partial | `\frac{\sqrt{15}}{5}` | `1\frac{\sqrt{15}}{5}` | 0.83 |  |
| 13 | p9/p9-r2 | normalized | `A_1B_1` | `A_{1}B_{1}` | 0.75 |  |
| 14 | p11/p11-r4 | differs | `A \cap \{201, 202, \cdots, 205\}; m (1 \leq m \leq 5); a_{100-m} = b` | `\mathcal{A}\bigcap\{201,202,\cdots,205\}` | 0.60 |  |
| 15 | p11/p11-r4 | differs | `A \cap \{201, 202, \cdots, 205\}; m (1 \leq m \leq 5); a_{100-m} = b` | `a_{_{100-m}}=b` | 0.87 |  |
| 16 | p12/p12-r2 | normalized | `a_{100-m} = b \leqslant 100` | `a_{100-m}=b\leqslant100` | 0.87 |  |
