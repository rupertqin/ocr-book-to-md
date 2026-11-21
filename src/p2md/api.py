# -*- coding: utf-8 -*-
"""库接口：把一个图片集从页图转成章节 Markdown，一次调用走完 ocr → detect → build。

- CLI（``main.py``）面向"一个项目一本书"，路径写死在项目根下；
- 库调用时源页图可来自任意目录，中间产物落在调用方给定的工作目录，
  最终 Markdown 可复制到指定输出目录。

约定：
- 中间产物（ocr/blocks/figs/md）落 ``work_root``，不污染项目根；
- 最终章节 Markdown 从 build 的 article 目录复制到 ``output_dir``（给了才复制），
  图片一并复制到 ``<output_dir>/images/``，md 里的 ``images/...`` 引用即可直接解析。

错误处理：底层阶段用 ``SystemExit`` 表示"前置条件不满足"（缺图、缺 blocks 等），
这里统一转成 ``ConvertError``，避免库调用把整个进程带走。
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional


class ConvertError(RuntimeError):
    """转换失败（输入缺失、工具不可用、阶段报错）。调用方据此判 failed/invalid。"""


@dataclass
class ConvertResult:
    """一次图片集转换的结果。``markdowns`` 是最终可用的章节文件（已含图片相对引用）。"""

    markdowns: List[Path] = field(default_factory=list)
    images: List[Path] = field(default_factory=list)
    pages: int = 0
    work_root: Optional[Path] = None
    warnings: List[str] = field(default_factory=list)


def _call(stage: str, fn, *args, **kwargs):
    """把阶段里的 SystemExit / 异常收敛成 ConvertError，并带上是哪一步挂的。"""
    try:
        return fn(*args, **kwargs)
    except ConvertError:
        raise
    except SystemExit as exc:                 # 底层用 SystemExit 表达前置条件不满足
        raise ConvertError(f"{stage}: {exc}") from exc
    except Exception as exc:                  # noqa: BLE001 - 统一收敛给调用方判定
        raise ConvertError(f"{stage}: {type(exc).__name__}: {exc}") from exc


def convert_images(project_dir, *, assets_dir=None, output_dir=None, work_root=None,
                   start=None, end=None, force=False, chapters=None,
                   log: Optional[Callable[[str], None]] = None) -> ConvertResult:
    """把一个图片集转成章节 Markdown。

    Args:
        project_dir: 项目根（读 ``config.yaml`` / ``.env``）。
        assets_dir: 源页图目录，默认 ``<project_dir>/assets``。
        output_dir: 最终 Markdown 落地目录；不给就只留在 work_root 的 article 目录。
        work_root: 中间产物目录，默认 ``<project_dir>/.derived/ocr/<assets 目录名>``。
        start/end: 页码范围（None=自动）。
        force: 忽略 OCR 缓存重跑。
        chapters: 人工章节结构（透传 build）。
        log: 日志回调，默认不打印（库调用不该刷屏）。

    Returns:
        ConvertResult。
    """
    from . import build_md, detect as detect_mod, middleware, ocr
    from .project import Project

    say = log or (lambda _msg: None)
    root = Path(project_dir).expanduser().resolve()
    if not root.is_dir():
        raise ConvertError(f"项目目录不存在: {root}")

    src = Path(assets_dir).expanduser().resolve() if assets_dir else root / "assets"
    if not src.is_dir():
        raise ConvertError(f"图片集源目录不存在: {src}")

    work = (Path(work_root).expanduser().resolve() if work_root
            else root / ".derived" / "ocr" / src.name)
    work.mkdir(parents=True, exist_ok=True)

    project = Project(str(root), output_dir=str(work), assets_dir=str(src))

    say(f"[ocr] 源 {src} → 中间产物 {work}")
    img_dir, _start, _end = _call("ocr", ocr.run, project, str(src),
                                  start, end, force_ocr=force)

    say("[middleware] 逐页修正")
    _call("middleware", middleware.run_all, project)

    say("[detect] 版面检测")
    _call("detect", detect_mod.detect, project, img_dir, None)

    say("[build] 组装章节")
    md_dir, article_dir, written = _call("build", build_md.build, project, chapters)

    bodies = sorted(Path(article_dir).glob("*.md"))
    images = sorted((Path(article_dir) / "images").glob("*")) \
        if (Path(article_dir) / "images").is_dir() else []

    pages_json = project.get_ocr_pages_json_dir()
    pages = len(list(pages_json.glob("*.json"))) if pages_json.is_dir() else 0

    result = ConvertResult(markdowns=bodies, images=images, pages=pages, work_root=work)

    if output_dir:
        dest = Path(output_dir).expanduser().resolve()
        dest.mkdir(parents=True, exist_ok=True)
        for path in bodies:
            shutil.copyfile(path, dest / path.name)
        if images:
            img_dest = dest / "images"
            img_dest.mkdir(parents=True, exist_ok=True)
            for path in images:
                shutil.copyfile(path, img_dest / path.name)
        result.markdowns = [dest / p.name for p in bodies]
        result.images = [dest / "images" / p.name for p in images]
        say(f"[done] {len(bodies)} 章 → {dest}")

    if not result.markdowns:
        result.warnings.append("没有产出任何 Markdown（检查页图与章节结构）")
    return result


def convert(project_dir, *, assets_dir=None, output_dir=None, work_root=None,
            start=None, end=None, force=False, chapters=None,
            log: Optional[Callable[[str], None]] = None) -> ConvertResult:
    """:func:`convert_images` 的短别名：``p2md.convert(project, ...)`` 一句话转完。"""
    return convert_images(project_dir, assets_dir=assets_dir, output_dir=output_dir,
                          work_root=work_root, start=start, end=end, force=force,
                          chapters=chapters, log=log)


__all__ = ["ConvertError", "ConvertResult", "convert", "convert_images"]
