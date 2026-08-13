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
├── main.py                       # 统一 CLI 入口（ocr/detect/build/pdf/all）
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
        ├── data/images/          # 输入截图
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
# 1. 初始化项目（生成 config.yaml + .env + data/images/）
python3 main.py init -p ./projects/进化心理学

# 2. 把书的截图放入 projects/进化心理学/data/images/

# 3. 一键全流程：OCR → 检测 → 组装 → PDF
python3 main.py all -p ./projects/进化心理学

# 只到组装为止，不生成 PDF
python3 main.py all -p ./projects/进化心理学 --no-pdf
```

### 分步执行（调试/换书时用）

```bash
# ① OCR：扫描页 → 文本 + 坐标（复用缓存，强制重跑加 --force-ocr）
python3 main.py ocr -p ./projects/进化心理学

# ② 检测：坐标 JSON + 原图 → 图/标题/正文三分
python3 main.py detect -p ./projects/进化心理学

# ③ 组装：blocks → 章节 Markdown + article 目录
python3 main.py build -p ./projects/进化心理学

# ④ PDF：把 content/ 交给 document-factory 生成 PDF
python3 main.py pdf -p ./projects/进化心理学
```

---

## projects 模式（每个书一个项目）

对齐 document-factory 的约定：**每个文件夹是一个独立项目，可复制迁移**。

`projects/<书名>/config.yaml` 全配置项：

```yaml
book:
  name: "进化心理学"       # 书名（默认取项目目录名）
  images_dir: "data/images"  # 截图目录（相对项目目录解析，默认 data/images）
  start_page: null        # 起始页（默认自动探测）
  end_page: null          # 结束页（默认自动探测）
  force_ocr: false        # 忽略 OCR 缓存，强制重跑

pdf:                      # document-factory PDF 流程
  document_factory: ""    # document-factory 项目路径（main.py 所在目录；留空自动猜同级 ../document-factory）
  title: ""               # PDF 封面大标题（默认书名）
  subtitle: null
  statement: null         # 封面下方免责声明
  engine: weasyprint      # weasyprint | reportlab
  chunk_articles: null    # 每册最大章数（null=不分册）
  jobs: 4
  mode: full              # full | incremental
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

`pdf` 步骤以子进程调用 document-factory 的 CLI：

```bash
python document-factory/main.py pdf -p <ocr-book-to-md>/projects/<书名>
```

因此你可以：
- 直接用 `python3 main.py pdf -p <项目>` 一键生成 PDF；
- 或手动进入 document-factory 目录，用它的全部 PDF 能力（分册、shuffle/sand、publications、压缩等）处理 `output/content/`。

> 注：`pdf.document_factory` 未配置时，默认猜工作区同级目录 `../document-factory`；也可用环境变量 `OCR_TO_MD_DF` 或 `.env` 的 `PDF_DOCUMENT_FACTORY` 指定。

---

## 安装

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

环境要求：
- macOS 13+（Vision 框架）、`swiftc`（随 Xcode Command Line Tools）
- Python 3.10+，依赖：`Pillow`、`numpy`、`pyyaml`
- 生成 PDF 需要 `document-factory`（其依赖 `weasyprint` 等）

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
python3 main.py init -p ./projects/进化心理学
# 把 data/images 的截图放入 projects/进化心理学/data/images/
python3 main.py all -p ./projects/进化心理学
```
