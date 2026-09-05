# -*- coding: utf-8 -*-
"""OCR pages 中间件（per-project 钩子）：在 OCR 产出 pages_json 后、text/layed 分流前，
按序运行 project 里配置的 py 脚本对 OCR lines 做清理/修改。

text 流经 detect 读 pages_json 生成 blocks，layed 流直接读 pages_json 的 lines——
两者都以 pages_json 为共同源头。故在 detect（text）与 layed 分开之前处理 pages_json
一次，两套 md 都干净。

配置（config.yaml）——统一为**列表** detect.middlewares，脚本按配置顺序执行
（如先清理头部、再纠错形近字）：
    detect:
      middlewares:
        - hooks/clean_ocr.py    # 清理：删/改 lines（公众号头部"已付费/作者署名/时间戳"）
        - hooks/fix_ocr.py      # 纠错：修 OCR 错字/错词（形近字等）

向后兼容旧单键：detect.middleware / detect.ocr_fix（middlewares 未配置时回退读取）。

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


def _run_script(project, rel, log_label):
    """运行一个 OCR 钩子脚本。rel 为相对项目目录的脚本路径。返回是否运行了。"""
    script = Path(str(project.path)) / str(rel)
    if not script.is_file():
        print(f"  ⚠ {log_label}脚本未找到: {script}")
        return False
    ocr_dir = str(project.get_ocr_pages_json_dir())
    if not os.path.isdir(ocr_dir):
        return False
    module = _load_module(script)
    fn = getattr(module, "process", None)
    if fn is None:
        raise AttributeError(f"{log_label}脚本 {script} 未定义 process(ocr_dir, project_path)")
    print(f"  (运行 OCR {log_label}: {script.name})")
    fn(ocr_dir, str(project.path))
    return True


def run_middlewares(project):
    """运行项目配置的全部 OCR 中间件脚本（detect.middlewares 列表，按序执行）。

    向后兼容：若未配 middlewares，回退读旧单键 detect.middleware（清理）与
    detect.ocr_fix（纠错）。返回运行脚本数。
    """
    from .config import Config
    cfg = Config(str(project.path))
    mws = cfg.get("detect.middlewares", None)
    if mws:
        n = 0
        for i, item in enumerate(mws):
            if isinstance(item, dict):
                rel = item.get("script") or item.get("path")
                label = item.get("name") or item.get("type") or ""
            else:
                rel, label = item, ""
            if not rel:
                continue
            # 未指定名称时用脚本文件名作日志标签
            if not label:
                label = Path(str(rel)).stem
            _run_script(project, rel, label)
            n += 1
        return n
    # 旧单键回退
    ran = 0
    ran += _run_script(project, cfg.get("detect.middleware", None), "清理中间件") \
        if cfg.get("detect.middleware", None) else 0
    ran += _run_script(project, cfg.get("detect.ocr_fix", None), "纠错钩子") \
        if cfg.get("detect.ocr_fix", None) else 0
    return ran


def run_all(project):
    """detect 前一次性运行全部 OCR 处理：per-project 中间件 → 内置通用纠错。

    顺序固定：
      1) per-project 中间件脚本（detect.middlewares 列表，如清理头部→纠错形近字）
      2) 内置通用纠错（ocr_fix.py GLOBAL_*，默认全项目；config detect.ocr_fix_global:false 关）
    """
    run_middlewares(project)
    from .ocr_fix import run_global_fix
    run_global_fix(project)
