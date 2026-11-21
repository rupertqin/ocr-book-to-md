# -*- coding: utf-8 -*-
"""p2md CLI：ocr / detect / build / pipeline。

接受图片输入，产出 Markdown 与裁图。参数来自命令行与显式 ``--config`` 文件，
命令行参数优先。
"""
from __future__ import annotations

import argparse
import contextlib
import glob
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

from . import __version__, defaults, paths


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


def _path_value(value, base=None):
    if value is None:
        return None
    p = Path(str(value)).expanduser()
    if not p.is_absolute() and base:
        p = base / p
    return str(p.resolve())


def _load_chapters(value, base=None):
    if not value:
        return None
    if isinstance(value, list):
        return value
    path = Path(str(value)).expanduser()
    if not path.is_absolute() and base:
        path = base / path
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise SystemExit(f"错误：读取 chapters 文件失败 {path.resolve()}: {exc}") from exc
    if isinstance(data, dict):
        data = data.get("chapters")
    if not isinstance(data, list):
        raise SystemExit(f"错误：chapters 文件必须是列表或含 chapters 列表: {path.resolve()}")
    return data


def _middlewares(value, base=None):
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


def _expand_images(values, base=None):
    """展开图片路径、目录与通配符，返回按自然序去重的图片文件。"""
    from .ocr import IMAGE_EXTS

    found = []
    seen = set()
    for raw in values or []:
        p = Path(str(raw)).expanduser()
        if not p.is_absolute() and base:
            p = base / p
        pattern = str(p)
        matches = sorted(glob.glob(pattern)) \
            if any(ch in pattern for ch in '*?[') else [str(p)]
        for match in matches:
            path = Path(match).resolve()
            candidates = sorted(path.iterdir()) if path.is_dir() else [path]
            for item in candidates:
                if not item.is_file() or item.suffix.lower() not in IMAGE_EXTS:
                    continue
                if item not in seen:
                    seen.add(item)
                    found.append(item)
    return found


def _argv_with_direct(argv):
    """把无子命令调用转成内部 _direct 子命令。"""
    if not argv or argv[0] in ('-h', '--help', '--version'):
        return argv
    if argv[0] in ('ocr', 'detect', 'build', 'pipeline', '_direct'):
        return argv
    return ['_direct', *argv]


def _direct_work(work_value, image: Path, index: int):
    if not work_value:
        return None
    stem = re.sub(r'[^0-9A-Za-z\u4e00-\u9fff_-]+', '_', image.stem) or f"image{index}"
    return Path(work_value) / f"{index:03d}_{stem}"


def _resolve_work(work, images=None) -> Path:
    if work:
        return paths.work_path(work)
    base = Path.cwd() / ".derived" / "ocr"
    if images:
        p = Path(images).expanduser().resolve()
        base = base / (p.stem if p.is_file() else p.name)
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


def _add_force_arg(p) -> None:
    p.add_argument('-f', '--force', action=argparse.BooleanOptionalAction,
                   default=None, help='强制重跑 / 关闭强制重跑')


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
  p2md ./book/*.jpg                 # 直接转换并打印 Markdown
  p2md -o ./book/*.jpg              # 在图片旁写出同名 .md
  p2md pipeline --config ./config.yaml
  p2md pipeline --images ./assets --work ./work --out ./output
  p2md ocr --images ./assets --engine rapidocr --force
  p2md build --work ./work
        """
    )
    parser.add_argument('--version', action='version', version=f'p2md {__version__}')
    sub = parser.add_subparsers(
        dest='command', help='可用命令',
        metavar='{ocr,detect,build,pipeline}')

    ocr_p = sub.add_parser('ocr', help='OCR 步骤')
    _add_config_arg(ocr_p)
    ocr_p.add_argument('--images', default=None, help='输入图片目录或单张图片')
    ocr_p.add_argument('--image', dest='images', default=None,
                       help='单张图片文件（等价于 --images 传文件）')
    ocr_p.add_argument('--work', default=None, help='中间产物目录')
    ocr_p.add_argument('--start', type=int, default=None, help='起始页')
    ocr_p.add_argument('--end', type=int, default=None, help='结束页')
    _add_force_arg(ocr_p)
    _add_engine_args(ocr_p)
    _add_split_args(ocr_p)

    det_p = sub.add_parser('detect', help='布局分析步骤')
    _add_config_arg(det_p)
    det_p.add_argument('--images', default=None, help='输入图片目录（用于推导 work）')
    det_p.add_argument('--work', default=None, help='中间产物目录')
    det_p.add_argument('pages', nargs='*', type=int, help='仅处理指定页（调试用）')
    _add_detect_args(det_p)

    bld_p = sub.add_parser('build', help='组装 Markdown')
    _add_config_arg(bld_p)
    bld_p.add_argument('--work', default=None, help='中间产物目录')
    bld_p.add_argument('--chapters', default=None, help='人工章节结构 JSON 文件')
    bld_p.add_argument('--out', default=None, help='最终交付目录')

    # 无子命令时由 main() 自动插入，支持 p2md IMAGE... / p2md -o IMAGE...
    dir_p = sub.add_parser('_direct', help=argparse.SUPPRESS)
    _add_config_arg(dir_p)
    dir_p.add_argument('images', nargs='*', help='图片文件、目录或通配符')
    dir_p.add_argument('-o', '--write', action='store_true',
                       help='把 Markdown 写到源图片旁（覆盖同名 .md）')
    dir_p.add_argument('--work', default=None, help='中间产物根目录')
    dir_p.add_argument('--out', default=None, help='最终 Markdown 目录')
    dir_p.add_argument('--base-dir', default=None, help='中间件脚本相对路径基准')
    dir_p.add_argument('--middleware', action='append', default=None,
                       help='中间件脚本路径（可重复）')
    _add_force_arg(dir_p)
    dir_p.add_argument('--ocr-fix-global', action=argparse.BooleanOptionalAction,
                       default=None, help='启用内置通用 OCR 纠错')
    dir_p.add_argument('--start', type=int, default=None, help='起始页')
    dir_p.add_argument('--end', type=int, default=None, help='结束页')
    dir_p.add_argument('--chapters', default=None, help='人工章节结构 JSON 文件')
    _add_engine_args(dir_p)
    _add_split_args(dir_p)
    _add_detect_args(dir_p)

    pipe_p = sub.add_parser('pipeline', help='全流程')
    _add_config_arg(pipe_p)
    pipe_p.add_argument('--images', default=None, help='输入图片目录或单张图片')
    pipe_p.add_argument('--image', dest='images', default=None,
                        help='单张图片文件（等价于 --images 传文件）')
    pipe_p.add_argument('--work', default=None, help='中间产物目录')
    pipe_p.add_argument('--out', default=None, help='最终交付目录')
    pipe_p.add_argument('--base-dir', default=None, help='中间件脚本相对路径基准')
    pipe_p.add_argument('--middleware', action='append', default=None,
                        help='中间件脚本路径（可重复，按序执行）')
    _add_force_arg(pipe_p)
    pipe_p.add_argument('--ocr-fix-global', action=argparse.BooleanOptionalAction,
                        default=None, help='启用内置通用 OCR 纠错')
    pipe_p.add_argument('--start', type=int, default=None, help='起始页')
    pipe_p.add_argument('--end', type=int, default=None, help='结束页')
    pipe_p.add_argument('--chapters', default=None, help='人工章节结构 JSON 文件')
    _add_engine_args(pipe_p)
    _add_split_args(pipe_p)
    _add_detect_args(pipe_p)
    sub._choices_actions = [
        action for action in sub._choices_actions
        if getattr(action, 'dest', None) != '_direct'
    ]
    return parser


def main() -> None:
    parser = _parser()
    args = parser.parse_args(_argv_with_direct(sys.argv[1:]))
    if args.command is None:
        parser.print_help()
        sys.exit(1)

    from .api import ConvertError

    try:
        cfg, cfg_base = _load_config(args.config)
        work_value = _path_value(_pick(args, cfg, 'work', ('work_dir',)), cfg_base)
        out_value = _path_value(_pick(args, cfg, 'out', ('out_dir',)), cfg_base)

        if args.command == '_direct':
            from .api import convert_image

            if args.images:
                raw_images, image_base = args.images, None
            else:
                raw_images = cfg.get('images_dir') or []
                image_base = cfg_base
            if isinstance(raw_images, str):
                raw_images = [raw_images]
            images = _expand_images(raw_images, image_base)
            if not images:
                raise SystemExit("错误：没有匹配的图片文件")

            raw_chapters = args.chapters if args.chapters is not None else cfg.get('chapters')
            chapters = _load_chapters(
                raw_chapters, None if args.chapters is not None else cfg_base)
            base_dir = _path_value(
                _pick(args, cfg, 'base_dir', ('base_dir',)), cfg_base) \
                or (str(cfg_base) if cfg_base else None)
            options = dict(
                engine=_pick(args, cfg, 'engine', ('engine',), defaults.ENGINE),
                force=_pick(args, cfg, 'force', ('force',), defaults.FORCE),
                start=_pick(args, cfg, 'start', ('start',)),
                end=_pick(args, cfg, 'end', ('end',)),
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
                middlewares=_middlewares(
                    _pick(args, cfg, 'middleware', ('middlewares',)), cfg_base),
                chapters=chapters,
                base_dir=base_dir,
                log=lambda _msg: None,
            )
            write_out = bool(args.write or out_value)
            for index, image in enumerate(images, 1):
                with contextlib.ExitStack() as stack:
                    if work_value:
                        work = _direct_work(work_value, image, index)
                        Path(work).mkdir(parents=True, exist_ok=True)
                    else:
                        work = stack.enter_context(
                            tempfile.TemporaryDirectory(prefix='p2md-direct-'))
                    result = convert_image(str(image), work_dir=str(work), **options)
                    if not result.markdowns:
                        raise ConvertError(f"没有生成 Markdown: {image}")
                    text = Path(result.markdowns[0]).read_text(encoding='utf-8')
                    if write_out:
                        target_dir = Path(out_value) if out_value else image.parent
                        target_dir.mkdir(parents=True, exist_ok=True)
                        target = target_dir / f"{image.stem}.md"
                        target.write_text(text, encoding='utf-8')
                        print(target)
                    else:
                        sys.stdout.write(text)
                        if not text.endswith('\n'):
                            sys.stdout.write('\n')
            return

        images = _path_value(
            _pick(args, cfg, 'images', ('images_dir',)), cfg_base)

        if args.command == 'ocr':
            from . import ocr as ocr_mod
            images = _require(images, 'images')
            work = _resolve_work(work_value, images)
            img_for_ocr, _start, _end = ocr_mod.run(
                images, str(work),
                start=_pick(args, cfg, 'start', ('start',)),
                end=_pick(args, cfg, 'end', ('end',)),
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
            img = str(paths.ocr_tmp_dir(work))
            pages = args.pages or None
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
            from .api import build_stage
            work = _resolve_work(work_value)
            raw_chapters = args.chapters if args.chapters is not None else cfg.get('chapters')
            chapters = _load_chapters(raw_chapters, None if args.chapters is not None else cfg_base)
            build_stage(str(work), out_dir=out_value, chapters=chapters, log=print)
            _print_summary(work)
            return

        if args.command == 'pipeline':
            from .api import convert_images
            images = _require(images, 'images')
            work = _resolve_work(work_value, images)
            base_dir = _path_value(_pick(args, cfg, 'base_dir', ('base_dir',)), cfg_base) \
                or (str(cfg_base) if cfg_base else None)
            middlewares = _middlewares(
                _pick(args, cfg, 'middleware', ('middlewares',)), cfg_base)
            raw_chapters = args.chapters if args.chapters is not None else cfg.get('chapters')
            chapters = _load_chapters(raw_chapters, None if args.chapters is not None else cfg_base)
            convert_images(
                images, work_dir=str(work), out_dir=out_value, base_dir=base_dir,
                engine=_pick(args, cfg, 'engine', ('engine',), defaults.ENGINE),
                force=_pick(args, cfg, 'force', ('force',), defaults.FORCE),
                start=_pick(args, cfg, 'start', ('start',)),
                end=_pick(args, cfg, 'end', ('end',)),
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
                middlewares=middlewares, chapters=chapters, log=print)
            _print_summary(work)
    except ConvertError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
