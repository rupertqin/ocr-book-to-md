# -*- coding: utf-8 -*-
"""显式配置文件加载（CLI 的 ``--config`` 参数）。

配置文件是扁平的键值表（YAML / JSON），键名与命令行参数同名；只有
``assets_dir`` / ``work`` / ``output`` / ``start_page`` / ``end_page`` 会转成
内部参数名。返回配置文件所在目录作为相对路径基准；未知键直接报错，避免拼写
错误被静默忽略。
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

# 配置文件键 → 内部参数名
_ALIASES = {
    "assets_dir": "images_dir",
    "work": "work_dir",
    "output": "out_dir",
    "start_page": "start",
    "end_page": "end",
}

_MD_TYPES = ("normal", "layed", "image")


def _check_type(values, cfg_path) -> None:
    if "type" not in values:
        return
    raw = values["type"]
    value = str(raw).strip().lower()
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

    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise SystemExit(f"错误：配置文件顶层必须是映射: {cfg_path}")

    unknown = sorted(set(data) - _ALLOWED)
    if unknown:
        raise SystemExit(f"错误：未知配置键: {', '.join(unknown)} ({cfg_path})")

    values = {_ALIASES.get(key, key): value for key, value in data.items()}
    _check_type(values, cfg_path)
    return values, cfg_path.parent
