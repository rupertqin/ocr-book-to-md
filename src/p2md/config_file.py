# -*- coding: utf-8 -*-
"""显式配置文件加载（CLI 的 ``--config`` 参数）。

配置文件路径由调用方给出。支持 YAML/JSON，并同时接受两种写法（可混用）：

- 顶层键：``engine: auto``、``work: work``（命令行同名参数）；
- 项目分节：``book`` / ``detect``，只取 p2md 认识的键，分节内其它键静默忽略，
  便于直接复用项目内的 ``config.yaml``。

``p2md:`` 段仍兼容但不再需要。优先级：分节 < 顶层键 < ``p2md:`` 段。返回配置
文件所在目录作为相对路径基准；未知的顶层键直接报错，避免拼写错误被静默忽略。
"""

import json
from pathlib import Path

_ALLOWED = {
    "engine",
    "assets_dir",
    "work",
    "output",
    "start_page",
    "end_page",
    "force",
    "split_long_image_max_ratio",
    "split_slice_max_h",
    "split_slice_min_h",
    "split_slice_ratio",
    "min_heading_ratio",
    "bold_thickness_ratio",
    "heading_plain_ratio",
    "heading_page_base",
    "bold_use_page_h",
    "ocr_fix_global",
    "middlewares",
    "chapters",
    "base_dir",
    # Markdown 还原方式（type：normal / layed / image）与 layed 的排版参数
    "type",
    "pages",
    "page_margin",
    "font_family",
    "justify",
    "width_scale",
    "letter_spacing",
    "punct_compress",
    "layed_font_ratio",
    "layed_min_font_pt",
}

# 项目配置分节 → p2md 读取的键（分节内其它键忽略，兼容各项目的历史配置）
_SECTION_KEYS = {
    "book": (
        "assets_dir",
        "start_page",
        "end_page",
        "force_ocr",
        "type",
        "split_long_image_max_ratio",
        "split_slice_max_h",
        "split_slice_min_h",
        "split_slice_ratio",
    ),
    "detect": (
        "min_heading_ratio",
        "bold_thickness_ratio",
        "heading_plain_ratio",
        "heading_page_base",
        "bold_use_page_h",
        "ocr_fix_global",
        "middlewares",
    ),
}

_SECTION_RENAMES = {
    # book 分节里的键与命令行参数不同名
    ("book", "force_ocr"): "force",
}

# 配置文件键 → 内部参数名
_ALIASES = {
    "assets_dir": "images_dir",
    "work": "work_dir",
    "output": "out_dir",
    "start_page": "start",
    "end_page": "end",
}

_MD_TYPES = ("normal", "layed", "image")
# 旧配置的写法：text → normal，raw / img → image
_TYPE_ALIASES = {"text": "normal", "raw": "image", "img": "image"}

# 分节合并顺序（后者覆盖前者）
_SECTION_ORDER = ("book", "detect")


def _section_values(name, section) -> dict:
    """从一个项目分节里取出 p2md 认识的键（分节内未知键忽略）。"""
    values = {}
    for key, value in section.items():
        if key not in _SECTION_KEYS[name]:
            continue
        out_key = _SECTION_RENAMES.get((name, key), _ALIASES.get(key, key))
        values[out_key] = value
    return values


def _normalize_type(values, cfg_path) -> None:
    if "type" not in values:
        return
    raw = values["type"]
    value = _TYPE_ALIASES.get(str(raw).strip().lower(), str(raw).strip().lower())
    if value not in _MD_TYPES:
        raise SystemExit(
            f"错误：未知还原方式 type: {raw}（可选 {' / '.join(_MD_TYPES)}）"
            f" ({cfg_path})")
    values["type"] = value


def load_config(path) -> tuple[dict, Path]:
    """加载配置文件，返回 (内部参数字典, 配置文件所在目录)。"""
    cfg_path = Path(path).expanduser().resolve()
    if not cfg_path.is_file():
        raise SystemExit(f"错误：配置文件不存在: {cfg_path}")

    try:
        text = cfg_path.read_text(encoding="utf-8")
    except Exception as exc:
        raise SystemExit(f"错误：读取配置文件失败 {cfg_path}: {exc}") from exc

    suffix = cfg_path.suffix.lower()
    try:
        if suffix in (".yaml", ".yml"):
            import yaml
            data = yaml.safe_load(text)
        elif suffix == ".json":
            data = json.loads(text)
        else:
            raise SystemExit(f"错误：配置文件只支持 .yaml / .yml / .json: {cfg_path}")
    except SystemExit:
        raise
    except Exception as exc:
        raise SystemExit(f"错误：解析配置文件失败 {cfg_path}: {exc}") from exc

    if not isinstance(data, dict):
        raise SystemExit(f"错误：配置文件顶层必须是映射: {cfg_path}")

    values = {}
    for name in _SECTION_ORDER:
        section = data.get(name)
        if section is None:
            continue
        if not isinstance(section, dict):
            raise SystemExit(f"错误：配置文件的 {name} 必须是映射: {cfg_path}")
        values.update(_section_values(name, section))

    flat = {key: value for key, value in data.items()
            if key not in _SECTION_KEYS and key != "p2md"}
    unknown = sorted(set(flat) - _ALLOWED)
    if unknown:
        raise SystemExit(f"错误：未知配置键: {', '.join(unknown)} ({cfg_path})")
    values.update({_ALIASES.get(key, key): value for key, value in flat.items()})

    if "p2md" in data:
        section = data["p2md"]
        if not isinstance(section, dict):
            raise SystemExit(f"错误：配置文件的 p2md 必须是映射: {cfg_path}")
        unknown = sorted(set(section) - _ALLOWED)
        if unknown:
            raise SystemExit(f"错误：未知配置键: {', '.join(unknown)} ({cfg_path})")
        values.update({_ALIASES.get(key, key): value for key, value in section.items()})

    _normalize_type(values, cfg_path)
    return values, cfg_path.parent
