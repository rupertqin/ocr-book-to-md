# -*- coding: utf-8 -*-
"""显式配置文件加载（CLI 的 ``--config`` 参数）。

配置文件路径由调用方给出。支持 YAML/JSON，读取 ``p2md:`` 段或顶层键，并返回
配置文件所在目录作为相对路径基准。未知键会直接报错，避免拼写错误被静默忽略。
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
}

# 配置文件键 → 内部参数名
_ALIASES = {
    "assets_dir": "images_dir",
    "work": "work_dir",
    "output": "out_dir",
    "start_page": "start",
    "end_page": "end",
}


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
    section = data.get("p2md", data)
    if not isinstance(section, dict):
        raise SystemExit(f"错误：配置文件的 p2md 必须是映射: {cfg_path}")

    unknown = sorted(set(section) - _ALLOWED)
    if unknown:
        raise SystemExit(f"错误：未知配置键: {', '.join(unknown)} ({cfg_path})")

    return {_ALIASES.get(key, key): value for key, value in section.items()}, cfg_path.parent
