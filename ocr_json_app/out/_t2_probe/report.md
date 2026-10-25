# T2 公式专项 · 本机可行性取证报告

- 取证时间：2026-10-25 12:25–12:35 (+08:00)
- 取证员：t2-probe（共享任务 task-1）
- 写作用域：`ocr_json_app/out/_t2_probe/`（本目录）；**未改仓库源码、未 git、未 pip install、未下载模型、未启动 llama-server、未跑仓库 OCR**
- 标记约定：**[已验证]** = 本次有命令+输出证据；**[推测]** = 有依据但未实测；**[未实测]** = 明确没做

---

## 0. 一句话结论

**核心公式模型可以完全离线跑通：`PP-FormulaNet_plus-M` 与它所需的 3 个前置模型全部已在磁盘上，pipeline 构建阶段零网络连接（socket 级实测），且是 CPU 版飞桨、不占显存。**
唯一没有实测的是「单张裁片的推理延迟与输出质量」——那需要跑一次真实推理，受本次禁令约束留待 lead 授权。

| 判定项 | 结论 | 依据 |
|---|---|---|
| PP-FormulaNet_plus-M 权重在不在本机 | **在**，588.5 MiB | [已验证] 见 §2 |
| 能否完全离线载入 | **能**，构建期 0 次网络连接 | [已验证] socket 护栏实测，见 §3 |
| 需不需要下载权重 | **不需要**（0 字节新增下载） | [已验证] 4/4 模型命中缓存 |
| 会不会占显存 / 抢 8080 | **不会**：CPU 版飞桨，无 CUDA 编译 | [已验证] `compiled_with_cuda False` |
| GPU 版飞桨能不能用 | **不能**：本机无 WSL、无 docker；Windows 无 GPU wheel | [已验证] 见 §5 |
| UniMERNet 能不能用 | **不能**（无包、无权重、无 torch） | [已验证] 见 §4 |
| PP-StructureV3 能不能离线跑 | **不能**：缺 12 个模型 | [已验证] 见 §4 |
| 单裁片推理延迟 | **未实测**，[推测] 1–10 s/张（CPU） | 见 §7 |
| 50–100 条人工标注工作量 | **约 1–3 套卷就能凑够** | [已验证] 见 §6 |

---

## 1. formula-venv 里到底装了什么

[已验证] 环境本体：

```
$ cat formula-venv/pyvenv.cfg
home = C:\Users\Administrator\AppData\Local\Programs\Python\Python312
include-system-site-packages = false
version = 3.12.10
executable = C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe
command = ...python.exe -m venv C:\Users\Administrator\Desktop\ocr\formula-venv
```

**关键关系**：venv 基底解释器与仓库主 Python **是同一个** `C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe`（3.12.10），只是 `include-system-site-packages = false` 隔离了 site-packages。所以「主环境干净 + 子环境带重依赖」的隔离是靠 venv 做到的，不是靠双 Python 版本。

[已验证] 版本（在 venv 内 `import` 实测，非读 metadata）：

```
$ ./formula-venv/Scripts/python.exe -c "import paddle, paddleocr, paddlex, numpy, cv2, PIL"
paddle 3.2.1        paddleocr 3.7.0     paddlex 3.7.2
numpy 2.3.5         opencv 4.10.0       pillow 13.0.0
```

[已验证] 依赖清单要点（`Lib/site-packages` 目录名）：

- **图像/OCR 栈**：`paddle`(383 MB)、`paddleocr-3.7.0`、`paddlex-3.7.2`、`cv2`、`scipy-1.18.1`、`scikit_learn-1.9.1`、`shapely`、`pyclipper`
- **公式相关**：`latex2mathml-3.81.1`（有）、`pylatexenc`（**没有**）、`unimernet`（**没有**）
- **下载客户端（空闲）**：`huggingface_hub-2.3.0`、`modelscope-1.40.2`、`aistudio_sdk-0.3.9`、`baidubce`、`hf_xet`、`requests`、`httpx`
- **可视化/其它**：`tokenizers-0.23.3`、`sentencepiece-0.2.2`、`safetensors`、`openpyxl`、`pandas-3.1.0`、`bidi`、`tiktoken`
- **没有**：`torch`、`transformers`、`onnxruntime`、`PyMuPDF/fitz`

[已验证] 可执行入口存在（可做子进程隔离集成）：

```
$ ls formula-venv/Scripts/*.exe
aistudio.exe  cffi-gen-src.exe  chardetect.exe  cpuinfo.exe  ...
paddleocr.exe  paddlex.exe  paddlex_genai_server.exe
latex2mathml.exe  l2m.exe  modelscope.exe  hf.exe
```

`paddleocr.exe` 可用 `paddleocr formula_recognition -i <png>` 直接跑，这对「主环境不引入依赖」的集成方式很关键。

---

## 2. 权重盘点：PP-FormulaNet_plus-M 已在盘上

[已验证] `C:\Users\Administrator\.paddlex\official_models\` 下有 **4 个** 模型（下载时间 2026-10-25 03:44–03:46，即上一次会话留下的）：

| 模型 | `inference.pdiparams` 字节 | 折合 | 绝对路径 |
|---|---|---|---|
| **PP-FormulaNet_plus-M** | **617,064,962** | 588.5 MiB | `C:\Users\Administrator\.paddlex\official_models\PP-FormulaNet_plus-M\inference.pdiparams` |
| PP-DocLayout_plus-L | 129,307,978 | 123.3 MiB | `...\official_models\PP-DocLayout_plus-L\inference.pdiparams` |
| UVDoc | 32,054,311 | 30.6 MiB | `...\official_models\UVDoc\inference.pdiparams` |
| PP-LCNet_x1_0_doc_ori | 6,754,166 | 6.4 MiB | `...\official_models\PP-LCNet_x1_0_doc_ori\inference.pdiparams` |
| **合计** | **785,181,417** | **748.8 MiB** | — |

公式模型目录内还带同源文件：
```
PP-FormulaNet_plus-M/config.json        3,951,676 B   ← 词表/tokenizer 内嵌（19077 词）
PP-FormulaNet_plus-M/inference.yml      2,244,564 B   ← 预处理配置
PP-FormulaNet_plus-M/inference.json      1,130,158 B
PP-FormulaNet_plus-M/.cache/huggingface/download/    ← HF 下载完成标记
```

[已验证] 证据：`.cache/huggingface/` 存在（CACHEDIR.TAG + download + trees），说明来自 HuggingFace 源 `PaddlePaddle/PP-FormulaNet_plus-M`。

[已验证] 这三个前置模型不是随便下的——它们**正好等于** `paddlex/configs/pipelines/formula_recognition.yaml` 的全部 4 个 SubModules：

```
LayoutDetection: PP-DocLayout_plus-L
FormulaRecognition: PP-FormulaNet_plus-M
DocOrientationClassify: PP-LCNet_x1_0_doc_ori
DocUnwarping: UVDoc
```

也就是说：**上一次会话已经把这套公式流水线的权重一次拉齐了，T2 主线不需要再下载任何东西。**

---

## 3. 完全离线能力：实测「构建期 0 次网络连接」

[已验证] 方法（脚本：`out/_t2_probe/probe_load.py`）：给 `socket.socket.connect` 与 `socket.create_connection` 打补丁，任何联网尝试都会被记录并抛 `OSError`，然后构建 `FormulaRecognitionPipeline(device="cpu")`。

```python
socket.socket.connect = _guard_connect          # 记录 + 抛错
socket.create_connection = _guard_create_connection
from paddleocr import FormulaRecognitionPipeline
pipe = FormulaRecognitionPipeline(
    formula_recognition_model_name="PP-FormulaNet_plus-M",
    layout_detection_model_name="PP-DocLayout_plus-L",
    doc_orientation_classify_model_name="PP-LCNet_x1_0_doc_ori",
    doc_unwarping_model_name="UVDoc", device="cpu")
```

输出（关键行）：

```
Creating model: ('PP-LCNet_x1_0_doc_ori', None, None)
Model files already exist. Using cached files. To redownload, please delete the directory manually:
  `C:\Users\Administrator\.paddlex\official_models\PP-LCNet_x1_0_doc_ori`.
Creating model: ('UVDoc', None, None)   ... Using cached files ...
Creating model: ('PP-DocLayout_plus-L', None, None) ... Using cached files ...
Creating model: ('PP-FormulaNet_plus-M', None, None) ... Using cached files ...

=== T2 load probe ===
{
 "steps": [["import paddle", 0.6], ["build FormulaRecognitionPipeline (cpu)", 6.7]],
 "connections": [],            <-- 关键：一次联网尝试都没有
 "paddle_version": "3.2.1", "compiled_with_cuda": false,
 "ok": true, "total_s": 9.5
}
```

[已验证] 代码级复核：`paddlex/inference/utils/official_models.py:930-968` `_get_model_local_path()` 的判定是

```python
candidate_dir = self._save_dir / f"{candidate_name}"
if os.path.exists(candidate_dir):
    logging.info("Model files already exist. Using cached files. ...")
    model_dir = candidate_dir
    break
...
if model_dir is None:            # 只有目录不存在时才走 hoster 探测/下载
    hosters = self._get_hosters()
```

即**目录存在时根本不进入 hoster 分支**，连「连通性检查」（`PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK` 那条）都不会触发。这解释了 `connections: []` 的来源，也说明离线性不依赖断网，只依赖这 4 个目录别删。

[已验证] 资源占用（第二次实测，`psutil`）：

```
import paddleocr+FormulaRecognitionPipeline: 1.4s
build pipeline(cpu):                          4.9s
RSS before 14 MiB -> after 1252 MiB  (delta 1238 MiB)   peak 1259.8 MiB
```

→ 常驻约 **1.2 GiB 内存**，**显存 0**。

---

## 4. 明确「不下载就跑不了」的部分（这条要交给用户决策）

[已验证] 用脚本把 pipeline yaml 里所有 `model_name` 与缓存目录求差集：

```
已缓存: ['PP-DocLayout_plus-L', 'PP-FormulaNet_plus-M', 'PP-LCNet_x1_0_doc_ori', 'UVDoc']

formula_recognition: 需要 4, 缺 []
layout_parsing:      需要 8, 缺 ['PP-FormulaNet-L', 'PP-OCRv4_server_det', 'PP-OCRv4_server_rec',
                                'PP-OCRv4_server_seal_det', 'RT-DETR-H_layout_17cls', 'SLANet_plus']
PP-StructureV3:      需要 15, 缺 ['PP-Chart2Table', 'PP-DocBlockLayout', 'PP-FormulaNet_plus-L',
                                'PP-LCNet_x1_0_table_cls', 'PP-LCNet_x1_0_textline_ori',
                                'PP-OCRv4_server_seal_det', 'PP-OCRv5_server_det', 'PP-OCRv5_server_rec',
                                'RT-DETR-L_wired_table_cell_det', 'RT-DETR-L_wireless_table_cell_det',
                                'SLANeXt_wired', 'SLANet_plus']
```

| 想要的东西 | 离线可行性 | 需要下载什么 | 与仓库约束冲突 |
|---|---|---|---|
| **公式专项（T2 主线）** | ✅ 可跑 | 无 | 否 |
| PP-StructureV3 整条（T3） | ❌ | 12 个模型（PP-OCRv5_server_det/rec、表格 5 件套、PP-FormulaNet_plus-L、Chart2Table…） | **是**（AGENTS.md「不引入需要联网的新依赖」；虽是一次性权重而非依赖，仍需用户点头） |
| layout_parsing 轻量版 | ❌ | 6 个模型 | 同上 |
| **UniMERNet（gpt.md 建议的复核模型）** | ❌ | 需要 `unimernet` 包 + `torch` + 独立权重 | **是**（新依赖，且本机无 torch） |
| GPU 版飞桨（官方流程） | ❌ | Windows 无 `paddlepaddle-gpu` wheel | **是** |

[已验证] UniMERNet / torch 全机不存在：

```
$ find C:/Users/Administrator/Desktop -maxdepth 4 -type d -name torch      -> (空)
$ find ~/.cache ~/.dsh-llama -maxdepth 3 -iname "*unimer*"                -> (空)
$ ls -d site-packages/*unimer*                                            -> (空)
main python: torch FAIL ModuleNotFoundError: No module named 'torch'
```

**建议**：`gpt.md` 里「UniMERNet 只复核失败公式」在**离线前提下不可执行**，要么删掉，要么当成「需要用户批准的下载项」单独报。T2 主线不要依赖它。

---

## 5. 显存 / 与 llama-server 共存

[已验证] GPU 现状（只读 `nvidia-smi`，未启任何推理进程）：

```
name, memory.total [MiB], memory.used [MiB], driver_version
NVIDIA GeForce RTX 5060, 8151 MiB, 613 MiB, 616.92
```

[已验证] 公式这条路**根本不进显存**，共存问题不存在：

- `paddle.device.is_compiled_with_cuda()` → **False**
- `paddle.device.cuda.device_count()` → **0**
- `paddlepaddle-3.2.1.dist-info/WHEEL` → `Tag: cp312-cp312-win_amd64`（包名 `paddlepaddle`，非 `-gpu`）
- `paddle/libs/` 里只有 `mklml.dll`、`mkldnn.dll`、`libiomp5md.dll`、`warpctc.dll`、`warprnnt.dll` —— **没有任何 cublas/cudnn/curand DLL**
- 包体积 383 MB（GPU 版通常 1.5 GB+）

所以：**PP-FormulaNet 与 llama.cpp 引擎在显存上零竞争，8080 端口也不冲突（paddle 只跑本地 python 进程，不监听端口）。**

[已验证] 官方流程 GPU 路线在本机不通：
```
$ wsl -l -v          -> 打印 WSL 安装帮助文本（无任何发行版）
$ docker --version   -> bash: docker: command not found
```
配合 AGENTS.md「Windows 装不了 GPU 版飞桨」——**本机唯一可用的飞桨就是 CPU 版**。

[推测] 真正的代价是 **CPU 争用**：pipeline 常驻 1.2 GiB 内存 + 多核占用。lead 正在跑的 23 套验收走的是 llama-server（GPU），CPU 上还有 PDF 渲染/校验，所以 T2 批量跑建议**排在 23 套验收之后**，或限制线程数（`OMP_NUM_THREADS=2`）。

---

## 6. 公式规模普查（只读既有 JSON，未重跑 OCR）

脚本：`out/_t2_probe/census.py`；明细：`out/_t2_probe/census.json`。

### 6.1 23 套验收集（`out/acceptance/native_final3/*.quality.json`）

[已验证]
```
native_final3: 23 files, 23 parsed
  formula_count total = 2729   (23 套, avg 118.7/套)
  formula_lines total = 1058   (avg 46.0/套)
  formula_broken total = 0, formula_suspicious total = 0
  含公式行最多的：2024北京卷 52 行 / 130 处；最少：2023上海 36 行 / 81 处
```

### 6.2 其它子集（交叉印证同一量级）

[已验证]
```
release(20 套):            formula_count 2598, formula_lines 1039 (52.0/套)
exams/quality(12/18 有字段): formula_count 1573, formula_lines  613 (51.1/套)
exams/*.exam.json 独立扫描(18 套): 含公式行 1222, 公式出现 2439, 平均 67.9 行/套
```

### 6.3 T1 `content_type` 已经落地

[已验证] `native_final3` 的 24 个 layout JSON 里逐行统计：

```
content_type='formula': 1068 行
content_type='text':     909 行
content_type='table':      3 行
total: 1980 行  → 54% 的行被判为 formula
```

行对象字段：`id / column / content_type / reading_index / style / top,bottom,left,right(pt) / px_*`，`schema_version=1`。
→ **T2 的「公式区域」来源已经有了，不必等 T1 从零做。**

### 6.4 人工标注 50–100 条要多久

[已验证 + 估算] 平均 **46 含公式行/套**、**118.7 公式出现/套**：

- 标 **50 条公式出现** → **1 套**就够（北京/新高考卷单套 128–143 处）
- 标 **100 条公式出现** → **1 套**（取大卷）或 2 套
- 若要覆盖 5 类结构（上下标/分式/根号/矩阵/分段函数），建议 **2–3 套**（约 92–138 行、240–360 处），足够分层抽样
- 按 30–60 s/条（对照裁片抄 LaTeX 并核对）→ **25–100 分钟**

**结论：标注成本不是瓶颈，一天内可以完成。**

---

## 7. 未实测项与风险（必须写清）

### 7.1 [未实测] 单裁片推理延迟与输出质量

**没跑**：禁令要求「不要跑 OCR」，且推理会占 CPU 影响 lead 的 23 套验收。我只验证到「模型能离线载入」。

[推测] 依据与估算：
- 输入固定 **384×384**（`inference.yml`: `UniMERNetImgDecode: input_size: [384, 384]`）
- 解码上限 **max_seq_len 2560**，自回归逐 token（`UniMERNetDecode` + `fast_tokenizer_file`，词表 19077）
- 权重 588.5 MiB fp32 ≈ **1.5 亿参数**
- CPU（无 GPU、无 oneDNN 加速保证）→ **每张约 1–10 s**，长公式（矩阵/分段函数）偏慢；100 张量级 ≈ 2–17 分钟，**可接受**，但不适合整页扫。

**要 lead 授权的一条命令**（CPU-only、不占 8080、不写 out/ 之外）：
```bash
"C:/Users/Administrator/Desktop/ocr/formula-venv/Scripts/paddleocr.exe" formula_recognition \
  -i <一张公式裁片.png> --device cpu
```

### 7.2 [已验证] 输出格式是**裸 LaTeX，没有定界符**

`paddlex/inference/models/formula_recognition/predictor.py` 的返回键是 `rec_formula`（字符串），**不含 `$...$` 或 `\(...\)`**。
仓库侧 `formula_quality.extract_formulas()` 靠定界符找公式 → 接入时必须自己包 `$...$`，否则公式结构门永远数到 0、KaTeX 也不会渲染。

### 7.3 [已验证] 「公式 bbox」不能直接拿现有 line bbox

现有 line 的 `content_type=formula` 是**整行**判定，bbox 也是一整行。抽样看一条真实行：

```
"10. 已知圆 \(C: \frac{x^{2}}{a^{2}} + \frac{y^{2}}{b^{2}} = 1 (a > b > 0)\), 则..."
```

把整行裁下来喂 PP-FormulaNet（训练目标是**单个公式**）会带来中英混排干扰。

**可用的替代路径（离线、模型已在盘）**：`PP-DocLayout_plus-L` 的 `label_list` 里直接有 `formula`，processors 里也有 `inline_formula` 的专门处理：

```
layout_analysis/processors.py:850  formula_index = self.labels.index("formula")
对应 config.json label_list: [... "formula" ... "formula_number" ...]
```

→ 用 DocLayout 出**公式区域 bbox**，再按 bbox 以 250–300dpi 重新渲染裁片，正好符合 NEXT_SESSION.md 的 T2 步骤 2。
**代价**：等于在页面上引入第二个版面来源，触碰 AGENTS.md「两个引擎产物不能混名 / 坐标不是语义」两条纪律。建议做法是 **DocLayout 只用于裁片几何，正文/结构仍以现有 line 为准**，并在产物里写明 `source=layout_plus_l`。

### 7.4 [已验证] 主环境不能直接 `import paddle`

```
main python (3.12.10): paddle FAIL ModuleNotFoundError: No module named 'paddle'
                       cv2   FAIL / torch FAIL / transformers FAIL
```
→ 集成必须走**子进程边界**（`formula-venv/Scripts/python.exe` 或 `paddleocr.exe`，JSON in/out），主环境零新依赖。这也顺带满足「不引入新依赖」的仓库约束。

### 7.5 [已验证] PyMuPDF(fitz) 两边都没有

```
main python : fitz FAIL ModuleNotFoundError: No module named 'fitz'
formula-venv: fitz FAIL / pymupdf FAIL
两边都有   : pypdfium2 OK（主 12.3.0 系 / venv 13.0.0 系 Pillow）
```
→ T2 的 250–300dpi 裁片继续用 **pypdfium2 + Pillow**（仓库现有做法），**不需要装 PyMuPDF**，也不算新依赖。注意仓库纪律：pypdfium2 渲染必须**串行**。

### 7.6 [已验证] venv 里有联网客户端，但缓存命中时不触发

`huggingface_hub / modelscope / aistudio_sdk / baidu bce / requests` 都在。§3 已证明缓存命中路径不会调用它们。
**风险提示**：如果谁删掉/改坏 `.paddlex/official_models/` 下任一目录，paddlex 会在**运行时静默尝试联网下载**（`_get_model_local_path` 的 `model_dir is None` 分支），在离线机器上表现为「卡很久然后报 No available model hosting platforms」。建议接入时显式传 `*_model_dir=` 绝对路径，彻底绕开下载逻辑。

---

## 8. T2 落地建议（最小风险路径）

1. **模型层**：`FormulaRecognitionPipeline` 只做公式识别（或 `PP-DocLayout_plus-L` + `PP-FormulaNet_plus-M` 两段直调），全程 `device="cpu"`，`OMP_NUM_THREADS=2`，显式 `formula_recognition_model_dir=C:\Users\Administrator\.paddlex\official_models\PP-FormulaNet_plus-M`。
2. **集成层**：主 Python 用 `subprocess` 调 `formula-venv\Scripts\python.exe`，JSON in/out；主环境不加依赖；不 import paddle。
3. **裁片层**：DocLayout 给公式 bbox → pypdfium2 串行渲 250–300dpi → Pillow 存临时 PNG（建议落在 `out/_t2_probe/crops/`，不要污染 `out/` 根）。
4. **写入层**：结果包 `$...$` 再交给现有 `formula_quality` + `verify_math.mjs` 双门；保留 `native_result / primary_result / review_result / final` 与 `source`，不静默覆盖（NEXT_SESSION.md 第 6 条）。
5. **时序**：先用 1–2 套卷跑通；**等 lead 的 23 套验收结束**再批量，避免 CPU 争用。
6. **验收**：先做 50–100 条人工标注的**内容级** A/B（现有 PaddleOCR-VL 的 `\(...\)` vs PP-FormulaNet），只在确有提升时才默认开启；不要用「KaTeX 能渲染」当准确率。

---

## 9. 证据文件清单

| 文件 | 内容 |
|---|---|
| `out/_t2_probe/probe_load.py` | socket 护栏 + 离线载入探针（可复跑） |
| `out/_t2_probe/load_probe.json` | 探针结果：`connections: []`、耗时、`compiled_with_cuda` |
| `out/_t2_probe/census.py` | 公式规模普查脚本（只读 JSON） |
| `out/_t2_probe/census.json` | 逐套明细（23 套 + 20 套 + 18 套） |
| `out/_t2_probe/report.md` | 本报告 |

**未产生副作用**：没有 pip install、没有下载、没有改源码、没有 git、没有起 llama-server、没有跑仓库 OCR、没有在 `out/` 其它位置写文件。
