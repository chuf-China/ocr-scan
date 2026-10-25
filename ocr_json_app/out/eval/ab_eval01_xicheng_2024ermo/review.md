# 公式内容级 A/B 复核表

- layout：`C:\Users\Administrator\Desktop\ocr\ocr scan\ocr_json_app\out\eval\eval01_xicheng_2024ermo.formula.json`
- 公式证据：`eval01_xicheng_2024ermo.formula.json`
- 总证据 249 条，抽样 16 条（配额 differs/partial/normalized/exact）

判定口径：对照裁片，比较「现有 OCR 片段（current）」与「模型 LaTeX（model）」，
逐条填 h = 模型更好 / o = 现有更好 / b = 相同（都好或都错）。

| # | 页/区域 | 一致性 | current（现有 OCR） | model（公式模型） | score | 判定 |
|---|---|---|---|---|---|---|
| 1 | p1/p1-r17 | exact | `a_{1}+a_{2}+a_{3}+a_{4}=` | `a_{1}+a_{2}+a_{3}+a_{4}=` | 0.89 |  |
| 2 | p1/p1-r20 | partial | `a \in \mathbb{R}` | `\mid a\in\mathbf{R},b\in\mathbf{R}` | 0.76 |  |
| 3 | p1/p1-r28 | normalized | `-1 - \tan x` | `-1-\tan x` | 0.70 |  |
| 4 | p2/p2-r24 | differs | `f(x)=\begin{cases} x^2+2x, & x<-2 \text{ 或 } x>1, \\ \sqrt{12-3x^2}, & -2 \leq x \leq 1, \end{cases}; g(x)=f(x)-a; a \in \mathbb{R}` | `x>1,` | 0.58 |  |
| 5 | p3/p3-r3 | differs | `N; n \geqslant N; a_n > a_{n-1}` | `a_{_n}>a_{_{n-1}}\text{;}` | 0.84 |  |
| 6 | p4/p4-r13 | differs | `f(x)` | `\left(x=0\right.` | 0.72 |  |
| 7 | p4/p4-r17 | normalized | `E: \frac{x^{2}}{a^{2}} + \frac{y^{2}}{b^{2}} = 1 (a > b > 0)` | `E:\frac{x^{2}}{a^{2}}+\frac{y^{2}}{b^{2}}=1(a>b>0)` | 0.95 |  |
| 8 | p7/p7-r2 | differs | `EM \perp AD` | `E M\bot A D` | 0.59 |  |
| 9 | p7/p7-r12 | partial | `EM \subset` | `EM\subset 平` | 0.58 |  |
| 10 | p7/p7-r12 | partial | `CDD_1C_1` | `\dot{\iota}C D D_{1}C_{1}` | 0.62 |  |
| 11 | p8/p8-r13 | normalized | `f(x)=x^{2}` | `f(x)=x^{2}\;.` | 0.67 |  |
| 12 | p8/p8-r17 | differs | `f'(x) = 4\cos x + 4x = 4(\cos x + x)` | `f^{\prime}(x)=4\cos x+4x=4(\cos x+x)` | 0.86 |  |
| 13 | p9/p9-r9 | partial | `P(m,n)` | `\mid P(m,n)` | 0.58 |  |
| 14 | p10/p10-r12 | differs | `j=1,2,\cdots,m; y_j < x_{k_0}; B_j; B_{k_0}` | `\left[y_{_{j}}<x_{_{k_{0}}}\right.` | 0.83 |  |
| 15 | p10/p10-r13 | differs | `j=1,2,\cdots,m; y_j \geqslant x_{k_0}` | `\cdot y_{j}` | 0.51 |  |
| 16 | p10/p10-r19 | differs | `n; x_k\in\{1,2,\cdots,\frac{n+1}{2}\}; y_k\in\{\frac{n+1}{2},\frac{n+3}{2},\cdots,n\}` | `y_{k}\in\left\{\frac{n+1}{2},\frac{n+3}{2},\cdots,n\right\}` | 0.58 |  |
