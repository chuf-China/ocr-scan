# 公式内容级 A/B 复核表

- layout：`C:\Users\Administrator\Desktop\ocr\ocr scan\ocr_json_app\out\eval\eval02_haidian_2024yimo.formula.json`
- 公式证据：`eval02_haidian_2024yimo.formula.json`
- 总证据 254 条，抽样 16 条（配额 differs/partial/normalized/exact）

判定口径：对照裁片，比较「现有 OCR 片段（current）」与「模型 LaTeX（model）」，
逐条填 h = 模型更好 / o = 现有更好 / b = 相同（都好或都错）。

| # | 页/区域 | 一致性 | current（现有 OCR） | model（公式模型） | score | 判定 |
|---|---|---|---|---|---|---|
| 1 | p1/p1-r20 | differs | `\alpha, \beta; l, m; m \subset \alpha; l \perp \alpha` | `\text{"}m//\beta\text{"}` | 0.62 |  |
| 2 | p1/p1-r23 | partial | `f(x)` | `f(x)=\left\{\begin{aligned}x^{3},\quad x\leq0\\ \lg(x+1),x>0\end{aligned}\right.` | 0.95 |  |
| 3 | p2/p2-r25 | partial | `\|AP\| \geq \frac{1}{2}` | `A P\left\|\geq{\frac{1}{2}}\right.` | 0.82 |  |
| 4 | p2/p2-r26 | differs | `M(x_1, y_1); y = f(x); N(x_2, y_2); y = -f(x)` | `\mid x_{1}+x_{2}\mid=1` | 0.84 |  |
| 5 | p3/p3-r7 | differs | `AD \parallel BC; AM \parallel; CDP` | `AD\mathbin{//}BC` | 0.54 |  |
| 6 | p4/p4-r8 | partial | `f(x)` | `\cdot f(x)` | 0.51 |  |
| 7 | p4/p4-r12 | normalized | `t \in \{0,1,\cdots,m-1\}` | `t\in\{0,1,\cdots,m-1\}` | 0.86 |  |
| 8 | p4/p4-r12 | differs | `k=0,1,\cdots,m-1; a_{km+i} \neq a_{km+j}; 1 \leq i < j \leq m; b_0=0` | `a_{_{km+i}}\ne a_{_{km+j}}(1\le i<j\le m)` | 0.87 |  |
| 9 | p6/p6-r4 | differs | `MN = \frac{1}{8}BC; MN \parallel BC` | `MN=\frac{1}{2}BC` | 0.78 |  |
| 10 | p6/p6-r18 | normalized | `EC = AD = 1` | `EC=AD=1` | 0.61 |  |
| 11 | p7/p7-r17 | normalized | `PA \perp AC` | `PA\perp AC.` | 0.57 |  |
| 12 | p11/p11-r9 | partial | `b_{3}\leq6` | `,b_{3}\leq6.` | 0.75 |  |
| 13 | p11/p11-r14 | differs | `b_{t+1}=\min\{n\|n>b_t,a_n>t\},\quad t=0,1,\cdots,2023` | `b_{t+1}=\min\left\{n\mid n>b_t,a_n>t\right\},\quad t=0,1,\cdots,2023` | 0.68 |  |
| 14 | p11/p11-r15 | differs | `b_{t+1} > b_t; t = 0,1,\cdots,2023` | `,b_{{}_{t+1}}>b_{{}_{t}}` | 0.61 |  |
| 15 | p12/p12-r6 | differs | `a_n \leq t + l; n; t + l` | `[t+l]` | 0.52 |  |
| 16 | p12/p12-r13 | exact | `C_{4}=1898` | `C_{4}=1898` | 0.59 |  |
