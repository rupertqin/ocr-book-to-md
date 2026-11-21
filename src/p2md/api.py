# -*- coding: utf-8 -*-
"""库接口：把一组图片从页图转成章节 Markdown，一次调用走完 ocr → detect → build。

所有参数都由调用方显式传入。

约定：
- 中间产物（ocr/blocks/figs/md/content）落 ``work_dir``；
- 最终章节 Markdown 从 build 的 content 目录复制到 ``out_dir``（给了才复制）；
- 图片一并复制到 ``<out_dir>/images/``，md 里的 ``images/...`` 引用可直接解析。

错误处理：底层阶段用 ``SystemExit`` 表示前置条件不满足，这里统一转成 ``ConvertError``。
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

from . import defaults, paths


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
    """把阶段里的 SystemExit / 异常收敛成 ConvertError，并带上阶段名。"""
    try:
        return fn(*args, **kwargs)
    except ConvertError:
        raise
    except SystemExit as exc:
        raise ConvertError(f"{stage}: {exc}") from exc
    except Exception as exc:                  # noqa: BLE001 - 统一收敛给调用方判定
        raise ConvertError(f"{stage}: {type(exc).__name__}: {exc}") from exc


def convert_images(
    images_dir,
    *,
    work_dir: str | None = None,
    out_dir: str | None = None,
    base_dir: str | None = None,
    engine: str | None = defaults.ENGINE,
    force: bool = defaults.FORCE,
    start: int | None = defaults.START,
    end: int | None = defaults.END,
    split_long_image_max_ratio: float = defaults.SPLIT_LONG_IMAGE_MAX_RATIO,
    split_slice_max_h: int = defaults.SPLIT_SLICE_MAX_H,
    split_slice_min_h: int = defaults.SPLIT_SLICE_MIN_H,
    split_slice_ratio: float = defaults.SPLIT_SLICE_RATIO,
    min_heading_ratio: float = defaults.MIN_HEADING_RATIO,
    bold_thickness_ratio: float = defaults.BOLD_THICKNESS_RATIO,
    heading_plain_ratio: float = defaults.HEADING_PLAIN_RATIO,
    heading_page_base: bool = defaults.HEADING_PAGE_BASE,
    bold_use_page_h: bool = defaults.BOLD_USE_PAGE_H,
    ocr_fix_global: bool = defaults.OCR_FIX_GLOBAL,
    middlewares: list | None = defaults.MIDDLEWARES,
    chapters=None,
    log: Optional[Callable[[str], None]] = None,
) -> ConvertResult:
    """把一组图片转成章节 Markdown。

    Args:
        images_dir: 源图片目录（必填）。
        work_dir: 中间产物目录；默认 ``<cwd>/.derived/ocr/<images_dir 名>``。
        out_dir: 最终 Markdown 复制目标；不给就只留在 ``work_dir/content``。
        base_dir: 中间件脚本相对路径的解析基准；默认当前工作目录。
        engine: OCR 引擎（auto/swift/rapidocr），None=按平台默认。
        force: 忽略 OCR 缓存重跑。
        start/end: 页码范围（None=自动）。
        split_*: 超长图切分参数。
        min_heading_ratio/bold_thickness_ratio/heading_plain_ratio/
        heading_page_base/bold_use_page_h: 版面检测阈值与开关。
        ocr_fix_global: 是否启用内置通用 OCR 纠错。
        middlewares: OCR 后执行的脚本列表（相对路径按 ``base_dir`` 解析）。
        chapters: 人工章节结构（透传 build）。
        log: 日志回调，默认静默。

    Returns:
        ConvertResult。
    """
    from . import build_md, detect as detect_mod, middleware, ocr

    say = log or (lambda _msg: None)
    src = Path(images_dir).expanduser().resolve()
    if not src.is_dir():
        raise ConvertError(f"图片目录不存在: {src}")

    work = paths.work_path(work_dir) if work_dir else \
        (Path.cwd() / ".derived" / "ocr" / src.name).resolve()
    work.mkdir(parents=True, exist_ok=True)
    base = Path(base_dir).expanduser().resolve() if base_dir else Path.cwd()

    say(f"[ocr] 源 {src} → 中间产物 {work}")
    img_dir, _start, _end = _call(
        "ocr", ocr.run, str(src), str(work), start, end,
        force=force, engine=engine,
        split_long_image_max_ratio=split_long_image_max_ratio,
        split_slice_max_h=split_slice_max_h,
        split_slice_min_h=split_slice_min_h,
        split_slice_ratio=split_slice_ratio)

    say("[middleware] 逐页修正")
    _call("middleware", middleware.run_all, str(work),
          middlewares=middlewares, ocr_fix_global=ocr_fix_global, base_dir=str(base))

    say("[detect] 版面检测")
    _call("detect", detect_mod.detect, str(work), img_dir, None,
          min_heading_ratio=min_heading_ratio,
          bold_thickness_ratio=bold_thickness_ratio,
          heading_plain_ratio=heading_plain_ratio,
          heading_page_base=heading_page_base,
          bold_use_page_h=bold_use_page_h)

    say("[build] 组装章节")
    md_dir, article_dir, written = _call("build", build_md.build, str(work), chapters)

    bodies = sorted(Path(article_dir).glob("*.md"))
    images = sorted((Path(article_dir) / "images").glob("*")) \
        if (Path(article_dir) / "images").is_dir() else []

    pages_json = paths.ocr_pages_json_dir(work)
    pages = len(list(pages_json.glob("*.json"))) if pages_json.is_dir() else 0

    result = ConvertResult(markdowns=bodies, images=images, pages=pages, work_root=work)

    if out_dir:
        dest = Path(out_dir).expanduser().resolve()
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
        result.warnings.append("没有产出任何 Markdown（检查图片与章节结构）")
    return result


def convert(images_dir, **kwargs) -> ConvertResult:
    """:func:`convert_images` 的短别名：``p2md.convert(images_dir, ...)`` 一句话转完。"""
    return convert_images(images_dir, **kwargs)


__all__ = ["ConvertError", "ConvertResult", "convert", "convert_images"]
