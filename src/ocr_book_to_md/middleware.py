# -*- coding: utf-8 -*-
"""OCR pages 中间件（per-project 钩子）：在 OCR 产出 pages_json 后、text/layed 分流前，
运行 project 里的 py 脚本对 OCR lines 做清理/修改。

text 流经 detect 读 pages_json 生成 blocks，layed 流直接读 pages_json 的 lines——
两者都以 pages_json 为共同源头。故在 detect（text）与 layed 分开之前清理 pages_json
一次，两套 md 都干净（如公众号文章头部的"已付费/作者署名/时间戳"）。

配置（config.yaml）:
    detect:
      middleware: hooks/clean_ocr.py   # 相对项目目录的 py 脚本路径

钩子脚本接口约定：定义
    def process(ocr_dir: str, project_path: str) -> None:
        # ocr_dir 是 output/ocr/pages_json/，遍历 *.{json}，
        # 每个文件 {"page": n, "lines": [{x,y,w,h,text}, ...]}，
        # 删除/修改 lines 后写回（保持 JSON 结构）。
脚本可自由 import。
"""

import importlib.util
import os
import sys
from pathlib import Path


def _load_module(script_path: Path):
    """加载 project 里的 py 脚本为模块。"""
    if not script_path.is_file():
        raise FileNotFoundError(f"中间件脚本不存在: {script_path}")
    spec = importlib.util.spec_from_file_location("project_middleware", str(script_path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def run_ocr_middleware(project) -> bool:
    """运行项目配置的 OCR 中间件（若有）。返回是否运行了。

    从 config detect.middleware 读脚本路径（相对项目目录），加载脚本调用
    process(ocr_pages_json_dir, project_path)。
    """
    from .config import Config
    cfg = Config(str(project.path))
    rel = cfg.get("detect.middleware", None)
    if not rel:
        return False
    script = Path(str(project.path)) / str(rel)
    if not script.is_file():
        print(f"  ⚠ 中间件脚本未找到: {script}")
        return False
    ocr_dir = str(project.get_ocr_pages_json_dir())
    if not os.path.isdir(ocr_dir):
        return False
    module = _load_module(script)
    fn = getattr(module, "process", None)
    if fn is None:
        raise AttributeError(f"中间件 {script} 未定义 process(ocr_dir, project_path)")
    print(f"  (运行 OCR 中间件: {script.name})")
    fn(ocr_dir, str(project.path))
    return True
