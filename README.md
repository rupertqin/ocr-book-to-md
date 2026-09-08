# 扫描书转 Markdown（OCR → 图 / 标题 / 正文三分）

把扫描版书籍的页面图片（PNG）自动转换为结构化 Markdown，输出**正文段落**、**章节/小节标题**、**插图与表格**三类元素。纯本地运行，无大模型、无云端依赖，**确定性输出**（同一输入必然得到字节级一致的产物）。

采用 **document-factory 的 `projects` 模式 + `pyproject.toml` + `src` 布局 + `.env` / `config.yaml` 配置**重构。最终章节 Markdown 产出为 `output/content/article_N.md`，可**直接嵌入 document-factory 原有的 PDF 制作流程**（`python main.py pdf -p <项目>`）生成 PDF。

```
输入: 686 张扫描页 p3.png ... p692.png
输出: 20 章 Markdown + 115 张裁图 + 每页结构化 blocks JSON + PDF（可选）
```

> 章节边界（前言/编/章/参考文献的页范围）**自动从 OCR 标题检测**：扫描每页 blocks 中的标题行（"第X章" / "第X编" / "参考文献"），相邻标题页之间构成一段。不再需要手写页范围表（本书自动检测出 5 编 + 13 章 + 前言 + 参考文献）。

---

## 为什么 Python + Swift 混合？

这是**技术选型的自然结果，不是刻意为之**：

| 阶段                         | 语言                      | 原因 |
| ---------------------------- | ------------------------- | ---- |
| OCR（文字识别）              | **Swift**（macOS Vision） | `VNRecognizeTextRequest` 是苹果原生 API，**只能从 Swift 调用**；本地中文 OCR 中识别质量最好（免费、离线，687 页约 13 分钟）。 |
| 布局分析（图/标题/正文分类） | **Python**                | OCR 之后全是数据活：图像裁剪（PIL/numpy）、文字像素颜色提取、行聚类、正则、JSON 组装。Python 生态成熟、迭代快。 |

**两者如何协作**：Swift 只负责产出每页的 OCR 结果（文本 + 归一化坐标 JSON），写入 `output/ocr/pages/` 与 `output/ocr/pages_json/`；Python 只读取这些文件做后续分析。**中间以 JSON 文件为协议解耦**——OCR 引擎可换，布局分析也可单独替换，互不影响，且每步产物都落盘、便于调试。

---

## 目录结构

```
ocr-book-to-md/
├── main.py                       # 统一 CLI 入口（ocr/detect/build/pdf/pipeline 等）
├── pyproject.toml                # 项目配置与依赖（src 布局）
├── requirements.txt              # Python 依赖（Pillow、numpy、pyyaml）
├── README.md
├── .env                          # 工作区级环境变量（可选）
├── .gitignore
├── src/ocr_book_to_md/           # 核心包（迁移自旧扁平脚本）
│   ├── __init__.py
│   ├── cli.py                    # 命令路由
│   ├── config.py                 # .env + config.yaml 配置管理
│   ├── project.py                # projects 模式路径管理
│   ├── ocr.py                    # ① OCR 引擎封装（Swift + 预处理 + 崩溃兜底）
│   ├── ocr_batch.swift           # ① OCR 引擎源码（macOS Vision，zh-Hans + en）
│   ├── detect.py                 # ② 图/标题/正文三分检测（核心算法）
│   ├── build_md.py               # ③ 组装章节 Markdown + article 目录
│   └── pdf.py                    # ④ 调用 document-factory 生成 PDF
└── projects/                     # 项目目录（每个书一个独立项目）
    └── <书名>/
        ├── config.yaml           # 项目配置（唯一配置来源）
        ├── .env                  # 敏感信息
        ├── assets/          # 输入截图
        └── output/               # 全部产物
            ├── ocr/              # ① OCR 产物
            ├── blocks/           # ② 布局中间产物
            ├── figs/             # ② 裁图
            ├── md/               # ③ 章节 Markdown（传统目录，便于复核）
            └── content/          # ③ document-factory 输入
                ├── article_0.md ... article_N.md
                └── images/       # 裁图（article 内引用 images/xxx.png）
```

---

## 快速开始（一键）

```bash
# 1. 初始化项目（生成 config.yaml + .env + assets/）
python main.py init -p ./projects/进化心理学

# 2. 把书的截图放入 projects/进化心理学/assets/

# 3. 一键全流程（最终 PDF 方式默认 text，也可 --pdf img/layed）
python main.py pipeline -p ./projects/进化心理学

# 只到流程中间产物为止，不生成 PDF
python main.py pipeline -p ./projects/进化心理学 --no-pdf
```

### 分步执行（调试/换书时用）

```bash
# ① OCR：扫描页 → 文本 + 坐标（复用缓存，强制重跑加 --force）
python main.py ocr -p ./projects/进化心理学

# ② 检测：坐标 JSON + 原图 → 图/标题/正文三分
python main.py detect -p ./projects/进化心理学

# ③ 组装（仅文字流需要）：blocks → 章节 Markdown + article 目录
python main.py build -p ./projects/进化心理学

# ④ PDF：三种方式用 --type 指定（按需选用）
python main.py pdf -p ./projects/进化心理学 --type text        # 文字流（document-factory，需先 build）
python main.py pdf -p ./projects/进化心理学 --type layed   # 文字置于图（按原书坐标，需先 ocr+detect，无需 build）
python main.py pdf -p ./projects/进化心理学 --type img         # 原图片（纯图，无需 OCR/detect/build）
```

### CLI 参数速查（短参数一览）

有短参数的选项（其余均只有长参数）：

| 短 | 长 | 适用命令 | 说明 |
|----|-----|---------|------|
| `-p` | `--project` | 全部命令 | 项目目录 |
| `-t` | `--type` | `pdf` | PDF 方式：text / img / layed |
| `-f` | `--force` | `ocr`, `pipeline` | 忽略缓存重跑（OCR / 全流程） |

只有长参数、**无短参数**的选项：

| 长 | 适用命令 | 说明 |
|----|---------|------|
| `--images` | `ocr`, `detect`, `pdf`, `imgpdf`, `pipeline` | 截图目录 |
| `--start` / `--end` | `ocr` | 起始 / 结束页 |
| `--chapters` | `build` | 人工章节结构 JSON |
| `--output` | `pdf`, `layedpdf` | 输出 PDF 路径 |
| `--font-size` / `--font` | `pdf`, `layedpdf` | 正文字号 / 字体族 |
| `--no-justify` / `--width-scale` / `--letter-spacing` / `--punct-compress` | `pdf`, `layedpdf` | layed 排版微调 |
| `--title` | `pdf`, `imgpdf` | PDF 标题（img 用） |
| `--no-pdf` | `pdf`, `imgpdf`, `pipeline` | 只准备结构 / 到中间产物为止 |
| `--pdf` | `pipeline` | 最终 PDF 方式 |
| `--version` | 顶层 | 版本号 |

---

## 三种 PDF 制作方式（`pipeline --pdf`）

本项目提供 **三种 PDF 制作方式**，通过 `pipeline` 命令的 `--pdf` 参数指定，也可在 `config.yaml` 配 `pipeline.pdf` 作为默认流程。三者作为流水线的最终输出方式，可自由切换。

**文件名规则**：`<书名>.<方式后缀>.unc.pdf`（未压缩），压缩后去掉 `.unc` 变 `<书名>.<方式后缀>.pdf`。`text` 为默认无方式后缀；`.unc` 是 document-factory 的压缩标记，放最后。

| 方式 | 未压缩 | 压缩后 |
|------|--------|--------|
| `text` | `<书名>.unc.pdf` | `<书名>.pdf` |
| `img` | `<书名>.img.unc.pdf` | `<书名>.img.pdf` |
| `layed` | `<书名>.layed.unc.pdf` | `<书名>.layed.pdf` |

| `--pdf` 值 | 名称 | 原理 | 文字可复制 | 版式贴近原书 |
|-----------|------|------|:---:|:---:|
| `text` | 文字流 | OCR 出文字 → Markdown → document-factory 排版 | ✅ | ❌（重排） |
| `img` | 原图片 | 页面图直接转 PDF（封面 cover.jpg），走 cover_images | ❌（纯图） | ✅（原样） |
| `layed` | 文字置于图 | 用 OCR 文字坐标 + detect 图片区域，按原书位置绝对定位 | ✅ | ✅（按坐标） |

> `--pdf` 取值用**方式名**（`text` / `img` / `layed`），对应底层命令 `pdf` / `imgpdf` / `layedpdf`。

```bash
# 全部流程（默认读 config.pipeline.pdf，未配置则 text）
python main.py pipeline -p ./projects/<书名>

# 指定最终 PDF 方式
python main.py pipeline -p ./projects/<书名> --pdf text        # 文字流（→ pdf）
python main.py pipeline -p ./projects/<书名> --pdf layed   # 文字置于图（→ layedpdf）
python main.py pipeline -p ./projects/<书名> --pdf img         # 原图片（→ imgpdf）
```

> 三种方式都是 `pipeline` 的可配置流程参数，`pipeline --pdf text` 等价于原来的 `all` 一键流程。

- **text（文字流）**：`ocr → detect → build → pdf`。文字可复制/搜索，但版式重排。适合要可检索文字、不介意版式。
- **layed（文字置于图）**：`ocr → detect → layedpdf`。按原书坐标摆放文字+图片，既贴近原书又文字可复制。适合既要版式又要文字。
- **img（原图片）**：直接 `imgpdf`（无需 OCR）。纯扫描图原样。适合只要原书扫描件。

### 统一的目录（TOC）机制：三种方式走同一套 document-factory 流水线

`text`、`img`、`layed` 三种方式**共用一套**章节切分 + 自动目录生成——它们的差异只是"每章用什么内容渲染"，目录本身是**统一**的：

- **章节切分**（同一份）：都走 `build_md.auto_chapters()`，从 OCR 检测到的标题（"第X章"/"第X编"/"参考文献"）自动推导章节边界。
- **多篇文章 → 自动 TOC**（同一个 document-factory 机制）：每章切成一篇文章（`article_N.md`），丢给 document-factory 的 `create_pdf`，由它自动生成 TOC 页面（章节标题 + 目标页码），三本 PDF 的 TOC 内容**逐字一致**。
- **差异只在"每章跳转到的内容"**：
  - `text`：跳到该章的 OCR 重组后的文字段落；
  - `img`：跳到该章的**单张原图**（整页铺满）；
  - `layed`：跳到该章的**绝对定位混排页**（文字 + 图片按原书坐标）。

| 方式 | OCR 章节切分 | 多文章 markdown | document-factory 渲染 | TOC 标题 | TOC 页码 |
|------|:---:|:---:|:---:|:---:|:---:|
| `text` | ✓ | `output/content/article_N.md` | `<h1>` 文字段落 | ✓ 一致 | ✓ 一致 |
| `img` | ✓ | `output/content_img/article_N.md` | `<img>` 单张原图 | ✓ 一致 | ✓ 一致 |
| `layed` | ✓ | `output/content_layed/article_N.md` | `<section class="layed-page">`（绝对定位） | ✓ 一致 | ✓ 一致 |

> 这个统一的代价是：layed 不再走"自定义 weasyprint 直渲染"管线，而是把内容做成 markdown 文章交给 document-factory。`pdf.page_margin` 在 layed 流下被**临时**写为 `'0'`（因为 `.layed-page` 占满整个 A4，留白由我们自己的 scale/offset 计算驱动），调用结束后恢复。

在 `config.yaml` 配置默认流程：

```yaml
pipeline:
  pdf: layed   # text / img / layed（命令行 --pdf 可覆盖）
```

### ① 文字流（`pdf --type text`）

走"OCR → 布局分析 → Markdown → document-factory"完整流水线，产出**可复制的章节文字 PDF**。

```bash
# 仅生成文字流 PDF（需先跑过 ocr/detect/build）
python main.py pdf -p ./projects/<书名> --type text

# 或一键全流程
python main.py pipeline -p ./projects/<书名> --pdf text
```

产物：`output/dist/<书名>.unc.pdf`（text 流未压缩，默认无方式后缀）；压缩后 `<书名>.pdf`。中间产物 `output/md/`（章节 Markdown）、`output/content/`（article_N.md，供 document-factory 读取）。

相关配置（`config.yaml` 的 `pdf:` 段）：`title`（封面标题）、`page_margin`（页边距）、`engine`（weasyprint/reportlab）、`chunk_articles`（分册）、`statement`（免责声明）等。

### ② 原图片（`pdf --type img`）

图片文件夹**直接做成 PDF**，封面用文件夹下 `cover.jpg`，走 document-factory 的 `cover_images`（每图一页、整页铺满 A4）。**无需 OCR**。

```bash
# 图片目录结构（页面图任意命名，按文件名数字排序；封面 cover.jpg 可选）
页面图目录/
├── cover.jpg      # 封面（第一页，可选）
├── p1.png / p2.png / p3.png ...

# 仅生成原图片 PDF（默认用项目 assets；也可用 -i 指定别的目录）
python main.py pdf -p ./projects/<书名> --type img
python main.py pdf -p ./projects/<书名> --type img -i ./页面图目录
python main.py pdf -p ./projects/<书名> --type img --no-pdf   # 只准备结构不生成

# 或一键全流程
python main.py pipeline -p ./projects/<书名> --pdf img
```

产物：`output/dist/<书名>.img.unc.pdf`（未压缩）；压缩后 `<书名>.img.pdf`。封面无边距 + 内容页有边距。

> `cover_images` 在 document-factory 是"整页铺满无边距"（适合封面/插页）；内容页走正文文章（有 `page_margin` 边距）。

### ③ 文字置于图（`pdf --type layed`）

用 OCR 的**文字坐标** + detect 的**图片区域**，把文字行与裁图按**原书位置**用 CSS `position:absolute` 绝对定位，排版成接近原书、且**文字可复制**的 PDF。**需先跑过 `ocr`+`detect`**。

```bash
# 仅生成文字置于图 PDF（需先跑过 ocr/detect）
python main.py pdf -p ./projects/<书名> --type layed
python main.py pdf -p ./projects/<书名> --type layed -o output/自定义.pdf --font "PingFang SC"
python main.py pdf -p ./projects/<书名> --type layed --pages 52 53   # 只渲染指定页（调试）

# 或一键全流程
python main.py pipeline -p ./projects/<书名> --pdf layed
```

产物：`output/dist/<书名>.layed.unc.pdf`（未压缩）；压缩后 `<书名>.layed.pdf`。封面 cover.jpg 整页铺满 + 内容页按坐标混排。

**与其他流共用 document-factory 多文章 + 自动 TOC**：layed 走与 `text`/`img` 流**同一套**章节切分（`auto_chapters()`）和**同一套**自动目录生成机制——把每章做成一篇 markdown 文章交给 document-factory 渲染，document-factory 自动产出**全文一致的 TOC**（章节标题 + 页码来自 OCR 自动检测）。这意味着三种方式下：

| 方式 | TOC 来源 | 视觉 |
|------|---------|------|
| `text` | document-factory 多文章 + 自动 TOC | 重排 |
| `img` | document-factory 多文章（按章节切分）+ 自动 TOC | 原图缩进边距 |
| `layed` | document-factory 多文章（按章节切分）+ 自动 TOC | 原图按坐标叠加文字 |

三种方式**TOC 内容（章节标题 + 页码）逐字一致**，因为章节切分和 OCR 数据完全相同，仅"每章跳转到的目标页"不同（text 页号 vs 原图页号，但 OCR 给出的页号一致）。

**排版配置**（`config.yaml` 的 `pdf:` 段，`layedpdf` 专用）：

```yaml
pdf:
  font_family: "Hiragino Sans GB"   # 字体族
  page_margin: "1.5cm 2cm"          # 页边距
  justify: true                     # 段落两端对齐
  width_scale: 0.96                 # 文字容器宽度系数（<1 适配宽字体）
  letter_spacing: "-0.02em"         # 整体字距压缩
  punct_compress:                   # 标点压缩（按类别分开设置）
    "，、。": "-0.3em"
    "？！；：": "-0.25em"
    "（）《》【】…—·": "-0.2em"
```

---

## projects 模式（每个书一个项目）

对齐 document-factory 的约定：**每个文件夹是一个独立项目，可复制迁移**。

`projects/<书名>/config.yaml` 全配置项：

```yaml
book:
  name: "进化心理学"       # 书名（默认取项目目录名）
  images_dir: "assets"  # 截图目录（相对项目目录解析，默认 assets）
  start_page: null        # 起始页（默认自动探测）
  end_page: null          # 结束页（默认自动探测）
  force_ocr: false        # 忽略 OCR 缓存，强制重跑

detect:                   # 布局检测参数
  min_heading_ratio: 1.28 # 标题字号/正文字号 最小比例（跨页面统一基准，相对全书正文）。
                          # 真标题字号明显大于正文（如"第X章"/"悖论系列N" 1.29+）；
                          # 正文里独立成行的诗歌/脚注行字号接近正文（1.07~1.27）会被过滤。
                          # 调高过滤更多误判，调低保留更多小字号标题。

pdf:                      # document-factory PDF 流程（以库方式调用，需先 pip install -e）
  title: ""               # PDF 封面大标题（默认书名）
  subtitle: null
  statement: null         # 封面下方免责声明
  engine: weasyprint      # weasyprint | reportlab
  chunk_articles: null    # 每册最大章数（null=不分册）
  jobs: 4
  mode: full              # full | incremental
  page_margin: "1.5cm 2cm" # 正文页边距（CSS margin 简写，封面 cover_images 不受影响）
```

### 配置优先级

```
命令行参数 > .env > config.yaml > 默认值
```

`.env` 变量命名规则：config.yaml 点号路径转大写下划线，例如 `pdf.title` → `PDF_TITLE`、`book.images_dir` → `BOOK_IMAGES_DIR`。敏感信息（如 document-factory 的 COOKIE）放 `.env`。

---

## 嵌入 document-factory 的 PDF 制作流程

`build` 步骤把章节产出为 **document-factory 的标准输入**：

```
projects/<书名>/output/content/
├── article_0.md            # 每章一篇文章（第 N 章 = article_N.md）
├── article_1.md
├── ...
└── images/                 # 裁图（article 内的图片引用统一为 images/xxx.png）
```

`pdf` 步骤以**库方式**调用 document-factory（`import document_factory` 的 `create_pdf`），无需手动调 CLI：

```python
from document_factory.pdf_generator import create_pdf
```

因此你可以：
- 直接用 `python main.py pdf -p <项目>` 一键生成 PDF；
- 或 `create_pdf` 的 `**kwargs` 透传 document-factory 的全部能力（分册、shuffle/sand、publications、压缩等），参数可直接传给 `make_pdf`，无需在本项目重复定义。

> document-factory 以库方式调用，需先 `pip install -e <document-factory 目录>`（开发模式改动即时生效）。

---

## 图片直转 PDF（`imgpdf`，无需 OCR/MD）

当手头只有扫描页图片、不需要转文字时，可用 `pdf --type img` 命令把图片文件夹**直接做成 PDF**，封面用文件夹下的 `cover.jpg`，复用 document-factory 的 `cover_images` 机制（每图一页、整页铺满 A4）。

```bash
# 图片目录结构（页面图任意命名，按文件名数字排序；封面 cover.jpg 可选）
页面图目录/
├── cover.jpg      # 封面（第一页，可选）
├── p1.png / p2.png / p3.png ...
```

```bash
# 一键：图片 → PDF（默认用项目的 assets；也可在 config 配 book.images_dir）
python main.py pdf -p ./projects/影印本 --type img

# 从别的目录生成（-i 可覆盖默认图片目录）
python main.py pdf -p ./projects/影印本 --type img -i ./页面图目录

# 只准备结构不生成 PDF（调试）
python main.py pdf -p ./projects/影印本 --type img --no-pdf
```

> `-i/--images` 参数可选：图片目录优先级为 `-i 命令行 > config.book.images_dir > 项目 assets`。图片放在项目 `assets/` 下（或配置好 `book.images_dir`）时，可完全省略 `-i`。

实现原理：
1. 扫描图片目录，`cover.jpg`（或 `cover.png`）作为封面，其余图片按页号排序；
2. **封面**走 document-factory 的 **`cover_images`**（`pdf.cover_images`）：每张图一页、**整页铺满无边距**；
3. **内容页**走**正文文章**：生成单篇 `article_0.md`（标题=书名），每张图一个 `<div class="page-break">`，**每页一图、带正文页边距**；
4. config 写入 `pdf.show_cover=false`（避免重复文字封面）、`pdf.numbered=false`（去掉标题编号）；
5. 调用 document-factory 的 `pdf`，单篇文章 → 自动不生成目录。

产物：`output/content/`（结构）+ `output/dist/<书名>.img.unc.pdf`（未压缩）。封面副本放在 `output/imgpdf_source/`（供 cover_images 相对引用）。

> 封面 vs 内容页边距：`cover_images` 是 document-factory 故意做成的整页铺满（无边距），适合封面/插页；内容页走正文文章则有 `@page` 边距（默认 1.5cm×2cm）+ 图片边距，适合正文页留白。

> 与 OCR→MD 流程的区别：`imgpdf` 完全跳过 OCR/检测/组装，直接把图片页做成 PDF，适合纯影印/扫描件转 PDF 场景；需要文字提取时才用 `pipeline --pdf text`（或 `layed`）流程。

---

## 安装

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# 或安装本包（含依赖）：
pip install -e .
```

环境要求：
- macOS 13+（Vision 框架）、`swiftc`（随 Xcode Command Line Tools）
- Python 3.10+，依赖：`Pillow`、`numpy`、`pyyaml`

### 安装 document-factory（库方式）

PDF 制作以**库方式**调用 document-factory（`import document_factory`），需先安装它：

```bash
# 开发模式（推荐，改动即时生效）：
pip install -e /Volumes/T9/code/document-factory

# 或已发布到 PyPI 后：
pip install document-factory
```

**开发模式（`pip install -e`）说明**：以"链接"方式安装，**document-factory 源码改动即时生效，无需重新 install**。普通 `pip install` 会复制文件到 site-packages，源码改动后需重新安装。

> 开发流程建议：两个项目都用可编辑安装（`pip install -e`），改任意一方的源码即生效，不用反复重装。

---

## 输出格式

### blocks JSON（每页一个，`output/blocks/<n>.json`）

```json
{
  "page": 52,
  "regions": 1,
  "body_h": 47.7,
  "n_paras": 2,
  "n_headings": 1,
  "blocks": [
    {"type": "text", "text": "性事件。", "y_top": 183, "y_bottom": 231},
    {"type": "heading", "level": 2, "text": "现代人类起源的里程碑事件", ...},
    {"type": "image", "file": "p52_f1.png", "kind": "table",
     "caption": "表1.1 人类进化历史上的里程碑", "y_top": 742, "y_bottom": 1982}
  ]
}
```

### Markdown（`output/md/<章>.md` 或 `output/content/article_N.md`）

```markdown
# 第1章 通往进化心理学的科学运动   ← 文件头（article_N.md 里作为文档标题）

## 原书第 52 页                   ← 页码标记

## 第1章 通往进化心理学的科学运动  ← 书内章节标题（合并两行）

## 进化思想史上的里程碑事件       ← 小节标题（H2）

### 达尔文的自然选择理论           ← 三级标题（H3）

![表1.1 人类进化历史上的里程碑](images/p52_f1.png)   ← 表格裁图（article 内为 images/）
正文段落……
```

---

## 检测原理（detect.py）

核心思想：**用字号（行高）区分"正文/标题/图内文字"，用颜色区分标题**。

| 算法                     | 说明 |
| ------------------------ | ---- |
| `body_h` 正文估计        | 满宽行（>85% 页宽）行高的中位数，避免表内两列/小字污染 |
| 段落聚类                 | 高行（≥0.82×body_h）按行距聚类，簇内含满宽行才算正文段 |
| 图注分区（Pass A）       | "图X.X" 图注向上扫到上方正文段下沿；"表X.X" 表注向下扫 |
| 照片扫描（Pass B）       | 逐行低阈值内容扫描 + 内部空白容忍 ≤40px + 两端扩展 |
| 标题识别                 | 文字像素颜色（饱和度>0.25 → 绿色标题 `##`）+ 字号（黑色大字 `###`） |
| tight_crop               | 低阈值保内容（浅色图顶不被切），空白 >12px 才裁 |

**关键阈值**（正文 ≈ 45–50px 时）见 `detect.py` 顶部常量；换书字号差异大时需调整。

---

## 确定性说明

- ②③ 是纯确定性代码（无随机、无网络、无 LLM）：实测重跑全量，blocks + 裁图与既有产物 **md5 字节级一致**。
- ① OCR 在同一台 Mac 同版本 macOS 下结果也确定；换系统版本可能略有差异。
- 重跑 ② 前**必须清空输出目录**，否则页面图区数变化后旧裁图会残留。

---

## 已知限制

- 无"图X.X"标题、内容稀疏的小图检不出，OCR 会泄漏几行坐标数字到文本流。
- 部分"学习目标"编号行字号略大，被误判为 `###`。
- 跨页表格的"（续）"表头在下一页作为文本保留（非错误）。
- **macOS ImageIO 解码"任何 PNG"都会 SIGBUS**：`run.py` 的前置预处理 `prepare_images` 无条件把所有源图转成 JPEG 再交给 Swift，彻底消除崩溃。
- 阈值针对正文 45–50px 的书籍调校；换书时若字号差异大，需调整 `detect.py` 顶部的阈值常量。

---

## 复现当前产物（示例书）

```bash
python main.py init -p ./projects/进化心理学
# 把 assets 的截图放入 projects/进化心理学/assets/
python main.py pipeline -p ./projects/进化心理学
```
