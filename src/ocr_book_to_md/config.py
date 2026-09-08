# -*- coding: utf-8 -*-
"""配置管理模块 - 支持 .env 与 config.yaml（对齐 document-factory 的约定）。

优先级链（不可违背）:
    命令行参数 > .env > config.yaml > 默认值

.env 变量命名规则:
    config.yaml 中的点号路径转大写下划线，例如:
        book.name       ->  BOOK_NAME
        pdf.title       ->  PDF_TITLE
        images.dir      ->  IMAGES_DIR
"""

import os
from pathlib import Path
from typing import Any, Optional


def _workspace_root() -> Path:
    """找到工作区根目录（main.py 所在目录）"""
    return Path(__file__).resolve().parent.parent.parent


def collect_env_files(start_dir: Path, root: Path = None) -> list[Path]:
    """从 start_dir 向上搜索到工作区根目录，收集所有 .env 文件。

    返回列表按优先级从低到高排列（外层在前，内层在后）。
    """
    if root is None:
        root = _workspace_root()
    env_files = []
    current = Path(start_dir).resolve()
    while True:
        env_path = current / ".env"
        if env_path.exists():
            env_files.append(env_path)
        if current == root:
            break
        parent = current.parent
        if parent == current:
            break
        current = parent
    return list(reversed(env_files))


def parse_env_file(path: Path) -> dict[str, str]:
    """解析单个 .env 文件为 {KEY: VALUE} 字典。"""
    env = {}
    if path.exists():
        for line in path.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            env[key.strip()] = value.strip().strip('"\'')
    return env


def load_env(environ: dict = None, start_dir: Path = None) -> dict[str, str]:
    """统一的 .env 加载器。

    从 start_dir（默认工作区根）向上收集所有 .env，内层覆盖外层合并。
    若传入 environ，则把合并结果强制写回。
    """
    if start_dir is None:
        start_dir = _workspace_root()
    merged: dict[str, str] = {}
    for env_path in collect_env_files(start_dir):
        merged.update(parse_env_file(env_path))
    if environ is not None:
        for k, v in merged.items():
            environ[k] = v
    return merged


class Config:
    """项目配置管理，封装 .env 和 config.yaml 读写。

    project_path 指向 `projects/<书名>/`，config.yaml 与 .env 都位于其下。
    """

    def __init__(self, project_path: str = "."):
        self.path = Path(project_path).resolve()
        self.config_file = self.path / "config.yaml"
        self._data = None
        self._env = None

    @staticmethod
    def _workspace_root() -> Path:
        return _workspace_root()

    def _collect_env_files(self) -> list[Path]:
        return collect_env_files(self.path)

    def _load_env(self) -> dict[str, str]:
        if self._env is not None:
            return self._env
        self._env = load_env(environ=None, start_dir=self.path)
        return self._env

    def _load(self) -> dict:
        if self._data is not None:
            return self._data
        if not self.config_file.exists():
            self._data = {}
            return self._data
        try:
            import yaml
            self._data = yaml.safe_load(self.config_file.read_text(encoding='utf-8')) or {}
        except Exception:
            self._data = {}
        return self._data

    def get(self, key: str, default: Any = None) -> Any:
        """获取配置值，支持点号嵌套键。优先级: .env > config.yaml > 默认值"""
        env_key = key.upper().replace('.', '_')
        env_value = self._load_env().get(env_key)
        if env_value is not None:
            return env_value
        data = self._load()
        for k in key.split('.'):
            if isinstance(data, dict) and k in data:
                data = data[k]
            else:
                return default
        return data

    def get_phased(self, phase: str, key: str, default: Any = None) -> Any:
        """按阶段获取配置，支持继承：{phase}.{key} > {key} > default"""
        val = self.get(f'{phase}.{key}')
        if val is not None:
            return val
        return self.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """设置配置值并保存到 config.yaml"""
        data = self._load()
        keys = key.split('.')
        d = data
        for k in keys[:-1]:
            if k not in d or not isinstance(d[k], dict):
                d[k] = {}
            d = d[k]
        d[keys[-1]] = value
        self._save(data)

    def _save(self, data: dict) -> None:
        try:
            import yaml
            self.config_file.write_text(
                yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
                encoding='utf-8',
            )
        except ImportError:
            pass

    # ---- 语义化访问 ----
    def get_book_name(self) -> str:
        return self.get('book.name') or self.path.name

    def get_images_dir(self) -> Optional[str]:
        """获取截图目录；config 中的相对路径以项目目录为基准解析。"""
        raw = self.get('book.images_dir')
        if not raw:
            return None
        p = Path(raw).expanduser()
        if p.is_absolute():
            return str(p)
        return str((self.path / p).resolve())

    def get_start_page(self) -> Optional[int]:
        return self.get('book.start_page')

    def get_end_page(self) -> Optional[int]:
        return self.get('book.end_page')

    def get_force_ocr(self) -> bool:
        return bool(self.get('book.force_ocr', False))

    def get_pdf_title(self) -> str:
        return self.get_phased('pdf', 'title', self.get_book_name())

    def get_pdf_subtitle(self) -> Optional[str]:
        return self.get_phased('pdf', 'subtitle')

    def get_pdf_engine(self) -> str:
        return self.get_phased('pdf', 'engine', 'weasyprint')

    def get_pdf_output(self) -> Optional[str]:
        return self.get_phased('pdf', 'output')

    def get_pdf_chunk_articles(self) -> Optional[int]:
        return self.get_phased('pdf', 'chunk_articles')

    def get_pdf_jobs(self) -> Optional[int]:
        return self.get_phased('pdf', 'jobs')

    def get_pdf_mode(self) -> str:
        return self.get_phased('pdf', 'mode', 'full')

    def get_pdf_statement(self) -> Optional[str]:
        return self.get_phased('pdf', 'statement')

    # ---- 模板 ----
    def write_default(self) -> None:
        template = """# ocr-book-to-md 项目配置
# 优先级: 命令行参数 > .env > config.yaml > 默认值
#
# 敏感信息（如 document-factory 相关 token）建议写入同目录 .env 文件。
# .env 的 KEY 与 config.yaml 的点号路径对应，例如:
#   config.yaml 的 pdf.title -> .env 的 PDF_TITLE

# 书籍信息
book:
  name: ""            # 书名（默认取项目目录名）
  images_dir: assets  # 输入素材目录（截图 .png/.jpg 或 .pdf；相对项目目录）。
                      # 默认 assets；历史项目曾用 data/images，可在此改回或写具体路径。
  start_page: null    # 起始页（默认自动探测）
  end_page: null      # 结束页（默认自动探测）
  force_ocr: false    # 忽略 OCR 缓存，强制重跑（等价命令行 --force）
  split_long_image_max_ratio: 3.0   # 高宽比超过该值视为超长图，OCR 前切分为多片（0 关闭）
  split_slice_max_h: 2600           # 长图切片每片最大高度（px，上限）
  split_slice_min_h: 1400           # 长图切片每片最小高度（px）
  split_slice_ratio: 3.0            # 切片高/宽比上限；layed 流对超高切片自动跨多页，故可给大值保护图表不被切

# 布局检测参数
detect:
  min_heading_ratio: 1.28  # 标题字号 / 正文字号 的最小比例（跨页面统一基准，相对全书正文）。
                      # 真实标题字号明显大于正文（"悖论系列N"1.31+/"第X章"1.29+）；
                      # 正文里独立成行的诗歌/脚注行字号接近正文（1.07~1.27）。
                      # 调高过滤更多误判，调低保留更多小字号标题。
  bold_thickness_ratio: 0.078  # 加粗判定阈值（相对笔画粗细 = 笔画宽/行高）。
                      # 用 scipy 距离变换估算笔画粗细；实测常规正文 ~0.075、粗体 ~0.082+。
                      # 调高更严格（少判粗），调低更宽松（多判粗）。
  heading_plain_ratio: 1.18 # 非彩色标题最小行高比例。长图文章标题常只比正文大 ~1.1×，
                      # 需调低（如 1.05）才能识别。默认 1.18 过滤正文独立成行的近等字号行。
  heading_page_base: true   # 标题字号基准：true=按页所属长图组聚合的像素正文行高中位
                      # （组内切片同字号；多张不同字号长图混排时，全书归一化中位数会被
                      # 大字号图主导、稀释小字号图标题）。false=全书归一化中位数。
  bold_use_page_h: true     # 加粗归一化：true=除以页所属长图组的像素正文中位行高；
                      # false=除以本行 OCR 高。OCR 把同一视觉行拆成多段时各段 OCR 高度
                      # 不一，除以段高会让加粗判定对同一行不一致；用组级字号归一化则
                      # 加粗=笔画粗细/组字号，与 OCR 段高无关。
  ocr_fix_global: true # 是否启用框架内置通用 OCR 纠错（ocr_fix.py GLOBAL_*，全 project
                      # 共享同一 Vision 框架的形近字错）。false=本项目关闭通用纠错。
  middlewares: []      # per-project OCR 中间件脚本列表（相对项目目录的 .py，按序执行）。
                      # OCR 产出 pages_json 后、text/layed 分流前运行，脚本须定义
                      # process(ocr_dir, project_path)。可混用清理（删头部等）与纠错
                      # （修形近字）脚本，执行顺序即列表顺序。旧单键 middleware/ocr_fix
                      # 仍兼容（middlewares 未配置时回退读取）。仅配置它的项目生效。

# 全部流程（pipeline）最终 PDF 方式
pipeline:
  pdf: text          # text(文字流) / img(原图片) / layed(文字置于图)。命令行 --pdf 可覆盖

# document-factory PDF 流程（把 output/content/ 交给它生成 PDF）
pdf:
  # document_factory 以库方式调用（import），需先 pip install -e <document-factory 目录>。
  # 不再需要在此配置路径。
  title: ""              # PDF 封面大标题（默认书名）
  subtitle: null
  statement: null        # 封面下方免责声明（多行用 \\n）
  engine: weasyprint     # weasyprint | reportlab
  chunk_articles: null   # 每册最大章数（null=不分册）
  jobs: 4
  mode: full             # full | incremental
  page_margin: "1.5cm 2cm"  # 正文页边距（CSS margin 简写，封面 cover_images 不受影响）
  output_dir: dist       # PDF 输出目录（相对 output/，如 dist / normal / pdf）
  font_family: ""        # layedpdf 字体族，如 "PingFang SC"、"Noto Sans CJK SC"（空=系统默认）
  justify: true          # layedpdf 段落是否两端对齐
  width_scale: 1.0       # layedpdf 文字容器宽度系数（<1 适配宽字体标点，如 0.95）
  letter_spacing: ""     # layedpdf CSS letter-spacing，如 "-0.02em" 轻微压缩
  punct_compress: ""     # layedpdf 标点压缩 CSS 值，如 "-0.1em"（统一压缩）
                         #   也可用 dict 按标点类别分开设置，如：
                         #   punct_compress: {"，、。": "-0.3em", "（）《》【】": "-0.2em"}
  layed_font_ratio: 0.78 # layedpdf 字号=OCR行高×该系数，随图片同比例缩放（解决文字与图大小不匹配）
  layed_min_font_pt: 6.0 # layedpdf 最小字号 pt（防止过小行高算出太小字号）
"""
        self.config_file.write_text(template, encoding='utf-8')

    def write_env_template(self) -> None:
        env_file = self.path / ".env"
        template = """# 项目环境变量（此文件已被 .gitignore 忽略）
# 优先级: 命令行参数 > .env > config.yaml > 默认值
#
# 变量命名规则: config.yaml 中的点号路径转大写下划线
#   例如: pdf.title -> PDF_TITLE
#
# 敏感信息放在这里（如 document-factory 的 COOKIE，传给 pdf 流程）
# COOKIE=
"""
        if not env_file.exists():
            env_file.write_text(template, encoding='utf-8')
