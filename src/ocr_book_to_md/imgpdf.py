# -*- coding: utf-8 -*-
"""图片直转 PDF：不需要 OCR → MD 流程，直接把图片文件夹做成 PDF。

设计（复用 document-factory 多文章 + 自动 TOC，与 img/layed 流统一）：
- 封面（文件夹下 `cover.jpg` / `cover.png`，若有）走 **cover_images**：
  每张图一页、整页铺满 A4、**无边距**（document-factory 的 cover_images 故意如此）。
- 内容页（页面图）走 **正文文章** 的每章一篇结构：每篇 article_N.md 内**每张
  图一个** `<section class="img-page">`，section 内 `<img>` 用**绝对定位**
  按 config.pdf.page_margin 缩放并贴到内容区。位置与缩放与 layed 完全一致。
- 单篇文章 → document-factory 不生成目录；`numbered=false` 去掉标题编号。

产物写入项目：
    output/content_img/article_N.md   每章一篇文章（每图一个绝对定位 section）
    output/content_img/images/        内容页图副本
    output/imgpdf_source/cover.*      复制的封面源图（cover_images 引用）
    config.yaml 写入 pdf.cover_images / show_cover=false / numbered=false / title
"""

import os
import re
import shutil
from pathlib import Path

from PIL import Image as PILImage

PAGE_RE = re.compile(r"(\d+)")


# 注入到每篇 img 文章的 <style> 块。两个目的：
#   1. 隐藏 .article h1：document-factory 自动注入的 `<h1 id="article-N">` 与
#      第一个图片落在不同页（H1 占第一页），产生"H1-only"空页。用
#      `position:absolute; 0 size; overflow:hidden` 清掉视觉占位但保留 anchor id
#      （display:none 会让 weasyprint 丢掉 TOC 链接注释）。
#   2. .img-page 绝对定位：每个 `.img-page` 是 210×297mm 容器（document-factory
#     的内容区与 document-factory 的 @page margin 完全一致（复用 config.pdf.page_margin），
#     让 img 的目录样式、底部页码、视觉边距与 text/layed 完全统一。
def _build_img_article_css(top_mm, right_mm, bottom_mm, left_mm):
    """构造 img 文章的 <style> 块。

    与 layed 的 .layed-page 完全一致：`.img-page` 大小 = A4 内容区
    （210-2*lr, 297-2*tb），视觉边距由 document-factory 的 @page margin 直接提供
    （不去覆盖 @page），section 内 `<img>` 以 (0,0) 为原点绝对定位。
    底部页码直接用 document-factory 的 @bottom-center——document-factory 已把底部
    margin clamp 到至少 PAGE_NUM_MIN_BOTTOM_MM，所以即使配置底部=0 页码也有空间显示，
    这里不再需要绝对定位覆盖，也不注入 @page imgpage。
    这样目录(TOC)、底部页码(@bottom-center)、页边距(text/layed)全部一致。
    """
    page_w_mm = 210.0
    page_h_mm = 297.0
    width_mm = page_w_mm - left_mm - right_mm
    height_mm = page_h_mm - top_mm - bottom_mm
    parts = ["<style>"]
    parts.append(
        # H1 隐藏（占位 0 视觉、保留 anchor，避免 H1-only 空页）
        ".article h1{position:absolute!important;top:0!important;left:0!important;"
        "width:0!important;height:0!important;margin:0!important;padding:0!important;"
        "font-size:0!important;line-height:0!important;color:transparent!important;"
        "overflow:hidden!important;visibility:hidden!important;}"
        # .article p 隐藏自身（绝对定位的 img 不需要 p 包裹 / 默认 1.2cm 外边距）
        ".article p{margin:0!important;padding:0!important;}"
        # .article img：覆盖 document-factory 默认 `margin:0.5cm auto` 带来的上外边距
        # 和 `max-width:100%` 对绝对定位 width 的干扰。
        # 重要：这里**不能**给 width/height 加 !important —— 否则会把每张 <img> 上
        # 我们的 inline `width:Nmm;height:Nmm` 一起覆盖成 auto，让 weasyprint 按图像
        # 像素原始尺寸渲染（1166×2342 → 308×620mm @ 96dpi），被 .img-page 的
        # overflow:hidden 裁切，表现为图片没等比缩小、且源图空白顶部变成页面上方留白。
        # 只重置 margin / padding / max-width 即可，宽高由 inline style 决定。
        ".article img{margin:0!important;padding:0!important;max-width:none!important;}"
        # .img-page section = A4 内容区（= document-factory clamp 后 @page 内容区），
        # 图像在 section 内绝对定位。底部页码走 document-factory 的 @bottom-center。
        f".img-page{{position:relative;width:{width_mm}mm;height:{height_mm}mm;"
        "overflow:hidden;break-after:page;page-break-after:always;}"
        ".img-page:last-of-type{break-after:auto;page-break-after:auto;}"
        ".img-page img{position:absolute;}"
    )
    parts.append("</style>")
    return "".join(parts)


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

    与 layed 流共用绝对定位 + 缩放机制：每张图按 page_margin 计算 top/left
    与 width/height，生成 `<section class="img-page"><img style="..."/></section>`。
    这保证 page_margin 真正控制图像位置与缩放，不再受文档工厂默认
    `.article img { margin: 0.5cm auto; max-width: 100%; }` 影响。

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

    # 过滤"完全空白、无信息"的图片（如透明白图）。优先读 OCR 预处理写的空白页记录
    # （<out>/blank_pages.txt），保证与 OCR 流判定一致；记录不存在时才现场检测并回写。
    from .ocr import read_blank_pages, write_blank_pages, _is_blank_image
    out_dir = str(project.output_dir)
    blank_nums = read_blank_pages(out_dir)
    if not blank_nums:
        # 无记录（如直接 img 未跑过 OCR）：现场检测所有页并回写，供后续统一过滤
        from PIL import Image as _PIL
        detected = []
        for f, page in pages:
            src_p = os.path.join(img_dir, f)
            try:
                with _PIL.open(src_p) as _im:
                    blank = _is_blank_image(_im)
            except Exception:
                blank = False
            if blank:
                detected.append(page)
        if detected:
            write_blank_pages(out_dir, detected)
        blank_nums = detected
    blank_set = set(blank_nums)
    pages_final = [(f, p) for f, p in pages if p not in blank_set]
    pages = pages_final
    if blank_nums:
        print(f"  img 已过滤 {len(blank_nums)} 张完全空白图片（无信息）：{sorted(blank_nums)}")

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

    # 复制内容页图——重新保存为无 ICC profile 的纯 PNG。
    # 原图（来自手机/扫描仪）通常带 ICC profile（如 Google sRGB），会让 weasyprint
    # 在 absolute positioning 下错位（图片缩小到 ~50% 高度并垂直居中，偏离 top:0）。
    # 重新保存为简单 PNG（去掉 icc_profile、去掉 dpi 等 EXIF）可让 weasyprint
    # 正确按 CSS width/height 渲染。详见 imgpdf._strip_image_meta。
    for f, _ in pages:
        _strip_image_meta(os.path.join(img_dir, f), os.path.join(images_out, f))

    # 读取 page_margin，计算内容区尺寸（与 layed 算法一致）。
    # 用 document-factory 的 effective_page_margin_mm：底部已 clamp 到至少
    # PAGE_NUM_MIN_BOTTOM_MM，与 @page 实际内容区一致，保证 .img-page 高度匹配。
    from .config import Config
    from document_factory.pdf_generator import effective_page_margin_mm
    cfg = Config(str(project.path))
    page_margin_str = cfg.get('pdf.page_margin', '1.5cm 2cm')
    top_mm, right_mm, bottom_mm, left_mm = effective_page_margin_mm(page_margin_str)
    page_w_mm = 210.0
    page_h_mm = 297.0
    width_mm = page_w_mm - left_mm - right_mm
    height_mm = page_h_mm - top_mm - bottom_mm

    # 缓存每张图的源尺寸（像素）
    page_size_cache = {}

    def get_page_size(fname):
        if fname not in page_size_cache:
            try:
                page_size_cache[fname] = PILImage.open(
                    os.path.join(str(images_out), fname)).size
            except Exception:
                page_size_cache[fname] = (1166, 2342)  # 已知 default
        return page_size_cache[fname]

    def render_image_section(fname):
        """生成单张图的 `<section class="img-page"><img style="position:absolute;..."/>`。

        与 layed 的 .layed-page 完全等价：`.img-page` 大小 = A4 内容区
        （width_mm × height_mm），section 自身以 (0,0) 为原点，@page margin 已在
        外层提供 inset（document-factory 的 @page 边距）。图像按 page_margin 计算
        scale + 居中偏移，落到 section 内绝对位置。这样 page_margin 真正生效，
        且目录、页码、边距与 layed 完全一致。
        """
        W, H = get_page_size(fname)
        scale = min(width_mm / W, height_mm / H)
        x_off = (width_mm - W * scale) / 2
        y_off = (height_mm - H * scale) / 2
        img_w = W * scale
        img_h = H * scale
        return (
            f'<section class="img-page">'
            f'<img src="images/{fname}" '
            f'style="position:absolute;left:{x_off:.2f}mm;top:{y_off:.2f}mm;'
            f'width:{img_w:.2f}mm;height:{img_h:.2f}mm;"/>'
            f'</section>'
        )

    # 按章节切分多篇文章（每章一篇，标题=章节标题），使 document-factory 生成 TOC。
    # 章节页码与图片页号对应（都来自 OCR/detect）。
    from .build_md import auto_chapters
    try:
        chapters = auto_chapters(str(project.get_blocks_dir()))
    except SystemExit:
        chapters = None

    def article_md(title, page_list):
        """生成单篇 article markdown：title + 每页一个 `<section class="img-page">`。

        注意：**不要在 section 之间插 `<div class="page-break">`**。`.img-page` 自带
        `break-after: page`，若再叠加 document-factory 的 `.page-break{break-after:page}`，
        两个连续分页会把相邻图片之间挤出**一整张空白页**（表现为"正常图/白图相间"）。
        """
        css_block = _build_img_article_css(top_mm, right_mm, bottom_mm, left_mm)
        lines = [f"# {title}", "", css_block, ""]
        for f in page_list:
            lines.append(render_image_section(f))
        return "\n".join(lines) + "\n"

    if chapters:
        written = 0
        for kind, slug, fname, title, start, end in chapters:
            if kind == 'front':
                continue  # 前言/版权不算章节，不单独成篇
            chapter_pages = [f for f, p in pages if start <= p <= end]
            if not chapter_pages:
                continue
            with open(os.path.join(str(article_dir), f"article_{written}.md"),
                      "w", encoding="utf-8") as fp:
                fp.write(article_md(title, chapter_pages))
            written += 1
        if written == 0:
            chapters = None
    if chapters is None:
        # 无章节：单篇，空标题
        all_pages = [f for f, _ in pages]
        with open(os.path.join(str(article_dir), "article_0.md"),
                  "w", encoding="utf-8") as fp:
            fp.write(article_md("", all_pages))

    # cover_images：仅封面（若无封面则为空）
    cover_images = []
    if cover_file:
        cover_images.append(f"output/imgpdf_source/{cover_file}")

    # 写配置 — 与 text/layed 流完全一致：document-factory 的 @page 边距直接用
    # 用户 config.pdf.page_margin（如 "1.5cm 2cm"），不覆盖、也不在 article 内注入
    # `@page{margin:0}`（否则会全局覆盖 @page 边距，导致 TOC 无边距、页码跑到页外）。
    # img 的 .img-page section 大小 = A4 内容区，图像在 section 内绝对定位。这样
    # 目录(TOC)、底部页码(@bottom-center)、页边距全部与其他模式统一。
    cfg.set("pdf.cover_images", cover_images)
    cfg.set("pdf.show_cover", False)
    cfg.set("pdf.numbered", False)
    cfg.set("pdf.title", name)

    print(f"图片直转 PDF 准备完成：{len(pages)} 页内容图" +
          (f" + 封面 {cover_file}" if cover_file else "（无封面）"))
    if cover_images:
        print(f"  封面走 cover_images（整页铺满无边距）：{cover_images}")
    if pages:
        # 计算 scale 用 content area 即可（与 layout 算法一致）
        any_page = pages[0][0]
        if any_page in page_size_cache:
            first_scale = min(width_mm / page_size_cache[any_page][0],
                              height_mm / page_size_cache[any_page][1])
        else:
            first_scale = -1
        print(f"  内容页走正文文章（每页一图，绝对定位 page_margin={page_margin_str}）："
              f"{len(pages)} 页, scale={first_scale:.4f}")
    else:
        print(f"  内容页走正文文章：{len(pages)} 页")

    # 返回原始 page_margin（仅用于诊断打印）。本流不修改 config.pdf.page_margin：
    # document-factory 的 @page 直接用用户配置的边距（与 layed 一致），
    # .img-page 的绝对定位已按 content-area 计算，无需再叠加或覆盖。
    return cover_images, len(pages), page_margin_str


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

    与 layed 流共用绝对定位设计：document-factory 的 @page 直接用用户
    config.pdf.page_margin（如 "1.5cm 2cm"），img 流**不在 article 内注入
    `@page{margin:0}`**（注入会全局覆盖 @page 边距，破坏目录/页码/视觉边距）。
    .img-page 大小 = A4 内容区，图像在 section 内按 page_margin 计算 scale + 居中，
    使视觉边距、目录、底部页码与其他模式完全一致。
    """
    from . import pdf as pdf_mod
    from .config import Config as LocalCfg
    cover_images, n_pages, _original_page_margin = prepare(project, img_dir, book_name)
    if no_pdf:
        print(f"（--no-pdf，跳过 PDF 生成；已准备 {n_pages} 页内容图）")
        return None
    # 传入 Config 实例：pdf_mod.make_pdf 才能读到 config.pdf.compress（否则传 None
    # 会跳过压缩，`pdf.compress: true` 不生效）。
    pdf_mod.make_pdf(project, LocalCfg(str(project.path)),
                     input_dir=str(project.get_content_img_dir()))
    _rename_imgpdf_outputs(project)
    return n_pages


# 复用 layed 流的 CSS margin 简写解析器（保持两种流视觉边距算法一致）。
def _margin_mm(margin_str):
    """CSS margin 简写 → (top, right, bottom, left) mm。"""
    def _v(s):
        m = re.match(r"^\s*([0-9.]+)\s*(mm|cm|pt|in)?\s*$", str(s))
        if not m:
            return None
        num = float(m.group(1))
        u = m.group(2) or "mm"
        return {"mm": num, "cm": num * 10, "pt": num * 25.4 / 72.0,
                "in": num * 25.4}[u]
    parts = str(margin_str).strip().split()
    vals = [_v(p) for p in parts]
    if any(v is None for v in vals) or not vals:
        return (15.0, 20.0, 15.0, 20.0)
    if len(vals) == 1:
        return (vals[0], vals[0], vals[0], vals[0])
    if len(vals) == 2:
        return (vals[0], vals[1], vals[0], vals[1])
    if len(vals) == 3:
        return (vals[0], vals[1], vals[2], vals[1])
    return (vals[0], vals[1], vals[2], vals[3])


def _strip_image_meta(src_path, dst_path):
    """把图像重新保存为无 ICC profile / EXIF 的纯 PNG/JPEG。

    背景：手机/扫描仪截图通常带 ICC profile（如 Google sRGB）。weasyprint 在
    `position: absolute` 下遇到带 ICC profile 的图片会**异常**缩放并垂直居中——
    图片渲染到约 50% 请求高度，且 y 偏移 ≈ (req_h - actual_h) / 2。这让 img 流
    的"图占满内容区"设计完全失效，表现为图片没有等比缩放到 CSS 尺寸、且距
    page_margin 顶部多出留白。

    解决：用 PIL 读取原图，**显式丢弃 icc_profile / exif / dpi 等所有元数据**
    （注意 PIL.Image.save 默认会保留 im.info 里的 icc_profile，必须主动 pop
    掉或传空 PngInfo），只保留像素数据再存。weasyprint 对纯像素 PNG 的
    width/height 处理与对纯色合成图像一致——按 CSS 指定的 mm 渲染并精确定位。

    保留原文件后缀（.png → PNG，.jpg/.jpeg → JPEG）；未知后缀按 PNG 处理。

    注意：源图（手机/扫描仪 PNG）常带 alpha（RGBA/LA）。直接 ``convert('RGB')``
    会把透明像素合成成黑色（其 RGB 常为 0,0,0），导致整页背景变黑。因此带 alpha
    的图先合成到白色底上再存。
    """
    try:
        with PILImage.open(src_path) as im:
            # 带 alpha（RGBA/LA）或调色板 P 可能含透明 → 先合成到白底，避免透明区变黑
            if im.mode in ('RGBA', 'LA', 'P'):
                im = im.convert('RGBA')
                bg = PILImage.new('RGB', im.size, (255, 255, 255))
                bg.paste(im, mask=im.split()[-1])
                im = bg
            else:
                im = im.convert('RGB')
            # 显式清除所有元数据（PIL 默认会把 im.info['icc_profile'] 等写入产物，
            # 导致 weasyprint 异常缩放）。PNG 用空 PngInfo 避免 tEXt/iCCP；
            # JPEG/其他：pop 已知元数据键。
            for k in ('icc_profile', 'exif', 'dpi', 'jfif', 'jfif_unit',
                      'jfif_density', 'photoshop'):
                im.info.pop(k, None)
            ext = os.path.splitext(dst_path)[1].lower()
            if ext in ('.jpg', '.jpeg'):
                im.save(dst_path, format='JPEG', quality=92)
            else:
                # 空 PngInfo → 产物 PNG 不带 iCCP / tEXt / iTXt，weasyprint 按 CSS 尺寸渲染
                from PIL.PngImagePlugin import PngInfo
                im.save(dst_path, format='PNG', optimize=False, pnginfo=PngInfo())
    except Exception as e:
        # 兜底：PIL 失败时直接复制原文件（保留旧行为）
        print(f"  ⚠ 重新保存图像失败，回退到直接复制: {src_path}: {e}")
        shutil.copy2(src_path, dst_path)
