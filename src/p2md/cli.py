# -*- coding: utf-8 -*-
"""p2md 统一 CLI：init / pdftotext / ocr / detect / build / pipeline。

只产出 Markdown 与裁图；不生成 PDF，不依赖 document-factory。
OCR 在 macOS 用 Swift Vision，其他平台用 RapidOCR。
"""
from __future__ import annotations

import argparse
import os
import sys

from .project import Project
from .config import Config


def _print_summary(project: Project) -> None:
    from pathlib import Path

    out = project.output_dir
    article_dir = project.get_article_dir()
    n_art = len([f for f in os.listdir(article_dir) if f.endswith('.md')]) \
        if os.path.isdir(article_dir) else 0
    img_dir = project.get_images_out_dir()
    n_img = len([f for f in os.listdir(img_dir) if f.endswith('.png')]) \
        if os.path.isdir(img_dir) else 0
    print(f"\n项目: {project.path}")
    print(f"  content/  {n_art} 章 Markdown")
    print(f"  content/images/  {n_img} 张裁图")
    print(f"  md/      章节 Markdown（便于复核）")
    print(f"  blocks/  布局中间产物（{len(list(Path(out / 'blocks').glob('*.json'))) if (out / 'blocks').is_dir() else 0} 页）")
    print(f"  ocr/     OCR 中间产物")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='p2md',
        description='p2md - 扫描书 / 长截图转 Markdown（OCR → 图/标题/正文三分 → 章节 Markdown）',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
命令:
  init        初始化项目（生成 config.yaml + .env + assets/）
  pdftotext   PDF 文字层直取 Markdown（无文字层的扫描 PDF 用 pipeline）
  ocr         OCR → output/ocr/（macOS: Vision；其他平台: RapidOCR）
  detect      版面分析 → output/blocks + output/figs
  build       组装章节 → output/md + output/content/article_N.md
  pipeline    全部流程：ocr → detect → build

示例:
  p2md init -p ./projects/进化心理学
  p2md pipeline -p ./projects/进化心理学
  p2md build -p ./projects/进化心理学
        """
    )
    parser.add_argument('--version', action='version', version='p2md 0.2.0')
    sub = parser.add_subparsers(dest='command', help='可用命令')

    init_p = sub.add_parser('init', help='初始化项目')
    init_p.add_argument('-p', '--project', default='.', help='项目目录（默认当前目录）')

    pdft_p = sub.add_parser('pdftotext', help='PDF 文字层 → Markdown（免 OCR）')
    pdft_p.add_argument('-p', '--project', default='.', help='项目目录')
    pdft_p.add_argument('--dpi', type=int, default=150, help='嵌图渲染分辨率（默认 150）')
    pdft_p.add_argument('--out', default=None, help='输出目录（默认 output/pdf_text/）')

    ocr_p = sub.add_parser('ocr', help='OCR 步骤')
    ocr_p.add_argument('-p', '--project', default='.', help='项目目录')
    ocr_p.add_argument('--assets', default=None, help='输入素材目录（图片或 PDF）')
    ocr_p.add_argument('--start', type=int, default=None, help='起始页')
    ocr_p.add_argument('--end', type=int, default=None, help='结束页')
    ocr_p.add_argument('-f', '--force', action='store_true', default=None,
                       help='忽略 OCR 缓存，重跑全部页')

    det_p = sub.add_parser('detect', help='布局分析步骤')
    det_p.add_argument('-p', '--project', default='.', help='项目目录')
    det_p.add_argument('--assets', default=None, help='输入素材目录（默认预处理 JPEG）')
    det_p.add_argument('pages', nargs='*', type=int, help='仅处理指定页（调试用）')

    bld_p = sub.add_parser('build', help='组装 Markdown')
    bld_p.add_argument('-p', '--project', default='.', help='项目目录')
    bld_p.add_argument('--chapters', default=None, help='人工章节结构 JSON')

    pipe_p = sub.add_parser('pipeline', help='全流程：ocr → detect → build')
    pipe_p.add_argument('-p', '--project', default='.', help='项目目录')
    pipe_p.add_argument('--assets', default=None, help='输入素材目录（图片或 PDF）')
    pipe_p.add_argument('-f', '--force', action='store_true', default=None,
                        help='忽略 OCR 缓存，重跑全部页')
    return parser


def main() -> None:
    parser = _parser()
    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        sys.exit(1)

    project = Project(args.project)
    config = Config(args.project)

    if args.command == 'init':
        project.ensure()
        if not config.config_file.exists():
            config.write_default()
        config.write_env_template()
        print(f"✓ 已初始化项目: {project.path}")
        print(f"  请把截图或 PDF 放入 {project.get_input_dir()}/")
        print(f"  然后运行: p2md pipeline -p {project.path}")
        return

    if not project.exists():
        print(f"错误: 项目目录不存在: {project.path}，请先 p2md init -p {args.project}")
        sys.exit(1)
    project.ensure()

    if args.command == 'pdftotext':
        from . import pdfsource
        from pathlib import Path as _Path
        inp = config.get_assets_dir() or str(project.get_input_dir())
        pdfs = pdfsource.find_pdfs(inp)
        if not pdfs:
            print(f"目录 {inp} 里没有 .pdf")
            return
        out_dir = _Path(args.out) if args.out else project.output_dir / "pdf_text"
        img_dir = out_dir / "images"
        out_dir.mkdir(parents=True, exist_ok=True)
        img_dir.mkdir(parents=True, exist_ok=True)
        n_ok = n_skip = 0
        for pdf in pdfs:
            md = pdfsource.pdf_to_markdown(os.path.join(inp, pdf), str(img_dir), dpi=args.dpi)
            if not md:
                print(f"  [跳过-无文字层] {pdf}（用 pipeline 渲染 OCR）")
                n_skip += 1
                continue
            out_md = out_dir / f"{os.path.splitext(pdf)[0]}.md"
            out_md.write_text(md, encoding='utf-8')
            n_ok += 1
            print(f"  ✓ {pdf} → {out_md.name}")
        print(f"\npdftotext：{n_ok} 篇成功，{n_skip} 篇无文字层")
        return

    if args.command == 'ocr':
        from . import ocr as ocr_mod
        assets = args.assets or config.get_assets_dir() or str(project.get_input_dir())
        force = args.force if args.force is not None else config.get_force_ocr()
        img_for_ocr, _start, _end = ocr_mod.run(
            project, assets, start=args.start or config.get_start_page(),
            end=args.end or config.get_end_page(), force_ocr=force)
        print(f"OCR 完成。detect 将使用预处理图目录: {img_for_ocr}")
        _print_summary(project)
        return

    if args.command == 'detect':
        from . import detect as detect_mod
        from .middleware import run_all as run_ocr_hooks
        run_ocr_hooks(project)
        assets = args.assets
        if not assets:
            pre_tmp = project.output_dir / "_ocr_tmp"
            assets = str(pre_tmp) if pre_tmp.is_dir() else (
                config.get_assets_dir() or str(project.get_input_dir()))
        detect_mod.detect(project, assets, pages=args.pages or None)
        _print_summary(project)
        return

    if args.command == 'build':
        from . import build_md as bm
        bm.build(project, chapters=args.chapters)
        _print_summary(project)
        return

    if args.command == 'pipeline':
        from . import build_md as bm
        from . import detect as detect_mod
        from . import ocr as ocr_mod
        from .middleware import run_all as run_ocr_hooks

        assets = args.assets or config.get_assets_dir() or str(project.get_input_dir())
        force = args.force if args.force is not None else config.get_force_ocr()

        print("=" * 40, "\n① OCR\n", "=" * 40, sep='')
        img_for_ocr, _start, _end = ocr_mod.run(project, assets, force_ocr=force)

        print("=" * 40, "\n② 布局分析\n", "=" * 40, sep='')
        run_ocr_hooks(project)
        detect_mod.detect(project, img_for_ocr)

        print("=" * 40, "\n③ 组装 Markdown\n", "=" * 40, sep='')
        bm.build(project)
        _print_summary(project)
        return


if __name__ == '__main__':
    main()
