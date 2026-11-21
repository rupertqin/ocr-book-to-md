# -*- coding: utf-8 -*-
"""显式配置文件加载（CLI 的 ``--config`` 参数）。

配置文件路径由调用方给出；本模块不会查找或推断默认配置文件。支持 YAML/JSON，
读取 ``p2md:`` 段或顶层键，并返回配置文件所在目录作为相对路径基准。
"""

import json
from pathlib import Path

_ALIASES = {
    "assets_dir": "images_dir",
    "output": "work_dir",
    "start_page": "start",
    "end_page": "end",
}


def load_config(path) -> tuple[dict, Path]:
    """加载配置文件，返回 (参数字典, 配置文件所在目录)。"""
    cfg_path = Path(path).expanduser().resolve()
    if not cfg_path.is_file():
        raise SystemExit(f"错误：配置文件不存在: {cfg_path}")
    suffix = cfg_path.suffix.lower()
    try:
        text = cfg_path.read_text(encoding="utf-8")
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

    values = {}
    for key, value in section.items():
        if key in ("name", "renderTypeOfPdf"):
            continue
        values[_ALIASES.get(key, key)] = value
    return values, cfg_path.parent
