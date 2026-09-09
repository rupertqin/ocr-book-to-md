# -*- coding: utf-8 -*-
"""第三种 PDF：按原书坐标绝对定位的文字 + 图片混排（layed）。

设计（与 img 流共用 document-factory 多文章 + 自动 TOC 机制）：
- 切分多篇文章（每章一篇），与 img 流一致（走 auto_chapters）。
- 每篇文章正文为：每页一个 `<section class="layed-page">`，内含按 OCR 坐标
  绝对定位的文字行 + 裁图。section 大小 = A4 内容区（210 - 2*lr, 297 - 2*tb）。
- 与 img 流一致直接复用 config.pdf.page_margin 给 document-factory @page：视觉
  边距 = @page margin（标准边距），section 大小正好匹配 @page 内容区。无须再
  在 section 内手算 offset；section 内坐标直接以 section 左上角为 0。
- 封面：与 img 流共用 cover_images 机制（@page cover-image { margin: 0 }）。

产物：
    output/content_layed/article_N.md     每章一篇文章
    output/imgpdf_source/cover.*          复制的封面（cover_images 引用）
    output/dist/<书名>.layed.unc.pdf / .layed.pdf（未压缩/压缩）

依赖：document-factory 通过 weasyprint 渲染（pip install -e <document-factory 目录>）。

用法：
    python main.py pdf -p <project> -t layed
"""

import html as html_mod
import json
import os
import re
import shutil
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent

# 源图默认尺寸（进化心理学扫描页）；以实际检测为准
DEF_W, DEF_H = 1166, 2342

# 标题 level → 样式（相对正文）
HEADING_STYLE = {
    2: {"scale": 1.22, "weight": "bold", "color": "#0b6e36"},
    3: {"scale": 1.10, "weight": "bold", "color": "#111111"},
}


def _page_img_size(project, n):
    """探测某页实际图片尺寸（像素）。找不到则用默认。

    优先用 OCR 预处理目录 _ocr_tmp/ 里的 p{n}.jpg——超长图被切分后，某页对应的是
    切片（如 1403×1822）而非原长图（1403×12060），用错尺寸会让 scale 严重偏差，
    导致文字/图位置挤成一小块。找不到再回退项目 assets/ 与默认值。
    """
    tmp = project.output_dir / "_ocr_tmp"
    cand = tmp / f"p{n}.jpg"
    if cand.is_file():
        try:
            return Image.open(cand).size
        except Exception:
            pass
    img_dir = project.get_input_dir()
    if img_dir.is_dir():
        for f in sorted(img_dir.iterdir()):
            m = re.search(r"(\d+)", f.name)
            if m and int(m.group(1)) == n and f.suffix.lower() in (".png", ".jpg", ".jpeg"):
                try:
                    return Image.open(f).size
                except Exception:
                    return (DEF_W, DEF_H)
    return (DEF_W, DEF_H)


def _load_blocks(project, n):
    p = project.get_blocks_dir() / f"{n}.json"
    if not p.exists():
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)["blocks"]


def _load_ocr_lines(project, n):
    p = project.get_ocr_pages_json_dir() / f"{n}.json"
    if not p.exists():
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)["lines"]


def _fig_abs_path(project, fname):
    p = project.get_figs_dir() / fname
    return p.resolve()


def _collect_image_regions(blocks):
    """收集图片区域（像素坐标，原书页坐标基准）。"""
    regions = []
    for b in blocks:
        if b["type"] == "image":
            regions.append(b)
    return regions


def _line_in_image(yc, regions):
    for r in regions:
        if r["y_top"] - 2 <= yc <= r["y_bottom"] + 2:
            return True
    return False


def _heading_lookup(blocks):
    """标题文本 → level 映射（用于文字行样式）。"""
    m = {}
    for b in blocks:
        if b["type"] == "heading":
            m[b["text"].strip()] = b["level"]
    return m


def _text_style_lookup(blocks):
    """文字行文本 → (centered, bold) 映射（detect 已判定每行是否居中/加粗）。"""
    m = {}
    for b in blocks:
        if b["type"] in ("text", "heading"):
            key = b["text"].strip()
            m[key] = (bool(b.get("centered")), bool(b.get("bold")))
    return m


# 默认全角标点集合（未分组时的统一压缩范围）
_DEFAULT_PUNCTS = "，。？！；：、\"\"''（）《》〈〉【】……—·～"


def _parse_punct_groups(punct_compress):
    """规范化 punct_compress 为 [(标点字符集, css值, class后缀), ...]。

    支持两种形式：
      - 字符串 "-0.3em"       → [("…全角标点…", "-0.3em", "")]（统一压缩，class=punct）
      - dict {"标点串": "值"}  → 每组一个条目，class=punct-a / punct-b ...
    返回空列表表示不压缩。
    """
    if not punct_compress:
        return []
    if isinstance(punct_compress, dict):
        groups = []
        for i, (chars, val) in enumerate(punct_compress.items()):
            chars = str(chars)
            if not chars or not val:
                continue
            suffix = "" if i == 0 else f"-{chr(97 + i)}"
            groups.append((chars, str(val), suffix))
        return groups
    return [(_DEFAULT_PUNCTS, str(punct_compress), "")]


def render_page_html(project, n, W, H, scale, base_font_pt, x_off_mm, win_top_px,
                     page_h_px, font_family=None, justify=True, width_scale=1.0,
                     letter_spacing=None, punct_compress=None,
                     font_height_ratio=0.78, min_font_pt=6.0):
    """生成单个"页窗口"的 HTML（绝对定位）。返回 (html, n_figs)。

    文字与图片统一用同一个 scale（等比缩放），保证相对位置一致、不重叠、不变形。
    W, H: 源图像素尺寸。scale: 像素→mm 统一比例（宽度受限，保证各页宽度一致）。
    base_font_pt: 正文基准字号(pt)。x_off_mm: 水平偏移(mm)。
    win_top_px: 本页窗口在切片像素坐标里的起点 y。page_h_px: 一页能容纳的切片像素高。
    只渲染 y 落在 [win_top_px, win_top_px+page_h_px) 内的内容，top=(y-win_top_px)*scale。
    支持超高切片跨多页：每页一个窗口，宽度始终一致。
    font_family: 字体族。justify: 是否两端对齐。width_scale: 文字容器宽度系数。
    letter_spacing/punct_compress: 见 make_layedpdf。
    """
    groups = _parse_punct_groups(punct_compress)
    blocks = _load_blocks(project, n)
    lines = _load_ocr_lines(project, n)
    if blocks is None and lines is None:
        return "", 0
    if blocks is None:
        blocks = []
    if lines is None:
        lines = []

    regions = _collect_image_regions(blocks)
    heading_map = _heading_lookup(blocks)
    text_style = _text_style_lookup(blocks)

    parts = []
    n_figs = 0

    # 图片：定位到原书区域（统一 scale），裁图与文字同基准、不变形。
    # 只渲染与当前页窗口重叠的图；top 相对窗口起点。
    win_end_px = win_top_px + page_h_px
    for r in regions:
        if "x0" not in r:
            continue
        if r["y_bottom"] < win_top_px or r["y_top"] >= win_end_px:
            continue  # 图不在本页窗口内，跳到下一页
        fig_path = _fig_abs_path(project, r["file"])
        if not fig_path.exists():
            continue
        w = (r["x1"] - r["x0"]) * scale
        h = (r["y_bottom"] - r["y_top"]) * scale
        if w <= 0 or h <= 0:
            continue
        x0 = x_off_mm + r["x0"] * scale
        y0 = (r["y_top"] - win_top_px) * scale
        n_figs += 1
        style = (f"position:absolute;left:{x0:.2f}mm;top:{y0:.2f}mm;"
                 f"width:{w:.2f}mm;height:{h:.2f}mm;")
        parts.append(f'<img src="file://{fig_path}" style="{style}">')

    # 文字：从 OCR lines 按坐标放置（统一 scale），排除图内文字
    for ln in lines:
        text = ln["text"].strip()
        if not text:
            continue
        # 把 OCR 误识的 U+2022 bullet（粗圆点）替换为 U+00B7（细中点），
        # 避免人名分隔符等在正文小字号下渲染过粗。
        text = text.replace("\u2022", "\u00B7")
        # OCR 归一化坐标 → 原书像素
        x_px = ln["x"] * W
        y_px = (1 - ln["y"] - ln["h"]) * H       # 顶
        h_px = ln["h"] * H
        w_px = ln["w"] * W
        yc = y_px + h_px / 2
        # 只渲染本页窗口内的行；行顶超出窗口则跳到下一页
        if y_px >= win_end_px:
            continue
        # 跳过图内文字（表格/图内说明），它们已包含在裁图里
        if _line_in_image(yc, regions):
            continue
        # 字号由 OCR 行高按 scale 换算，随图片同比例缩放——否则固定字号与图片大小
        # 不匹配（图撑满宽度时文字仍很小）。字号 ≈ 行高(像素→mm→pt) × font_height_ratio。
        line_h_mm = h_px * scale
        base_pt = max(line_h_mm * (72.0 / 25.4) * font_height_ratio, min_font_pt)
        # 标题样式（仅改变字号/粗细/颜色）
        level = heading_map.get(text)
        if level in HEADING_STYLE:
            hs = HEADING_STYLE[level]
            font_pt = base_pt * hs["scale"]
            weight = hs["weight"]
            color = hs["color"]
        else:
            font_pt = base_pt
            weight = "normal"
            color = "#111111"
        # detect 判定的居中/加粗：覆盖默认样式（加粗/居中行更贴近原图）
        _centered, _bold = text_style.get(text, (False, False))
        if _bold:
            weight = "bold"
        if _centered:
            color = "#111111"  # 居中行多为标题/落款，用正文色
        # 位置换算到 mm（统一 scale）；top 相对窗口起点
        left = x_off_mm + x_px * scale
        top = (y_px - win_top_px) * scale
        w_mm = w_px * scale
        # 基线对齐：同一字号统一 line-height，按 y_top 定位，基线自然一致。
        # white-space:nowrap 保证一行不换行（OCR 每行已是独立行，无需内部换行）。
        family = f"font-family:{font_family};" if font_family else ""
        align = "text-align:center;" if _centered else ("text-align:justify;" if justify else "text-align:left;")
        # 容器宽度 = OCR 宽 × width_scale（<1 让容器略窄，适配标点宽的字体）
        text_w = w_mm * width_scale
        ls = f"letter-spacing:{letter_spacing};" if letter_spacing else ""
        style = (f"position:absolute;left:{left:.2f}mm;top:{top:.2f}mm;"
                 f"width:{text_w:.2f}mm;font-size:{font_pt:.2f}pt;"
                 f"font-weight:{weight};color:{color};"
                 f"{family}{align}{ls}"
                 f"line-height:1.0;margin:0;padding:0;"
                 f"white-space:nowrap;")
        # 标点压缩：把中文标点包进 <span class="punct...">（套用各组的 letter-spacing）
        body = html_mod.escape(text)
        if groups:
            import re as _re
            # 先对字符做正则转义（标点里可能有正则特殊字符）
            for chars, _val, suffix in groups:
                if not chars:
                    continue
                cls = f"punct{suffix}"
                pattern = "".join(_re.escape(c) for c in chars)
                body = _re.sub(
                    f"([{pattern}])",
                    lambda m, _c=cls: f'<span class="{_c}">{m.group(1)}</span>',
                    body)
        parts.append(f'<p style="{style}">{body}</p>')

    return "\n".join(parts), n_figs


def _collect_pages(project, pages=None):
    """收集所有要渲染的页号（排序）。默认从 blocks/ocr 目录发现。"""
    if pages:
        return sorted(pages)
    found = set()
    for d in (project.get_blocks_dir(), project.get_ocr_pages_json_dir()):
        if d.is_dir():
            for f in os.listdir(str(d)):
                if f.endswith(".json"):
                    found.add(int(f[:-5]))
    return sorted(found)


def find_cover(project):
    """在输入素材目录找封面 cover.jpg / cover.png。找不到返回 None。

    目录优先级：config.book.assets_dir > 项目 assets/（get_input_dir）。
    """
    from .config import Config
    cfg = Config(str(project.path))
    assets = cfg.get_assets_dir()
    candidates = [assets] if assets else []
    inp = project.get_input_dir()
    if inp.is_dir():
        candidates.append(str(inp))
    for d in candidates:
        if not d or not os.path.isdir(d):
            continue
        for name in ("cover.jpg", "cover.jpeg", "cover.png"):
            p = os.path.join(d, name)
            if os.path.isfile(p):
                return p
    return None


# =============================================================================
# 多文章 + document-factory 流水线（layed 与 img 共用同一套 TOC 机制）
# =============================================================================


def prepare_layed_articles(project, pages=None, base_font_pt=11.0,
                            page_margin_str=None, font_family=None,
                            justify=None, width_scale=None,
                            letter_spacing=None, punct_compress=None):
    """为 layed 生成多篇文章 Markdown：每章一篇，绝对定位 HTML。

    与 img 流共用 ``auto_chapters()`` 的章节切分。每篇文章的第一行
    ``# {chapter_title}`` 由 document-factory 提取为 art['title']（用于 TOC）。
    ``<style>`` 块定义 .layed-page 的尺寸（210×297mm）与断页规则；每个原书页
    一个 ``<section class="layed-page">``，里面是 OCR 文字行 + 裁图的绝对定位。

    Args:
        pages: 仅渲染指定页（调试），None 用全部页。
        其他参数同 make_layedpdf（来自 config 或显式传入）。

    Returns:
        (article_count, article_dir)
        article_count: 写入的文章数（0 时仍写文件以便 document-factory 不报错）
        article_dir: 文档工厂会读这个目录
    """
    from .config import Config
    from .build_md import auto_chapters

    cfg = Config(str(project.path))

    if page_margin_str is None:
        page_margin_str = cfg.get('pdf.page_margin', '1.5cm 2cm')
    if font_family is None:
        font_family = cfg.get('pdf.font_family', None)
    if justify is None:
        justify = bool(cfg.get('pdf.justify', True))
    if width_scale is None:
        width_scale = float(cfg.get('pdf.width_scale', 1.0))
    if letter_spacing is None:
        letter_spacing = cfg.get('pdf.letter_spacing', None)
    if punct_compress is None:
        punct_compress = cfg.get('pdf.punct_compress', None)

    # 字号随图片缩放：字号 = OCR 行高(→mm→pt) × font_height_ratio，让文字与图片大小匹配。
    font_height_ratio = float(cfg.get('pdf.layed_font_ratio', 0.78))
    min_font_pt = float(cfg.get('pdf.layed_min_font_pt', 6.0))

    # section 大小 = A4 内容区（A4 减去 @page margin）；视觉边距由 document-factory
    # @page margin 直接提供（与 img 流完全一致），section 内坐标以左上角为 0。
    # 用 document-factory 的 effective_page_margin_mm：底部已 clamp 到至少
    # PAGE_NUM_MIN_BOTTOM_MM，与 @page 实际内容区一致，保证 .layed-page 高度匹配。
    from document_factory.pdf_generator import effective_page_margin_mm
    top_mm, right_mm, bottom_mm, left_mm = effective_page_margin_mm(page_margin_str)
    page_w_mm = 210.0
    page_h_mm = 297.0
    width_mm = page_w_mm - left_mm - right_mm
    height_mm = page_h_mm - top_mm - bottom_mm

    page_nums_all = _collect_pages(project)
    if not page_nums_all:
        raise SystemExit("错误：未找到任何页面数据（blocks 或 ocr）")

    # 统一过滤完全空白页：读 <out>/blank_pages.txt（OCR 预处理已写），避免空白页入 PDF
    from .ocr import read_blank_pages
    _blank = set(read_blank_pages(str(project.output_dir)))
    if _blank:
        page_nums_all = [n for n in page_nums_all if n not in _blank]
        print(f"  layed 已过滤 {len(_blank)} 张完全空白页：{sorted(_blank)}")
    if not page_nums_all:
        raise SystemExit("错误：过滤空白页后无剩余页面")

    # 调试 --pages 时只渲染指定页
    if pages:
        page_nums = sorted(set(pages) & set(page_nums_all))
    else:
        page_nums = page_nums_all

    # 章节切分
    try:
        chapters = auto_chapters(str(project.get_blocks_dir()))
    except SystemExit:
        chapters = None

    # 准备 article 目录（独立于 text 流的 content/ 和 img 流的 content_img/）
    article_dir = project.output_dir / "content_layed"
    if article_dir.exists():
        # 清旧 .md（保留 images 等子目录以便调试）
        for f in os.listdir(str(article_dir)):
            p = os.path.join(str(article_dir), f)
            if os.path.isfile(p) and f.endswith(".md"):
                os.remove(p)
    os.makedirs(str(article_dir), exist_ok=True)

    # 注入每篇文章的 CSS（点睛 .layed-page 尺寸 + 标点压缩分组）
    body_family = f"font-family:{font_family};" if font_family else ""
    ls_css = f"letter-spacing:{letter_spacing};" if letter_spacing else ""
    css_lines = [
        '<style>',
        # 隐藏 document-factory 自动注入的 `<h1 id="article-N">`：
        # 不清掉 H1 时会与第一个 .layed-page 出现在同一页 → 强制换页 → 单独占页。
        # 清成 0 视觉占位但保留 anchor id（避免 display:none 让 weasyprint 丢链接注释）。
        '.article h1 { position: absolute !important; top: 0 !important; '
        'left: 0 !important; width: 0 !important; height: 0 !important; '
        'margin: 0 !important; padding: 0 !important; font-size: 0 !important; '
        'line-height: 0 !important; color: transparent !important; '
        'overflow: hidden !important; visibility: hidden !important; }',
        # section 大小 = A4 内容区（210-2*lr, 297-2*tb）；与 document-factory @page
        # 边距一致，section 内坐标以 (0,0) 为原点。视觉边距直接由 @page 提供（与 img 流一致），
        # TOC 与底部页码也用 @page 标准位置 → 与 img 流目录样式 / 页码位置完全一致。
        f'.layed-page {{ position: relative; width: {width_mm}mm; '
        f'height: {height_mm}mm; overflow: hidden; break-after: page; '
        f'page-break-after: always; }}',
        '.layed-page:last-of-type { break-after: auto; page-break-after: auto; }',
        '.layed-page img { position: absolute; }',
        f'.layed-page p {{ position: absolute; margin: 0; padding: 0; '
        f'line-height: 1.0; white-space: nowrap; '
        f'{body_family} {ls_css} }}',
    ]
    for chars, val, suffix in _parse_punct_groups(punct_compress):
        css_lines.append(
            f'.layed-page .punct{suffix} {{ letter-spacing:{val}; }}')
    css_lines.append('</style>')
    css_block = '\n'.join(css_lines)

    # 缓存每页源图尺寸
    page_size_cache = {}

    def get_page_size(n):
        if n not in page_size_cache:
            page_size_cache[n] = _page_img_size(project, n)
        return page_size_cache[n]

    def render_chapter_pages(chapter_pages):
        sections = []
        for n in chapter_pages:
            W, H = get_page_size(n)
            # 统一宽度缩放：scale = width_mm/W → 各页图片宽度一致（长图切片不出现宽度不一）。
            # 每页能容纳的切片像素高 page_h_px = height_mm/scale。
            scale = width_mm / W
            x_off = (width_mm - W * scale) / 2  # 恒 0（宽度受限）
            page_h_px = height_mm / scale
            if H <= page_h_px:
                # 单页：垂直居中（win_top_px = 窗口起点在切片像素里的偏移）
                win_top_px = max(0.0, (page_h_px - H) / 2)
                seg, _nf = render_page_html(
                    project, n, W, H, scale, base_font_pt, x_off, win_top_px,
                    page_h_px,
                    font_family=font_family, justify=justify,
                    width_scale=width_scale, letter_spacing=letter_spacing,
                    punct_compress=punct_compress,
                    font_height_ratio=font_height_ratio, min_font_pt=min_font_pt,
                )
                sections.append(f'<section class="layed-page">{seg}</section>')
            else:
                # 超高切片：跨多页，每页一个 page_h_px 高的窗口，宽度一致，内容不丢
                k = 0
                while k * page_h_px < H:
                    win_top_px = k * page_h_px
                    seg, _nf = render_page_html(
                        project, n, W, H, scale, base_font_pt, x_off, win_top_px,
                        page_h_px,
                        font_family=font_family, justify=justify,
                        width_scale=width_scale, letter_spacing=letter_spacing,
                        punct_compress=punct_compress,
                        font_height_ratio=font_height_ratio, min_font_pt=min_font_pt,
                    )
                    sections.append(f'<section class="layed-page">{seg}</section>')
                    k += 1
        return sections

    article_count = 0

    if chapters:
        for kind, slug, fname, title, start, end in chapters:
            # front（前言/版权）也渲染为章节：有些书的前几页是正文（如长图首屏标题页），
            # 若跳过会丢内容。有真实封面走 cover_images，front 页仍可保留。
            chapter_pages = [n for n in page_nums if start <= n <= end]
            if not chapter_pages:
                continue
            lines = [f"# {title}", "", css_block, ""]
            lines.extend(render_chapter_pages(chapter_pages))
            with open(os.path.join(str(article_dir), f"article_{article_count}.md"),
                       "w", encoding="utf-8") as fp:
                fp.write("\n".join(lines) + "\n")
            article_count += 1
    else:
        # 无章节：单篇（整书一页一段）
        lines = ["#", "", css_block, ""]
        lines.extend(render_chapter_pages(page_nums))
        with open(os.path.join(str(article_dir), "article_0.md"),
                  "w", encoding="utf-8") as fp:
            fp.write("\n".join(lines) + "\n")
        article_count = 1

    print(f"  layed 准备完成：{article_count} 章 + 共享 CSS → {article_dir}")
    return article_count, article_dir


def _setup_layed_pdf_config(project):
    """为 layed 配置 document-factory 所需的 pdf.* 设置。

    与 img 流共用 document-factory 配置约定，仅差异是 `.layed-page` 内容由
    layed 准备，document-factory 的 @page 直接用用户的 `pdf.page_margin`
    （如 "1.5cm 2cm"）→ 视觉边距、TOC 样式、底部页码位置与 img 流完全一致。

    - pdf.cover_images：与 img 流共用 cover_images 机制（@page cover-image 边距 = 0）
    - pdf.show_cover = False（封面用 cover_images，不渲染文字封面）
    - pdf.numbered = False（章节名是完整标题，不需要 document-factory 再加 "1." 前缀）

    Returns:
        cover_images：用于调试打印
    """
    from .config import Config
    cfg = Config(str(project.path))

    # 封面（cover_images）—— 与 imgpdf.prepare 行为一致
    cover_path = find_cover(project)
    cover_images = []
    if cover_path:
        src_dir = project.output_dir / "imgpdf_source"
        os.makedirs(str(src_dir), exist_ok=True)
        cover_name = Path(cover_path).name
        target = src_dir / cover_name
        if not target.exists():
            try:
                shutil.copy2(cover_path, target)
            except OSError as e:
                print(f"  ⚠ 复制封面到 {target} 失败：{e}")
        cover_images = [f"output/imgpdf_source/{cover_name}"]
    cfg.set('pdf.cover_images', cover_images)
    cfg.set('pdf.show_cover', False)
    cfg.set('pdf.numbered', False)

    return cover_images


def _restore_layed_pdf_config(project):
    """layed 不再临时覆盖 pdf.page_margin，无须恢复。其他字段
    （cover_images 等）保留为 layed 视角下的项目配置（与 imgpdf 行为一致）。"""
    return None


def make_layedpdf(project, pages=None, output=None, base_font_pt=11.0,
                  font_family=None, justify=None, width_scale=None,
                  letter_spacing=None, punct_compress=None):
    """生成 layed PDF：document-factory 多文章 + 自动 TOC。

    流程：
        ① 配置 pdf.* 让 document-factory 把每个章节当一篇，自动生成 TOC；
           直接复用用户 config.pdf.page_margin（不强制覆盖为 '0'），让 document-factory
           的 @page、TOC、底部页码全部走标准边距，与 img 流完全一致。
        ② 按章节切分多篇文章，每篇内每原书页一个绝对定位 section。
        ③ 调 document-factory.create_pdf 渲染（content_layed/）。
        ④ 重命名加 .layed 后缀；页面压缩走 document-factory 默认（pdf.compress）。

    Args:
        project: Project 实例
        pages: 仅渲染指定页（调试）
        output: 输出 PDF 路径（None 用 <output/dist/<书名>.layed.unc.pdf> 默认名）
        base_font_pt, font_family, justify, width_scale, letter_spacing,
        punct_compress: 同 build_layed_html 的同名参数，None 时读 config
    """
    from . import pdf as pdf_mod
    from .config import Config

    cover_images = _setup_layed_pdf_config(project)
    if cover_images:
        print(f"  封面走 cover_images（@page cover-image 边距 = 0）：{cover_images}")

    try:
        article_count, article_dir = prepare_layed_articles(
            project, pages=pages, base_font_pt=base_font_pt,
            font_family=font_family, justify=justify,
            width_scale=width_scale, letter_spacing=letter_spacing,
            punct_compress=punct_compress,
        )

        # 直接让 document-factory 生成到 <书名>.layed.unc.pdf（而非先生成 text 默认名再改名），
        # 避免覆盖已存在的 text 模式 PDF。压缩由 pdf_mod.make_pdf 依据 config.pdf.compress 处理
        # （compress_pdf 会把 <书名>.layed.unc.pdf 压成 <书名>.layed.pdf）。
        from .config import Config as LocalCfg
        cfg = LocalCfg(str(project.path))
        out_sub = cfg.get('pdf.output_dir', 'dist')
        out_dir = project.output_dir / out_sub
        os.makedirs(str(out_dir), exist_ok=True)
        name = cfg.get_book_name() or project.path.name
        unc_output = str(out_dir / f"{name}.layed.unc.pdf")
        if output is not None:
            unc_output = output

        # 复用一个 cfg 实例（pdf_mod.make_pdf 走库调用，无 shell 副作用）
        pdf_mod.make_pdf(project, cfg,
                         input_dir=str(article_dir), output=unc_output)

        print(f"layedpdf 生成完成：{article_count} 章"
              "（document-factory 自动 TOC 与 img/text 模式一致）")
    finally:
        _restore_layed_pdf_config(project)

    return output or str(out_dir / f"{name}.layed.unc.pdf")
