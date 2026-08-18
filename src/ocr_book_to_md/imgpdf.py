# -*- coding: utf-8 -*-
"""图片直转 PDF：不需要 OCR → MD 流程，直接把图片文件夹做成 PDF。

设计（复用 document-factory 的机制）：
- 封面（文件夹下 `cover.jpg` / `cover.png`，若有）走 **cover_images**：
  每张图一页、整页铺满 A4、**无边距**（document-factory 的 cover_images 故意如此）。
- 内容页（页面图）走 **正文文章**：放到一篇文章里，每张图一个 `<div class="page-break">`，
  每页一图、受正文 `@page` 边距约束（**有边距**）。
- 单篇文章 → document-factory 不生成目录；`numbered=false` 去掉标题编号。

产物写入项目：
    output/content/article_0.md     单篇文章（标题=书名，正文=每页一图）
    output/content/images/          内容页图副本
    output/imgpdf_source/           复制的封面源图（供 cover_images 引用）
    config.yaml 写入 pdf.cover_images / show_cover=false / numbered=false / title
"""

import os
import re
import shutil

PAGE_RE = re.compile(r"(\d+)")


def _sorted_images(img_dir):
    """扫描图片目录，按文件名数字页号排序，返回 (文件名, 页号) 列表。"""
    files = []
    for f in os.listdir(img_dir):
        if not f.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp")):
            continue
        m = PAGE_RE.search(f)
        page = int(m.group(1)) if m else 10 ** 9  # 无数字的排最后
        files.append((f, page))
    files.sort(key=lambda t: t[1])
    return files


def prepare(project, img_dir, book_name=None):
    """把图片文件夹整理为 document-factory 可消费的项目结构。

    Args:
        project: Project 实例
        img_dir: 页面图片目录
        book_name: 书名（文章标题与 PDF 标题；None 用项目名）
    Returns:
        (cover_images 相对项目路径列表, 内容页数)
    """
    if not os.path.isdir(img_dir):
        raise SystemExit(f"错误：图片目录不存在: {img_dir}")
    images = _sorted_images(img_dir)
    if not images:
        raise SystemExit(f"错误：{img_dir} 下没有图片文件")

    name = book_name or project.path.name
    # 分离封面（cover.jpg / cover.png）与内容页
    cover_file = None
    pages = []
    for f, page in images:
        base = f.lower()
        if base in ("cover.jpg", "cover.jpeg", "cover.png"):
            cover_file = f
            continue
        pages.append((f, page))
    if not pages:
        raise SystemExit(f"错误：{img_dir} 下没有页面图片（除封面外）")

    # 复制封面到 imgpdf_source（供 cover_images 相对项目根引用）
    src_dir = project.output_dir / "imgpdf_source"
    os.makedirs(src_dir, exist_ok=True)
    if cover_file:
        shutil.copy2(os.path.join(img_dir, cover_file), os.path.join(src_dir, cover_file))

    # 内容页图复制到 content_img/images/，并生成单篇文章（每图一页，正文边距）
    # 用独立的 content_img 目录，避免与 text 流的 output/content/ 互相污染
    img_content = project.get_content_img_dir()
    article_dir = img_content
    images_out = img_content / "images"
    os.makedirs(article_dir, exist_ok=True)
    os.makedirs(images_out, exist_ok=True)

    # 清掉旧 article 与旧 images（避免残留）
    for f in os.listdir(article_dir):
        if f.endswith(".md"):
            os.remove(os.path.join(article_dir, f))
    for f in os.listdir(images_out):
        p = os.path.join(images_out, f)
        if os.path.isfile(p):
            os.remove(p)

    # 复制内容页图
    for f, _ in pages:
        shutil.copy2(os.path.join(img_dir, f), os.path.join(images_out, f))

    # 单篇文章：空标题（避免 document-factory 渲染标题页），正文=每张图一个 page-break
    # 空标题用仅含 "#" 的行表示（extract_title 识别为 ""，两个引擎均跳过标题渲染）
    lines = ["#", ""]
    for i, (f, _) in enumerate(pages):
        if i > 0:
            lines.append('<div class="page-break"></div>')
        lines.append(f"![](images/{f})")
    with open(os.path.join(article_dir, "article_0.md"), "w", encoding="utf-8") as fp:
        fp.write("\n".join(lines) + "\n")

    # cover_images：仅封面（若无封面则为空）
    cover_images = []
    if cover_file:
        cover_images.append(f"output/imgpdf_source/{cover_file}")

    # 写配置
    from .config import Config
    config = Config(str(project.path))
    config.set("pdf.cover_images", cover_images)
    config.set("pdf.show_cover", False)
    config.set("pdf.numbered", False)
    config.set("pdf.title", name)

    print(f"图片直转 PDF 准备完成：{len(pages)} 页内容图" +
          (f" + 封面 {cover_file}" if cover_file else "（无封面）"))
    if cover_images:
        print(f"  封面走 cover_images（整页铺满无边距）：{cover_images}")
    print(f"  内容页走正文文章（每页一图，有边距）：{len(pages)} 页")
    return cover_images, len(pages)


def _rename_imgpdf_outputs(project):
    """把 document-factory 生成的默认名产物加 .img 二级后缀。

    document-factory 生成 <书名>.unc.pdf（未压缩），若 compress 还产出 <书名>.pdf。
    img 流把两者重命名加 .img，避免占用 text 流的 <书名>.pdf / <书名>.unc.pdf：
      <书名>.unc.pdf  → <书名>.img.unc.pdf
      <书名>.pdf      → <书名>.img.pdf（compress 压缩版）
    """
    out_dir = project.output_dir / "normal"
    from .config import Config
    cfg = Config(str(project.path))
    name = cfg.get_book_name() or project.path.name
    for src, dst in [
        (out_dir / f"{name}.unc.pdf", out_dir / f"{name}.img.unc.pdf"),
        (out_dir / f"{name}.pdf", out_dir / f"{name}.img.pdf"),
    ]:
        if src.exists():
            if dst.exists():
                dst.unlink()
            src.rename(dst)
            print(f"  原图片 PDF: {dst.name}")


def make_imgpdf(project, img_dir, book_name=None, no_pdf=False):
    """图片直转 PDF 完整流程：准备结构 → 调用 document-factory。

    img 流用独立的 content_img 目录（document-factory -i），生成默认名的
    <书名>.unc.pdf（未压缩）/ <书名>.pdf（compress 压缩版），随后重命名为
    <书名>.img.unc.pdf / <书名>.img.pdf（保留 .img，不占用 text 流的文件名）。
    no_pdf=True 时只准备不生成 PDF（调试用）。
    """
    from . import pdf as pdf_mod
    cover_images, n_pages = prepare(project, img_dir, book_name)
    if no_pdf:
        print(f"（--no-pdf，跳过 PDF 生成；已准备 {n_pages} 页内容图）")
        return None
    pdf_mod.make_pdf(project, None,
                     input_dir=str(project.get_content_img_dir()))
    _rename_imgpdf_outputs(project)
    return n_pages
