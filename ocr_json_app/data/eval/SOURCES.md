# OCR 验收用「原生电子卷」数学试卷来源清单

采集时间：2026-10-25
采集范围：公开可下载（无需登录/付费/网盘跳转）的中文高中数学试卷 PDF，要求 **10–15 页**、**原生文字层（非扫描件）**、含数学公式。
验证环境：主环境 `python` 3.12.10 + `pdfplumber` 0.11.10，`page.extract_text()` 逐页取字符数。

## 汇总

| # | 文件 | 页数 | 前3页 text 字符数 | 全页最少字符 | 中位字符 | 判定 |
|---|------|------|-------------------|--------------|----------|------|
| 1 | `ocr_json_app/data/eval/eval01_xicheng_2024ermo.pdf` | 10 | 724 / 826 / 587 | 450 | 826 | ✅ 达标 |
| 2 | `ocr_json_app/data/eval/eval02_haidian_2024yimo.pdf` | 12 | 1171 / 1270 / 947 | 596 | 749 | ✅ 达标 |
| 3 | `ocr_json_app/data/eval/eval03_shenzhen_2025yimo.pdf` | 12 | 765 / 1343 / 894 | 765 | 1034 | ✅ 达标 |
| 4 | `ocr_json_app/data/eval/eval04_xicheng_2021qimo.pdf` | 12 | 618 / 704 / 892 | 618 | 750 | ✅ 达标 |
| 5 | `ocr_json_app/data/eval/eval05_xicheng_2020qimo.pdf` | 12 | 677 / 532 / 686 | 532 | 750 | ✅ 达标 |

5 个文件均：页数 10–15、每页字符数 ≥450（全部远高于 300 阈值）、零空页（`zero_pages = 0`）、含大量中文与数学算式。
绝对路径前缀：`C:\Users\Administrator\Desktop\ocr\ocr scan\`。

---

## 1. eval01_xicheng_2024ermo.pdf

- **路径**：`C:\Users\Administrator\Desktop\ocr\ocr scan\ocr_json_app\data\eval\eval01_xicheng_2024ermo.pdf`
- **内容**：2024 北京西城高三二模 数学（教师版，含试题 + 答案）
- **页数**：10
- **每页字符数**：`[724, 826, 587, 767, 450, 698, 865, 876, 860, 978]`，min=450，median=826，空页 0
- **来源 URL**：
  `https://cdn.zgkao.com/17150472063752024%E5%8C%97%E4%BA%AC%E8%A5%BF%E5%9F%8E%E9%AB%98%E4%B8%89%E4%BA%8C%E6%A8%A1%E6%95%B0%E5%AD%A6%EF%BC%88%E6%95%99%E5%B8%88%E7%89%88%EF%BC%89.pdf`
  （北京中考在线 cdn.zgkao.com 静态 CDN，直链无鉴权，HTTP 200 直接下载）
- **原生电子卷证据**：
  - 第 1 页文字层原文：`2024 北京西城高三二模 数 学 2024.5 本试卷共 6 页， 150 分。考试时长 120 分钟。…第一部分（选择题 共 40 分）一、选择题共10小题，每小题4分…`
  - 第 2 页含公式级文本：`AB=6cm ， EF =3cm`、`已知{a }是无穷等比数列，其前n项和为S`、`（A）(−3,1) （B）[−2,1)`——排版数学符号可被逐字抽取，证明是排版生成的文字而非图片。
  - 全文 `=` 符号 159 处、括号 287 处，汉字 2054 字。
  - 10 页全部有文字，无空白页/整页图片页。

## 2. eval02_haidian_2024yimo.pdf

- **路径**：`C:\Users\Administrator\Desktop\ocr\ocr scan\ocr_json_app\data\eval\eval02_haidian_2024yimo.pdf`
- **内容**：2024 北京海淀高三一模 数学（教师版，含试题 + 答案）
- **页数**：12
- **每页字符数**：`[1171, 1270, 947, 710, 605, 670, 682, 605, 749, 947, 596, 873]`，min=596，median=749，空页 0
- **来源 URL**：
  `https://cdn.zgkao.com/17126554976532024%E5%8C%97%E4%BA%AC%E6%B5%B7%E6%B7%80%E9%AB%98%E4%B8%89%E4%B8%80%E6%A8%A1%E6%95%B0%E5%AD%A6%EF%BC%88%E6%95%99%E5%B8%88%E7%89%88%EF%BC%89%20(1).pdf`
- **原生电子卷证据**：
  - 第 1 页：`2024 北京海淀高三一模 数 学 本试卷共9页，150分。考试时长120分钟。…`
  - 第 2 页含分段公式文本：`f ( 3 ) = 0 . 设 f ( x ) 是 f ( x ) 的导函数，则关于 x 的不等式 f ( x + 1 ) f ( x ) 0 的解集是`、`A. [ 0 , 2 ]  B. [ − 3 , 0 ] [ 3 , 4 )`——逐字符可抽，是矢量排版文字。
  - 全文 `=` 58 处、括号 219 处，汉字 2257 字。

## 3. eval03_shenzhen_2025yimo.pdf

- **路径**：`C:\Users\Administrator\Desktop\ocr\ocr scan\ocr_json_app\data\eval\eval03_shenzhen_2025yimo.pdf`
- **内容**：2025 年深圳市高三年级第一次调研考试 数学试题参考答案及评分标准（逐题解析卷，含题目原文与解答）
- **页数**：12
- **每页字符数**：`[765, 1343, 894, 814, 917, 1002, 1057, 1637, 1242, 1034, 1279, 875]`，min=765，median=1034，空页 0（本批中文字密度最高）
- **来源 URL**（官方，深圳市教育科学研究院）：
  `http://www.szjky.edu.cn/attachment/0/88/88011/1529937.pdf`
  所在文章页：`http://www.szjky.edu.cn/jydt/content/post_1529937.html`
- **原生电子卷证据**：
  - 第 1 页：`2025 年深圳市高三年级第一次调研考试 数学试题参考答案及评分标准 一、选择题：本题共8小题，每小题5分…题号 1 2 3 4 5 6 7 8 答案 C A B C B C B D`
  - 第 2 页含完整解答：`15．（13分）在△ABC中，角A,B,C 所对的边分别为a,b,c，c2 a2 b2 ab，cos2BsinC．（1）求B；…由余弦定理推论 cosC 及 c2 a2 b2 ab 得 ………1分`
  - 带评分点分步给分（`………………………1分`），是教研院用排版工具生成的官方 PDF（页眉有「深圳市教育科学研究院」水印字），绝非扫描。
- **备注**：本文件是「参考答案及评分标准」而非空白试卷，但正文完整复述了每题题干并给出带公式的解答，数学内容密度最高，适合作为 OCR 验收目标。

## 4. eval04_xicheng_2021qimo.pdf

- **路径**：`C:\Users\Administrator\Desktop\ocr\ocr scan\ocr_json_app\data\eval\eval04_xicheng_2021qimo.pdf`
- **内容**：北京市西城区 2020—2021 学年度第一学期期末试卷 高三数学（试题 + 参考答案，共 12 页）
- **页数**：12
- **每页字符数**：`[618, 704, 892, 749, 631, 802, 837, 750, 694, 683, 977, 1100]`，min=618，median=750，空页 0
- **来源 URL**：
  `https://download.s21i.co99.net/17094505/0/0/ABUIABA9GAAghpfEgAYo-Mu84QE.pdf?f=%E8%A5%BF%E5%9F%8E%E5%8C%BA2021%E5%B1%8A%E9%AB%98%E4%B8%89%E7%AC%AC%E4%B8%80%E5%AD%A6%E6%9C%9F%E6%9C%9F%E6%9C%AB%E8%80%83%E8%AF%95%E6%95%B0%E5%AD%A6%E8%AF%95%E9%A2%98%E5%8F%8A%E7%AD%94%E6%A1%88.pdf&v=1611729798`
  （原文件名：`西城区2021届高三第一学期期末考试数学试题及答案.pdf`，凡科建站静态资源直链）
- **原生电子卷证据**：
  - 第 1 页：`北京市西城区 2020—2021 学年度第一学期期末试卷 高三数学 2021.1 本试卷共5页，共150分。…`
  - 第 2 页含公式：`（7）已知函数 f(x)sin2x,x[a,b]，则“ba≥ ”是“ f(x)的值域为[1,1]”的`、`香农提出了一个著名的公式：C Wlog (1 )`——上标/下标/特殊符号均以文字形式存在。
  - 第 6 页起为「高三数学参考答案」，同一文件内试题与答案连续排版（12 页 = 6 页试题 + 6 页答案），汉字 2583 字、括号 313 处。

## 5. eval05_xicheng_2020qimo.pdf

- **路径**：`C:\Users\Administrator\Desktop\ocr\ocr scan\ocr_json_app\data\eval\eval05_xicheng_2020qimo.pdf`
- **内容**：北京市西城区 2019—2020 学年度第一学期期末试卷 高三数学（试题 + 参考答案，共 12 页）
- **页数**：12
- **每页字符数**：`[677, 532, 686, 863, 654, 710, 796, 833, 750, 914, 875, 658]`，min=532，median=750，空页 0
- **来源 URL**：
  `https://cdn.gaokzx.com/zixunzhan/202403/b24ee403-caf6-4d15-94be-064100d5b764.pdf`
  （北京高考在线 cdn.gaokzx.com CDN 直链）
- **原生电子卷证据**：
  - 第 1 页：`北京市西城区 — 学年度第一学期期末试卷 2019 2020 高三数学 2020.1 本试卷共 页 共 分 考试时长 分钟…`（年份/数字被拆成独立文本块，是排版 PDF 的典型特征）
  - 第 2 页含向量/几何题：`.设三个向量a b c互不共线 则 a b c 0 是 以 a b c 为边长的三角形存在 的`、`其中 石瓢壶的壶体可以近似看成一个圆台…(A)100c`
  - 汉字 2646 字、`=` 28 处、括号 244 处；12 页均有文字。

---

## 复现 / 校验命令

```bash
export PYTHONIOENCODING=utf-8
python -c "import glob,pdfplumber,statistics;[print(f, len(pdfplumber.open(f).pages)) for f in glob.glob('ocr_json_app/data/eval/*.pdf')]"
```

单文件快速校验（页数 + 每页字符数）：

```bash
python -c "
import pdfplumber,sys
p=sys.argv[1]
with pdfplumber.open(p) as d:
    ch=[len(pg.extract_text() or '') for pg in d.pages]
print(p,'pages=',len(ch),'min=',min(ch),'median=',sorted(ch)[len(ch)//2])
" ocr_json_app/data/eval/eval01_xicheng_2024ermo.pdf
```

---

## 失败记录（尝试过但不合格）

### A. 仓库自带的旧 PDF（页数不符，未采用）

| 文件 | 页数 | 原因 |
|------|------|------|
| `ocr_json_app/data/2024新高考I卷数学.pdf` | 4 | 页数 < 10（文字层正常） |
| `ocr_json_app/data/静安区2026届高三一模数学试卷.pdf` | 6 | 页数 < 10 |
| `ocr_json_app/data/exams/2023全国乙理.pdf` 等 7 个 | 各 2 | 全部是 2 页 A3 合订样式，明确排除 |

### B. 下载后不合格的来源

| 来源 | 页数 / 情况 | 原因 |
|------|-------------|------|
| `https://download.s21i.co99.net/28274090/.../ABUIABA9GAAgsIPVsAYoiPT5xwc.pdf`（2024 东城高三一模数学试卷版） | 6 页，每页 0 字符 | 扫描件，无文字层 |
| `https://28274090.s21i.faiusr.com/61/1/ABUIABA9GAAgsf3TsAYonqKupAM.pdf`（海淀 2023-2024 第二学期期中 高三数学） | 5 页，min=163 | 页数不足 + 字符偏少 |
| `http://file.xdf.cn/uploads/201017/1458_2010170119485EgfGeYBbIg4wtPX.pdf`（六校 10 月联考高三数学） | 13 页，13 页全 0 字符 | 扫描件 |
| `https://cdn.zizzs.com/zixunzhan/202310/808928f6-….pdf` | 6 页全 0 | 扫描件 |
| `https://cdn.zizzs.com/zixunzhan/202310/accb694c-….pdf`（2023 深圳市第二次调研 数学参考答案） | 9 页全 0 | 扫描件 |
| `https://cdn.zgkao.com/zixunzhan/1740140047314…2025深圳一模数学试题及答案.pdf` | 18 页全 0 | 页数超 15 + 扫描件 |
| `https://cdn.gaokzx.com/1717396085708…东直门中学高三三模数学试题及答案.pdf` | 22 页，含 0 字符页 | 页数超 15 + 部分页无文字层 |
| `https://cdn.zizzs.com/zixunzhan/202402/611eafec-….pdf`（永州 2024 高三二模 数学参考答案） | 6 页（min=1078） | 页数 < 10 |
| `https://cdn.zizzs.com/1720059493299…深圳高二下期末数学答案.pdf` | 7 页（min=327） | 页数 < 10 |
| `https://17094505.s21i.faiusr.com/61/ABUIABA9GAAg3tScsQYoitivjQc.pdf`（2024 朝阳高三一模数学） | 13 页，末页 123 字符 | 最后 1 页为解答续页仅 123 字符，不满足「每页 ≥300」（其余 12 页 510–1337 字符，文字层完好）→ 列为备选 |
| `https://17094505.s21i.faiusr.com/61/ABUIABA9GAAgz9easAYolPqglwI.pdf`（2024 石景山高三一模数学） | 13 页，末页 123 字符 | 同上，备选 |
| `https://cdn.zizzs.com/zixunzhan/202310/774e0285-….pdf`（济南 2023.3 高三模拟） | 4 页全 0 | 页数不足 + 扫描件 |
| `https://cdn.zizzs.com/1734058762624…北京二中2025届高三12月月考.pdf` | 21 页（min=520） | 页数超 15 |
| `http://www.szjky.edu.cn/attachment/0/98/98968/1659407.pdf`（2026 深圳一模数学参考答案） | 8 页（min=318） | 页数 < 10 |
| `https://cdn.zizzs.com/1733968344427…上海静安区2025届高三一模数学试卷及答案.pdf` | 8 页全 0 | 页数不足 + 扫描件 |
| `https://cdn.zgkao.com/1730973267714…2024北京朝阳高三（上）期中数学 有答案.pdf` | 12 页全 0 | 扫描件 |
| `http://imgs.app.gaokaozhitongche.com/resource/pdf/paper/202401/20240129070148_mIxytU.pdf` | 14 页全 0 | 扫描件 |
| `https://cdn.zizzs.com/1726131735735…2025届深圳高三9月联考.pdf` | 13 页全 0 | 扫描件 |
| `https://cdn.zizzs.com/zixunzhan/202310/edf7466c-….pdf`（河西区 2019 高三数学理） | 8 页全 0 | 扫描件 |

### C. 不可用渠道（需登录 / 付费 / 无直链）

| 渠道 | 原因 |
|------|------|
| Scribd | 全部为需登录阅读的转存文档，非公开直链 |
| 百度文库 / 腾讯文库 / 道客 / 豆丁 / 五三文库 | 需登录或付费购买 |
| 学科网 zxxk.com、51jiaoxi、教习网、7cxk、book118 | 需注册登录 / 会员付费 |
| 百度网盘、夸克网盘（如 cc518.com、jiaoyuwu.com 的下载按钮） | 网盘跳转，需客户端/登录，按要求排除 |
| mathup.cn 全国高中数学联赛真题集 | 教师版需付费 ¥9.9 或 VIP |
| GitHub `deekur/gaokaomath` | 777 个 PDF 均为 2–4 页历年真题扫描/无答案版，页数不符 |
| `www.neea.edu.cn`（教育部教育考试院） | 试题以 Markdown 文本内嵌，无 PDF 附件 |
| `jhgk.cn` / `wh111.com` / `zgkao.com` 旧链接 | 汇总页为 JS 渲染无直链；旧 CDN 链接（1718259982331 等）实测 404 已清除 |
| `cdn.zizzs.com`/`cdn.gaokzx.com` 2024 赛季无 `zixunzhan` 前缀的路径 | 全部 404（CDN 路径策略变更） |
| DuckDuckGo / Bing / Mojeek / Sogou / search.ch | 已限流或返回 0 个 PDF 直链，无法作为发现渠道 |
| `www.zgkao.com`（北京中考在线，初中向） | 枚举 92000–93437 号文章仅 8 篇数学，全部初中卷且多为扫描件 |

### D. 隐私合规说明

以上全部文件均为公开的**试卷/答案文本**，不含学生姓名、准考证号、成绩等个人信息；未从任何需登录的个人中心或成绩系统下载。文件名以「考试名称 + 年份」命名，不涉及个人隐私。
