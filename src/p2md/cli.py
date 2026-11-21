# -*- coding: utf-8 -*-
"""p2md CLI：ocr / detect / build / pipeline。

接受图片输入，产出 Markdown 与裁图。参数来自命令行与显式 ``--config`` 文件，
命令行参数优先。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import defaults, paths


def _load_config(path):
    if not path:
        return {}, None
    from .config_file import load_config
    return load_config(path)


def _pick(args, cfg, arg_name, cfg_names, default=None):
    value = getattr(args, arg_name, None)
    if value is not None:
        return value
    for key in cfg_names:
        if key in cfg:
            return cfg[key]
    return default


def _path_value(value, base):
    if value is None:
        return None
    p = Path(str(value)).expanduser()
    if p.is_absolute():
        return str(p)
    return str((base / p).resolve() if base else p.resolve())


def _middlewares(value, base):
    if not value:
        return None
    resolved = []
    for item in value:
        if isinstance(item, dict):
            out = dict(item)
            key = "script" if "script" in out else "path"
            if key in out:
                out[key] = _path_value(out[key], base)
            resolved.append(out)
        else:
            resolved.append(_path_value(item, base))
    return resolved


def _require(value, name):
    if not value:
        raise SystemExit(f"错误：请用 --{name} 或配置文件提供该参数")
    return value


def _resolve_work(work, images=None) -> Path:
    if work:
        return paths.work_path(work)
    base = Path.cwd() / ".derived" / "ocr"
    if images:
        base = base / Path(images).expanduser().resolve().name
    return base.resolve()


def _print_summary(work_dir) -> None:
    work = paths.work_path(work_dir)
    article_dir = paths.article_dir(work)
    img_dir = paths.images_dir(work)
    n_art = len([f for f in os.listdir(article_dir) if f.endswith('.md')]) \
        if os.path.isdir(article_dir) else 0
    n_img = len([f for f in os.listdir(img_dir) if f.endswith('.png')]) \
        if os.path.isdir(img_dir) else 0
    print(f"\n工作目录: {work}")
    print(f"  content/  {n_art} 章 Markdown")
    print(f"  content/images/  {n_img} 张裁图")
    print("  md/      章节 Markdown（便于复核）")
    print("  blocks/  布局中间产物")
    print("  ocr/     OCR 中间产物")


def _add_config_arg(p) -> None:
    p.add_argument('-c', '--config', default=None,
                   help='配置文件（YAML/JSON）；命令行参数优先')


def _add_engine_args(p) -> None:
    p.add_argument('--engine', choices=('auto', 'swift', 'rapidocr'),
                   default=None, help='OCR 引擎（默认按平台选择）')


def _add_split_args(p) -> None:
    p.add_argument('--split-long-image-max-ratio', type=float, default=None,
                   help='高宽比超过该值视为超长图（0 关闭，默认 3.0）')
    p.add_argument('--split-slice-max-h', type=int, default=None,
                   help='长图切片最大高度（px）')
    p.add_argument('--split-slice-min-h', type=int, default=None,
                   help='长图切片最小高度（px）')
    p.add_argument('--split-slice-ratio', type=float, default=None,
                   help='切片高/宽比上限')


def _add_detect_args(p) -> None:
    p.add_argument('--min-heading-ratio', type=float, default=None,
                   help='标题/正文字号最小比例')
    p.add_argument('--bold-thickness-ratio', type=float, default=None,
                   help='加粗判定阈值')
    p.add_argument('--heading-plain-ratio', type=float, default=None,
                   help='非彩色标题最小字号比例')
    p.add_argument('--heading-page-base', action=argparse.BooleanOptionalAction,
                   default=None, help='按长图组统一字号基准')
    p.add_argument('--bold-use-page-h', action=argparse.BooleanOptionalAction,
                   default=None, help='用组级字号归一化加粗')


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='p2md',
        description='p2md - 图片（扫描页 / 长截图）转 Markdown（OCR → 版面分析 → 章节 Markdown）',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
命令:
  ocr         OCR → <work>/ocr/
  detect      版面分析 → <work>/blocks + <work>/figs
  build       组装章节 → <work>/md + <work>/content
  pipeline    全流程：ocr → middleware → detect → build

示例:
  p2md pipeline --config ./projects/么瑞/config.yaml
  p2md pipeline --images ./assets --work ./work --out ./out
  p2md ocr --images ./assets --engine rapidocr --force
  p2md build --work ./work
        """
    )
    parser.add_argument('--version', action='version', version='p2md 0.5.0')
    sub = parser.add_subparsers(dest='command', help='可用命令')

    ocr_p = sub.add_parser('ocr', help='OCR 步骤')
    _add_config_arg(ocr_p)
    ocr_p.add_argument('--images', default=None, help='输入图片目录')
    ocr_p.add_argument('--work', default=None, help='中间产物目录')
    ocr_p.add_argument('--start', type=int, default=None, help='起始页')
    ocr_p.add_argument('--end', type=int, default=None, help='结束页')
    ocr_p.add_argument('-f', '--force', action='store_true', default=None,
                       help='忽略 OCR 缓存，重跑全部页')
    _add_engine_args(ocr_p)
    _add_split_args(ocr_p)

    det_p = sub.add_parser('detect', help='布局分析步骤')
    _add_config_arg(det_p)
    det_p.add_argument('--images', default=None, help='输入图片目录')
    det_p.add_argument('--work', default=None, help='中间产物目录')
    det_p.add_argument('pages', nargs='*', type=int, help='仅处理指定页（调试用）')
    _add_detect_args(det_p)

    bld_p = sub.add_parser('build', help='组装 Markdown')
    _add_config_arg(bld_p)
    bld_p.add_argument('--work', default=None, help='中间产物目录')
    bld_p.add_argument('--chapters', default=None, help='人工章节结构 JSON')

    pipe_p = sub.add_parser('pipeline', help='全流程')
    _add_config_arg(pipe_p)
    pipe_p.add_argument('--images', default=None, help='输入图片目录')
    pipe_p.add_argument('--work', default=None, help='中间产物目录')
    pipe_p.add_argument('--out', default=None, help='最终 Markdown 复制目标')
    pipe_p.add_argument('--base-dir', default=None, help='中间件脚本相对路径基准')
    pipe_p.add_argument('--middleware', action='append', default=None,
                        help='中间件脚本路径（可重复，按序执行）')
    pipe_p.add_argument('-f', '--force', action='store_true', default=None,
                        help='忽略 OCR 缓存，重跑全部页')
    pipe_p.add_argument('--ocr-fix-global', action=argparse.BooleanOptionalAction,
                        default=None, help='启用内置通用 OCR 纠错')
    pipe_p.add_argument('--start', type=int, default=None, help='起始页')
    pipe_p.add_argument('--end', type=int, default=None, help='结束页')
    _add_engine_args(pipe_p)
    _add_split_args(pipe_p)
    _add_detect_args(pipe_p)
    return parser


def main() -> None:
    parser = _parser()
    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        sys.exit(1)

    cfg, cfg_base = _load_config(args.config)

    images = _path_value(
        _pick(args, cfg, 'images', ('images_dir', 'assets_dir')), cfg_base)
    work_value = _path_value(_pick(args, cfg, 'work', ('work_dir',)), cfg_base)

    if args.command == 'ocr':
        from . import ocr as ocr_mod
        images = _require(images, 'images')
        work = _resolve_work(work_value, images)
        img_for_ocr, _start, _end = ocr_mod.run(
            images, str(work),
            start=_pick(args, cfg, 'start', ('start', 'start_page')),
            end=_pick(args, cfg, 'end', ('end', 'end_page')),
            force=_pick(args, cfg, 'force', ('force',), defaults.FORCE),
            engine=_pick(args, cfg, 'engine', ('engine',), defaults.ENGINE),
            split_long_image_max_ratio=_pick(
                args, cfg, 'split_long_image_max_ratio',
                ('split_long_image_max_ratio',), defaults.SPLIT_LONG_IMAGE_MAX_RATIO),
            split_slice_max_h=_pick(args, cfg, 'split_slice_max_h',
                                    ('split_slice_max_h',), defaults.SPLIT_SLICE_MAX_H),
            split_slice_min_h=_pick(args, cfg, 'split_slice_min_h',
                                    ('split_slice_min_h',), defaults.SPLIT_SLICE_MIN_H),
            split_slice_ratio=_pick(args, cfg, 'split_slice_ratio',
                                    ('split_slice_ratio',), defaults.SPLIT_SLICE_RATIO))
        print(f"OCR 完成。detect 将使用预处理图目录: {img_for_ocr}")
        _print_summary(work)
        return

    if args.command == 'detect':
        from . import detect as detect_mod
        work = _resolve_work(work_value, images)
        img = images or str(paths.ocr_tmp_dir(work))
        pages = args.pages or cfg.get('pages') or None
        detect_mod.detect(
            str(work), img, pages=pages,
            min_heading_ratio=_pick(args, cfg, 'min_heading_ratio',
                                    ('min_heading_ratio',), defaults.MIN_HEADING_RATIO),
            bold_thickness_ratio=_pick(
                args, cfg, 'bold_thickness_ratio',
                ('bold_thickness_ratio',), defaults.BOLD_THICKNESS_RATIO),
            heading_plain_ratio=_pick(args, cfg, 'heading_plain_ratio',
                                      ('heading_plain_ratio',), defaults.HEADING_PLAIN_RATIO),
            heading_page_base=_pick(args, cfg, 'heading_page_base',
                                    ('heading_page_base',), defaults.HEADING_PAGE_BASE),
            bold_use_page_h=_pick(args, cfg, 'bold_use_page_h',
                                  ('bold_use_page_h',), defaults.BOLD_USE_PAGE_H))
        _print_summary(work)
        return

    if args.command == 'build':
        from . import build_md as bm
        work = _resolve_work(work_value)
        chapters = _pick(args, cfg, 'chapters', ('chapters',), defaults.CHAPTERS)
        bm.build(str(work), chapters=chapters)
        _print_summary(work)
        return

    if args.command == 'pipeline':
        from .api import convert_images
        images = _require(images, 'images')
        work = _resolve_work(work_value, images)
        out = _path_value(_pick(args, cfg, 'out', ('out_dir', 'out')), cfg_base)
        base_dir = _path_value(_pick(args, cfg, 'base_dir', ('base_dir',)), cfg_base) \
            or (str(cfg_base) if cfg_base else None)
        middlewares = _middlewares(
            _pick(args, cfg, 'middleware', ('middlewares',)), cfg_base)
        convert_images(
            images, work_dir=str(work), out_dir=out, base_dir=base_dir,
            engine=_pick(args, cfg, 'engine', ('engine',), defaults.ENGINE),
            force=_pick(args, cfg, 'force', ('force',), defaults.FORCE),
            start=_pick(args, cfg, 'start', ('start', 'start_page')),
            end=_pick(args, cfg, 'end', ('end', 'end_page')),
            split_long_image_max_ratio=_pick(
                args, cfg, 'split_long_image_max_ratio',
                ('split_long_image_max_ratio',), defaults.SPLIT_LONG_IMAGE_MAX_RATIO),
            split_slice_max_h=_pick(args, cfg, 'split_slice_max_h',
                                    ('split_slice_max_h',), defaults.SPLIT_SLICE_MAX_H),
            split_slice_min_h=_pick(args, cfg, 'split_slice_min_h',
                                    ('split_slice_min_h',), defaults.SPLIT_SLICE_MIN_H),
            split_slice_ratio=_pick(args, cfg, 'split_slice_ratio',
                                    ('split_slice_ratio',), defaults.SPLIT_SLICE_RATIO),
            min_heading_ratio=_pick(args, cfg, 'min_heading_ratio',
                                    ('min_heading_ratio',), defaults.MIN_HEADING_RATIO),
            bold_thickness_ratio=_pick(
                args, cfg, 'bold_thickness_ratio',
                ('bold_thickness_ratio',), defaults.BOLD_THICKNESS_RATIO),
            heading_plain_ratio=_pick(args, cfg, 'heading_plain_ratio',
                                      ('heading_plain_ratio',), defaults.HEADING_PLAIN_RATIO),
            heading_page_base=_pick(args, cfg, 'heading_page_base',
                                    ('heading_page_base',), defaults.HEADING_PAGE_BASE),
            bold_use_page_h=_pick(args, cfg, 'bold_use_page_h',
                                  ('bold_use_page_h',), defaults.BOLD_USE_PAGE_H),
            ocr_fix_global=_pick(args, cfg, 'ocr_fix_global',
                                 ('ocr_fix_global',), defaults.OCR_FIX_GLOBAL),
            middlewares=middlewares, log=print)
        _print_summary(work)


if __name__ == '__main__':
    main()
