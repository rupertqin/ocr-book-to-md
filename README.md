# p2md

**图片（扫描页 / 长截图）→ 结构化 Markdown。纯本地、无 LLM、确定性输出。**

`p2md` 是通用基础库与 CLI：把一组图片转换成章节 Markdown，识别正文段落、标题、插图与表格，裁出图片并生成 `images/...` 引用。**最终产物是 Markdown 和裁图**。

```
输入：./assets/p3.png ... p692.png
输出：<work_dir>/content/article_0.md ... + <work_dir>/content/images/*.png
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

```bash
# 全流程：图片 → OCR → 版面检测 → Markdown
p2md pipeline --images ./assets --work ./work --out ./out

# 指定 OCR 引擎并忽略缓存
p2md pipeline --images ./assets --work ./work --engine rapidocr --force
```

分步执行：

```bash
p2md ocr      --images ./assets --work ./work
p2md detect   --work ./work
p2md build    --work ./work --chapters ./chapters.json   # 可选人工章节
```

## 配置文件

CLI 支持显式传入配置文件：

```bash
p2md pipeline --config ./projects/么瑞/config.yaml
```

配置文件为 YAML 或 JSON，命令行参数优先于配置文件。配置文件中的相对路径按**配置文件所在目录**解析。

```yaml
engine: auto
assets_dir: assets
output: output
start_page: null
end_page: null
force: false
min_heading_ratio: 1.28
bold_thickness_ratio: 0.082
middlewares:
- hooks/clean_ocr.py
- hooks/fix_ocr.py
- hooks/drop_reward_button.py
```

`assets_dir` 等价于 `images_dir`，`output` 等价于中间产物目录 `work_dir`，`start_page` / `end_page` 等价于 `--start` / `--end`。

## Python API

```python
import p2md

result = p2md.convert(
    "./assets",
    work_dir="./work",
    out_dir="./out",
    engine=None,          # auto / swift / rapidocr；None=按平台默认
    force=False,
)
print(result.pages, len(result.markdowns))
```

- `convert_images(...)` 与 `convert(...)` 参数相同；返回 `ConvertResult(markdowns, images, pages, work_root, warnings)`。
- 失败抛 `ConvertError`（底层 `SystemExit` 已统一转换）。
- 不传 `work_dir` 时使用 `<cwd>/.derived/ocr/<images 目录名>`。

## 产物结构

```
<work_dir>/
├── _ocr_tmp/             # 预处理后的页图
├── ocr/                  # OCR 产物（pages_json + pages + broken.txt）
├── blocks/               # 每页 blocks JSON
├── figs/                 # 裁出的插图/表格
├── md/                   # 章节 Markdown（便于复核）
└── content/
    ├── article_0.md ...
    └── images/           # 裁图，article 内引用 images/xxx.png
```

传 `--out` / `out_dir` 时，`content/` 下的 Markdown 与 `images/` 会复制到该目录。

## CLI 参数

所有子命令都支持 `-c, --config <file>`：显式加载 YAML/JSON 参数，命令行 flag 覆盖文件中的同名项。

### `p2md ocr`

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--images` | 必填 | 输入图片目录 |
| `--work` | `<cwd>/.derived/ocr/<images 名>` | 中间产物目录 |
| `--start` / `--end` | 自动探测 | OCR 页码范围 |
| `-f, --force` | `false` | 忽略 OCR 缓存，重跑全部页 |
| `--engine` | `auto` | `auto` / `swift` / `rapidocr` |
| `--split-long-image-max-ratio` | `3.0` | 高/宽比超过该值视为超长图并切片；`0` 关闭 |
| `--split-slice-max-h` | `2600` | 长图切片最大高度（px） |
| `--split-slice-min-h` | `1400` | 长图切片最小高度（px） |
| `--split-slice-ratio` | `3.0` | 切片高/宽比上限 |

### `p2md detect`

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--images` | `<work>/_ocr_tmp` | 输入图片目录 |
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
| `--chapters` | 自动分章 | 人工章节结构 JSON 路径 |

### `p2md pipeline`

包含 `ocr`、`detect`、`build` 的全部参数，另加：

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--out` | `None` | 最终 Markdown 复制目标；不传则只留在 `<work>/content` |
| `--base-dir` | 当前目录 | 中间件脚本相对路径的解析基准 |
| `--middleware` | 无 | 中间件脚本路径，可重复，按序执行；脚本须定义 `process(ocr_dir, base_dir)` |
| `--ocr-fix-global` / `--no-ocr-fix-global` | `true` | 启用内置通用 OCR 纠错 |
| `--start` / `--end` | 自动探测 | OCR 页码范围 |

## Python API 参数

```python
p2md.convert(
    images_dir, *, work_dir=None, out_dir=None, base_dir=None,
    engine=None, force=False, start=None, end=None,
    split_long_image_max_ratio=3.0,
    split_slice_max_h=2600, split_slice_min_h=1400, split_slice_ratio=3.0,
    min_heading_ratio=1.28, bold_thickness_ratio=0.078,
    heading_plain_ratio=1.18, heading_page_base=True, bold_use_page_h=True,
    ocr_fix_global=True, middlewares=None, chapters=None, log=None,
)
```

| 参数 | 默认值 | 说明 |
|---|---|---|
| `images_dir` | 必填 | 输入图片目录 |
| `work_dir` | `<cwd>/.derived/ocr/<images 名>` | 中间产物目录 |
| `out_dir` | `None` | 最终 Markdown 复制目标 |
| `base_dir` | 当前目录 | 中间件脚本相对路径基准 |
| `engine` | `None` | `auto` / `swift` / `rapidocr`；`None` 按平台默认 |
| `force` | `false` | 忽略 OCR 缓存重跑 |
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
| `chapters` | `None` | 人工章节结构 |
| `log` | `None` | 日志回调 `Callable[[str], None]`，默认静默 |

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
