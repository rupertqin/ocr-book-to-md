# -*- coding: utf-8 -*-
"""统一 CLI 入口：ocr / detect / build / pdf 子命令，projects 模式。

用法（对齐 document-factory）:
    python main.py init  -p ./projects/进化心理学
    python main.py ocr   -p ./projects/进化心理学
    python main.py detect -p ./projects/进化心理学
    python main.py build -p ./projects/进化心理学
    python main.py pdf   -p ./projects/进化心理学
    python main.py all   -p ./projects/进化心理学   # ocr→detect→build→pdf 一键
"""

import argparse
import os
import sys

from .project import Project
from .config import Config


def _print_summary(project):
    out = project.output_dir
    article_dir = project.get_article_dir()
    n_art = len([f for f in os.listdir(article_dir) if f.endswith('.md')]) if os.path.isdir(article_dir) else 0
    img_dir = project.get_images_out_dir()
    n_img = len([f for f in os.listdir(img_dir) if f.endswith('.png')]) if os.path.isdir(img_dir) else 0
    print(f"\n项目: {project.path}")
    print(f"  content/  {n_art} 章 Markdown（document-factory 输入）")
    print(f"  content/images/  {n_img} 张裁图")
    print(f"  md/      章节 Markdown（传统目录，便于复核）")
    print(f"  blocks/  布局中间产物")
    print(f"  ocr/     OCR 中间产物（可删除）")


def main():
    parser = argparse.ArgumentParser(
        description='ocr-book-to-md - 扫描书转 Markdown（OCR → 图/标题/正文三分）',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
命令:
  init    初始化项目（生成 config.yaml + .env + data/images/）
  ocr     ① OCR（Swift macOS Vision）→ output/ocr/
  detect  ② 布局分析 → output/blocks + output/figs
  build   ③ 组装章节 → output/md + output/content/article_N.md
  pdf     ④ 调用 document-factory 生成 PDF
  all     一键 ocr→detect→build→pdf

示例:
  python main.py init   -p ./projects/进化心理学
  python main.py all    -p ./projects/进化心理学
        """
    )
    parser.add_argument('--version', action='version', version='%(prog)s 0.1.0')
    subparsers = parser.add_subparsers(dest='command', help='可用命令')

    # init
    init_p = subparsers.add_parser('init', help='初始化项目')
    init_p.add_argument('-p', '--project', default='.', help='项目目录（默认: 当前目录）')

    # ocr
    ocr_p = subparsers.add_parser('ocr', help='OCR 步骤')
    ocr_p.add_argument('-p', '--project', default='.', help='项目目录')
    ocr_p.add_argument('--images', default=None, help='截图目录（默认 config.yaml 的 book.images_dir 或 data/images）')
    ocr_p.add_argument('--start', type=int, default=None, help='起始页')
    ocr_p.add_argument('--end', type=int, default=None, help='结束页')
    ocr_p.add_argument('--force-ocr', action='store_true', default=None, help='忽略 OCR 缓存')

    # detect
    det_p = subparsers.add_parser('detect', help='布局分析步骤')
    det_p.add_argument('-p', '--project', default='.', help='项目目录')
    det_p.add_argument('--images', default=None, help='截图目录（默认自动：预处理 JPEG 或原图）')
    det_p.add_argument('pages', nargs='*', type=int, help='仅处理指定页（调试用）')

    # build
    bld_p = subparsers.add_parser('build', help='组装 Markdown')
    bld_p.add_argument('-p', '--project', default='.', help='项目目录')
    bld_p.add_argument('--chapters', default=None, help='人工章节结构 JSON')

    # pdf
    pdf_p = subparsers.add_parser('pdf', help='调用 document-factory 生成 PDF')
    pdf_p.add_argument('-p', '--project', default='.', help='项目目录')

    # all
    all_p = subparsers.add_parser('all', help='一键 ocr→detect→build→pdf')
    all_p.add_argument('-p', '--project', default='.', help='项目目录')
    all_p.add_argument('--images', default=None, help='截图目录')
    all_p.add_argument('--force-ocr', action='store_true', default=None, help='忽略 OCR 缓存')
    all_p.add_argument('--no-pdf', action='store_true', help='不生成 PDF，只到 build 为止')

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
        print(f"  请把书的截图放入 {project.images_dir}/")
        print(f"  然后在 {config.config_file} 配置 book.name 等")
        return

    if not project.exists():
        print(f"错误: 项目目录不存在: {project.path}，请先 python main.py init -p {args.project}")
        sys.exit(1)
    project.ensure()

    if args.command == 'ocr':
        from . import ocr as ocr_mod
        images = args.images or config.get_images_dir() or str(project.images_dir)
        force = args.force_ocr if args.force_ocr is not None else config.get_force_ocr()
        img_for_ocr, start, end = ocr_mod.run(project, images,
                                              start=args.start or config.get_start_page(),
                                              end=args.end or config.get_end_page(),
                                              force_ocr=force)
        # 记录预处理图目录，供 detect 默认使用
        project.config_file  # noqa
        print(f"OCR 完成。detect 将使用预处理图目录: {img_for_ocr}")
        _print_summary(project)
        return

    if args.command == 'detect':
        from . import detect as detect_mod
        # detect 默认用预处理 JPEG 目录；若原图就是 pN.png 命名且非真 PNG，可用原图
        images = args.images
        if not images:
            images = config.get_images_dir() or str(project.images_dir)
        # 优先用 OCR 预处理后的 JPEG 目录（统一 pN.jpg，规避 PNG SIGBUS）
        pre_tmp = project.output_dir / "_ocr_tmp"
        if pre_tmp.is_dir():
            images = str(pre_tmp)
        detect_mod.detect(project, images, pages=args.pages)
        _print_summary(project)
        return

    if args.command == 'build':
        from . import build_md as bm
        bm.build(project, chapters=args.chapters)
        _print_summary(project)
        return

    if args.command == 'pdf':
        from . import pdf as pdf_mod
        pdf_mod.make_pdf(project, config)
        return

    if args.command == 'all':
        from . import ocr as ocr_mod
        from . import detect as detect_mod
        from . import build_md as bm
        from . import pdf as pdf_mod

        images = args.images or config.get_images_dir() or str(project.images_dir)
        force = args.force_ocr if args.force_ocr is not None else config.get_force_ocr()

        print("=" * 40)
        print("① OCR")
        print("=" * 40)
        img_for_ocr, start, end = ocr_mod.run(project, images, force_ocr=force)

        print("=" * 40)
        print("② 布局分析")
        print("=" * 40)
        detect_mod.detect(project, img_for_ocr)

        print("=" * 40)
        print("③ 组装 Markdown")
        print("=" * 40)
        bm.build(project)

        _print_summary(project)

        if args.no_pdf:
            print("\n（--no-pdf，跳过 PDF 生成）")
            return

        print("=" * 40)
        print("④ 生成 PDF（document-factory）")
        print("=" * 40)
        pdf_mod.make_pdf(project, config)
        return


if __name__ == '__main__':
    main()
