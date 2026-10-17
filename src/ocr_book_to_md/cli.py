# -*- coding: utf-8 -*-
"""统一 CLI 入口：ocr / detect / build / pdf 子命令，projects 模式。

用法（对齐 document-factory）:
    python main.py init  -p ./projects/进化心理学
    python main.py ocr   -p ./projects/进化心理学
    python main.py detect -p ./projects/进化心理学
    python main.py build -p ./projects/进化心理学
    python main.py pdf   -p ./projects/进化心理学
    python main.py pipeline -p ./projects/进化心理学   # 全部流程（--pdf text/img/layed）
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
  init     初始化项目（生成 config.yaml + .env + assets/）
  ocr      ① OCR（Swift macOS Vision）→ output/ocr/
  detect   ② 布局分析 → output/blocks + output/figs
  build    ③ 组装章节 → output/md + output/content/article_N.md
  pdf      生成 PDF（--type 指定方式：text/img/layed）
  pipeline 全部流程，用 --pdf 指定最终 PDF 方式
  imgpdf / layedpdf   兼容别名（= pdf --type img / layed）

PDF 制作方式（pdf --type / pipeline --pdf <方式>）:
  text      文字流：ocr→detect→build→pdf（document-factory 排版，文字可复制）
  img       原图片：imgpdf（页面图直接转 PDF，封面 cover.jpg）
  layed 文字置于图：ocr→detect→layedpdf（按原书坐标绝对定位混排）

示例:
  python main.py pdf -p ./projects/进化心理学 --type text
  python main.py pdf -p ./projects/进化心理学 --type layed
  python main.py pdf -p ./projects/进化心理学 --type img
  python main.py pipeline -p ./projects/进化心理学 --pdf layed
        """
    )
    parser.add_argument('--version', action='version', version='%(prog)s 0.1.0')
    subparsers = parser.add_subparsers(dest='command', help='可用命令')

    # init
    init_p = subparsers.add_parser('init', help='初始化项目')
    init_p.add_argument('-p', '--project', default='.', help='项目目录（默认: 当前目录）')

    # pdftotext：assets 里 .pdf 有文字层则直取为 markdown（免 OCR）
    pdft_p = subparsers.add_parser('pdftotext', help='PDF 文字层 → markdown（免 OCR）')
    pdft_p.add_argument('-p', '--project', default='.', help='项目目录')
    pdft_p.add_argument('--dpi', type=int, default=150, help='嵌图渲染分辨率（默认 150）')
    pdft_p.add_argument('--out', default=None, help='输出目录（默认 output/pdf_text/）')

    # ocr
    ocr_p = subparsers.add_parser('ocr', help='OCR 步骤')
    ocr_p.add_argument('-p', '--project', default='.', help='项目目录')
    ocr_p.add_argument('--assets', default=None, help='输入素材目录（图片或 PDF；默认 config.yaml 的 book.assets_dir 或 assets/）')
    ocr_p.add_argument('--start', type=int, default=None, help='起始页')
    ocr_p.add_argument('--end', type=int, default=None, help='结束页')
    ocr_p.add_argument('-f', '--force', action='store_true', default=None, help='忽略缓存（OCR 缓存，重跑全部页）')

    # detect
    det_p = subparsers.add_parser('detect', help='布局分析步骤')
    det_p.add_argument('-p', '--project', default='.', help='项目目录')
    det_p.add_argument('--assets', default=None, help='输入素材目录（默认自动：预处理 JPEG 或原图）')
    det_p.add_argument('pages', nargs='*', type=int, help='仅处理指定页（调试用）')

    # build
    bld_p = subparsers.add_parser('build', help='组装 Markdown')
    bld_p.add_argument('-p', '--project', default='.', help='项目目录')
    bld_p.add_argument('--chapters', default=None, help='人工章节结构 JSON')

    # pdf（统一入口，-t/--type 指定三种方式）
    pdf_p = subparsers.add_parser('pdf', help='生成 PDF（-t/--type 指定方式：text/img/layed）')
    pdf_p.add_argument('-p', '--project', default='.', help='项目目录')
    pdf_p.add_argument('-t', '--type', dest='pdf_type', default='text',
                       choices=['text', 'img', 'layed'],
                       help='PDF 方式：text(文字流)/img(原图片)/layed(文字置于图)（默认 text）')
    # text / layed 通用
    pdf_p.add_argument('--output', default=None, help='输出 PDF 路径（layed 用）')
    pdf_p.add_argument('--font-size', type=float, default=11.0, help='正文基准字号 pt（layed 用，默认 11）')
    pdf_p.add_argument('--font', default=None, help='字体族（layed 用，默认读 config.pdf.font_family）')
    pdf_p.add_argument('--no-justify', action='store_true', help='关闭段落两端对齐（layed 用）')
    pdf_p.add_argument('--width-scale', type=float, default=None, help='文字容器宽度系数（layed 用）')
    pdf_p.add_argument('--letter-spacing', default=None, help='CSS letter-spacing（layed 用）')
    pdf_p.add_argument('--punct-compress', default=None, help='标点压缩 CSS 值（layed 用）')
    # img 专用
    pdf_p.add_argument('--assets', default=None, help='输入素材目录（img 用，默认 assets/，封面 cover.jpg）')
    pdf_p.add_argument('--title', default=None, help='PDF 标题（img 用，默认书名）')
    pdf_p.add_argument('--no-pdf', action='store_true', help='只准备结构不生成（img 用）')
    pdf_p.add_argument('pages', nargs='*', type=int, help='仅渲染指定页（layed 调试用）')

    # imgpdf / layedpdf（兼容别名，等价于 pdf --type img / layed）
    img_p = subparsers.add_parser('imgpdf', help='（兼容别名）= pdf --type img')
    img_p.add_argument('-p', '--project', default='.', help='项目目录')
    img_p.add_argument('--assets', default=None, help='输入素材目录')
    img_p.add_argument('--title', default=None, help='PDF 标题')
    img_p.add_argument('--no-pdf', action='store_true', help='只准备结构，不生成 PDF')

    lay_p = subparsers.add_parser('layedpdf', help='（兼容别名）= pdf --type layed')
    lay_p.add_argument('-p', '--project', default='.', help='项目目录')
    lay_p.add_argument('--output', default=None, help='输出 PDF 路径')
    lay_p.add_argument('--font-size', type=float, default=11.0, help='正文基准字号 pt（默认 11）')
    lay_p.add_argument('--font', default=None, help='字体族')
    lay_p.add_argument('--no-justify', action='store_true', help='关闭段落两端对齐')
    lay_p.add_argument('--width-scale', type=float, default=None, help='文字容器宽度系数')
    lay_p.add_argument('--letter-spacing', default=None, help='CSS letter-spacing')
    lay_p.add_argument('--punct-compress', default=None, help='标点压缩 CSS 值')
    lay_p.add_argument('pages', nargs='*', type=int, help='仅渲染指定页（调试用）')

    # pipeline（全部流程，--pdf 指定最终方式）
    pipe_p = subparsers.add_parser('pipeline', help='全部流程，用 --pdf 指定最终 PDF 方式')
    pipe_p.add_argument('-p', '--project', default='.', help='项目目录')
    pipe_p.add_argument('--pdf', '--type', dest='pdf_mode', default=None,
                        choices=['text', 'img', 'layed'],
                        help='最终 PDF 方式：text(文字流)/img(原图片)/layed(文字置于图)（默认读 config.pipeline.pdf 或 text；--type 为别名）')
    pipe_p.add_argument('--assets', default=None, help='输入素材目录（图片或 PDF）')
    pipe_p.add_argument('-f', '--force', action='store_true', default=None,
                        help='全部步骤不使用缓存（当前指 OCR 缓存，重跑全部页）')
    pipe_p.add_argument('--no-pdf', action='store_true', help='不生成 PDF，只到流程中间产物为止')

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
        print(f"  请把书的截图或 PDF 放入 {project.get_input_dir()}/")
        print(f"  然后在 {config.config_file} 配置 book.name 等")
        return

    if not project.exists():
        print(f"错误: 项目目录不存在: {project.path}，请先 python main.py init -p {args.project}")
        sys.exit(1)
    project.ensure()

    if args.command == 'pdftotext':
        from . import pdfsource
        from pathlib import Path as _P
        inp = config.get_assets_dir() or str(project.get_input_dir())
        pdfs = pdfsource.find_pdfs(inp)
        if not pdfs:
            print(f"目录 {inp} 里没有 .pdf")
            return
        out_dir = _P(args.out) if args.out else project.output_dir / "pdf_text"
        img_dir = out_dir / "images"
        out_dir.mkdir(parents=True, exist_ok=True)
        img_dir.mkdir(parents=True, exist_ok=True)
        n_ok = n_skip = 0
        for pdf in pdfs:
            pdf_path = os.path.join(inp, pdf)
            md = pdfsource.pdf_to_markdown(pdf_path, str(img_dir),
                                           dpi=args.dpi)
            if not md:
                print(f"  [跳过-无文字层] {pdf}（纯图/扫描，用渲染 OCR 流程）")
                n_skip += 1
                continue
            stem = os.path.splitext(pdf)[0]
            # 清理标题里含 / 的文件名隐患：用文章内部标题
            out_md = out_dir / f"{stem}.md"
            with open(out_md, "w", encoding="utf-8") as f:
                f.write(md)
            n_ok += 1
            print(f"  ✓ {pdf} → {out_md.name}")
        print(f"\npdftotext：{n_ok} 篇文字层直取成功，{n_skip} 篇无文字层（建议走渲染 OCR）")
        return

    if args.command == 'ocr':
        from . import ocr as ocr_mod
        assets = args.assets or config.get_assets_dir() or str(project.get_input_dir())
        force = args.force
        if force is None:
            force = config.get_force_ocr()
        img_for_ocr, start, end = ocr_mod.run(project, assets,
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
        assets = args.assets
        if not assets:
            assets = config.get_assets_dir() or str(project.get_input_dir())
        # 优先用 OCR 预处理后的 JPEG 目录（统一 pN.jpg，规避 PNG SIGBUS）
        # OCR 中间件 + 纠错钩子（per-project）：先清理 pages_json，再修正 OCR 错字，
        # 之后 detect 才产干净 blocks
        from .middleware import run_all as run_ocr_hooks
        run_ocr_hooks(project)
        pre_tmp = project.output_dir / "_ocr_tmp"
        if pre_tmp.is_dir():
            assets = str(pre_tmp)
        detect_mod.detect(project, assets, pages=args.pages or None)
        _print_summary(project)
        return

    if args.command == 'build':
        from . import build_md as bm
        bm.build(project, chapters=args.chapters)
        _print_summary(project)
        return

    # 三种 PDF 方式统一由 pdf 命令分发（--type），imgpdf/layedpdf 为兼容别名转发
    if args.command == 'pdf':
        _pdf_type = args.pdf_type
    elif args.command == 'imgpdf':
        _pdf_type = 'img'
    elif args.command == 'layedpdf':
        _pdf_type = 'layed'
    else:
        _pdf_type = None

    if _pdf_type == 'img':
        from . import imgpdf as imgpdf_mod
        assets = args.assets or config.get_assets_dir() or str(project.get_input_dir())
        imgpdf_mod.make_imgpdf(project, assets, book_name=args.title,
                               no_pdf=args.no_pdf)
        return

    if _pdf_type == 'layed':
        from . import layedpdf as layed_mod
        layed_mod.make_layedpdf(project, pages=args.pages or None,
                                output=args.output,
                                base_font_pt=args.font_size,
                                font_family=args.font,
                                justify=not args.no_justify,
                                width_scale=args.width_scale,
                                letter_spacing=args.letter_spacing,
                                punct_compress=args.punct_compress)
        return

    if _pdf_type == 'text':
        from . import pdf as pdf_mod
        pdf_mod.make_pdf(project, config)
        return

    if args.command == 'pipeline':
        from . import ocr as ocr_mod
        from . import detect as detect_mod
        from . import build_md as bm
        from . import pdf as pdf_mod
        from . import layedpdf as layed_mod
        from . import imgpdf as imgpdf_mod

        assets = args.assets or config.get_assets_dir() or str(project.get_input_dir())
        force = args.force
        if force is None:
            force = config.get_force_ocr()

        # 确定最终 PDF 方式：命令行 > config.pipeline.pdf > 默认
        pdf_mode = args.pdf_mode or config.get('pipeline.pdf', 'text')

        _labels = {'text': '文字流(text)', 'img': '原图片(img)', 'layed': '文字置于图(layed)'}
        print(f"流水线: {_labels.get(pdf_mode, pdf_mode)}")

        # 三种方式的前置差异：
        #   text      → ocr → detect → build → pdf
        #   img       → 直接 imgpdf（不需要 ocr/detect）
        #   layed → ocr → detect → layedpdf（不需要 build）
        if pdf_mode == 'img':
            # 纯图片流：直接 imgpdf
            print("=" * 40)
            print("图片直转 PDF（imgpdf）")
            print("=" * 40)
            imgpdf_mod.make_imgpdf(project, assets, no_pdf=args.no_pdf)
            return

        # text 和 layed 都需要 ocr + detect
        print("=" * 40)
        print("① OCR")
        print("=" * 40)
        img_for_ocr, start, end = ocr_mod.run(project, assets, force_ocr=force)

        print("=" * 40)
        print("② 布局分析")
        print("=" * 40)
        # OCR 中间件 + 纠错钩子（per-project）：先清理 pages_json，再修正 OCR 错字，
        # 之后 detect 才产干净 blocks
        from .middleware import run_all as run_ocr_hooks
        run_ocr_hooks(project)
        detect_mod.detect(project, img_for_ocr)

        if args.no_pdf:
            _print_summary(project)
            print("\n（--no-pdf，跳过 PDF 生成）")
            return

        if pdf_mode == 'layed':
            print("=" * 40)
            print("③ 按原书坐标混排 PDF（layedpdf）")
            print("=" * 40)
            layed_mod.make_layedpdf(project)
            _print_summary(project)
            return

        # text 流
        print("=" * 40)
        print("③ 组装 Markdown")
        print("=" * 40)
        bm.build(project)
        _print_summary(project)

        print("=" * 40)
        print("④ 生成 PDF（document-factory）")
        print("=" * 40)
        pdf_mod.make_pdf(project, config)
        return


if __name__ == '__main__':
    main()
