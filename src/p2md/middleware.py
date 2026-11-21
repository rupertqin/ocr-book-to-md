# -*- coding: utf-8 -*-
"""OCR pages 中间件：在 OCR 产出 pages_json 后、detect 之前，按序运行调用方提供的
Python 脚本，对 OCR lines 做清理/修改。

脚本路径由调用方显式传入（API 的 ``middlewares`` / CLI 的 ``--middleware``）。
相对路径按 ``base_dir``（默认当前工作目录）解析。

脚本接口约定：定义
    def process(ocr_dir: str, base_dir: str) -> None:
        # ocr_dir 是 <work_dir>/ocr/pages_json/，遍历 *.json，
        # 每个文件 {"page": n, "lines": [{x,y,w,h,text}, ...]}，
        # 删除/修改 lines 后写回（保持 JSON 结构）。
脚本可自由 import。
"""

import importlib.util
import sys
from pathlib import Path
from typing import Callable, Optional

from . import defaults, paths


def _load_module(script_path: Path):
    """把指定 py 脚本加载为模块。"""
    if not script_path.is_file():
        raise FileNotFoundError(f"中间件脚本不存在: {script_path}")
    spec = importlib.util.spec_from_file_location("p2md_middleware", str(script_path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _run_script(work_dir, rel, log_label, base_dir: Path,
                log: Optional[Callable[[str], None]] = None) -> bool:
    """运行一个 OCR 钩子脚本。rel 为脚本路径，相对路径按 base_dir 解析。"""
    say = log or (lambda _msg: None)
    script = Path(str(rel)).expanduser()
    if not script.is_absolute():
        script = base_dir / script
    ocr_dir = str(paths.ocr_pages_json_dir(work_dir))
    if not script.is_file():
        say(f"  ⚠ {log_label}脚本未找到: {script}")
        return False
    if not Path(ocr_dir).is_dir():
        return False
    module = _load_module(script)
    fn = getattr(module, "process", None)
    if fn is None:
        raise AttributeError(f"{log_label}脚本 {script} 未定义 process(ocr_dir, base_dir)")
    say(f"  (运行 OCR {log_label}: {script.name})")
    fn(ocr_dir, str(base_dir))
    return True


def run_middlewares(work_dir, middlewares=None, base_dir=None,
                    log: Optional[Callable[[str], None]] = None) -> int:
    """按序运行调用方传入的中间件脚本，返回**实际运行**数量。"""
    say = log or (lambda _msg: None)
    base = Path(base_dir).expanduser().resolve() if base_dir else Path.cwd()
    n = 0
    for item in (middlewares or []):
        if isinstance(item, dict):
            rel = item.get("script") or item.get("path")
            label = item.get("name") or item.get("type") or ""
        else:
            rel, label = item, ""
        if not rel:
            continue
        if not label:
            label = Path(str(rel)).stem
        if _run_script(work_dir, rel, label, base, say):
            n += 1
    return n


def run_all(work_dir, middlewares=None,
            ocr_fix_global=defaults.OCR_FIX_GLOBAL, base_dir=None,
            log: Optional[Callable[[str], None]] = None) -> None:
    """detect 前一次性运行全部 OCR 处理：中间件脚本 → 内置通用纠错。"""
    say = log or (lambda _msg: None)
    run_middlewares(work_dir, middlewares, base_dir, say)
    from .ocr_fix import run_global_fix
    run_global_fix(work_dir, enabled=ocr_fix_global, log=say)
