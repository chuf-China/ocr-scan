# 公式内容级 A/B 复核表

- layout：`C:\Users\Administrator\Desktop\ocr\ocr scan\ocr_json_app\out\eval\eval03_shenzhen_2025yimo.formula.json`
- 公式证据：`eval03_shenzhen_2025yimo.formula.json`
- 总证据 296 条，抽样 16 条（配额 differs/partial/normalized/exact）

判定口径：对照裁片，比较「现有 OCR 片段（current）」与「模型 LaTeX（model）」，
逐条填 h = 模型更好 / o = 现有更好 / b = 相同（都好或都错）。

| # | 页/区域 | 一致性 | current（现有 OCR） | model（公式模型） | score | 判定 |
|---|---|---|---|---|---|---|
| 1 | p2/p2-r13 | normalized | `B = \frac{\pi}{12}` | `B=\frac{\pi}{12}` | 0.73 |  |
| 2 | p3/p3-r17 | differs | `EM, BC \subset; B_1BCC_1; EM \cap BC = M; AM \perp` | `B_{1}BC_{1}` | 0.59 |  |
| 3 | p5/p5-r8 | partial | `A_1B` | `\Delta A_{1}BN\sim\Delta BB_{1}N` | 0.74 |  |
| 4 | p7/p7-r14 | differs | `f(p) = -8p^{3} + 12p^{2} - 2; 0 < p < 1; f'(p) = -24p^{2} + 24p = -24p(p-1) > 0` | `0<\bar{p}\le1` | 0.62 |  |
| 5 | p7/p7-r23 | partial | `l_1, l_2` | `\mid l_{1},l_{2}` | 0.73 |  |
| 6 | p8/p8-r7 | normalized | `\begin{cases} x_1 + x_2 = 4 + \dfrac{2}{k^2} \\ x_1 x_2 = 4 \end{cases}` | `\left\{\begin{aligned}x_{1}+x_{2}=4+\frac{2}{k^{2}}\\ x_{1}x_{2}=4\end{aligned}\right.` | 0.88 |  |
| 7 | p8/p8-r7 | differs | `\begin{cases} y = kx - 2k \\ y^2 = 2x \end{cases}; k^2x^2 - (4k^2 + 2)x + 4k^2 = 0; \begin{cases} x_1 + x_2 = 4 + \dfrac{2}{k^2} \\ x_1 x_2 = 4 \end{cases}` | `y=kx-2k\atop y^{2}\equiv2x` | 0.83 |  |
| 8 | p9/p9-r1 | exact | `M(-2,0)` | `M(-2,0)` | 0.50 |  |
| 9 | p9/p9-r13 | partial | `f(z) = \sqrt{12}+z-\sqrt{-4}+z` | `n=\frac{\left(\sqrt{12+z}-\sqrt{-4+z}\right)^{2}}{8},\quad f(z)=\sqrt{12+z}-\sqrt{-4+z},\quad z>8` | 0.62 |  |
| 10 | p10/p10-r3 | differs | `f(x_1)=\frac{x_1^2+6x_1+4}{x_1^2-2x_1+4}=\frac{8x_1}{x_1^2-2x_1+4}+1=\frac{8}{x_1+\frac{4}{x_1}-2}+1>1, x_1>0` | `f\left(x_{1}\right)=\frac{x_{1}^{2}+6x_{1}\pm4}{x_{1}^{2}-2x_{1}+4}=\frac{8x_{1}}{x_{1}^{2}-2x_{1}+4}+1=\frac{8}{x_{1}+\frac{4}{x_{1}}-2}+1>1,x_{1}>0` | 0.89 |  |
| 11 | p10/p10-r13 | differs | `a_1 \ne a_2; \{a_n\}; a_n < 2025; a_1, a_2` | `\left(\bar{a_{1}}\neq\bar{a_{2}}\right)` | 0.74 |  |
| 12 | p10/p10-r18 | differs | `n=1; 1=a_1=\|a_2-a_3\|; 1=\|a_2-2\|; a_2=1` | `,1=\lfloor a_{2}-2\rfloor` | 0.85 |  |
| 13 | p11/p11-r5 | normalized | `x, y, x + y, x, y` | `x,y,x+y,x,y` | 0.69 |  |
| 14 | p11/p11-r10 | differs | `\{a_n\}; \cdots; n\geq3; a_{n+2}=a_{n+1}+a_n` | `\left\{a_{n}\right\}` | 0.53 |  |
| 15 | p12/p12-r2 | differs | `p_1 < p_2 < \cdots < p_k; n \geq p_k + 1; **; a_{n+2} = a_{n+1} + a_n \geq a_{n+1} + 1` | `\mid n\geq p_{k}` | 0.74 |  |
| 16 | p12/p12-r5 | partial | `i_m = p` | `i_{m}=p,\quad i_{m+1}=p+j,\quad j\geq2` | 0.60 |  |
