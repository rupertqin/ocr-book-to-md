# -*- coding: utf-8 -*-
"""第三种 PDF：按原书坐标绝对定位的文字 + 图片混排。

思路：OCR 已提供每行文字的精确坐标（归一化 x/y/w/h），detect 已提供每张裁图在
原书页面的区域（像素 x0/y_top/x1/y_bottom）。把每个原书页作为一张 A4 画布，
用 HTML `position: absolute` 把文字行与裁图按原书坐标铺回去，实现接近原书的混排。

坐标基准：
- 文字：ocr/pages_json/<n>.json 的 lines，归一化坐标 × 源图尺寸 = 像素
- 图片：blocks/<n>.json 的 image block（x0/y_top/x1/y_bottom，像素）
- 源图统一尺寸：1166 × 2342 px（若不同则以实际为准）

渲染：weasyprint，@page 默认 A4 + 可配 page_margin；页面内容区按比例映射原书页。

用法：
    python main.py layedpdf -p <project>
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
    """探测某页源图实际尺寸（像素）。找不到则用默认。"""
    img_dir = project.data_dir / "images"
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


def render_page_html(project, n, W, H, scale, base_font_pt, x_off_mm, y_off_mm,
                     font_family=None, justify=True, width_scale=1.0,
                     letter_spacing=None, punct_compress=None):
    """生成单个原书页的 HTML（绝对定位）。返回 (html, n_figs)。

    文字与图片统一用同一个 scale（等比缩放），保证相对位置一致、不重叠、不变形。
    W, H: 源图像素尺寸。scale: 像素→mm 统一比例（contain）。
    base_font_pt: 正文基准字号(pt)。x_off_mm/y_off_mm: 画布左上角偏移(mm)。
    font_family: 字体族（None 用系统默认衬线）。justify: 是否段落两端对齐。
    width_scale: 文字容器宽度系数（OCR 宽 × scale，宽字体标点溢出时调小如 0.95）。
    letter_spacing: CSS letter-spacing 值（如 '-0.02em' 轻微压缩）。
    punct_compress: 标点压缩。可传字符串（如 '-0.1em'，统一压缩所有标点）
        或 dict（如 {"，。、": "-0.3em", "（）《》": "-0.2em"}，按标点类别分别压缩）。
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

    parts = []
    n_figs = 0

    # 图片：定位到原书区域（统一 scale），裁图与文字同基准、不变形
    for r in regions:
        if "x0" not in r:
            continue
        fig_path = _fig_abs_path(project, r["file"])
        if not fig_path.exists():
            continue
        w = (r["x1"] - r["x0"]) * scale
        h = (r["y_bottom"] - r["y_top"]) * scale
        if w <= 0 or h <= 0:
            continue
        x0 = x_off_mm + r["x0"] * scale
        y0 = y_off_mm + r["y_top"] * scale
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
        # 跳过图内文字（表格/图内说明），它们已包含在裁图里
        if _line_in_image(yc, regions):
            continue
        # 标题样式（仅改变字号/粗细/颜色，字号基于正文统一缩放，不随行高抖动）
        level = heading_map.get(text)
        if level in HEADING_STYLE:
            hs = HEADING_STYLE[level]
            font_pt = base_font_pt * hs["scale"]
            weight = hs["weight"]
            color = hs["color"]
        else:
            font_pt = base_font_pt
            weight = "normal"
            color = "#111111"
        # 位置换算到 mm（统一 scale）
        left = x_off_mm + x_px * scale
        top = y_off_mm + y_px * scale
        w_mm = w_px * scale
        # 基线对齐：同一字号统一 line-height，按 y_top 定位，基线自然一致。
        # white-space:nowrap 保证一行不换行（OCR 每行已是独立行，无需内部换行）。
        family = f"font-family:{font_family};" if font_family else ""
        align = "text-align:justify;" if justify else "text-align:left;"
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
    """在项目图片目录找封面 cover.jpg / cover.png。找不到返回 None。

    图片目录优先级：config.book.images_dir > 项目 data/images。
    """
    from .config import Config
    cfg = Config(str(project.path))
    img_dir = cfg.get_images_dir()
    candidates = [img_dir] if img_dir else []
    if project.images_dir.is_dir():
        candidates.append(str(project.images_dir))
    for d in candidates:
        if not d or not os.path.isdir(d):
            continue
        for name in ("cover.jpg", "cover.jpeg", "cover.png"):
            p = os.path.join(d, name)
            if os.path.isfile(p):
                return p
    return None


def build_layed_html(project, pages=None, base_font_pt=11.0,
                     page_margin_str=None, font_family=None, justify=None,
                     width_scale=None, letter_spacing=None, punct_compress=None):
    """生成整本 layed 排版 HTML（每原书页一个 @page/div）。

    font_family: 字体族，None 时读 config.pdf.font_family（如 'PingFang SC'、'Noto Sans CJK SC'）。
    justify: 是否两端对齐，None 时读 config.pdf.justify（默认 True）。
    width_scale: 文字容器宽度系数（OCR 宽 × scale，<1 让容器略窄、避免宽字体标点溢出），None 读 config。
    letter_spacing: CSS letter-spacing（如 '-0.02em'），None 读 config。
    punct_compress: 标点压缩 CSS 值（如 '-0.1em'），None 读 config.pdf.punct_compress。

    Returns:
        (html, n_pages, n_figs)
    """
    page_nums = _collect_pages(project, pages)
    if not page_nums:
        raise SystemExit("错误：未找到任何页面数据（blocks 或 ocr）")

    # 页面与边距
    from .config import Config
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
    # 解析 margin（top right bottom left, mm）
    top_mm, right_mm, bottom_mm, left_mm = _margin_mm(page_margin_str)
    page_w_mm = 210.0
    page_h_mm = 297.0
    width_mm = page_w_mm - left_mm - right_mm
    height_mm = page_h_mm - top_mm - bottom_mm

    # 封面（cover.jpg）作为第一页，整页铺满无边距
    page_htmls = []
    cover_path = find_cover(project)
    if cover_path and not pages:
        cover_html = ('<img src="file://' + str(Path(cover_path).resolve()) + '" '
                      f'style="position:absolute;left:0;top:0;width:{page_w_mm}mm;'
                      f'height:{page_h_mm}mm;object-fit:cover;">')
        page_htmls.append(f'<section class="page cover">{cover_html}</section>')

    # 每页源图尺寸（假设统一；逐页探测）
    total_figs = 0

    for n in page_nums:
        W, H = _page_img_size(project, n)
        # 统一 scale（contain）：等比缩放填满内容区的一边，另一边居中留白。
        # 文字与图片同基准，不变形、不重叠。留白是原书比例(0.498)≠A4比例(0.637)的必然结果。
        scale = min(width_mm / W, height_mm / H)
        x_off = left_mm + (width_mm - W * scale) / 2
        y_off = top_mm + (height_mm - H * scale) / 2
        seg, nf = render_page_html(project, n, W, H, scale, base_font_pt,
                                   x_off, y_off, font_family=font_family,
                                   justify=justify, width_scale=width_scale,
                                   letter_spacing=letter_spacing,
                                   punct_compress=punct_compress)
        total_figs += nf
        page_htmls.append(f'<section class="page">{seg}</section>')

    n_total = len(page_htmls)
    pages_css = "\n".join(
        f'.page:nth-child({i+1}) {{ width:{page_w_mm}mm; height:{page_h_mm}mm; '
        f'position:relative; overflow:hidden; page-break-after:always; }}'
        for i in range(n_total)
    )

    body_family = f"font-family:{font_family};" if font_family else ""
    # 标点压缩 CSS：按分组生成 .punct / .punct-a ... 的 letter-spacing
    punct_css = ""
    for _chars, val, suffix in _parse_punct_groups(punct_compress):
        punct_css += f".page .punct{suffix} {{ letter-spacing:{val}; }}\n"
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
  @page {{ size: A4; margin: 0; }}
  body {{ margin: 0; padding: 0; {body_family} }}
  .page {{ position: relative; overflow: hidden; }}
  .page img {{ position: absolute; }}
  .page p {{ position: absolute; }}
  .page.cover img {{ object-fit: cover; }}
  {punct_css}
  {pages_css}
</style>
</head><body>
{chr(10).join(page_htmls)}
</body></html>"""
    return html, n_total, total_figs


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


def make_layedpdf(project, pages=None, output=None, base_font_pt=11.0,
                  font_family=None, justify=None, width_scale=None,
                  letter_spacing=None, punct_compress=None):
    """生成 layed 排版 PDF（textOnImg 流）。

    Args:
        project: Project 实例
        pages: 仅渲染指定页（调试）
        output: 输出路径（默认 output/normal/<书名>.textOnImg.unc.pdf）
        base_font_pt: 正文基准字号(pt)
        font_family: 字体族（None 读 config.pdf.font_family）
        justify: 是否两端对齐（None 读 config.pdf.justify）
        width_scale: 文字容器宽度系数（<1 适配宽字体标点溢出，None 读 config）
        letter_spacing: CSS letter-spacing（如 '-0.02em'，None 读 config）
        punct_compress: 标点压缩 CSS 值（如 '-0.1em'，None 读 config）
    """
    html, n_pages, n_figs = build_layed_html(project, pages, base_font_pt,
                                             font_family=font_family,
                                             justify=justify,
                                             width_scale=width_scale,
                                             letter_spacing=letter_spacing,
                                             punct_compress=punct_compress)
    if output is None:
        out_dir = project.output_dir / "normal"
        os.makedirs(out_dir, exist_ok=True)
        from .config import Config
        cfg = Config(str(project.path))
        name = cfg.get_book_name() or project.path.name
        # <书名>.textOnImg.unc.pdf：.textOnImg=方式二级后缀，.unc=压缩标记（未压缩）
        output = str(out_dir / f"{name}.textOnImg.unc.pdf")

    try:
        from weasyprint import HTML
    except ImportError:
        raise SystemExit("错误：需要 weasyprint，请 pip install weasyprint")

    HTML(string=html, base_url=str(project.path)).write_pdf(output)
    print(f"layedpdf 生成完成：{n_pages} 页，{n_figs} 张图 → {output}")

    # 若 config pdf.compress=true，调用 document-factory 压缩 <书名>.textOnImg.pdf
    _compress_if_needed(project, output)
    return output


def _compress_if_needed(project, output):
    """若 config pdf.compress=true，调用 document-factory 的 compress_pdf 压缩产物。

    compress_pdf 对以 .unc 结尾的文件输出去掉 .unc 的 .pdf：
    <书名>.textOnImg.unc.pdf → <书名>.textOnImg.pdf。
    通过把 document-factory 的 src 加入 sys.path，正常包导入以支持相对导入。
    """
    from .config import Config
    if not Config(str(project.path)).get('pdf.compress', False):
        return
    from . import pdf as pdf_mod
    df_dir = pdf_mod._find_document_factory(None)
    import sys as _sys
    src_dir = df_dir / "src"
    if str(src_dir) not in _sys.path:
        _sys.path.insert(0, str(src_dir))
    cfg = Config(str(project.path))
    resolution = int(cfg.get('compress.resolution', 100))
    try:
        from document_factory.pdf_generator import compress_pdf
        compress_pdf(output, resolution=resolution)
        out_name = Path(output).name.replace(".unc.pdf", ".pdf")
        print(f"  textOnImg 压缩完成 → {out_name}")
    except Exception as e:
        print(f"  ⚠ textOnImg 压缩失败: {e}")
