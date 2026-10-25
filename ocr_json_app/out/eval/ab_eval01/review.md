# 公式内容级 A/B 复核表

- layout：`C:\Users\Administrator\Desktop\ocr\ocr scan\ocr_json_app\out\eval\eval01.formula.json`
- 公式证据：`eval01.formula.json`
- 总证据 249 条，抽样 48 条（配额 differs/partial/normalized/exact）

判定口径：对照裁片，比较「现有 OCR 片段（current）」与「模型 LaTeX（model）」，
逐条填 h = 模型更好 / o = 现有更好 / b = 相同（都好或都错）。

| # | 页/区域 | 一致性 | current（现有 OCR） | model（公式模型） | score | 判定 |
|---|---|---|---|---|---|---|
| 1 | p1/p1-r12 | differs | `a + b = 0; a \cdot b = 0` | `\boldsymbol{a}+\boldsymbol{b}=\boldsymbol{0}` | 0.83 |  |
| 2 | p1/p1-r12 | differs | `a + b = 0; a \cdot b = 0` | `\pmb{a}\cdot\pmb{b}=0` | 0.81 |  |
| 3 | p1/p1-r17 | exact | `a_{1}+a_{2}+a_{3}+a_{4}=` | `a_{1}+a_{2}+a_{3}+a_{4}=` | 0.89 |  |
| 4 | p1/p1-r20 | partial | `a \in \mathbb{R}` | `\mid a\in\mathbf{R},b\in\mathbf{R}` | 0.76 |  |
| 5 | p1/p1-r28 | normalized | `-1 - \tan x` | `-1-\tan x` | 0.70 |  |
| 6 | p2/p2-r11 | normalized | `(-3, \frac{3}{2})` | `(-3,\frac{3}{2})` | 0.64 |  |
| 7 | p2/p2-r19 | differs | `(-1,0); (3,0)` | `;y=2` | 0.68 |  |
| 8 | p2/p2-r22 | differs | `\|AB\|=\frac{\pi}{4}; f(x); \left(\frac{5\pi}{12},\frac{11\pi}{12}\right)` | `\mid A B\mid={\frac{\pi}{4}}` | 0.86 |  |
| 9 | p2/p2-r24 | differs | `f(x)=\begin{cases} x^2+2x, & x<-2 \text{ 或 } x>1, \\ \sqrt{12-3x^2}, & -2 \leq x \leq 1, \end{cases}; g(x)=f(x)-a; a \in \mathbb{R}` | `x>1,` | 0.58 |  |
| 10 | p3/p3-r2 | partial | `n \geqslant N` | `\mid n\geqslant N` | 0.69 |  |
| 11 | p3/p3-r3 | differs | `N; n \geqslant N; a_n > a_{n-1}` | `a_{_n}>a_{_{n-1}}\text{;}` | 0.84 |  |
| 12 | p3/p3-r10 | partial | `\triangle ABC` | `\therefore\triangle ABC` | 0.61 |  |
| 13 | p3/p3-r15 | normalized | `EM \perp AD` | `EM\perp AD` | 0.65 |  |
| 14 | p3/p3-r16 | differs | `EM \parallel; CDD_{1}C_{1}` | `\left[CD_{1}C_{1}\right.` | 0.81 |  |
| 15 | p4/p4-r12 | normalized | `f(x)=4a\sin x+(a^2+1)x^2` | `f(x)=4a\sin x+(a^{2}+1)x^{2}` | 0.91 |  |
| 16 | p4/p4-r13 | differs | `f(x)` | `\left(x=0\right.` | 0.72 |  |
| 17 | p4/p4-r14 | partial | `f(x)` | `\cdot f(x)` | 0.62 |  |
| 18 | p4/p4-r17 | normalized | `E: \frac{x^{2}}{a^{2}} + \frac{y^{2}}{b^{2}} = 1 (a > b > 0)` | `E:\frac{x^{2}}{a^{2}}+\frac{y^{2}}{b^{2}}=1(a>b>0)` | 0.95 |  |
| 19 | p5/p5-r2 | differs | `A:a_1,a_2,\cdots,a_n; A; i_1; i_2` | `i_{\textit{k}}` | 0.63 |  |
| 20 | p5/p5-r4 | differs | `k \geqslant 2; B; s \in \{1,2,\cdots,k-1\}; i_{s+1} - i_s = 1` | `i_{_{s+1}}-i_{_{s}}=1` | 0.75 |  |
| 21 | p6/p6-r11 | exact | `f(x)=\sqrt{3}\sin x+2\cos^{2}\frac{x}{2}` | `f(x)=\sqrt{3}\sin x+2\cos^{2}\frac{x}{2}` | 0.86 |  |
| 22 | p6/p6-r18 | normalized | `A+B=\frac{2\pi}{3}` | `A+B=\frac{2\pi}{3}.` | 0.79 |  |
| 23 | p7/p7-r2 | differs | `EM \perp AD` | `E M\bot A D` | 0.59 |  |
| 24 | p7/p7-r4 | partial | `CDD_1C_1` | `\left[CDD_{1}C_{1}\right.` | 0.66 |  |
| 25 | p7/p7-r12 | partial | `EM \subset` | `EM\subset 平` | 0.58 |  |
| 26 | p7/p7-r12 | partial | `CDD_1C_1` | `\dot{\iota}C D D_{1}C_{1}` | 0.62 |  |
| 27 | p7/p7-r20 | differs | `\boldsymbol{m} = (x, y, z); \begin{cases} \boldsymbol{m} \cdot \overrightarrow{DC} = 0, \\ \boldsymbol{m} \cdot \overrightarrow{DM} = 0, \end{cases}; \begin{cases} y = 0, \\ x + y + z = 0. \end{cases}` | `\begin{array}{r}{\mathcal{1}\left\{\begin{aligned}{}&{{}y=0,}\\ {}&{{}x+y+z=0.}\end{aligned}\right.}\end{array}` | 0.73 |  |
| 28 | p7/p7-r21 | partial | `m=(1,0,-1)` | `\boldsymbol{m}=(1,0,-1)` | 0.82 |  |
| 29 | p7/p7-r21 | differs | `m=(1,0,-1)` | `\left[\begin{array}{l}{z=-1}\end{array}\right.` | 0.69 |  |
| 30 | p7/p7-r21 | differs | `m=(1,0,-1)` | `\cdot x=1` | 0.65 |  |
| 31 | p8/p8-r1 | exact | `b=15.4` | `b=15.4` | 0.54 |  |
| 32 | p8/p8-r11 | differs | `f'(x) = 4a\cos x + 2(a^2 + 1)x` | `f^{\prime}(x)=4a\cos x+2(a^{2}+1)x` | 0.86 |  |
| 33 | p8/p8-r13 | normalized | `f(x)=x^{2}` | `f(x)=x^{2}\;.` | 0.67 |  |
| 34 | p8/p8-r13 | differs | `f(x)=x^{2}` | `\mathrm{i}a=0` | 0.56 |  |
| 35 | p8/p8-r17 | differs | `f'(x) = 4\cos x + 4x = 4(\cos x + x)` | `f^{\prime}(x)=4\cos x+4x=4(\cos x+x)` | 0.86 |  |
| 36 | p8/p8-r21 | partial | `f(x)` | `,f(x)` | 0.53 |  |
| 37 | p8/p8-r22 | differs | `f'(x)=4a\cos x+2(a^{2}+1)x` | `f^{\prime}(x)=4a\cos x+2(a^{2}+1)x` | 0.89 |  |
| 38 | p8/p8-r26 | differs | `g(x)=f'(x)` | `\mid a>0` | 0.57 |  |
| 39 | p8/p8-r29 | differs | `g'(x) \geqslant 0; g(x); (-\infty, +\infty)` | `g^{\prime}(x)\geqslant0` | 0.76 |  |
| 40 | p8/p8-r29 | normalized | `(-\infty, +\infty)` | `(-\infty,+\infty)` | 0.62 |  |
| 41 | p9/p9-r7 | partial | `a^{2}=4` | `a^{2}=4,b^{2}=2` | 0.86 |  |
| 42 | p9/p9-r9 | partial | `P(m,n)` | `\mid P(m,n)` | 0.58 |  |
| 43 | p10/p10-r1 | exact | `2^{4}-1=15` | `2^{4}-1=15` | 0.72 |  |
| 44 | p10/p10-r7 | differs | `T(B) + T(B') = \sum a_{i_j} + \sum (n+1 - a_{i_j}) = k(n+1)` | `T(B)+T(B')=\sum_{j=1}^{k}a_{i_{j}}+\sum_{j=1}^{k}(n+1-a_{i_{j}})=k(n+1)` | 0.94 |  |
| 45 | p10/p10-r12 | differs | `j=1,2,\cdots,m; y_j < x_{k_0}; B_j; B_{k_0}` | `\left[y_{_{j}}<x_{_{k_{0}}}\right.` | 0.83 |  |
| 46 | p10/p10-r13 | differs | `j=1,2,\cdots,m; y_j \geqslant x_{k_0}` | `\cdot y_{j}` | 0.51 |  |
| 47 | p10/p10-r18 | partial | `x_{k_0}(n+1-x_{k_0}) \geqslant m` | `x_{k_0}(n+1-x_{k_0})` | 0.90 |  |
| 48 | p10/p10-r19 | differs | `n; x_k\in\{1,2,\cdots,\frac{n+1}{2}\}; y_k\in\{\frac{n+1}{2},\frac{n+3}{2},\cdots,n\}` | `y_{k}\in\left\{\frac{n+1}{2},\frac{n+3}{2},\cdots,n\right\}` | 0.58 |  |
