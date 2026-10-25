# 公式内容级 A/B 复核表

- layout：`C:\Users\Administrator\Desktop\ocr\ocr scan\ocr_json_app\out\eval\eval04_xicheng_2021qimo.formula.json`
- 公式证据：`eval04_xicheng_2021qimo.formula.json`
- 总证据 281 条，抽样 16 条（配额 differs/partial/normalized/exact）

判定口径：对照裁片，比较「现有 OCR 片段（current）」与「模型 LaTeX（model）」，
逐条填 h = 模型更好 / o = 现有更好 / b = 相同（都好或都错）。

| # | 页/区域 | 一致性 | current（现有 OCR） | model（公式模型） | score | 判定 |
|---|---|---|---|---|---|---|
| 1 | p2/p2-r1 | partial | `f(x)` | `\text{"}f(x)` | 0.55 |  |
| 2 | p4/p4-r4 | partial | `CC_1` | `\cdot C C_{1}` | 0.65 |  |
| 3 | p6/p6-r26 | differs | `AB_1 \perp BE` | `,4a-4=0` | 0.81 |  |
| 4 | p6/p6-r26 | differs | `AB_1 \perp BE` | `\mid a=1` | 0.75 |  |
| 5 | p7/p7-r14 | differs | `c = 4\sqrt{3}` | `,c=4{\sqrt{3}}.` | 0.78 |  |
| 6 | p7/p7-r22 | differs | `\triangle ABC; A=C; a=c` | `,a=c` | 0.71 |  |
| 7 | p8/p8-r2 | differs | `a = c = 3\sqrt{2}` | `a=c=3{\sqrt{2}}` | 0.81 |  |
| 8 | p9/p9-r6 | partial | `f(1)=0` | `:f(1)=0` | 0.56 |  |
| 9 | p9/p9-r8 | normalized | `f'(x)=0` | `f^{\prime}(x)=0` | 0.67 |  |
| 10 | p10/p10-r1 | normalized | `g'(x) > 0` | `g^{\prime}(x)>0` | 0.51 |  |
| 11 | p10/p10-r16 | normalized | `a^2 = b^2 + c^2` | `a^{2}=b^{2}+c^{2}` | 0.81 |  |
| 12 | p11/p11-r1 | differs | `A,B; \Delta>0; k^2>\frac{1}{2}` | `\Delta{\geq}0` | 0.51 |  |
| 13 | p11/p11-r18 | exact | `a_{2}=\frac{1}{2}` | `a_{2}=\frac{1}{2}` | 0.53 |  |
| 14 | p11/p11-r20 | differs | `n=1; S_{1}^{*}=a_{2}-a_{1}; \|a_{2}-a_{1}\|=\|S_{1}^{*}\|=\|a_{1}^{*}\|=1` | `\verb\|n=1` | 0.71 |  |
| 15 | p12/p12-r6 | partial | `n \in \mathbb{N}^*` | `n\in\mathbf{N}^{*},\quad S_{n}^{*}=a_{n+1}-a_{1}` | 0.55 |  |
| 16 | p12/p12-r18 | differs | `a_{m+1} - a_m \in \{-1, 0, 1\}; k = 0` | `,k=0` | 0.71 |  |
