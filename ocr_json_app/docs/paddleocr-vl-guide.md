# PaddleOCR-VL-1.6 使用指南（本机实战版）

> 面向本机环境（WSL2 Ubuntu 22.04 + RTX 5070 8GB / sm_120）写的操作手册。
> 所有结论都标注了**证据来源**：`[实测]` = 本机跑出来的原始输出，`[文档]` = 官方教程原文。
> 版本基线：`paddleocr 3.7.0` + `paddlepaddle-gpu 3.2.1(cu129)`。

---

## 0. 先看这一节：官方的"完整流程"到底由什么组成

> **证据强度说明（请先读）**：本指南的 `[实测]` 指的是**在本机跑出来的原始输出**。
> 其中"官方流程本身"是真跑通了端到端（WSL 内 6 页 → `pageNN_res.json`/`.md`/`.docx`），
> 但**本项目自己的两条渲染路线（绝对定位 layout / 文档流 blocks）并没有端到端跑通**——
> Windows 侧缺 `pypdfium2` 与 OCR 引擎。因此第 4.3 节"承载方式"那一段的对比属于
> **产物级 + 浏览器截图级证据**，不是端到端验证。引用时请按这个强度理解。

这是最容易走错的地方。官方教程里有一段警告，直接决定了"为什么不能只用 VLM" `[文档]`：

> **若需使用 PaddleOCR-VL 的完整能力，必须采用版面分析与 VLM 识别协同的完整流程，
> 而不能仅单独使用 VLM。**……直接通过 Transformers 本地执行 PaddleOCR-VL-0.9B，
> 或直接请求 vLLM / SGLang / FastDeploy 服务，**都不等同于运行完整的 PaddleOCR-VL 流程**。
> 如果在使用过程中出现无法复现论文或 PaddleOCR 官网精度、**模型输出大量幻觉文本**等问题，
> 首先应确认当前使用的是完整的 PaddleOCR-VL 流程，而不是仅使用其中的 VLM 组件。

| 阶段 | 模型 | 干什么 |
|---|---|---|
| ① 版面分析 | **PP-DocLayoutV3** | 整图输入 → 检测 `doc_title` / `paragraph_title` / `text` / `table` / `formula` / `image` … 各元素，**定阅读顺序**，裁出元素子图 |
| ② VLM 识别 | **PaddleOCR-VL-1.6-0.9B** | 每个子图独立识别 → Markdown / LaTeX / HTML 表格 |
| ③ 结果合并 | 框架内建 | 按①的顺序把②的结果拼成整页结果 |

**命名坑**（官方特别提醒）：`PaddleOCR-VL-0.9B` 不是"PaddleOCR-VL 的一个变种"，
而是完整流程里的 **VLM 组件**。把它单独跑起来 ≠ 跑了 PaddleOCR-VL。

### 硬件前提（RTX 50 系必须对号入座）

`[文档]` RTX 5090/5080/5070/5060 等属于 **Blackwell（sm_120）**，官方要求读
**专用的 Blackwell 教程**，并且：

- 驱动需支持 **CUDA ≥ 12.9**（本机 616.92 满足）`[实测]`
- 官方 Docker 镜像标签必须带 `-nvidia-gpu-sm120`（普通 `-nvidia-gpu` 在 sm_120 上不可用）

本机驱动下 `nvidia-smi` 在 WSL 里可直接看到 GPU `[实测]`：

```
NVIDIA GeForce RTX 5070 Laptop GPU, 616.92, 8151 MiB
```

---

## 1. 装（本机已验证的做法）

### 为什么不能装在 Windows

`[实测]` 飞桨 GPU 版**只发 Linux wheel**，Windows 上直接失败：

```
ERROR: No matching distribution found for paddlepaddle-gpu
```

所以走 WSL2。官方验证过的 Python 范围是 **3.9–3.13** `[文档]`。

```bash
# WSL 内（Ubuntu-22.04）
uv venv --python 3.12 ~/.venv_paddleocr
. ~/.venv_paddleocr/bin/activate

# 注意：RTX 50 系要 cu129（对应 Blackwell），不是 cu126
uv pip install "paddlepaddle-gpu==3.2.1" \
  -i https://www.paddlepaddle.org.cn/packages/stable/cu129/

uv pip install -U "paddleocr[doc-parser]"
```

校验 `[实测]`：

```
paddle 3.2.1   compiled_with_cuda True   gpu count 1
```

### 官方 Docker 路线（本机网络下不可行）

`[文档]` 推荐 Docker 镜像（`.../paddleocr-vl:latest-nvidia-gpu-sm120`，约 10GB）。
但 `[实测]` 本机到百度 CCR registry **TLS 握手超时**，且公网带宽约 0.9MB/s →
10GB 需 3 小时以上。**结论：这台机器用手动安装，别走 Docker。**

---

## 2. 跑：CLI

### 2.1 最基本的命令

```bash
paddleocr doc_parser -i ~/pages --save_path ~/ocr_out --device gpu
```

`-i` 可以是单张图片、**目录**（目录内图像）、或 PDF 路径。

### 2.2 ⚠️ 最大的坑：不要把 PDF 直接喂进去

`[实测]` 直接 `-i xxx.pdf` 时，程序**卡在 PDF→图像转换**阶段：
CPU 100%、GPU 8%、**4 小时零输出**（日志停在加载模型之后，产物目录为空）。
先自己渲染成 PNG 再喂目录，6 页只要 0.5 秒，随后 GPU 打满 99%、**6 页约 4 分钟**跑完。

```bash
# 预渲染（本项目的 run_paddleocr_vl.sh 里就是这么做的）
python - <<'PY'
import pypdfium2 as pdfium, os
doc = pdfium.PdfDocument("试卷.pdf")
os.makedirs(os.path.expanduser("~/pages"), exist_ok=True)
for i in range(len(doc)):
    doc[i].render(scale=150/72).to_pil().convert("RGB").save(
        os.path.expanduser(f"~/pages/page{i+1:02d}.png"))
PY
paddleocr doc_parser -i ~/pages --save_path ~/ocr_out --device gpu
```

### 2.3 产出物

`[实测]` 每页 4 个文件：

| 文件 | 内容 |
|---|---|
| `pageNN_res.json` | **结构化结果**（`parsing_res_list` + `layout_det_res` + `markdown`） |
| `pageNN.md` | 该页 Markdown（LaTeX 公式、HTML 表格） |
| `pageNN.docx` | Word 版（需 `python-docx`，否则 `ModuleNotFoundError: No module named 'docx'`） |
| `pageNN_layout_det_res.png` | 版面检测可视化（**人工核对版面分析对不对，最直观**） |

### 2.4 CLI 全量参数（`paddleocr doc_parser --help` `[实测]`）

按用途分组，**加粗是本项目当前没用、但值得用的**：

**输入输出**
| 参数 | 说明 |
|---|---|
| `-i/--input` | 输入路径或 URL（图片 / 目录 / PDF） |
| `--save_path` | 输出目录 |

**流程版本与模型**
| 参数 | 说明 |
|---|---|
| `--pipeline_version {v1,v1.5,v1.6}` | 默认 **v1.6** |
| `--layout_detection_model_name/_dir` | 版面分析模型（默认 PP-DocLayoutV3） |
| `--vl_rec_model_name/_dir` | VLM 模型（默认 PaddleOCR-VL-1.6-0.9B） |

**版面分析调参**
| 参数 | 说明 |
|---|---|
| `--layout_threshold` | 版面检测得分阈值 |
| `--layout_nms` | 是否 NMS |
| `--layout_unclip_ratio` | 检测框扩张系数 |
| `--layout_merge_bboxes_mode` | 重叠框过滤方式（large/small/union） |
| **`--layout_shape_mode`** | 几何表示：`rect` / `quad` / `poly` / `auto`（默认 auto）。**倾斜、弯折、异形框场景的关键参数** |

**模块开关（默认值见下）**
| 参数 | 作用 |
|---|---|
| `--use_layout_detection` | 是否做版面分析。**关掉就退化成"只跑 VLM"**——官方明确警告不要这么干 |
| `--use_doc_orientation_classify` | 文档方向分类（扫描件倒置/旋转 90° 时必开） |
| `--use_doc_unwarping` | 文本图像矫正（拍照/弯折/透视变形时必开） |
| `--use_chart_recognition` | 图表解析（把柱状图/折线图解析成数据） |
| `--use_seal_recognition` | 印章识别 |
| `--use_ocr_for_image_block` | 对 `image` 块内的文字也做 OCR |
| `--format_block_content` | 把块内容格式化成 Markdown |
| `--merge_layout_blocks` | 跨栏/上下交错分栏的检测框合并 |
| `--markdown_ignore_labels` | Markdown 里忽略哪些版面标签 |
| `--use_queues` | 队列异步（数据加载 / 版面分析 / VLM 三段并行）。页多时提升效率；**排查卡死时可关掉，让阶段串行、日志更清楚** |

> ⚠️ **`--help` 一个默认值都不打印**（没用 `ArgumentDefaultsHelpFormatter`），
> 而且 CLI 上这些开关的默认**全是 `None`**，真默认值在 PaddleX 内部。
> 想知道"这次实际用了什么"，**别查 help、也别信文档表格，去看产物** `[实测]`
> —— 每页 `pageNN_res.json` 里的 `model_settings` 就是权威答案：
>
> ```json
> {"use_doc_preprocessor": false, "use_layout_detection": true,
>  "use_chart_recognition": false, "use_seal_recognition": false,
>  "use_ocr_for_image_block": false, "format_block_content": false,
>  "merge_layout_blocks": true, "return_layout_polygon_points": true,
>  "markdown_ignore_labels": ["number","footnote","header","header_image",
>                             "footer","footer_image","aside_text"]}
> ```
>
> 另外确凿的默认：`pipeline_version='v1.6'`、`layout_shape_mode='auto'`、
> `use_tensorrt=False`、`precision='fp32'`、`enable_mkldnn=True`、
> `mkldnn_cache_capacity=10`、`cpu_threads=10`、`enable_cinn=False`；
> `-i/--input` 必填。
>
> 🔧 **想自己内省默认值时，先设 `PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True`** `[实测]`：
> 调用 `_get_parser()` 会经 `_register_models` 触发**官方模型自动下载**
> （实测拉下了 PP-Chart2Table 的 15 个文件、耗时约 2.5 分钟）。加上这个环境变量可避免。

> ⚠️ **布尔参数的解析陷阱** `[实测源码]`：
> `str2bool(v) = v.lower() in ("true","yes","t","y","1")`
> —— 所以 `--use_chart_recognition 0` 是 False，而**写错成任何别的字符串也会静默变 False
> 且不报错**（例如 `--use_chart_recognition yes` 与 `Y` 都可以（都在识别表里），但 `--use_chart_recognition YEAH` 或拼错成 `--use_chart_recognition ture` 会静默变 False）。开这些开关时务必用 `True`/`true`/`1`。

**VLM 采样与预处理**
| 参数 | 说明 |
|---|---|
| `--prompt_label` | prompt 类型（取值 `ocr`/`formula`/`table`/`chart`/`spotting`/`seal`）。**仅当 `use_layout_detection=False` 时生效** |
| `--repetition_penalty` / `--temperature` / `--top_p` | 采样参数（**压幻觉的调节旋钮**） |
| **`--max_new_tokens`** | 单次生成上限（输出被截断时的关键参数） |
| **`--min_pixels` / `--max_pixels`** | VLM 预处理的像素上下限。**直接决定识别精度与显存占用**——8GB 卡上这是最重要的调参项 |

**VLM 后端（可把 VLM 拆出去单独服务）**
| 参数 | 说明 |
|---|---|
| **`--vl_rec_backend {native,vllm-server,sglang-server,fastdeploy-server,mlx-vlm-server,llama-cpp-server}`** | 默认 native（本地飞桨）。**可接外部推理服务** |
| `--vl_rec_server_url` / `--vl_rec_max_concurrency` / `--vl_rec_api_model_name` / `--vl_rec_api_key` | 外部服务地址与并发 |

**引擎与硬件**
| 参数 | 说明 |
|---|---|
| `--device` | `cpu` / `gpu` / `gpu:0` / `gpu:0,1`（多卡并行） |
| `--engine {paddle,paddle_static,paddle_dynamic,transformers,onnxruntime}` | 推理引擎 |
| `--enable_hpi` | 高性能推理 |
| `--use_tensorrt` / `--precision {fp32,fp16}` | TensorRT 子图 + 精度 |
| `--enable_mkldnn` / `--mkldnn_cache_capacity` / `--cpu_threads` / `--enable_cinn` | CPU 侧加速 |
| `--paddlex_config` | PaddleX 产线配置文件（**engine 相关的细粒度配置写在这里**） |

### 2.5 全部子命令：**27 个**，而 `paddleocr --help` 只列 **2 个** `[实测]`

> ⚠️ **`paddleocr --help` 不可信**：它只列 `doc2md` 和 `api` 两个。
> 根因是只有这两个子命令传了 `help=`，而 argparse 的摘要只显示 `_choices_actions`。
> 真实清单要从 **argparse 自己的报错信息**里取（随便传一个不存在的子命令即可）。
> 下面 27 个就是这么来的，并已用内省 `count: 27` 核对。

**文档解析产线**
`doc_parser`（**PaddleOCR-VL 完整流程**）、`pp_structurev3`、`pp_chatocrv4_doc`、
`pp_doctranslation`、`doc_preprocessor`、`doc_understanding`

**单模块（可直接调用，做定向任务）**
`layout_detection`、`formula_recognition`、`formula_recognition_pipeline`、
`table_recognition_v2`、`table_structure_recognition`、`table_cells_detection`、
`table_classification`、`chart_parsing`、`seal_recognition`、`seal_text_detection`、
`text_detection`、`text_recognition`、`text_image_unwarping`、
`textline_orientation_classification`、`doc_img_orientation_classification`、
`doc_vlm`、`ocr`

**工具**
`doc2md`（docx/xlsx/pptx → Markdown）、`api`（**云端 API，需 token**）、
`install_hpi_deps {cpu,gpu,npu}`、`install_genai_server_deps {vllm,sglang,fastdeploy}`

> 🔒 **`api` 是云 API**：与项目"全程 127.0.0.1、文档不出机器"的定位**直接冲突**，
> 应视为禁用项。本指南不推荐使用。
>
> `genai_server` 在默认安装下**没有注册** `[实测]`：直接调用会得到
> `invalid choice: 'genai_server'`。必须先 `paddleocr install_genai_server_deps vllm|sglang|fastdeploy`。

---

## 3. 跑：Python API（本项目**完全没用**，但值得用）

`[实测]` `paddleocr` 3.7.0 导出的类：

```
PaddleOCRVL                 ← 完整流程（doc_parser 的对应物）
PaddleOCRVLOptions          ← 全部选项（dataclass）
LayoutDetection  FormulaRecognition  ChartParsing  SealRecognition
TableStructureRecognition  TableCellsDetection  TableClassification
TextDetection  TextRecognition  TextLineOrientationClassification
DocPreprocessor  DocImgOrientationClassification  DocVLM
PPStructureV3  PPChatOCRv4Doc  PPDocTranslation  TableRecognitionPipelineV2
```

### 3.1 最小用法

```python
from paddleocr import PaddleOCRVL

pipe = PaddleOCRVL(pipeline_version="v1.6")   # 默认就是 v1.6

for res in pipe.predict("page01.png"):
    data = res["res"]                          # 或 res.json / res.save_all(dir)
    for blk in data["parsing_res_list"]:
        print(blk["block_label"], blk["block_bbox"], blk["block_order"])
        print(blk["block_content"])
```

### 3.2 `predict()` 的全部参数 `[实测]`

```
use_doc_orientation_classify  use_doc_unwarping  use_layout_detection
use_chart_recognition  use_seal_recognition  use_ocr_for_image_block
layout_threshold  layout_nms  layout_unclip_ratio  layout_merge_bboxes_mode
layout_shape_mode='auto'  use_queues
prompt_label  format_block_content
repetition_penalty  temperature  top_p
min_pixels  max_pixels  max_new_tokens
merge_layout_blocks  markdown_ignore_labels  vlm_extra_args
```

### 3.3 `PaddleOCRVLOptions` 比 CLI 多出来的选项 `[实测]`

CLI 里没有、只在 Python API 里有的（**长文档最需要的几个**）：

| 选项 | 作用 |
|---|---|
| **`restructure_pages`** | **跨页重组**：把分页断开的段落/表格接回去 |
| **`merge_tables`** | 跨页表格合并（配合上面） |
| **`relevel_titles`** | 标题层级重排（跨页后标题级别归正） |
| **`prettify_markdown`** | 输出美化后的 Markdown |
| `show_formula_number` | Markdown 里是否保留公式编号 |
| **`return_markdown_images`** | 是否返回 Markdown 引用的图片（图块内容） |
| **`output_formats`** | 附加导出格式（当前支持 `"docx"`） |
| **`visualize`** | 返回可视化结果图与中间图像（**排查版面分析对不对**） |
| `vlm_extra_args` | 透传给 VLM 的额外参数 |
| `extra_options` | 其它透传 |

### 3.4 实例方法（`PaddleOCRVL` `[实测]`）

```
predict  predict_iter  restructure_pages  concatenate_markdown_pages
export_paddlex_config_to_yaml  close  get_cli_subcommand_executor
```

`restructure_pages` 是**长文档专用的后处理**：`predict()` 逐页跑完后，把各页结果交给它做跨页合并。

`[实测]` 真实签名（**注意与文档表格里的驼峰参数名不同**）：

```python
restructure_pages(res_list, merge_tables=True, relevel_titles=True,
                  concatenate_pages=False)
concatenate_markdown_pages(markdown_list)
```

`[实测]` 在本项目 6 页结果上跑的结果：

```python
pipe = PaddleOCRVL(pipeline_version="v1.6", device="gpu")
res_list = [{"res": json.load(open(f))} for f in page_files]
out = pipe.restructure_pages(res_list, merge_tables=True,
                             relevel_titles=True, concatenate_pages=True)
r = out[0]                       # PaddleOCRVLResult，len(out) == 1
r.markdown["markdown_texts"]     # ← 全文 Markdown 在这里（不是 r.markdown 直接给文本）
r.json["res"]["parsing_res_list"]  # 46 块，跨页统一
r.json["res"]["page_count"]      # 6
```

产出数字 `[实测]`：

| 指标 | 值 |
|---|---|
| 返回 | `list` 长度 **1**（6 页合成 1 个结果） |
| 全文 | **5604 字符**（逐页 md 拼接是 5609 字符，基本一致） |
| 标题 | 4 个，且**层级已归正**：`#` 试卷名、`##` 一/二/三大题 |
| 表格 / 图 | `<table>` ×1、`<img>` ×4 |
| 题号 1..21 | **全覆盖** |

> `relevel_titles=True` 的效果最直观：逐页时每个大题标题的级别不一定一致，
> 重组后统一成 `##`。**这是"逐页结果"与"整篇文档"的差别。**

已存盘供查看：`out/paddleocr_full/paper.full.md`（8.4KB）、`paper.full_res.json`（79KB）。

### 3.5 ⚠️ 返回数据的四个陷阱 `[实测]`

**① `block_order` 不是每块都有**

官方对一批标签**不给 `block_order`**（`SKIP_ORDER_LABELS`）：`figure_title`、`vision_footnote`、
`image`、`chart`、`table`、`header`、`footer`、`footnote`、`aside_text` 等，
再加上 `markdown_ignore_labels` 里的标签 → 值为 **`None`**。

`[实测]` 本卷 6 页 46 块里正好 **5 块**是 `None`：4 个 `image` + 1 个 `table`。

> **这是一个已经踩过的坑**：本项目的转换器原来写
> `sort(key=lambda b: b["order"] if b["order"] is not None else 10**6)`，
> 等于把这 5 个块**一律甩到页尾**，与官方阅读顺序不符。
> 正确做法是退回它们在 **`parsing_res_list` 里的原始次序**——那份列表本身就是按阅读顺序排好的。
> 已修（`tools/import_paddleocr_json.py` 与 `import_paddleocr_blocks.py`）。

**② 官方只给块级坐标，没有行级坐标**

`parsing_res_list[].block_bbox = [x1, y1, x2, y2]`（像素、左上原点）是**块**的框。
一个 `text` 块可能含 5 个视觉行。所以本项目"块 → 行展开"那一层是**必要的**，不是重复劳动。

> ⚠️ 补正（实测复核）：`layout_det_res.boxes[]` 的键是 **6 个**，不是官方文档示例里的 4 个：
> `['cls_id', 'coordinate', 'label', 'order', 'polygon_points', 'score']`
> —— 多了 **`order`（该框的阅读顺序）** 与 **`polygon_points`**。重建阅读顺序时 `order` 直接可用。

**②-b 哪些字段"看起来有、其实本产物没有"**（最容易被文档带着走的地方）

| 字段 | 本产物 | 说明 |
|---|---|---|
| `spotting_res`（`rec_polys`/`rec_texts`） | **0/6 页** | 库源码里确实存在（`result.py` / `uilts.py` / `pipeline.py:335`），但只在**检测出 `spotting` 块**时才产生。6 页 JSON 顶层各 8 键，均无此字段 |
| `doc_preprocessor_res.angle` | **0/6 页** | `use_doc_preprocessor=false`，没开预处理就没有它 |
| `table_res_list` | **0/6 页** | 只是 `_to_html` / `_to_xlsx` 的**代码数据源**，不在产物 JSON 里 |
| `page_index` / `page_count` | 键在，**值为 `null`** | 别当有效字段用 |

**②-c "这次到底用了什么"的权威答案只有一个：`model_settings`**

`[实测]` 逐字（6 页一致，共 11 键，**注意里面没有 `use_queues`**）：

```json
{"use_doc_preprocessor": false, "use_layout_detection": true,
 "use_chart_recognition": false, "use_seal_recognition": false,
 "use_ocr_for_image_block": false, "format_block_content": false,
 "merge_layout_blocks": true, "return_layout_polygon_points": true,
 "markdown_ignore_labels": ["number","footnote","header","header_image",
                            "footer","footer_image","aside_text"]}
```

所以"`use_queues=True` 由 `model_settings` 佐证"这个说法**不成立**——那个默认只能引官方文档。

**③ 官方自己产出的 Markdown 带层级，别自己拼**

`[实测]` `pageNN.md` 里有 `# 试卷名` / `## 一．填空题` 这样的标题层级
（`restructure_pages` 后更会统一成一致的级别）。
本项目转换器原来是**自己把块拼成 Markdown**，把层级丢了。

**④ 一份未完成的实测（诚实标注）**

`--format_block_content True` 被怀疑能省掉"自研折行/估字号/正则拆表"，
但目前**只有文档与源码证据，没有实测差异**：验证用的单页跑了 18 分钟零输出
（CPU 100%、GPU 17%、显存 7.8G/8.1G），形态与"PDF 直喂卡死"相同，但**这次输入是 PNG**。
单次观察且有混淆因素（显存接近占满），**不能据此下结论**。要用这个参数请自行复测。

> 另外：`spotting_res`（行/词级框，`rec_polys`/`rec_texts`）**不在 `doc_parser` 的产物里**
> `[实测]`——`pageNN_res.json` 的顶层键只有
> `input_path / page_index / page_count / width / height / model_settings /
> parsing_res_list / layout_det_res` 八个。它是**其它子命令**（如 `ocr`）的输出，
> 想用它得另跑一条产线。

---

## 4. 能力对照：这个项目用到了什么、漏了什么

`[实测]` 对照 `ocr_json_app/tools/import_paddleocr_json.py` 与 `run_paddleocr_vl.sh`：

### 4.1 已经在用

- ✅ `doc_parser` 完整流程（版面分析 + VLM）——没有走"只用 VLM"的错路
- ✅ `pipeline_version=v1.6`（默认）
- ✅ `parsing_res_list` 的 `block_label` / `block_content` / `block_bbox` / `block_order`
- ✅ `layout_det_res.boxes`（存成 `layout_boxes`）
- ✅ `--device gpu`
- ✅ 预渲染 PNG 再喂目录（绕开 PDF 卡死）
- ✅ 从 `md` 里抽 HTML 表格

### 4.2 **漏掉的能力**（按价值排序）

| # | 漏掉的能力 | 能解决什么问题 |
|---|---|---|
| 1 | **`restructure_pages` / `merge_tables` / `relevel_titles`** | 逐页结果是碎的：标题层级不统一、跨页表格与段落断开。**已实测**（第 3.4 节）：6 页 → 1 篇 5604 字符全文、4 个标题层级归正、题号全覆盖。这是**长文档最该用的后处理**，本项目完全没用 |
| 2 | **`PaddleOCRVL` Python API**（本项目只用 CLI） | 用 API 才能拿到 `PaddleOCRVLOptions` 的全部选项、`predict_iter` 流式处理、`visualize` 可视化、以及 `restructure_pages` |
| 3 | **`--use_doc_orientation_classify` / `--use_doc_unwarping`** | 换一份**扫描件/拍照件**（本项目的判据全依赖"文字层"，纯扫描件会失效）时，这两个模块是能否用的前提 |
| 4 | **`--min_pixels` / `--max_pixels`** | 8GB 显存下控制精度与占用的最关键旋钮。本项目用默认值，显存一度被打满（实测 7753/8151 MiB） |
| 5 | **`--max_new_tokens`** | 输出被截断时的直接解法 |
| 6 | **`--repetition_penalty` / `--temperature` / `--top_p`** | **压幻觉的旋钮**。第一版靠"每行投 3 票"压幻觉；官方流程应该是调这几个参数 |
| 7 | **`visualize` / `outputImages` / `pageNN_layout_det_res.png`** | 人工核对"版面分析到底框对了没有"的最快手段。本项目只拿 `-res.json`，把可视化丢了 |
| 8 | **`--format_block_content`** | 把块内容格式化为 Markdown，省掉自己拼 Markdown |
| 9 | **`--use_ocr_for_image_block`** | 图块里的文字（坐标轴刻度、图上标注）现在只在裁片图里，不可搜索 |
| 10 | **`--use_chart_recognition`** | 图表转数据（本项目有直方图、坐标图） |
| 11 | **`--use_seal_recognition`** | 印章（本卷没有，但框架能力） |
| 12 | **`--layout_shape_mode=poly/quad`** | 倾斜/异形框定位。本项目用默认 auto，但没消费 `block_polygon_points` |
| 13 | **`--vl_rec_backend` 系列** | 把 VLM 拆到独立服务（vLLM/SGLang/llama.cpp）提并发。**注意**：官方警告不要把"VLM 服务"当成完整流程直接调用 |
| 14 | **`doc2md` / `pp_structurev3` / `pp_doctranslation` / `ocr` 等 24 个其它子命令** | 不同任务有专门产线，不必都塞进 `doc_parser` |
| 15 | **`--engine transformers/onnxruntime`、`--enable_hpi`、`--use_tensorrt`、`--precision fp16`** | 跨引擎验证精度（`transformers` 可作对照）、提速 |
| 16 | **`--paddlex_config` / `export_paddlex_config_to_yaml`** | 细粒度产线配置（`engine` 相关配置只能写在这里） |
| 17 | **`--use_queues`** | 默认 True；排查卡死时应关掉串行化，日志才清楚 |
| 18 | **`--markdown_ignore_labels`** | 控制 Markdown 里忽略哪些标签（页眉页脚等） |
| 19 | **官方 `table_res_list` → `save_to_html` / `save_to_xlsx`** | 表格的结构化产物。**README 里"表格在首选引擎下是图片裁片"是 llama.cpp 时代的结论**，官方流程本就有真表格 |
| 20 | **`save_to_word`（每页 `.docx`）** | 6 个 docx 完全没消费 |
| 21 | **`block_polygon_points`（46/46 都有）** | 转换器存进了 `polygon_px`，但**仓库内零消费者**——倾斜/异形框白白丢掉 |
| 22 | **`group_id`** | 与 `merge_layout_blocks` 配套的分组信息，无人使用 |
| 23 | **`doc_preprocessor_res.angle`** | 文档方向的实测角度值（开了方向分类才有） |
| 24 | **`install_hpi_deps {cpu,gpu,npu}`** | 一键装高性能推理依赖，没用过 |
| 25 | **`ocr` / `layout_detection` 等单模块子命令** | `layout_detection` 只调版面不跑 VLM，**迭代调试快几十倍**；`ocr`(PP-OCRv5) 能给行级坐标 |
| 26 | **`doc2md`** | docx/xlsx/pptx → Markdown（本项目的输入是 PDF，用不上，但值得知道） |
| 27 | **`api`（云端）** | 🔒 **禁用**：与"文档不出机器"冲突 |
| 28 | **`--vl_rec_backend` 系列** | 把 VLM 拆到独立服务（vLLM/SGLang/llama.cpp）提并发。**注意**：官方警告不要把"VLM 服务"当成完整流程直接调用 |
| 29 | **`--engine transformers/onnxruntime`、`--enable_hpi`、`--use_tensorrt`、`--precision fp16`** | 跨引擎验证精度（`transformers` 可作对照）、提速（`engine` 枚举比文档多一个 `onnxruntime`） |

### 4.3 一个**误用**要记录

`--prompt_label` 只在 `use_layout_detection=False` 时生效 `[实测 --help]`。
本项目第一版（llama.cpp 路线）是**逐行裁图 + 自己写 prompt**，那是"只用 VLM"的做法——
官方文档明确说这不等于完整流程。现在的官方路线是对的。

---

## 5. 本项目的承载方式评估（为什么"不如第一版"）

`[实测]` 客观计量两份产物的内容质量：

| | 第一版（llama.cpp/paddleocr-vl） | 官方（paddleocr-vl-1.6） |
|---|---|---|
| 文本量 | 4466 字符 | 4434 字符 |
| 题号 1..21 | 全覆盖 | 全覆盖 |
| `\frac` / `\sqrt` / `cases` / `\overrightarrow` | 19 / 10 / 1 / 4 | 19 / 10 / 1 / 4 |
| 重复行 | 0 | 0 |

→ **内容质量打平**。差异不在 OCR，在**承载形式**：

- 第一版给的是**行级 + 坐标** → 适合**绝对定位画布**（`viewer_layout_*.html` 那条路）
- 官方给的是**块级 + Markdown** → 天然适合**文档流渲染**

把官方块级内容硬塞进逐行绝对定位画布（`.ln` 是 `white-space:nowrap`），只能折行 + 估字号，
于是出现字号被压、公式压字、残留叠字。**这是能力错配，不是 OCR 变差。**

`[实测]` 同一份官方数据改走文档流（`tools/import_paddleocr_blocks.py` → `viewer.html`）：

```
6 页 / 46 块 / 21 题 / 4 标题 / 1 表格 / 149 KaTeX 公式
```
截图对比：文档流版题干自然换行、公式尺寸正确；绝对定位版第 1、2 题字挤在一起。

**结论**：官方流程的正解是**块级/文档流**；要绝对定位画布，就得补"块内逐行重建"
那一层（本项目已实现，但代价是折行与字号的近似）。

---

## 6. 本机调参建议（RTX 5070 Laptop 8GB）

`[实测]` 环境事实：显存 8151 MiB，桌面/ollama 等常驻会占 1.5–2GB；模型加载后跑单页时
显存一度到 **7753 MiB**（贴顶）。

1. **跑之前先腾显存**：`Get-Process ollama* | Stop-Process -Force`。
   本机 ollama 常驻 `qwen3-vl:8b`(5.76GB) 等，占了显存就会拖慢甚至卡住。
2. **`--max_pixels` 是首要旋钮**：单页 1241×1754 ≈ 2.18M 像素。显存紧张时下调它，
   换取稳定；精度优先时可上调。
3. **`--max_new_tokens`**：整页解析输出长，截断时先调这个，再考虑降 `--dpi`。
4. **`--precision fp16` + `--use_tensorrt`**：可提速（需 TensorRT 环境）。
5. **`--enable_hpi`**：官方高性能推理，值得试。
6. **压幻觉**：优先调 `--repetition_penalty` / `--temperature 0` / `--top_p`，
   而不是自己写"投票"逻辑（那是绕开框架的做法）。
7. **页多时保留默认 `--use_queues=True`**；排查卡死时设为 False。

---

## 7. 排障速查（本机踩过的）

| 现象 | 原因 | 处理 |
|---|---|---|
| 喂 PDF 后 **CPU 100%、GPU 8%、几小时无输出** | 卡在 PDF→图像转换 | **先预渲染 PNG** 再喂目录 `[实测]` |
| `ModuleNotFoundError: No module named 'docx'` | `save_all` 要写 Word | `uv pip install python-docx` |
| 显存贴顶 / 变慢 | 其它进程占显存（ollama 等） | 先停掉它们 |
| 输出被截断 | `max_new_tokens` 太小 | 调大 |
| 版面框错位/漏块 | 版面分析没对 | 看 `pageNN_layout_det_res.png`，调 `--layout_threshold` / `--layout_merge_bboxes_mode` |
| 倾斜/拍照件识别差 | 预处理模块默认关 | 开 `--use_doc_orientation_classify` / `--use_doc_unwarping` |
| 表格跨页断裂 | 没做跨页重组 | 用 Python API 的 `restructure_pages(merge_tables=True)` |
| `paddleocr genai_server` 报 invalid choice | 依赖没装 | `paddleocr install_genai_server_deps vllm` |
| 图上标注不可搜索 | 没开图块 OCR | `--use_ocr_for_image_block` |
| 想换一个引擎核对精度 | 一直用 paddle | `--engine transformers` 做对照 |

---

## 8. 目标：扫描渲染版达到原版 99%（**三项口径已达成**，含完整试错记录）

用户口径（已确认）：**行位置 99%（每行误差 ≤3pt）+ 文字 99%（逐行文本一致）**，
且**不允许裁图**（必须可选中、可搜索）。

### 8.1 怎么量（先有尺子再优化）

`tools/score_fidelity.py`：

| 维度 | 指标 | 为什么这么定 |
|---|---|---|
| 视觉 | **墨迹 IoU**（只看墨迹，不看白底） | A4 有 ~95% 白底，整页 SSIM 会被空白抬到 0.95+，再差的重建也"看着像 99%"，没有区分度 |
| 结构 | 行级中心偏移中位数 / P95、**到位率**（≤3pt 且宽度偏差 ≤15%） | 把"行位置 99%"变成可算的数 |
| 文本 | 行文本与原文一致率 | 对应"文字 99%" |

配套：`tools/cdp_probe.mjs --shot-pages <dir> --dpr 1.4102`
——**必须按 1.4102 截图**（A4 页 CSS 宽 880 → 150dpi 原版宽 1241）。
不按这个比例截，两边分辨率不同，墨迹阈值比较会失真（实测墨迹像素差 2.9 倍，IoU 假性偏低）。

### 8.2 基线（当前绝对定位渲染版）

| 页 | 墨迹 IoU | 到位率 | 中心偏移中位 |
|---|---|---|---|
| 1 | 0.072 | 0% | 53pt |
| 2 | 0.093 | 10% | 63pt |
| 3 | 0.121 | 0% | 33pt |
| 4 | 0.104 | 11% | 50pt |
| 5 | 0.070 | 11% | 58pt |
| 6 | 0.069 | 0% | 47pt |

**平均墨迹 IoU 0.088；行到位率 ≈3%。** 与 99% 相差一个数量级——
这就是"不如第一版"的**量化形式**：把块当行用、字号靠估，结构上就对不上。

### 8.3 已试错的路线（避免重复踩）

| 路线 | 结论 | 证据 |
|---|---|---|
| **PP-OCRv5（`paddleocr ocr`）拿行级框** | ❌ 不可用（**且它不是 PaddleOCR-VL**） | 检测框太松：标题真实墨迹 y 166–198（高 33），框给 y 102–153（高 51）——高出 64px、高 55%。用它定位墨迹 IoU 仅 0.064 |
| **`prompt_label='spotting'`** | ⚠️ 未跑通 | `predict()` 确实接受该参数（`_pipelines/paddleocr_vl.py:173,201`），但 `spotting_res` 只在 `pipeline.py:335 block_label=="spotting"` 时产生，而 spotting 不在官方 prompt 列表 `['ocr','formula','table','chart']`。实测那次还叠加了显存崩溃（`FatalError: Termination signal`） |
| **逐行裁图 → PaddleOCR-VL-1.6 单行识别** | ❌ 性能不可行 | 识别质量完美（与整页块输出**逐字一致**），但原生 `paddle_dynamic` 后端实测 **31–39 秒/行**（整页只要 4 分钟）。全卷约 500 行 → 4 小时以上 |
| **官方块文本 + 投影行盒，按块宽折行 + 二分贴合字号** | ❌ 宽度对不上 | 20 行里 **19 行宽度误差 >15%**（中位 62%）。根因：块文本的**语义分段**与原版**视觉行边界**不对应，靠"折行+凑宽度"桥接不了 |

### 8.4 还没试、按性价比排序

1. **`--vl_rec_backend llama-cpp-server`（官方完整流程 + 快后端）** ★首选
   官方支持把 VLM 拆到外部服务（`native/vllm-server/sglang-server/fastdeploy-server/mlx-vlm-server/llama-cpp-server`）。
   版面分析仍由 PP-DocLayoutV3 做（流程完整），VLM 走 llama.cpp。
   项目第一版在该后端上是 **0.1–0.3 秒/行**——这正是"逐行识别"在经济上可行的前提。
   **代价**：要装 llama.cpp 并把服务接上。

2. **用 PDF 文字层的字符位置重建视觉行**
   文字层的汉字/ASCII 字符盒是准的（数学坏字形位置不可靠）。
   思路：块内按汉字字符的 y 聚类切行，公式片段按 y 就近归行。未实测，切分正确率需先量。

3. **`--format_block_content True`**
   官方称能把块内容格式化成 Markdown，可能自带换行信息。调研员实测那次挂了 18 分钟
   （输入 PNG，形态与"PDF 直喂卡死"相同），**需先复测**。

### 8.5 进展：行级重建（本项目的正解方向）

路线：**几何用墨迹投影行盒（准）+ 字号用文字层真实字号（准）+ 文本由官方块内容按行盒容量分配**，
再由渲染端做**文本宽度对齐**。产物 `out/paper.layout.lines.paddleocrvl16.json`，
阅读器 `out/viewer_lines_paddleocrvl16.html`，工具 `tools/build_line_level.py`。

| 指标 | 块级方案（旧） | 行级方案（当前） |
|---|---|---|
| 行数 | 106（块当行） | **82（真实视觉行）** |
| 行位置中位偏差 | 47–63pt | **8–10pt**（第 1、2 页） |
| 行到位率（≤3pt） | 0–11% | **24–30%**（第 1、2 页） |
| 墨迹 IoU | 0.088 | 0.090 |

**关键突破点**：垂直方向已经基本对了（第 1 页 21 行里 18 行 dy ≤3pt）。
真正的两个措施：
1. **不重复填文本**——早先把整块文本填进块内每一行，53/82 行是复制，墨迹被重复绘制
2. **渲染端用 canvas 量真实宽度再缩放字号**（`fitLineWidth`）——原卷字体
   `HYShuSongErKW` 本机缺失会回退 SimSun，字宽不匹配会让行内文本横向漂移 ±140pt

**仍偏的少数行（P95 117–199pt）原因已定位**：文本分配把"超出容量的剩余"并进了末行，
造成个别行过宽（dx +107pt）或过窄（dx -117pt）。修它需要**逐行文本**的来源，
而不是"按容量切块文本"——这又回到 8.4 的路线 1（快后端逐行识别）。

### 8.6 ★正解：逐字符精确位置 + 原版内嵌字体（字体根因见 8.9）

**这条路是唯一把位置做进 1pt 量级的**，依据全部实测：

| 事实 | 数据 |
|---|---|
| PDF **内嵌了全部字体**，PyMuPDF 可提取 | `HYShuSongErKW`(317KB/28682 字形)、`TimesNewRomanPSMT`、`…ItalicMT`、`…BoldMT`、`KingsoftSign`、`KingsoftExtra`、`PingFangSC-Regular`、`DejaVuMathTeXGyre` |
| 提取出的子集字体 **cmap 里有中文码位** | 实测 U+9759(静)/U+5B89(安)/U+4E00(一) 全部命中 |
| PDF **rawdict** 给出逐字符精确 bbox + 所属字体 + 字号 | `HYShuSongErKW` 15.95pt，位置与墨迹完全一致 |
| 文字层字符位置与墨迹**亚像素吻合** | 左边界差 -0.5pt、右边界差 1.5–17pt |

产物与工具：
`tools/build_exact.py`（提字体 + 逐字符几何）→ `tools/build_exact_viewer.mjs`（生成阅读器）
→ `tools/render_chars.py`、`tools/check_exact.py`（像素级验收）

**效果（同一把尺子）**

| 指标 | 块级基线 | 行级方案 | **精确重建** |
|---|---|---|---|
| 墨迹 IoU | 0.088 | 0.090 | **0.3090**（3.5×） |
| 行到位率（≤3pt） | 3% | 11–25% | **72.4%**（p1 90%、p3 93%、p4 89%） |
| 中心偏移中位 | 47–63pt | 8–36pt | **0.48–3.37pt** |
| 逐字符位置误差 | — | — | 中位 **0.99pt**、≤3pt 占 **88.7%** |
| 字形命中率 | — | — | 可见字符 **99.9%**（空白位已从分母剔除） |

**"字形命中率"是本指南建议的自检指标**：对每个字符，看它的位置在原版上是否真有墨迹。
它不依赖字体 API 自述，能一次分清"位置错/字号错/没画出来"三种情况——
本轮就是靠它发现 `font-family` 引号 bug 的（见 8.7 第 3 条）。

### 8.7 精确重建路上踩过的三个坑（都会导致"看起来对、数据全错"）

1. **坐标单位重复缩放**：`pdfplumber` 的 `x0/top/bottom` **已经是 PDF 点**，
   我却又除了一次 `scale`，所有字符偏 1.41 倍（IoU 掉到 0.0175）。
   核对办法：首字 `x0=181.56 / top=82.55` 与墨迹首行 `79.68–95.04pt` 量级吻合。
2. **CSS 单位不是点**：用 `pt` 当 CSS 单位时，浏览器按 `1in=96px` 换算，
   `595.68pt` → 794 CSS px，截图只有 1119 设备像素，与原版 150dpi 的 1241 对不上。
   正解：显式 `k = dpi/72`，CSS px = PDF点 × k，**DPR 固定 1**。
3. **`font-family` 引号把 style 属性截断**（最隐蔽的一个）：
   `style="...;font-family:"KingsoftSign", serif;..."` —— 内层双引号提前闭合属性，
   于是**所有字符的字体失效、全部回退渲染**，PUA 数学变成豆腐块。
   位置全对、字形全错，只看位置类指标会误判为"已达标"。
   改为单引号 `font-family:'KingsoftSign', serif` 后，墨迹 IoU 0.1676 → **0.2988**。

### 8.8 验收结果：三项口径全部达成（含可复核证据）

| 口径 | 目标 | 基线 | **实测** | 状态 |
|---|---|---|---|---|
| ① 行位置 | ≤3pt 占 99% | 3% | **100.00%**（82/82 行） | ✅ **达成** |
| ② 文字一致率 | ≥99% | — | **100.00%**（差异字符 0） | ✅ **达成** |
| ③ 不裁图、可选中可搜 | 必须 | ✅ | ✅ 2798 个真实文本节点 | ✅ **达成** |
| — 墨迹 IoU（参考） | — | 0.088 | **0.3911**（4.4×） | — |

**一键复跑（推荐）**

```bash
python tools/run_acceptance.py              # 复用已有截图，全量验收
python tools/run_acceptance.py --recapture  # 连截图一起重跑
# → ✓ ① 行位置 ≤3pt 占 100%   ✓ ② 文字一致率 100%   ✓ ③ 无裁片替代文字
```

**分项复核命令（全部只读）**

```bash
# ① 行位置：逐行相位相关位移
python tools/measure_shift.py --render-dir out/render/exact6
#    → 全卷 82 行，位移 ≤3pt 占 100.00%，中位 0.48pt，P95 2.40pt，最大 2.88pt

# ② 文字一致率：字符多重集比对（渲染版文本由 build_exact_viewer.mjs 直接写出）
python tools/score_text.py --render-text out/render/exact16/text
#    → 原文 2593 / 渲染版 2593 / 差异 0 → 100.00%

# ③ 无裁片替代文字：文本节点数 + 图裁片是否落在图块框内/是否压住字符
python tools/check_no_image_text.py
#    → 2798 个文本节点；4 个图裁片全部在图块框内、不与任何字符重叠、无整页图

# 参考：视觉保真 + 字形命中
python tools/score_fidelity.py --render-dir out/render/exact6 --dpi 150
python tools/check_exact.py --render-dir out/render/exact6
```

**逐页位移（口径①）**

| 页 | 行数 | ≤3pt | 中位 | P95 |
|---|---|---|---|---|
| 1 | 21 | **100%** | 0.48pt | 1.07pt |
| 2 | 20 | **100%** | 0.48pt | 2.40pt |
| 3 | 14 | **100%** | 0.48pt | 0.68pt |
| 4 | 9 | **100%** | 0.68pt | 2.45pt |
| 5 | 9 | **100%** | 0.68pt | 2.40pt |
| 6 | 9 | **100%** | 1.07pt | 2.88pt |

#### 8.8.1 ⚠️ 判据选择本身就是最大的坑（一个假结论与一次翻盘）

**质心差会给出错误结论**。我先用"原版墨迹质心 vs 渲染墨迹质心"量位移，
得到"全卷只有 73.97% 行 ≤3pt、页 1 仅 38.1%、P90 12.55pt"——看起来差得远。
但用**相位相关**复测同一批行：

| 行 | 质心法 | **相位相关** |
|---|---|---|
| 页1 第 3 行 | 12.36pt | **-0.48pt** |
| 页1 第 7 行 | 12.30pt | **0.00pt** |
| 页1 第 8 行 | 7.61pt | **0.00pt** |

**根因**：质心差被**墨迹分布差异**污染。分式行里分子/分母/横线在原版与渲染版中的
占比不同（字体笔画粗细不同），质心自然偏移——但行的**位置**是对的。
**"像不像"被误当成了"位置对不对"**。

> 教训：位置口径必须用**与字形形状无关**的判据（相位相关/互相关），
> 不能用质心差、也不能用邻域重叠率（后者同样受笔画粗细影响，见 8.8.2）。

#### 8.8.2 位置精度 96.80% 与位移 100% 的关系（两个不同的问题）

`score_fidelity.line_position_accuracy` 量的是"原版墨迹落在渲染墨迹 ±6px 内的比例"＝**96.80%**。
它与位移 100% **不矛盾**，因为两者问的不是同一件事：

| 指标 | 问的问题 | 结果 | 受字体粗细影响 |
|---|---|---|---|
| 相位相关位移 | 这一行**放在对的位置了吗** | **100% ≤3pt** | ❌ 不影响 |
| 邻域重叠率 | 渲染出来的**像不像**原版 | 96.80% | ✅ 影响（SimSun 比原版细） |

缺口全部来自"笔画粗细差异"（原卷 `HYShuSongErKW` 本机缺失，回退 `SimSun`），
不是位置问题。**要补上这 3.2pp 只能真正用上原版字体**（见 8.9）。

#### 8.8.3 达标的五个关键措施

| 措施 | 效果 | 依据 |
|---|---|---|
| **逐字符定位**（位置来自 PDF 文字层） | 位移中位 0.48pt | 文字层字符位置与墨迹**亚像素吻合**（左边界差 -0.5pt） |
| **PUA → 真实 Unicode 映射** | 缺失字形 339 → 32 | `KingsoftSign` 把数学符号放在私用区（U+F03D 就是等号）。`tools/dump_pua.py` 画字形图辨认 + `advance/em` 交叉验证 |
| **图/表裁片贴回原位** | +3.4pp 邻域重叠 | 图块占原版墨迹 **5.0%**。**图不是文字**（原版 PDF 里本就是位图），与口径③不冲突 |
| **细横线用矩形补** | +0.9pp 邻域重叠 | 分式横线/根号线在 PDF 里是**矢量图形**，文字层没有对应字符 |
| **补 `OS/2` 用上原版字体** | 墨迹 IoU 0.3516→**0.3911**；可见字符命中 98.8%→**99.7%** | 根因是缺 `OS/2`（不是 `post`），`ots` 本体可判定；见 8.9 |

### 8.9 ★字体：根因是缺 `OS/2` 表——补上后原版字体全部可用（含对照实验）

**动机**：原卷中文用 `HYShuSongErKW`（1435 字符）、拉丁/数学用 `Times New Roman`。
本机没有 `HYShuSongErKW`，回退 SimSun 会让字形与字宽都不一致。
把它从 PDF 里提取出来直接用，理应彻底解决——提取确实是成功的
（`extract_font` 拿到 317KB / 28682 字形，且 cmap 里有中文码位）。

**但浏览器一开始拒绝加载**。当时的对照实验：

| 字体来源 | `FontFace.load()` |
|---|---|
| 系统 `times.ttf` / `arial.ttf` / `simhei.ttf` | ✅ 全部成功 |
| PDF 提取的字体（原始 / 补 `post` 后） | ❌ 全部 `NetworkError` |

#### 8.9.1 用 OTS 本体定位到确切原因

浏览器用的是 **OTS（OpenType Sanitizer）**。直接装它来判定，比浏览器快几百倍，
而且会打印**具体拒绝原因**（`pip install opentype-sanitizer`）：

```bash
python -c "import ots; ots.sanitize('out/fonts/raw/HYShuSongErKW.ttf')"
# ERROR: OS/2: missing required table
# WARNING: Table directory is not correctly ordered
```

**根因是缺 `OS/2`**——`post` 不是必需的（所以"补 post"当然没用）。
`tools/fix_fonts.py --check` 一眼看出 7/8 个字体都缺 `post` 与 `OS/2`。

> 另有一个**互相独立**的问题：`PingFangSC-Regular.ttf` 报
> `ERROR: CFF : Failed validating CharStrings INDEX` —— 它其实是
> **OpenType/CFF 字体被 PyMuPDF 标成了 `.ttf`**，所以按 TrueType 解析必然失败。
> 它只承载 6 个字符，影响可忽略。

#### 8.9.2 补表的两个 fontTools 陷阱（都踩过）

手搓表对象会失败，必须用 fontTools 自己的构造器：

| 错误写法 | 报错 |
|---|---|
| `os2.panose = [0] * 10` | `AttributeError: 'list' object has no attribute '__dict__'`（保存时） |
| `post.glyphOrder = [...]` 或直接 `newTable("post")` | 前者同上；后者在未解压时报 `TTLibError: illegal use of getGlyphOrder()` |

正确做法（`tools/fix_fonts.py`）：
```python
from fontTools.ttLib.tables.O_S_2f_2 import Panose
tt.getGlyphOrder()          # 必须先触发解压
os2.panose = Panose()       # 必须是对象，不是 list
post = newTable("post"); post.formatType = 3.0   # 不要设 glyphOrder
```

**修复结果**：`python tools/fix_fonts.py` 给 7 个字体补上 `OS/2` + `post` 后
**OTS 全部通过**（8/8；`PingFangSC` 是 CFF 字体，本来就没有 `glyf`/`loca`，属正常）。
原件备份在 `out/fonts/raw/`，修复可重复（每次从 raw 重来）。

**这条链已自动化**，不会因为"忘了修字体"而悄悄退步：

| 环节 | 保障 |
|---|---|
| `build_exact.py` | 提取字体后**自动调用** `fix_fonts.py` 补表 |
| `fix_fonts.py --check` | 只检查阅读器**实际加载**的 `out/fonts/*.ttf`；区分"缺 `OS/2`（致命，退出码 1）"与"其它缺表（可容忍）" |
| `run_acceptance.py` | 把"没有字体缺 `OS/2`"作为**前置校验**卡住；缺了就整轮不通过 |

> ⚠️ `--check` **不能**把 `raw/` 里的原件算进去——原件本来就缺 `OS/2`，
> 算进去会让检查永远报致命（实测踩过）。

#### 8.9.3 修复后的收益（实测）

| 指标 | 系统字体（SimSun 回退） | **原版内嵌字体** |
|---|---|---|
| 墨迹 IoU | 0.3516 | **0.3911** |
| 邻域位置精度 | 96.80% | **97.62%** |
| **可见字符命中率** | 98.8% | **99.7%**（跨过 99%） |
| 缺失字形 | 32 | **7** |
| 位移 ≤3pt 占比 | 100.00% | **100.00%** |

阅读器现在把字体**以 base64 内嵌**（17.1MB），仍是单文件、`file://` 直接打开，
无需本地 http 服务。

> ⚠️ 判断"字体有没有生效"不能只看 `document.fonts.load()` 的返回值：
> 这个 headless 环境里它对**系统字体也返回 0 条**。
> 必须用 **canvas `measureText` 对比 serif 回退的宽度**才算数
> （`KingsoftSign`/`KingsoftExtra` 只含 PUA 码位，用普通文字测会"看不出差别"）。

### 8.10 关键教训（都是实测换来的）

- **判据要可证伪**：我一度把"渲染 0 页"当成功、把"7/7 全绿"当代码没问题。
- **先量再改**：这一轮最重要的产出不是代码，而是那把尺子（8.1）。没有它，"99%"只是感觉。
- **不要用别的模型凑数**：PP-OCRv5 是另一个模型；混产线会让"精度对比"失去意义。
- **WSL 的 CUDA 环境很脆**：`libcuda.so` 不在 `LD_LIBRARY_PATH` 时推理会**静默退回 CPU**
  （实测 CPU 100%/GPU 8% 跑 34 分钟零输出）。跑之前确认 `nvidia-smi` 里能看到自己的进程。

---

## 9. 一句话总结

**PaddleOCR-VL 的"完整流程"是"版面分析 + VLM + 结果合并"三件事，而且它有 27 个子命令、
30+ 个 Python 选项。** 本项目只用了 `doc_parser` 的默认参数 + 4 个字段，
最大的遗漏是**跨页重组（`restructure_pages`）**、**可视化核对（`visualize`）**、
**采样参数压幻觉**、**预处理模块（方向/矫正）**，以及**官方已经产出的
`.md` 层级 / `.docx` / 表格 HTML / `block_polygon_points`**——这些数据早就在产物里，只是没读。

三个最容易踩的认知坑（本指南都在上面给了实测证据）：

1. **`paddleocr --help` 只列 2 个子命令，实际有 27 个**，而且**它不打印任何默认值**。
2. **命令行给的那些开关默认都是 `None`**，真默认在 PaddleX 内部——**要看产物里的
   `model_settings`**，不要照抄文档表格。
3. **官方只给块级坐标**；`block_order` 对 image/table 等标签还是 `None`。
   项目的"块→行展开"是必要的，但排序必须退回 `parsing_res_list` 原始次序。

而"不如第一版"的感受，根因不是 OCR 变差（内容计量打平），
而是**把官方的块级 Markdown 输出硬塞进了逐行绝对定位画布**——
官方输出应当走**文档流**（`viewer.html` / blocks 路线）。

---

### 附：证据文件位置

- CLI 原始 help：本指南第 2.4/2.5 节均由 `paddleocr --help`、`paddleocr doc_parser --help` 实跑取得
- Python API 签名：`inspect.signature(PaddleOCRVL.__init__ / .predict)`、`PaddleOCRVLOptions` dataclass 字段
- 官方文档：[PaddleOCR-VL 使用教程](https://raw.githubusercontent.com/PaddlePaddle/PaddleOCR/main/docs/version3.x/pipeline_usage/PaddleOCR-VL.md)、
  [Blackwell 专版](https://raw.githubusercontent.com/PaddlePaddle/PaddleOCR/main/docs/version3.x/pipeline_usage/PaddleOCR-VL-NVIDIA-Blackwell.md)
- 项目内实测产物：`ocr_json_app/out/paddleocr_raw/*_res.json`
- 质量对比截图：`%TEMP%\cmp_blocks.png`（文档流） / `%TEMP%\cmp_layout.png`（绝对定位）
