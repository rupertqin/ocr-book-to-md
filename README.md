# p2md

**扫描书 / 长截图 → 结构化 Markdown。纯本地、无 LLM、确定性输出。**

`p2md` 把一本书或一篇长截图转换成可发布的章节 Markdown：识别出正文段落、标题、插图与表格，裁出图片并生成规范的 `images/...` 引用。**功能只到 Markdown 为止**——不生成 PDF，不依赖 document-factory。

```
输入：assets/p3.png ... p692.png（或扫描 PDF / 带文字层 PDF）
输出：output/content/article_0.md ... + output/content/images/*.png
```

## 特性

- **本地 OCR，按平台自动选择**：macOS 使用 Apple Vision（Swift，中文识别质量最佳）；其他平台使用 RapidOCR（onnxruntime 小模型，自带中文）。两者输出完全相同的 JSON schema，后续检测逻辑与平台无关。
- **长图按段落边界切分**：超长截图先按"段间留白 > 行内间距 × 1.8"落刀，避免把段落腰斩。
- **领域化的版面分析**：从 OCR 坐标恢复正文/标题/插图/表格；针对浅底 App 截图、K 线/行情截图、图内文字泄漏、标题颜色与字号做了专门修正。
- **确定性输出**：布局与 Markdown 组装是纯函数（无网络、无 LLM），同输入得到字节级一致的产物。
- **双入口**：CLI 与 Python API 都能跑完整流程。

## 安装

```bash
pip install p2md
```

- macOS 需要 `swiftc`（Xcode Command Line Tools）；首次运行会编译包内的 `ocr_batch.swift`。
- 非 macOS 已自动安装 RapidOCR；如需在 macOS 强制使用 RapidOCR：`pip install "p2md[rapidocr]"`，然后用 `p2md ocr/pipeline --engine rapidocr`、设 `P2MD_ENGINE=rapidocr`，或在 `config.yaml` 的 `p2md.engine` 配 `rapidocr`。
- 需要把 PDF 作为输入（渲染页图 / 提取文字层）时安装：`pip install "p2md[pdf]"`。

## 快速开始（CLI）

```bash
p2md init -p ./projects/进化心理学
# 把截图或 PDF 放进 projects/进化心理学/assets/
p2md pipeline -p ./projects/进化心理学
# 显式指定 OCR 引擎（auto / swift / rapidocr）
p2md pipeline -p ./projects/进化心理学 --engine rapidocr
```

分步执行：

```bash
p2md ocr      -p ./projects/进化心理学   # ① OCR → output/ocr/
p2md detect   -p ./projects/进化心理学   # ② 版面分析 → output/blocks + output/figs
p2md build    -p ./projects/进化心理学   # ③ 组装 Markdown → output/content/
```

其他命令：

- `p2md pdftotext -p <项目>`：PDF 有文字层时直接抽取为 Markdown（免 OCR）。
- `p2md pipeline -p <项目> --force`：忽略 OCR 缓存重跑。
- `python -m p2md --help`：查看完整帮助。

> 兼容命令 `ocr-book-to-md` 与 `p2md` 等价。

## Python API

```python
import p2md

result = p2md.convert(
    "./projects/进化心理学",
    assets_dir=None,      # 默认 <project>/assets
    output_dir="./out",   # 最终 Markdown 复制到这里（可选）
    force=False,
    engine=None,          # auto / swift / rapidocr；None=按配置
)
print(result.pages, len(result.markdowns))
```

- `convert(project_dir, *, assets_dir, output_dir, work_root, start, end, force, chapters, engine, log)` 一次跑完 `ocr → detect → build`。
- `convert_images(...)` 是同义函数；返回 `ConvertResult(markdowns, images, pages, work_root, warnings)`。
- 失败抛 `ConvertError`（底层 `SystemExit` 已统一转换）。

## 项目结构

```
projects/<书名>/
├── config.yaml           # 项目配置
├── assets/               # 输入截图 / PDF
└── output/
    ├── ocr/              # ① OCR 产物（pages_json + 中间页图）
    ├── blocks/           # ② 每页 blocks JSON
    ├── figs/             # ② 裁出的插图/表格
    ├── md/               # ③ 章节 Markdown（便于复核）
    └── content/
        ├── article_0.md ...
        └── images/       # 裁图，article 内引用 images/xxx.png
```

## 参数参考

### CLI 参数

| 命令 | 参数 | 默认值 | 说明 |
|---|---|---|---|
| 全局 | `--version` | — | 输出版本号 |
| `init` | `-p, --project` | `.` | 项目目录；生成 `config.yaml`、`.env` 模板和 `assets/` |
| `pdftotext` | `-p, --project` | `.` | 项目目录 |
| | `--dpi` | `150` | PDF 内嵌图片渲染 DPI |
| | `--out` | `output/pdf_text/` | Markdown 输出目录 |
| `ocr` | `-p, --project` | `.` | 项目目录 |
| | `--assets` | `p2md.assets_dir` 或 `assets/` | 输入图片/PDF 目录 |
| | `--start` / `--end` | 自动探测 | OCR 页码范围 |
| | `-f, --force` | `p2md.force` | 忽略 OCR 缓存，重跑全部页 |
| | `--engine` | `p2md.engine` 或平台默认 | `auto` / `swift` / `rapidocr` |
| `detect` | `-p, --project` | `.` | 项目目录 |
| | `--assets` | `_ocr_tmp/` 或 `assets/` | 预处理后的页图目录 |
| | `pages` | 全部页 | 仅处理指定页号（调试用） |
| `build` | `-p, --project` | `.` | 项目目录 |
| | `--chapters` | 自动分章 | 人工章节结构 JSON 路径 |
| `pipeline` | `-p, --project` | `.` | 项目目录 |
| | `--assets` | `p2md.assets_dir` 或 `assets/` | 输入图片/PDF 目录 |
| | `-f, --force` | `p2md.force` | 忽略 OCR 缓存 |
| | `--engine` | `p2md.engine` 或平台默认 | `auto` / `swift` / `rapidocr` |

### `config.yaml` 的 `p2md:` 参数

```yaml
p2md:
  name: "进化心理学"
  engine: auto
  renderTypeOfPdf: ""
  assets_dir: assets
  force: false
  start_page: null
  end_page: null
  split_long_image_max_ratio: 3.0
  split_slice_max_h: 2600
  split_slice_min_h: 1400
  split_slice_ratio: 3.0
  min_heading_ratio: 1.28
  bold_thickness_ratio: 0.078
  heading_plain_ratio: 1.18
  heading_page_base: true
  bold_use_page_h: true
  ocr_fix_global: true
  middlewares: []
```

| 键 | 默认值 | 说明 |
|---|---|---|
| `name` | `""` | 项目名；空值取项目目录名 |
| `engine` | `auto` | OCR 引擎：`auto` 按平台选（macOS→`swift`，其他→`rapidocr`），也可强制 `swift` / `rapidocr` |
| `renderTypeOfPdf` | `""` | PDF 渲染类型标记（如 `layed`）；兼容旧 `pipeline.pdf`。当前仅保存该值，不切换 OCR 渲染流程 |
| `assets_dir` | `assets` | 输入素材目录，相对项目目录解析；可放 `.png` / `.jpg` / `.pdf` |
| `force` | `false` | 忽略 OCR 缓存并重跑全部页；等价 CLI `-f` / `--force` |
| `start_page` | `null` | 起始页；`null` 为自动探测 |
| `end_page` | `null` | 结束页；`null` 为自动探测 |
| `split_long_image_max_ratio` | `3.0` | 高/宽比超过该值视为超长图并切片；`0` 关闭切片 |
| `split_slice_max_h` | `2600` | 长图切片最大高度（px） |
| `split_slice_min_h` | `1400` | 长图切片最小高度（px） |
| `split_slice_ratio` | `3.0` | 切片高/宽比上限；调大可保护图表不被切 |
| `min_heading_ratio` | `1.28` | 标题/正文字号最小比例；调高更严格，调低保留小标题 |
| `bold_thickness_ratio` | `0.078` | 加粗判定阈值（笔画宽/行高）；调高更严格 |
| `heading_plain_ratio` | `1.18` | 非彩色标题最小字号比例；长图标题可调低到 `1.05` |
| `heading_page_base` | `true` | `true` 按长图组的像素正文中位统一字号基准；`false` 用全书基准 |
| `bold_use_page_h` | `true` | `true` 用组级字号归一化加粗；`false` 用每行 OCR 高度（同一视觉行易不一致） |
| `ocr_fix_global` | `true` | 是否启用内置通用 OCR 纠错；RapidOCR 错字形态不同，可按项目关闭 |
| `middlewares` | `[]` | OCR 后、detect 前执行的脚本列表；脚本须定义 `process(ocr_dir, project_path)` |

优先级：命令行 > `.env` > `config.yaml` 的 `p2md.*` > 旧 `book.*` / `detect.*` / `pipeline.pdf` > 默认值。`.env` 的 KEY 是配置路径大写下划线形式，例如 `p2md.name` → `P2MD_NAME`、`p2md.engine` → `P2MD_ENGINE`、`p2md.renderTypeOfPdf` → `P2MD_RENDERTYPEOFPDF`。旧键只在代码中兼容，文档与模板只展示 `p2md.*`。

### Python API 参数

```python
p2md.convert(
    project_dir, *, assets_dir=None, output_dir=None, work_root=None,
    start=None, end=None, force=False, chapters=None, engine=None, log=None,
)
```

| 参数 | 默认值 | 说明 |
|---|---|---|
| `project_dir` | 必填 | 项目根目录，读取其中 `config.yaml` / `.env` |
| `assets_dir` | `None` | 源页图目录；`None` 表示 `<project_dir>/assets` |
| `output_dir` | `None` | 最终 Markdown 复制目标；`None` 时只留在 `work_root` |
| `work_root` | `None` | 中间产物目录；默认 `<project>/.derived/ocr/<assets 目录名>` |
| `start` / `end` | `None` | OCR 页码范围；`None` 自动 |
| `force` | `False` | 忽略 OCR 缓存重跑 |
| `chapters` | `None` | 人工章节结构（透传 build） |
| `engine` | `None` | `auto` / `swift` / `rapidocr`；`None` 走配置/环境/平台默认 |
| `log` | `None` | 日志回调 `Callable[[str], None]`；默认静默 |

`convert_images(...)` 与 `convert(...)` 参数相同。返回 `ConvertResult(markdowns, images, pages, work_root, warnings)`；失败抛 `ConvertError`。

## 输出格式

每页 `output/blocks/<n>.json`：

```json
{
  "page": 52,
  "body_h": 47.7,
  "blocks": [
    {"type": "heading", "level": 2, "text": "进化思想史上的里程碑事件", "y_top": 120, "y_bottom": 168},
    {"type": "text", "text": "正文段落……", "y_top": 183, "y_bottom": 231},
    {"type": "image", "file": "p52_f1.png", "kind": "table", "caption": "表1.1 …"}
  ]
}
```

章节 Markdown 示例：

```markdown
# 第1章 通往进化心理学的科学运动

## 进化思想史上的里程碑事件

正文段落……

![表1.1 人类进化历史上的里程碑](images/p52_f1.png)
```

## 已知限制

- RapidOCR 的中文识别质量低于 Apple Vision；关键项目建议 macOS 跑最终产物。
- `pdftotext` 需要 PyMuPDF（`p2md[pdf]`）；纯扫描 PDF 走 `pipeline` 渲染 OCR。
- 图片裁切针对正文 45–50px 的书籍调校；字号差异大时调整 `detect.*` 阈值。
- 不生成 PDF；如需 PDF 请在下游工具中消费 `output/content/`。

## License

MIT
