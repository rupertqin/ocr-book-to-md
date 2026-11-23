# p2md

**图片（扫描页 / 长截图）→ 结构化 Markdown。纯本地、无 LLM、确定性输出。**

`p2md` 是通用基础库与 CLI：把一组图片还原成章节 Markdown，识别正文段落、标题、插图与表格，裁出图片并生成 `images/...` 引用。**最终产物是 Markdown 和裁图**。

```
输入：   ./assets/p3.png ... p692.png
中间产物：<work>/ocr/ blocks/ figs/ content/
最终交付：<output>/article_0.md ... + <output>/images/*.png
```

所有选项都由 CLI flag 或 Python API 显式传入。

## 特性

- **本地 OCR，按平台自动选择**：macOS 使用 Apple Vision（`swift`）；其他平台使用 RapidOCR（`rapidocr`）。输出同一 JSON schema。
- **长图按段落边界切分**：用“段间留白 > 行内间距 × 1.8”落刀，避免腰斩段落。
- **领域化版面分析**：从 OCR 坐标恢复正文/标题/插图/表格；针对浅底 App 截图、K 线/行情截图、图内文字泄漏做了专门修正。
- **确定性输出**：布局与 Markdown 组装是纯函数，同输入得到一致产物。
- **双入口**：CLI 与 Python API 都能跑完整流程。

## 安装

```bash
pip install p2md
```

- macOS 需要 `swiftc`（Xcode Command Line Tools）；首次运行会编译包内 `ocr_batch.swift`。
- 非 macOS 已自动安装 RapidOCR；如需在 macOS 强制使用 RapidOCR：`pip install "p2md[rapidocr]"`，然后传 `--engine rapidocr` 或 `engine="rapidocr"`。

## 快速开始（CLI）

**不带子命令就是全流程**（ocr → middleware → detect → build）；`ocr` / `detect` / `build` 只是它的分解步骤，用来单独重跑某一阶段。全流程本身没有独立命令名。

```bash
# 目录 = 一本书：逐章产出 Markdown
p2md ./assets --out ./output

# 用配置文件（assets_dir / work / output 都写在里面）
p2md --config ./config.yaml

# 指定 OCR 引擎并忽略缓存
p2md ./assets --work ./work --out ./out --engine rapidocr --force

# 单张图片（页号固定为 1）
p2md --image ./one.png --work ./work --out ./output
```

逐张快速转换（给的是**文件/通配符**时，每张单独出一份 Markdown，不切章节）：

```bash
# 打印 Markdown 到终端
p2md ~/Desktop/book_imgs/*.jpg

# 在图片旁写出同名 .md（覆盖已有同名文件）
p2md -o ~/Desktop/book_imgs/*.jpg
# 01.jpg -> 01.md，02.jpg -> 02.md
```

分解步骤：

```bash
p2md ocr      --images ./assets --work ./work
p2md detect   --work ./work
p2md build    --work ./work --chapters ./chapters.json   # 可选人工章节
p2md build    --work ./work --type layed                 # 版式还原（见「还原方式」）
```

## 配置文件

CLI 支持显式传入配置文件：

```bash
p2md --config ./config.yaml
```

配置文件是 YAML 或 JSON 的扁平键值表，键名与命令行参数同名；命令行参数优先。
文件里的相对路径按**配置文件所在目录**解析，未知键直接报错。

```yaml
assets_dir: assets
work: output
output: out
type: layed
page_margin: 1cm 1.5cm
bold_thickness_ratio: 0.082
middlewares:
- hooks/clean_ocr.py
```

| 键 | 说明 |
|---|---|
| `engine` | OCR 引擎，`auto` / `swift` / `rapidocr` |
| `assets_dir` | 输入图片目录 |
| `work` | 中间产物目录 |
| `output` | 最终交付目录 |
| `start_page` / `end_page` | OCR 页码范围 |
| `force` | 强制重跑 OCR |
| `split_long_image_max_ratio` / `split_slice_max_h` / `split_slice_min_h` / `split_slice_ratio` | 长图切分参数 |
| `min_heading_ratio` / `bold_thickness_ratio` / `heading_plain_ratio` / `heading_page_base` / `bold_use_page_h` | 版面检测参数 |
| `ocr_fix_global` | 内置通用 OCR 纠错开关 |
| `middlewares` | OCR 后执行的脚本列表 |
| `chapters` | 人工章节结构 JSON 文件 |
| `base_dir` | 中间件脚本相对路径基准 |
| `type` | Markdown 还原方式，`normal` / `layed` / `image` |
| `pages` | 只处理指定页（`layed` / `image`） |
| `page_margin` / `font_family` / `justify` / `width_scale` / `letter_spacing` / `punct_compress` / `layed_font_ratio` / `layed_min_font_pt` | `layed` 的排版参数 |

## Python API

```python
import p2md

result = p2md.convert(
    "./assets",
    work_dir="./work",
    out_dir="./output",
    engine=None,          # auto / swift / rapidocr；None=按平台默认
    force=False,
)
print(result.pages, len(result.markdowns))

# 单张图片
result = p2md.convert_image("./one.png", work_dir="./work", out_dir="./output")
```

- `type` 是 Markdown 还原方式：`normal`（默认）/ `layed` / `image`；`layed` / `image` 的产物目录是 `<work>/content_layed/`、`<work>/content_image/`。
- `convert_images(...)` 与 `convert(...)` 参数相同；返回 `ConvertResult(markdowns, images, pages, work_root, warnings)`。
- `convert_image(...)` 是单图入口；直接传图片文件路径，页号固定为 1。
- 失败抛 `ConvertError`（底层 `SystemExit` 已统一转换）。
- 不传 `work_dir` 时使用 `<cwd>/.derived/ocr/<images 目录名>`。

## 产物结构

```
<work>/
├── _ocr_tmp/             # 预处理后的页图
├── ocr/                  # OCR 产物（pages_json + pages + broken.txt）
├── blocks/               # 每页 blocks JSON
├── figs/                 # 裁出的插图/表格
├── md/                   # 章节 Markdown（便于复核）
├── content/              # type=text（默认）：build 原始产物
├── content_layed/        # type=layed：版式还原
└── content_image/        # type=image：每页原图
```

传 `--out` / `out_dir` 时，对应 `content[_type]/` 下的 Markdown 与 `images/` 会复制到 `<output>`。

## CLI 参数

所有子命令都支持 `-c, --config <file>`：显式加载 YAML/JSON 参数，命令行 flag 覆盖文件中的同名项。

### `p2md ocr`

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--images` / `--image` | 必填 | 输入图片目录或单张图片；单图页号固定为 1 |
| `--work` | `<cwd>/.derived/ocr/<images 名>` | 中间产物目录 |
| `--start` / `--end` | 自动探测 | OCR 页码范围 |
| `-f, --force` / `--no-force` | `false` | 忽略 OCR 缓存，重跑全部页 / 关闭强制重跑 |
| `--engine` | `auto` | `auto` / `swift` / `rapidocr` |
| `--split-long-image-max-ratio` | `3.0` | 高/宽比超过该值视为超长图并切片；`0` 关闭 |
| `--split-slice-max-h` | `2600` | 长图切片最大高度（px） |
| `--split-slice-min-h` | `1400` | 长图切片最小高度（px） |
| `--split-slice-ratio` | `3.0` | 切片高/宽比上限 |

### `p2md detect`

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--images` | 无 | 仅用于推导 `--work`；检测固定读取 `<work>/_ocr_tmp` |
| `--work` | `<cwd>/.derived/ocr` | 中间产物目录 |
| `pages` | 全部页 | 仅处理指定页号（调试用） |
| `--min-heading-ratio` | `1.28` | 彩色标题/正文字号最小比例；调高更严格 |
| `--bold-thickness-ratio` | `0.078` | 加粗判定阈值（笔画宽/行高）；调高更严格 |
| `--heading-plain-ratio` | `1.18` | 非彩色标题最小字号比例；长图可调低到 `1.05` |
| `--heading-page-base` / `--no-heading-page-base` | `true` | 按长图组统一字号基准 |
| `--bold-use-page-h` / `--no-bold-use-page-h` | `true` | 用组级字号归一化加粗 |

### `p2md build`

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--work` | `<cwd>/.derived/ocr` | 中间产物目录 |
| `--chapters` | 自动分章 | 人工章节结构 JSON 路径（仅 `--type normal`） |
| `--out` | `None` | 最终交付目录；不传只写 `<work>/content[_type]` |
| `--type` | `normal` | 还原方式：`normal` / `layed` / `image`（见下） |
| `--pages` / `--page-margin` / `--font-family` / `--assets` | — | layed / image 的排版与页图参数 |

### 默认模式（全流程）

不带子命令时跑 ocr → middleware → detect → build，参数取 `ocr`、`detect`、`build` 的并集，另加：

| 参数 | 默认值 | 说明 |
|---|---|---|
| `IMAGE...`（位置参数） | 无 | 目录 = 整本书（逐章）；文件/通配符 = 逐张各出一份 Markdown |
| `--images` / `--image` | config `assets_dir` | 输入图片目录或单张图片（总按整本书处理） |
| `-o, --write` | 关 | 逐张转换并把 Markdown 写到源图片旁（强制逐张模式） |
| `--config` | 无 | 显式 YAML/JSON 配置文件；命令行参数优先 |
| `--work` / `--out` | config `work` / `output` | 中间产物目录 / 最终交付目录 |
| `--base-dir` | 当前目录或配置文件目录 | 中间件脚本相对路径的解析基准 |
| `--middleware` | 无 | 中间件脚本路径，可重复，按序执行；脚本须定义 `process(ocr_dir, base_dir)` |
| `--chapters` | 自动分章 | 人工章节结构 JSON 路径 |
| `--ocr-fix-global` / `--no-ocr-fix-global` | `true` | 启用内置通用 OCR 纠错 |
| `--start` / `--end` | 自动探测 | OCR 页码范围 |

### 还原方式（`--type`）

默认模式与 `build` 都用 `--type` 选择 Markdown 的还原方式：

| `type` | 内容目录 | 组织方式 |
|---|---|---|
| `normal`（默认） | `<work>/content/` | 纯文本流：识别出的标题/正文/图重新排版 |
| `layed` | `<work>/content_layed/` | 版式还原：按 OCR 坐标绝对定位文字 + 裁图，每页一个 `<section class="layed-page">` |
| `image` | `<work>/content_image/` | 纯图片，不做 OCR：每页原图一页，一个 `<section class="img-page">` |

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--type` | `normal` | `normal` / `layed` / `image` |
| `--pages` | 全部页 | 仅处理指定页（layed / image，调试） |
| `--page-margin` | `1cm 1.5cm` | 页面边距，CSS margin 简写（layed / image） |
| `--font-family` | 无 | 正文字体族（layed） |
| `--width-scale` / `--letter-spacing` / `--justify` | 1.0 / 无 / 开 | layed 的文字排版微调 |
| `--assets` | `<work>/_ocr_tmp` | 页图目录（image） |

产物：`<work>/content_<type>/article_N.md`（`normal` 用 `<work>/content/`），给了 `--out` 时同步到交付目录。
`layed` 全书统一缩放（取常规页里最大的一页），字号跨页一致，超高页按文字行间隙切窗口；
`image` 有 blocks 时按章节分篇并跳过前言，没有 blocks（未跑 OCR）时全书一篇。

## Python API 参数

```python
p2md.convert(
    images_dir, *, work_dir=None, out_dir=None, base_dir=None,
    engine=None, force=False, start=None, end=None,
    split_long_image_max_ratio=3.0,
    split_slice_max_h=2600, split_slice_min_h=1400, split_slice_ratio=3.0,
    min_heading_ratio=1.28, bold_thickness_ratio=0.078,
    heading_plain_ratio=1.18, heading_page_base=True, bold_use_page_h=True,
    ocr_fix_global=True, middlewares=None, chapters=None,
    type="normal", pages=None, page_margin="1cm 1.5cm",
    font_family=None, justify=True, width_scale=1.0, letter_spacing=None,
    punct_compress=None, font_height_ratio=0.78, min_font_pt=6.0,
    assets_dir=None, log=None,
)
```

| 参数 | 默认值 | 说明 |
|---|---|---|
| `images_dir` | 必填 | 输入图片目录 |
| `work_dir` | `<cwd>/.derived/ocr/<images 名>` | 中间产物目录 |
| `out_dir` | `None` | 最终 Markdown 复制目标 |
| `base_dir` | 当前目录 | 中间件脚本相对路径基准 |
| `engine` | `None` | `auto` / `swift` / `rapidocr`；`None` 按平台默认 |
| `force` | `false` | 忽略 OCR 缓存重跑；传 `True` 强制重跑 |
| `start` / `end` | `None` | OCR 页码范围 |
| `split_long_image_max_ratio` | `3.0` | 超长图切分阈值；`0` 关闭 |
| `split_slice_max_h` / `split_slice_min_h` / `split_slice_ratio` | `2600` / `1400` / `3.0` | 切片尺寸约束 |
| `min_heading_ratio` | `1.28` | 彩色标题/正文字号最小比例 |
| `bold_thickness_ratio` | `0.078` | 加粗判定阈值 |
| `heading_plain_ratio` | `1.18` | 非彩色标题最小字号比例 |
| `heading_page_base` | `true` | 按长图组统一字号基准 |
| `bold_use_page_h` | `true` | 用组级字号归一化加粗 |
| `ocr_fix_global` | `true` | 内置通用 OCR 纠错 |
| `middlewares` | `None` | OCR 后执行的脚本列表 |
| `chapters` | `None` | 人工章节结构列表；CLI 的 `--chapters` 会先加载 JSON 文件 |
| `log` | `None` | 日志回调 `Callable[[str], None]`，默认静默 |

已有中间产物时，可只跑 build 并复制到最终目录：

```python
result = p2md.build_stage("./work", out_dir="./output", chapters=None)
```

## 输出格式

每页 `<work_dir>/blocks/<n>.json`：

```json
{
  "page": 52,
  "body_h": 47.7,
  "blocks": [
    {"type": "heading", "level": 2, "text": "章节标题", "y_top": 120, "y_bottom": 168},
    {"type": "text", "text": "正文段落……", "y_top": 183, "y_bottom": 231},
    {"type": "image", "file": "p52_f1.png", "kind": "table", "caption": "表1.1 …"}
  ]
}
```

章节 Markdown 示例：

```markdown
# 第1章 通往进化心理学的科学运动

## 章节标题

正文段落……

![表1.1 人类进化历史上的里程碑](images/p52_f1.png)
```

## 已知限制

- RapidOCR 的中文识别质量低于 Apple Vision；关键任务建议 macOS 跑最终产物。
- 图片裁切针对正文 45–50px 的截图调校；字号差异大时调整检测阈值参数。

## License

MIT
