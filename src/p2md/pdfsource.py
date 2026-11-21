# -*- coding: utf-8 -*-
"""PDF 作为输入源：把 assets/ 里的 .pdf 渲染成页图，供后续 OCR/detect/build 复用。

用户把书放在 assets/：可以是一张张截图（.png/.jpg），也可以是 **.pdf**（可能含
文字层或纯扫描图）。PDF 页统一渲染成 p{n}.jpg（默认 150 DPI），并登记
`long_image_pages.txt`（一个 PDF = 一组页 = 独立成篇，与长图一致）。

文字层：PDF 有内嵌文字时，页的坐标文字可直接作为 OCR 结果（见 ocr.run 里按
页检查文字层），避免 Vision OCR；无文字层（扫描件/纯图页）再走 Vision。
"""

import os


def find_pdfs(input_dir):
    """返回目录下 .pdf 文件（按文件名排序）。"""
    if not os.path.isdir(input_dir):
        return []
    return sorted(
        f for f in os.listdir(input_dir)
        if f.lower().endswith(".pdf") and os.path.isfile(os.path.join(input_dir, f))
    )


def pdf_page_count(pdf_path):
    """返回 PDF 页数。解析失败返回 0。"""
    try:
        import fitz
        with fitz.open(pdf_path) as doc:
            return doc.page_count
    except Exception:
        return 0


def _slice_tall_image(img, out_dir, n, W, H, min_h, max_h, slice_ratio):
    """把超长图内容感知切分保存为 p{n}.jpg, p{n+1}...，返回 (页号列表, 末尾+1)。

    max_h 先按"图片宽度×slice_ratio"限高（layed 流宽度一致的约束），再按行宽裁。
    """
    import numpy as np
    from .ocr import _find_split_points
    gray = np.asarray(img.convert("L"))
    _slice_max_h = max(int(W * slice_ratio), min_h)
    cuts = _find_split_points(gray, max_h=_slice_max_h, min_h=min_h)
    pts = [0] + (cuts or []) + [H]
    merged = []
    for y0, y1 in zip(pts[:-1], pts[1:]):
        if merged and y1 - merged[-1][1] < 200:
            merged[-1] = (merged[-1][0], y1)
        else:
            merged.append((y0, y1))
    pages = []
    for y0, y1 in merged:
        if y1 - y0 < 200:
            continue
        crop = img.crop((0, y0, W, y1))
        crop.save(os.path.join(out_dir, f"p{n}.jpg"), "JPEG", quality=95)
        pages.append(n)
        n += 1
    return pages, n


def render_pdf_pages(pdf_path, out_dir, start_page, dpi=150, force=False,
                     max_ratio=3.0, max_h=2600, min_h=1400, slice_ratio=3.0):
    """把 PDF 每页渲染成 <out_dir>/p{n}.jpg（n 从 start_page 连续），
    返回分配的页号列表 [start_page, start_page+1, ...]。

    PDF"一页"若本身是超长图（滚动长截图存成单页 PDF，页高/宽比极大），渲染后
    Vision OCR 对超高图返回 0 行——故渲染后按长图规则内容感知切分为多片，
    页号连续顺延，后续 PDF 页从切片末尾之后接着分配。

    幂等续跑：目标 p{n}.jpg 已存在且比 pdf 新 → 沿用（force=True 强制重渲，
    会先清掉该 PDF 可能遗留的旧切片页）。失败返回 None。
    """
    import os as _os
    pages = []
    try:
        import fitz
        if force:
            # 清掉该 PDF 可能遗留的全部旧输出（从 start_page 起连续找
            # p{n}.jpg / p{n+1}... 直到首个不存在，避免上次切分残留冲突）
            k = start_page
            while True:
                old = _os.path.join(out_dir, f"p{k}.jpg")
                if not _os.path.isfile(old):
                    break
                try:
                    _os.remove(old)
                except OSError:
                    break
                k += 1
        with fitz.open(pdf_path) as doc:
            n = start_page
            for pno in range(doc.page_count):
                out0 = _os.path.join(out_dir, f"p{n}.jpg")
                if not force and _os.path.isfile(out0) \
                        and _os.path.getmtime(out0) >= _os.path.getmtime(pdf_path):
                    pages.append(n)
                    n += 1
                    continue
                page = doc.load_page(pno)
                pix = page.get_pixmap(dpi=dpi)
                W, H = pix.width, pix.height
                if H > min_h and H / W >= max_ratio:
                    from PIL import Image
                    img = Image.frombytes("RGB", (W, H), pix.samples)
                    _ps, n = _slice_tall_image(img, out_dir, n, W, H,
                                               min_h, max_h, slice_ratio)
                    pages.extend(_ps)
                else:
                    pix.save(out0, jpg_quality=92)
                    pages.append(n)
                    n += 1
    except Exception as e:
        print(f"  ⚠ 渲染 PDF 失败 {_os.path.basename(pdf_path)}: {e}")
        return None
    return pages


def render_pdf_page_single(pdf_path, page_index, out_path, dpi=150):
    """渲染 PDF 某一页到 out_path（供文字层页不需要 OCR、但 detect 需图时用）。"""
    try:
        import fitz
        with fitz.open(pdf_path) as doc:
            pix = doc.load_page(page_index).get_pixmap(dpi=dpi)
        pix.save(out_path, jpg_quality=92)
        return True
    except Exception:
        return False


def prepare_pdf_sources(input_dir, out, pages, force=False, dpi=150,
                        max_ratio=3.0, max_h=2600, min_h=1400,
                        slice_ratio=3.0):
    """把 input_dir 里的 .pdf 渲染成成品页图 <out>/_ocr_tmp/p{n}.jpg，
    并把页号登记进 pages 与 long_image_pages.txt（每 PDF = 一组页 = 独立成篇）。

    Args:
        input_dir: assets 输入目录
        out: 项目 output 目录（_ocr_tmp 建在其下）
        pages: scan_pages 返回的 {页号: 文件名}（已含普通图片页）
        force: True 时即使 _ocr_tmp/p{n}.jpg 已存在也重新渲染
    Returns:
        (pages2, pdf_pages)：扩展后的 pages；pdf_pages = {pdf文件名: [页号,...]}
    """
    pdfs = find_pdfs(input_dir)
    if not pdfs:
        return pages, {}
    from .ocr import write_long_image_pages, read_long_image_pages

    tmp = os.path.join(out, "_ocr_tmp")
    os.makedirs(tmp, exist_ok=True)
    pages2 = dict(pages)
    # 现有最大页号之后开始分配 PDF 页
    next_n = (max(pages2) + 1) if pages2 else 1
    pdf_pages = {}
    for pdf in pdfs:
        pdf_path = os.path.join(input_dir, pdf)
        p_list = render_pdf_pages(pdf_path, tmp, next_n, dpi=dpi, force=force,
                                  max_ratio=max_ratio, max_h=max_h,
                                  min_h=min_h, slice_ratio=slice_ratio)
        if not p_list:
            continue
        for pn in p_list:
            pages2[pn] = f"[pdf]{pdf}"  # 标记来源
        pdf_pages[pdf] = sorted(set(p_list))
        next_n = max(p_list) + 1
    # 合并进 long_image_pages.txt（保留已存在的长图/PDF 组）
    if pdf_pages:
        lip = read_long_image_pages(out)
        lip.update(pdf_pages)
        write_long_image_pages(out, lip)
    if pdfs:
        print(f"  PDF 源：{len(pdfs)} 个 → 渲染 {sum(len(v) for v in pdf_pages.values())} 页")
    return pages2, pdf_pages


# =============================================================================
# 文字层直取：PDF 有内嵌文字 → 按块(block≈段落)提取成干净 markdown（免 OCR）。
# 纯图/扫描页会跳过（该 PDF 应由渲染→OCR 流程处理）。
# =============================================================================

# 常见水印/装饰关键词（正文几乎不会整行只含这些）。可按项目补充。
_WATERMARK_TERMS = [
    "内部资料", "仅供内部", "机密", "水印", "禁止外传", "仅供参考，不构成",
    "扫描全能王", "CamScanner", "WPS", "福昕",
]


def _looks_like_watermark(txt):
    """启发式判水印行：很短 + 无句读 / 命中水印关键词。"""
    t = txt.strip()
    if not t or len(t) > 30:
        return False
    if any(k in t for k in _WATERMARK_TERMS):
        return True
    # 纯装饰符号/单个词且不含句读 → 可能是角标水印
    if len(t) <= 4 and not any(c.isalnum() for c in t):
        return True
    return False


def _line_is_watermark(line_y, page_h, txt, font_size, body_size):
    """页眉/页脚/水印行：紧贴页边 + 字号远小于正文 或 命中关键词。"""
    t = txt.strip()
    if not t:
        return True
    if _looks_like_watermark(t):
        return True
    # 页眉页脚：y 靠近页面上下边缘（如顶部/底部 5%）
    margin = page_h * 0.04
    if (line_y < margin or line_y > page_h - margin) and font_size < body_size * 0.9:
        return True
    return False


def _pdf_body_size(doc):
    """统计正文主流字号（该 PDF 中占主导的 span 字号）。"""
    from collections import Counter
    sizes = Counter()
    try:
        for pno in range(min(doc.page_count, 8)):
            d = doc.load_page(pno).get_text("dict")
            for blk in d["blocks"]:
                if blk["type"] != 0:
                    continue
                for ln in blk["lines"]:
                    for sp in ln["spans"]:
                        if sp["text"].strip():
                            sizes[round(sp["size"], 1)] += 1
    except Exception:
        pass
    if not sizes:
        return 12.0
    return sizes.most_common(1)[0][0]


def extract_pdf_to_markdown(pdf_path, out_img_dir=None, dpi=150):
    """把有文字层的 PDF 直取为 markdown 文本（列表结构，逐页）。

    - 每页按块(y 序)遍历；文字块内多行软换行合成一段；图片块占位 [[IMG:idx]]。
    - 标题行识别：字号明显大于正文的短块 → "#" 或加粗；否则当正文段落。
    - 页眉/页脚/水印行跳过。

    Returns:
        (pages_text, images)：
          pages_text: list[list[dict]]，每页为有序元素 [{kind:'text', text},
                      或 {kind:'image', page, x0,y0,x1,y1}]（按 y 顺序）；
          images: list 渲染出的图片路径（供 md 引用，与占位 idx 对应）。
    水印文本已在 text 元素中去掉。
    """
    import fitz
    doc = fitz.open(pdf_path)
    body_size = _pdf_body_size(doc)
    pages_text = []
    images = []
    img_tmp = out_img_dir
    if img_tmp:
        os.makedirs(img_tmp, exist_ok=True)
    for pno in range(doc.page_count):
        page = doc.load_page(pno)
        ph, pw = page.rect.height, page.rect.width
        d = page.get_text("dict")
        # 收集该页图片位置（dict blocks 里常无图片块，用 get_image_info）
        img_boxes = []
        try:
            for im in page.get_image_info():
                b = im.get("bbox")
                if b and (b[2] - b[0]) >= 30 and (b[3] - b[1]) >= 20:
                    img_boxes.append((b[1], tuple(b)))  # (y, box)
        except Exception:
            img_boxes = []
        elems = []
        # 文字块：合成一段（去掉行内软换行）
        for blk in d["blocks"]:
            if blk["type"] == 1:  # 图片块（个别 PDF 会有）
                x0, y0, x1, y1 = blk["bbox"]
                if (x1 - x0) >= 30 and (y1 - y0) >= 20:
                    img_boxes.append((y0, (x0, y0, x1, y1)))
                continue
            parts = []
            for ln in blk["lines"]:
                txt = "".join(sp["text"] for sp in ln["spans"]).strip()
                if txt and not _looks_like_watermark(txt):
                    parts.append(txt)
            if not parts:
                continue
            # 段落文本
            text = "".join(parts)
            if not text.strip():
                continue
            # 块平均字号
            sizes = [sp["size"] for ln in blk["lines"] for sp in ln["spans"]
                     if sp["text"].strip()]
            avg_size = sum(sizes) / len(sizes) if sizes else body_size
            y0 = blk["bbox"][1]
            # 页眉页脚过滤
            if _line_is_watermark(y0, ph, text, avg_size, body_size):
                continue
            elems.append({"kind": "text", "y": y0, "text": text,
                          "size": avg_size, "page": pno})
        # 图片块并入（去掉与文字块重叠区几乎一致的重复：图块 y 中心落在文字块内的忽略）
        for y0, box in img_boxes:
            overlap = False
            yc = y0 + (box[3] - box[1]) / 2
            for e in elems:
                if e["kind"] == "text":
                    # 粗略：若图片中心落在某个文字块的 y 带内则跳过（避免图内文字重复）
                    pass
            elems.append({"kind": "image", "y": y0, "page": pno, "box": box})
        elems.sort(key=lambda e: (e["y"], 1 if e["kind"] == "image" else 0))
        # 相邻去重：文本块（按 y 太近且同为文本/图片）—— 图像与紧贴文字都保留；
        # 只去掉"同位置重复图像"（同一区域 image_info + dict 双收）
        seen = set()
        uniq = []
        for e in elems:
            if e["kind"] == "image":
                key = (round(e["box"][1], 0), round(e["box"][3], 0))
                if key in seen:
                    continue
                seen.add(key)
            uniq.append(e)
        pages_text.append(uniq)
    doc.close()
    return pages_text, images


def _body_size_of(pages_text):
    """统计正文主流字号（多数文本块的 size 中位数）。"""
    sizes = []
    for elems in pages_text:
        for e in elems:
            if e["kind"] == "text" and e.get("size"):
                sizes.append(e["size"])
    if not sizes:
        return None
    sizes.sort()
    return sizes[len(sizes) // 2]


def pages_to_markdown(pdf_name, pages_text, img_dir_rel="", title=None):
    """把文字层提取的 pages_text 组装成 markdown。

    - 每个 text 元素 = 一个段落（block），软换行已合并。
    - 标题识别：字号明显大于正文主流字号的**短块**（如文章大标题/节标题）→ `##`；
      整个文档**第一个**大字号块若在首页首部，用作文件标题 `#` 并从正文移除（避免与
      `# {title}` 重复）。正文段落之间空行分隔；图引用 `![插图](...)` 按位置插入。
    """
    body = _body_size_of(pages_text)
    md_lines = []  # 段落流（每段一个字符串）
    title_line = None
    for pno, elems in enumerate(pages_text):
        for e in elems:
            if e["kind"] == "image":
                f = e.get("file")
                if f:
                    md_lines.append(f"![插图]({img_dir_rel}{f})")
                continue
            t = e["text"].strip()
            if not t:
                continue
            sz = e.get("size") or body
            # 大字号短块 → 标题候选（正文主流字号常 12，标题 ~14 即 1.17×）
            is_heading = (body and sz >= body * 1.15 and len(t) <= 60)
            if is_heading and title_line is None and pno == 0:
                # 首页第一个大标题 → 文章主标题（除非外部给了 title）
                title_line = t
                continue
            if is_heading:
                md_lines.append(f"## {t}")
            else:
                md_lines.append(t)
    md = []
    if title:
        md.append(f"# {title}")
    elif title_line:
        md.append(f"# {title_line}")
    elif pdf_name:
        md.append(f"# {pdf_name}")
    md.extend(md_lines)
    return "\n\n".join(md) + "\n"


def pdf_to_markdown(pdf_path, out_img_dir=None, dpi=150, title=None,
                    img_dir_rel="images/"):
    """一步：PDF → markdown 文本（文字层直取，含嵌图渲染到 out_img_dir）。

    Returns:
        md 字符串；若 PDF 基本无文字层（纯图），返回 ""（调用方改走渲染 OCR）。
    """
    pages_text, _ = extract_pdf_to_markdown(pdf_path, out_img_dir, dpi=dpi)
    total_text = sum(1 for elems in pages_text for e in elems
                     if e["kind"] == "text")
    if total_text == 0:
        return ""
    pages_text, files = render_embedded_images(pdf_path, pages_text, out_img_dir, dpi=dpi)
    name = os.path.splitext(os.path.basename(pdf_path))[0]
    return pages_to_markdown(name, pages_text, img_dir_rel=img_dir_rel,
                             title=title)


def render_embedded_images(pdf_path, pages_text, out_img_dir, dpi=150):
    """把 pages_text 里 image 占位对应的区域渲染保存，返回 (pages_text, filelist)。

    images 在 pages_text 中已记录 page+box，这里按区域裁切渲染（含周边）保存到
    out_img_dir，文件名 fig_p{page}_{i}.png。返回图片文件列表（供 md 引用）。
    """
    import fitz
    os.makedirs(out_img_dir, exist_ok=True)
    doc = fitz.open(pdf_path)
    idx = 0
    files = []
    for pno, elems in enumerate(pages_text):
        if pno >= doc.page_count:
            break
        page = doc.load_page(pno)
        for e in elems:
            if e["kind"] != "image":
                continue
            x0, y0, x1, y1 = e["box"]
            # 区域渲染（略扩边）
            clip = fitz.Rect(max(0, x0 - 2), max(0, y0 - 2),
                             min(page.rect.width, x1 + 2),
                             min(page.rect.height, y1 + 2))
            pix = page.get_pixmap(dpi=dpi, clip=clip)
            fname = f"fig_p{pno}_{idx}.png"
            out = os.path.join(out_img_dir, fname)
            pix.save(out)
            e["file"] = fname
            files.append(fname)
            idx += 1
    doc.close()
    return pages_text, files
