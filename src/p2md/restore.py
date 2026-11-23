# -*- coding: utf-8 -*-
"""p2md 的 Markdown 还原方式（``type``）：``normal`` / ``layed`` / ``image``。

p2md 的产物就是 Markdown；三种方式只是把 work 目录整理成不同形态的 Markdown：

- ``normal`` 纯文本流：直接用 build 产出的 ``<work>/content/``（图与文字重新排版）。
- ``layed``  版式还原：按 OCR 坐标把每页排成绝对定位的文字 + 裁图，
             产物 ``<work>/content_layed/``（HTML section 内嵌在 Markdown 里）。
- ``image``  图片流，不做 OCR：每页原图一页，产物 ``<work>/content_image/``。

三者写不同目录，互不覆盖。

用法：
    p2md build --work <work> --type layed
    p2md build --work <work> --type image --assets <扫描页目录>
"""

from __future__ import annotations

import html as html_mod
import json
import os
import re
import shutil
from pathlib import Path

from PIL import Image

from . import defaults, paths

MD_TYPES = ("normal", "layed", "image")

PAGE_W_MM = 210.0
PAGE_H_MM = 297.0

# 源图默认尺寸（拿不到真实页图时兜底）
DEF_W, DEF_H = 1166, 2342

# 标题 level → 样式（相对正文，仅 layed）
HEADING_STYLE = {
    2: {"scale": 1.22, "weight": "bold", "color": "#0b6e36"},
    3: {"scale": 1.10, "weight": "bold", "color": "#111111"},
}

# 默认全角标点集合（未分组时的统一压缩范围）
_DEFAULT_PUNCTS = "，。？！；：、\"\"''（）《》〈〉【】……—·～"

_IMG_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp")
_PAGE_RE = re.compile(r"(\d+)")


# =============================================================================
# work 目录读取
# =============================================================================


def page_img_size(work_dir, n, assets_dir=None):
    """探测某页实际页图尺寸（像素），找不到用 DEF_W/DEF_H。

    优先 ``<work>/_ocr_tmp/p{n}.jpg``——超长图被切分后某页对应的是切片
    （如 1403×1822）而非原长图（1403×12060），用错尺寸会让 scale 严重偏差，
    文字与图挤成一小块。其次查 ``assets_dir`` 下带该页号的图片。
    """
    cand = paths.ocr_tmp_dir(work_dir) / f"p{n}.jpg"
    if cand.is_file():
        try:
            return Image.open(cand).size
        except Exception:
            pass
    if assets_dir:
        d = Path(assets_dir)
        if d.is_dir():
            for f in sorted(d.iterdir()):
                m = _PAGE_RE.search(f.name)
                if m and int(m.group(1)) == n and f.suffix.lower() in _IMG_EXTS:
                    try:
                        return Image.open(f).size
                    except Exception:
                        return (DEF_W, DEF_H)
    return (DEF_W, DEF_H)


def _load_blocks(work_dir, n):
    p = paths.blocks_dir(work_dir) / f"{n}.json"
    if not p.exists():
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)["blocks"]


def _load_ocr_lines(work_dir, n):
    p = paths.ocr_pages_json_dir(work_dir) / f"{n}.json"
    if not p.exists():
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)["lines"]


def _collect_image_regions(blocks):
    """收集图片区域（像素坐标，原书页坐标基准）。"""
    return [b for b in blocks if b["type"] == "image"]


def _line_in_image(yc, regions):
    for r in regions:
        if r["y_top"] - 2 <= yc <= r["y_bottom"] + 2:
            return True
    return False


def _heading_lookup(blocks):
    """标题文本 → level 映射（用于文字行样式）。"""
    return {b["text"].strip(): b["level"] for b in blocks if b["type"] == "heading"}


def _text_style_lookup(blocks):
    """文字行文本 → (centered, bold) 映射（detect 已判定每行是否居中/加粗）。"""
    return {
        b["text"].strip(): (bool(b.get("centered")), bool(b.get("bold")))
        for b in blocks
        if b["type"] in ("text", "heading")
    }


def estimate_text_width_mm(text, font_pt):
    """估算一行文字的渲染宽度（mm）：全角 1.05em、半角 0.55em。

    系数略大于 1 / 0.5 是有意的：中文标点/引号在多数 CJK 字体里比 1em 更宽，
    低估会把行末字排到 section 外面被 overflow:hidden 切掉。只用于给超宽行
    做缩字兜底，轻微高估只让行尾多留一点空白。
    """
    em = sum(0.55 if ord(ch) < 128 else 1.05 for ch in text)
    return em * font_pt * 25.4 / 72.0


def parse_punct_groups(punct_compress):
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


def slice_windows(line_boxes, H, page_h_px):
    """把超高页切成若干竖直窗口，切口落在文字行之间（避免半行被裁）。

    Args:
        line_boxes: [(top_px, bottom_px), ...] 该页所有文字行的竖直范围。
        H: 页图高度（像素）。
        page_h_px: 一页能容纳的像素高。

    Returns:
        [(win_top_px, win_h_px), ...]，覆盖 [0, H)，每段高度 ≤ page_h_px。
    """
    boxes = sorted((float(t), float(b)) for t, b in line_boxes if b > t)
    windows = []
    start = 0.0
    while start < H - 0.5:
        limit = start + page_h_px
        if limit >= H:
            windows.append((start, H - start))
            break
        # 在 [start + 0.5*page_h_px, limit] 里找最近的"行间隙"当作切口
        cut = limit
        low = start + page_h_px * 0.5
        best = None
        for t, b in boxes:
            if t >= limit:
                break
            if b <= limit and b >= low:
                best = b
        if best is not None:
            cut = best
        windows.append((start, cut - start))
        start = cut
    return windows


def collect_pages(work_dir, pages=None):
    """收集所有要渲染的页号（排序）。默认从 blocks/ocr 目录发现。"""
    if pages:
        return sorted(pages)
    found = set()
    for d in (paths.blocks_dir(work_dir), paths.ocr_pages_json_dir(work_dir)):
        if d.is_dir():
            for f in os.listdir(str(d)):
                if f.endswith(".json"):
                    found.add(int(f[:-5]))
    return sorted(found)


def sorted_page_images(img_dir):
    """按文件名里的页号排序图片，返回 [(文件名, 页号)]；无数字的排最后。"""
    d = Path(img_dir)
    if not d.is_dir():
        raise SystemExit(f"错误：图片目录不存在: {d}")
    files = []
    for f in sorted(d.iterdir()):
        if f.suffix.lower() not in _IMG_EXTS:
            continue
        m = _PAGE_RE.search(f.name)
        files.append((f.name, int(m.group(1)) if m else 10 ** 9))
    files.sort(key=lambda t: t[1])
    return files


# =============================================================================
# layed：单页 HTML
# =============================================================================


def render_page_html(work_dir, n, W, H, scale, x_off_mm, win_top_px, page_h_px,
                     figs_rel="../figs", font_family=None, justify=True,
                     width_scale=1.0, letter_spacing=None, punct_compress=None,
                     y_off_mm=0.0, section_w_mm=PAGE_W_MM,
                     font_height_ratio=defaults.LAYED_FONT_HEIGHT_RATIO,
                     min_font_pt=defaults.LAYED_MIN_FONT_PT):
    """生成单个"页窗口"的 HTML（绝对定位）。返回 (html, n_figs)。

    文字与图片统一用同一个 scale（等比缩放），保证相对位置一致、不重叠、不变形。
    W, H: 源图像素尺寸。scale: 像素→mm 统一比例（宽度受限，保证各页宽度一致）。
    x_off_mm: 水平偏移(mm)。win_top_px: 本页窗口在切片像素坐标里的起点 y。
    page_h_px: 一页能容纳的切片像素高。只渲染 y 落在
    [win_top_px, win_top_px+page_h_px) 内的内容，top=(y-win_top_px)*scale+y_off_mm。
    支持超高切片跨多页：每页一个窗口，宽度始终一致。
    y_off_mm: 整页垂直居中用的偏移（单页比内容区矮时往下推，而不是裁掉顶部）。
    figs_rel: content_layed 目录到 figs 目录的相对路径（md 里的图片引用前缀）；
    相对引用可让整个 work 目录搬迁后仍能渲染。
    """
    groups = parse_punct_groups(punct_compress)
    blocks = _load_blocks(work_dir, n)
    lines = _load_ocr_lines(work_dir, n)
    if blocks is None and lines is None:
        return "", 0
    blocks = blocks or []
    lines = lines or []

    regions = _collect_image_regions(blocks)
    heading_map = _heading_lookup(blocks)
    text_style = _text_style_lookup(blocks)

    parts = []
    n_figs = 0

    # 图片：定位到原书区域（统一 scale），只渲染与当前页窗口重叠的图。
    win_end_px = win_top_px + page_h_px
    for r in regions:
        if "x0" not in r:
            continue
        if r["y_bottom"] < win_top_px or r["y_top"] >= win_end_px:
            continue
        if not (paths.figs_dir(work_dir) / r["file"]).is_file():
            continue
        w = (r["x1"] - r["x0"]) * scale
        h = (r["y_bottom"] - r["y_top"]) * scale
        if w <= 0 or h <= 0:
            continue
        x0 = x_off_mm + r["x0"] * scale
        y0 = (r["y_top"] - win_top_px) * scale + y_off_mm
        n_figs += 1
        style = (f"position:absolute;left:{x0:.2f}mm;top:{y0:.2f}mm;"
                 f"width:{w:.2f}mm;height:{h:.2f}mm;")
        parts.append(f'<img src="{figs_rel}/{r["file"]}" style="{style}">')

    # 文字：从 OCR lines 按坐标放置（统一 scale），排除图内文字
    for ln in lines:
        text = ln["text"].strip()
        if not text:
            continue
        # OCR 误识的 U+2022 bullet（粗圆点）换成 U+00B7（细中点），
        # 避免人名分隔符等在正文小字号下渲染过粗。
        text = text.replace("\u2022", "\u00B7")
        # OCR 归一化坐标 → 原书像素
        x_px = ln["x"] * W
        y_px = (1 - ln["y"] - ln["h"]) * H       # 顶
        h_px = ln["h"] * H
        w_px = ln["w"] * W
        yc = y_px + h_px / 2
        if y_px >= win_end_px:
            continue
        # 跳过图内文字（表格/图内说明），它们已包含在裁图里
        if _line_in_image(yc, regions):
            continue
        # 字号由 OCR 行高按 scale 换算，随图片同比例缩放——固定字号会与图片
        # 大小不匹配（图撑满宽度时文字仍很小）。
        line_h_mm = h_px * scale
        base_pt = max(line_h_mm * (72.0 / 25.4) * font_height_ratio, min_font_pt)
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
        # detect 判定的居中/加粗覆盖默认样式（更贴近原图）
        centered, bold = text_style.get(text, (False, False))
        if bold:
            weight = "bold"
        if centered:
            color = "#111111"  # 居中行多为标题/落款，用正文色
        left = x_off_mm + x_px * scale
        top = (y_px - win_top_px) * scale + y_off_mm
        w_mm = w_px * scale
        # 行宽兜底：扫描页常把白边裁掉，正文排到页边，字体宽度稍有出入行末字
        # 就会被 .layed-page 的 overflow:hidden 切掉。只压真正会溢出 section
        # 右边界的行（不整体缩字号，各行字号保持一致），且不低于 min_font_pt。
        avail_mm = section_w_mm - left
        est_mm = estimate_text_width_mm(text, font_pt)
        if avail_mm > 0 and est_mm > avail_mm:
            font_pt = max(font_pt * (avail_mm / est_mm), min_font_pt)
        family = f"font-family:{font_family};" if font_family else ""
        align = "text-align:center;" if centered else \
            ("text-align:justify;" if justify else "text-align:left;")
        # 容器宽度 = OCR 宽 × width_scale（<1 让容器略窄，适配标点宽的字体）
        text_w = w_mm * width_scale
        ls = f"letter-spacing:{letter_spacing};" if letter_spacing else ""
        style = (f"position:absolute;left:{left:.2f}mm;top:{top:.2f}mm;"
                 f"width:{text_w:.2f}mm;font-size:{font_pt:.2f}pt;"
                 f"font-weight:{weight};color:{color};"
                 f"{family}{align}{ls}"
                 f"line-height:1.0;margin:0;padding:0;"
                 f"white-space:nowrap;")
        # 标点压缩：中文标点包进 <span class="punct...">（套用各组的 letter-spacing）
        body = html_mod.escape(text)
        for chars, _val, suffix in groups:
            if not chars:
                continue
            cls = f"punct{suffix}"
            pattern = "".join(re.escape(c) for c in chars)
            body = re.sub(f"([{pattern}])",
                          lambda m, _c=cls: f'<span class="{_c}">{m.group(1)}</span>',
                          body)
        parts.append(f'<p style="{style}">{body}</p>')

    return "\n".join(parts), n_figs


# =============================================================================
# 页面尺寸与 <style>（layed / image 共用）
# =============================================================================


def content_area_mm(page_margin):
    """页面内容区尺寸（mm）：A4 减去 CSS margin 简写给出的四边边距。

    ``page_margin`` 与 CSS ``margin`` 同格式：1~4 个值（上 右 下 左），
    单位支持 cm / mm / in / pt / px，无单位按 mm 处理。
    """
    top_mm, right_mm, bottom_mm, left_mm = _margin_mm(page_margin)
    return (PAGE_W_MM - left_mm - right_mm,
            PAGE_H_MM - top_mm - bottom_mm)


_MARGIN_UNITS = {"": 1.0, "mm": 1.0, "cm": 10.0, "in": 25.4,
                 "pt": 25.4 / 72.0, "px": 25.4 / 96.0}
_MARGIN_RE = re.compile(r"\s*([0-9.]+)\s*(mm|cm|in|pt|px)?\s*", re.IGNORECASE)


def _margin_mm(page_margin):
    """把 CSS margin 简写解析成 (上, 右, 下, 左) 的 mm 值。"""
    text = str(page_margin or "").strip()
    if not text:
        return (0.0, 0.0, 0.0, 0.0)
    parts = []
    for chunk in text.split():
        m = _MARGIN_RE.fullmatch(chunk)
        if not m:
            raise SystemExit(f"错误：无法解析 page_margin: {page_margin!r}")
        unit = (m.group(2) or "").lower()
        parts.append(float(m.group(1)) * _MARGIN_UNITS[unit])
        if len(parts) == 4:
            break
    if not parts:
        raise SystemExit(f"错误：无法解析 page_margin: {page_margin!r}")
    if len(parts) == 1:
        return (parts[0],) * 4
    if len(parts) == 2:
        return (parts[0], parts[1], parts[0], parts[1])
    if len(parts) == 3:
        return (parts[0], parts[1], parts[2], parts[1])
    return tuple(parts)


_HIDE_H1_CSS = (
    # 隐藏渲染端自动注入的 `<h1 id="article-N">`：不隐藏时它会与第一个 section
    # 同页 → 强制换页 → 白占一页。清成 0 视觉占位但保留 anchor id
    # （display:none 会让分页渲染丢链接注释）。
    '.article h1 { position: absolute !important; top: 0 !important; '
    'left: 0 !important; width: 0 !important; height: 0 !important; '
    'margin: 0 !important; padding: 0 !important; font-size: 0 !important; '
    'line-height: 0 !important; color: transparent !important; '
    'overflow: hidden !important; visibility: hidden !important; }'
)


def _layed_css(page_margin, font_family, letter_spacing, punct_compress):
    """layed 每篇注入的 <style>：.layed-page 尺寸与断页规则。"""
    width_mm, height_mm = content_area_mm(page_margin)
    body_family = f"font-family:{font_family};" if font_family else ""
    ls_css = f"letter-spacing:{letter_spacing};" if letter_spacing else ""
    lines = [
        '<style>',
        _HIDE_H1_CSS,
        # section 大小 = A4 内容区；视觉边距由渲染端的 @page margin 提供。
        f'.layed-page {{ position: relative; width: {width_mm}mm; '
        f'height: {height_mm}mm; overflow: hidden; break-after: page; '
        f'page-break-after: always; }}',
        '.layed-page:last-of-type { break-after: auto; page-break-after: auto; }',
        '.layed-page img { position: absolute; }',
        f'.layed-page p {{ position: absolute; margin: 0; padding: 0; '
        f'line-height: 1.0; white-space: nowrap; '
        f'{body_family} {ls_css} }}',
    ]
    for _chars, val, suffix in parse_punct_groups(punct_compress):
        lines.append(f'.layed-page .punct{suffix} {{ letter-spacing:{val}; }}')
    lines.append('</style>')
    return "\n".join(lines), width_mm, height_mm


def _image_css(page_margin):
    """image 每篇注入的 <style>：.img-page 尺寸与断页规则。"""
    width_mm, height_mm = content_area_mm(page_margin)
    lines = [
        '<style>',
        _HIDE_H1_CSS,
        # 图片已绝对定位，正文默认外边距会额外撑开高度
        '.article p { margin: 0 !important; padding: 0 !important; }',
        # 只重置 margin/padding/max-width；宽高必须由 inline style 决定，
        # 加 !important 会把 width/height 覆盖回 auto，weasyprint 会按图片像素
        # 原始尺寸（1166×2342 → 300+mm）渲染并被 section 裁掉。
        '.article img { margin: 0 !important; padding: 0 !important; '
        'max-width: none !important; }',
        f'.img-page {{ position: relative; width: {width_mm}mm; '
        f'height: {height_mm}mm; overflow: hidden; break-after: page; '
        f'page-break-after: always; }}',
        '.img-page:last-of-type { break-after: auto; page-break-after: auto; }',
        '.img-page img { position: absolute; }',
        '</style>',
    ]
    return "\n".join(lines), width_mm, height_mm


# =============================================================================
# normal：直接用 build 的产物
# =============================================================================


def prepare_normal(work_dir):
    """normal 方式：不整理任何东西，直接用 ``<work>/content``。

    Returns:
        content 目录（含 article_*.md）。
    """
    content = paths.content_dir(work_dir)
    if not content.is_dir() or not sorted(content.glob("article_*.md")):
        raise SystemExit(
            f"错误：{content} 下没有 article_*.md；normal 方式需要先跑 p2md（全流程）或 p2md build")
    print(f"  normal 直接使用已有 Markdown：{content}")
    return content


# =============================================================================
# layed：按 OCR 坐标重排每一页
# =============================================================================


def prepare_layed(work_dir, *, pages=None,
                  page_margin=defaults.PAGE_MARGIN,
                  font_family=None, justify=defaults.LAYED_JUSTIFY,
                  width_scale=defaults.LAYED_WIDTH_SCALE,
                  letter_spacing=None, punct_compress=None,
                  font_height_ratio=defaults.LAYED_FONT_HEIGHT_RATIO,
                  min_font_pt=defaults.LAYED_MIN_FONT_PT,
                  assets_dir=None):
    """组装 layed 的多篇文章：每章一篇，正文是绝对定位的逐页 HTML。

    与 build 共用 ``auto_chapters()`` 的章节切分。每篇文章首行是
    ``# {章节标题}``。

    Args:
        work_dir: p2md 的 work 目录（含 blocks/、figs/、ocr/）。
        pages: 仅处理指定页（调试），None 用全部页。
        page_margin: 页面边距（CSS margin 简写），决定内容区尺寸。
        其余参数控制文字排版（通常来自 config.yaml 的同类键）。

    Returns:
        (article_count, content_layed 目录)
    """
    from . import build_md
    from .ocr import read_blank_pages, read_long_image_pages

    work = paths.work_path(work_dir)
    page_nums_all = collect_pages(work)
    if not page_nums_all:
        raise SystemExit("错误：未找到任何页面数据（blocks 或 ocr）")

    # 过滤完全空白页：OCR 预处理已写 blank_pages.txt
    blank = set(read_blank_pages(str(work)))
    if blank:
        page_nums_all = [n for n in page_nums_all if n not in blank]
        print(f"  layed 已过滤 {len(blank)} 张完全空白页：{sorted(blank)}")
    if not page_nums_all:
        raise SystemExit("错误：过滤空白页后无剩余页面")

    page_nums = sorted(set(pages) & set(page_nums_all)) if pages else page_nums_all

    try:
        chapters = build_md.auto_chapters(str(paths.blocks_dir(work)))
    except SystemExit:
        chapters = None

    article_dir = paths.content_dir_for(work, "layed")
    article_dir.mkdir(parents=True, exist_ok=True)
    # 清旧 .md（保留其他文件以便调试）
    for f in os.listdir(str(article_dir)):
        p = article_dir / f
        if p.is_file() and f.endswith(".md"):
            os.remove(p)

    css_block, width_mm, height_mm = _layed_css(
        page_margin, font_family, letter_spacing, punct_compress)
    figs_rel = os.path.relpath(str(paths.figs_dir(work)), str(article_dir))

    page_size_cache = {}

    def get_page_size(n):
        if n not in page_size_cache:
            page_size_cache[n] = page_img_size(work, n, assets_dir=assets_dir)
        return page_size_cache[n]

    # 全书统一缩放：参考"常规页里最大的一页"（排除长图切片），同时满足宽/高
    # 限制。这样字号全书一致，且除长图外每页都正好落在一页里——按中位页缩放
    # 会让一半的页高出内容区，各切成两页（第二页只剩一两行）。
    long_pages = set()
    for nums in read_long_image_pages(str(work)).values():
        long_pages.update(nums)
    _sizes = [get_page_size(n) for n in page_nums]
    _normal = [sz for n, sz in zip(page_nums, _sizes) if n not in long_pages] or _sizes
    ref_w = max(w for w, _h in _normal) or 1
    ref_h = max(h for _w, h in _normal) or 1
    book_scale = min(width_mm / ref_w, height_mm / ref_h)

    def render_chapter_pages(chapter_pages):
        sections = []
        for n in chapter_pages:
            W, H = get_page_size(n)
            scale = min(book_scale, width_mm / W)
            x_off = (width_mm - W * scale) / 2
            page_h_px = height_mm / scale
            kwargs = dict(
                figs_rel=figs_rel, font_family=font_family, justify=justify,
                width_scale=width_scale, letter_spacing=letter_spacing,
                punct_compress=punct_compress, section_w_mm=width_mm,
                font_height_ratio=font_height_ratio, min_font_pt=min_font_pt,
            )
            if H <= page_h_px:
                # 单页：整页垂直居中（往下推，不能裁顶——页图是 trim 后的内容框，
                # 顶部偏移必须补回，否则抬头几行会被 section 裁掉）
                y_off = max(0.0, (height_mm - H * scale) / 2)
                seg, _ = render_page_html(work, n, W, H, scale, x_off, 0.0,
                                          page_h_px, y_off_mm=y_off, **kwargs)
                sections.append(f'<section class="layed-page">{seg}</section>')
            else:
                # 超高切片：跨多页，切口落在文字行之间（避免半行被裁）
                lines = _load_ocr_lines(work, n) or []
                boxes = [((1 - ln["y"] - ln["h"]) * H, (1 - ln["y"]) * H)
                         for ln in lines if ln.get("h", 0) > 0]
                for win_top, win_h in slice_windows(boxes, H, page_h_px):
                    seg, _ = render_page_html(work, n, W, H, scale, x_off,
                                              win_top, win_h, **kwargs)
                    sections.append(f'<section class="layed-page">{seg}</section>')
        return sections

    article_count = 0
    if chapters:
        for _kind, _slug, _fname, title, start, end in chapters:
            chapter_pages = [n for n in page_nums if start <= n <= end]
            if not chapter_pages:
                continue
            body = [f"# {title}", "", css_block, ""]
            body.extend(render_chapter_pages(chapter_pages))
            (article_dir / f"article_{article_count}.md").write_text(
                "\n".join(body) + "\n", encoding="utf-8")
            article_count += 1
    else:
        body = ["#", "", css_block, ""]
        body.extend(render_chapter_pages(page_nums))
        (article_dir / "article_0.md").write_text(
            "\n".join(body) + "\n", encoding="utf-8")
        article_count = 1

    print(f"  layed 准备完成：{article_count} 章 + 共享 CSS → {article_dir}")
    return article_count, article_dir


# =============================================================================
# image：每页原图一页，不做 OCR
# =============================================================================


def _page_images_dir(work, assets_dir=None):
    """image 的页面图片目录：显式 --assets > <work>/_ocr_tmp。"""
    if assets_dir:
        d = Path(assets_dir)
        if d.is_dir():
            return d
    tmp = paths.ocr_tmp_dir(work)
    if tmp.is_dir():
        return tmp
    raise SystemExit(
        "错误：找不到页面图片目录；请用 --assets 指定扫描页目录")


def _strip_image_meta(src_path, dst_path):
    """重新保存为不带 ICC/EXIF 的图（weasyprint 遇 ICC 会异常缩放+垂直居中）。

    带 alpha 的图先合成到白底（否则透明区变黑）。保存失败时回退直接复制。
    """
    try:
        with Image.open(src_path) as im:
            if im.mode in ("RGBA", "LA", "P"):
                im = im.convert("RGBA")
                bg = Image.new("RGB", im.size, (255, 255, 255))
                bg.paste(im, mask=im.split()[-1])
                im = bg
            else:
                im = im.convert("RGB")
            for k in ("icc_profile", "exif", "dpi", "jfif", "jfif_unit",
                      "jfif_density", "photoshop"):
                im.info.pop(k, None)
            if Path(dst_path).suffix.lower() in (".jpg", ".jpeg"):
                im.save(dst_path, format="JPEG", quality=92)
            else:
                from PIL.PngImagePlugin import PngInfo
                im.save(dst_path, format="PNG", optimize=False, pnginfo=PngInfo())
    except Exception as e:
        print(f"  ⚠ 重新保存图像失败，回退到直接复制: {src_path}: {e}")
        shutil.copy2(src_path, dst_path)


def prepare_image(work_dir, *, page_margin=defaults.PAGE_MARGIN,
                  assets_dir=None, pages=None):
    """组装 image 的多篇文章：每页一张原图、一个绝对定位 section，不做 OCR。

    有 blocks（跑过 detect）时按章节切分并跳过 front（前言/版权）；
    没有 blocks 时全书一篇。

    Returns:
        (article_count, content_image 目录)
    """
    from . import build_md

    work = paths.work_path(work_dir)
    img_dir = _page_images_dir(work, assets_dir=assets_dir)
    images = sorted_page_images(img_dir)
    if not images:
        raise SystemExit(f"错误：{img_dir} 下没有页面图片")

    # 封面不进正文
    cover = find_cover(img_dir)
    page_images = [(f, p) for f, p in images
                   if not cover or (Path(img_dir) / f).resolve() != cover.resolve()]
    if not page_images:
        raise SystemExit("错误：除封面外没有页面图片")

    # 空白页：优先用 OCR 预处理记录；没有记录时现场检测并写回
    from .ocr import _is_blank_image, read_blank_pages, write_blank_pages

    blank_nums = set(read_blank_pages(str(work)))
    if not blank_nums:
        detected = []
        for f, page in page_images:
            try:
                with Image.open(Path(img_dir) / f) as im:
                    if _is_blank_image(im):
                        detected.append(page)
            except Exception:
                continue
        if detected:
            write_blank_pages(str(work), detected)
        blank_nums = set(detected)
    if blank_nums:
        page_images = [(f, p) for f, p in page_images if p not in blank_nums]
        print(f"  image 已过滤 {len(blank_nums)} 张完全空白页：{sorted(blank_nums)}")
    if pages:
        page_images = [(f, p) for f, p in page_images if p in set(pages)]
        if not page_images:
            raise SystemExit("错误：指定页范围内没有页面图片")

    article_dir = paths.content_dir_for(work, "image")
    images_out = article_dir / "images"
    images_out.mkdir(parents=True, exist_ok=True)
    for f in os.listdir(str(article_dir)):
        p = article_dir / f
        if p.is_file() and f.endswith(".md"):
            os.remove(p)
    for f in os.listdir(str(images_out)):
        p = images_out / f
        if p.is_file():
            os.remove(p)

    for f, _page in page_images:
        _strip_image_meta(str(Path(img_dir) / f), str(images_out / f))

    css_block, width_mm, height_mm = _image_css(page_margin)
    size_cache = {}

    def render_image_section(fname):
        if fname not in size_cache:
            try:
                size_cache[fname] = Image.open(images_out / fname).size
            except Exception:
                size_cache[fname] = (DEF_W, DEF_H)
        W, H = size_cache[fname]
        scale = min(width_mm / W, height_mm / H)
        x_off = (width_mm - W * scale) / 2
        y_off = (height_mm - H * scale) / 2
        return (
            '<section class="img-page">'
            f'<img src="images/{fname}" '
            f'style="position:absolute;left:{x_off:.2f}mm;top:{y_off:.2f}mm;'
            f'width:{W * scale:.2f}mm;height:{H * scale:.2f}mm;">'
            '</section>'
        )

    def article_md(title, page_list):
        lines = [f"# {title}", "", css_block, ""]
        lines.extend(render_image_section(f) for f in page_list)
        return "\n".join(lines) + "\n"

    # 有章节信息才能分篇（image 不做 OCR，blocks 可能不存在）
    chapters = None
    if paths.blocks_dir(work).is_dir():
        try:
            chapters = build_md.auto_chapters(str(paths.blocks_dir(work)))
        except SystemExit:
            chapters = None

    article_count = 0
    if chapters:
        for _kind, _slug, _fname, title, start, end in chapters:
            # 前言/版权/致谢也要留：p2md 只出 Markdown，跳章会丢页
            chapter_files = [f for f, p in page_images if start <= p <= end]
            if not chapter_files:
                continue
            (article_dir / f"article_{article_count}.md").write_text(
                article_md(title, chapter_files), encoding="utf-8")
            article_count += 1
    if not article_count:
        (article_dir / "article_0.md").write_text(
            article_md("", [f for f, _p in page_images]), encoding="utf-8")
        article_count = 1

    print(f"  image 准备完成：{len(page_images)} 页图 / {article_count} 篇 → {article_dir}")
    return article_count, article_dir


# =============================================================================
# 封面与统一入口
# =============================================================================


def find_cover(assets_dir=None):
    """在 assets 目录里找封面 cover.jpg / cover.jpeg / cover.png。"""
    if not assets_dir:
        return None
    d = Path(assets_dir)
    for name in ("cover.jpg", "cover.jpeg", "cover.png"):
        p = d / name
        if p.is_file():
            return p
    return None


def organize(work_dir, *, type=defaults.MD_TYPE, pages=None,
             page_margin=defaults.PAGE_MARGIN, font_family=None,
             justify=defaults.LAYED_JUSTIFY,
             width_scale=defaults.LAYED_WIDTH_SCALE,
             letter_spacing=None, punct_compress=None,
             font_height_ratio=defaults.LAYED_FONT_HEIGHT_RATIO,
             min_font_pt=defaults.LAYED_MIN_FONT_PT,
             assets_dir=None):
    """按 ``type`` 把 work 目录整理成对应形态的 Markdown。

    Args:
        work_dir: p2md 的 work 目录。
        type: ``normal`` / ``layed`` / ``image``。
        pages: 仅处理指定页（layed / image）。
        page_margin: 页面边距（CSS margin 简写），决定 layed/image 的内容区尺寸。
        assets_dir: 页图目录（image 用；默认回退 ``<work>/_ocr_tmp``）。
        其余参数只对 layed 的文字排版有意义。

    Returns:
        (article_count, 该形态的 Markdown 目录)。
    """
    if type not in MD_TYPES:
        raise SystemExit(f"错误：type 只能是 {'/'.join(MD_TYPES)}，收到: {type}")

    if type == "normal":
        content = prepare_normal(work_dir)
        count = len(sorted(Path(content).glob("article_*.md")))
        return count, Path(content)
    if type == "layed":
        return prepare_layed(
            work_dir, pages=pages, page_margin=page_margin,
            font_family=font_family, justify=justify, width_scale=width_scale,
            letter_spacing=letter_spacing, punct_compress=punct_compress,
            font_height_ratio=font_height_ratio, min_font_pt=min_font_pt,
            assets_dir=assets_dir)
    return prepare_image(work_dir, page_margin=page_margin,
                         assets_dir=assets_dir, pages=pages)


__all__ = [
    "MD_TYPES", "PAGE_W_MM", "PAGE_H_MM",
    "collect_pages", "content_area_mm", "estimate_text_width_mm",
    "find_cover", "organize", "page_img_size", "parse_punct_groups",
    "prepare_image", "prepare_layed", "prepare_normal", "render_page_html",
    "slice_windows", "sorted_page_images",
]
