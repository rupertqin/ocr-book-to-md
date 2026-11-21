# -*- coding: utf-8 -*-
"""组装章节 Markdown，并产出可直接发布的 article 目录。

章节边界从 blocks 的 heading 自动推导（"第X章"/"第X编"/"参考文献"）。

产物（<work_dir>/ 下）：
    md/                         传统章节 Markdown（保留便于复核）
    content/article_N.md        每章一篇文章（Markdown 交付物）
    content/images/             裁图（article 内的图片引用统一改为 images/）
"""

import json
import os
import re
import shutil

from . import paths

CN = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
      "七": 7, "八": 8, "九": 9, "十": 10}

# 有序列表项：行首"数字+分隔符(半角点/全角点/顿号/括号)+内容"，如 "1. 投多少？"。
# 分隔符后可直接接中文（全角．后常无空格）；但不能接数字/小数点（排除 3.5倍、1.2节）。
# 全角点 ． 不是标准 md 有序列表标记，识别后统一输出半角点。
OL_RE = re.compile(r"^\s*(\d{1,2})\s*[.．、)）]\s*([^0-9\s].*)$")
# 无序列表项：行首 bullet（半角 - / 全角 •·▪）
UL_RE = re.compile(r"^\s*[-•·▪●]\s+(.+)$")


def _is_list_item(t):
    """识别一行是否列表项。返回 ('ol', 序号, 内容) / ('ul', None, 内容) / None。

    有序列表项需是**行首数字编号**（如 "1. xxx" / "3．xxx" / "2、xxx"）。
    排除正文里以数字开头的普通行：正文行通常铺满版心且不以编号格式出现。
    """
    if not t:
        return None
    m = OL_RE.match(t)
    if m:
        return ("ol", int(m.group(1)), m.group(2).strip())
    m = UL_RE.match(t)
    if m:
        return ("ul", None, m.group(1).strip())
    return None


def cn2int(s):
    if not s:
        return 0
    if len(s) == 1:
        return CN.get(s, 0)
    if s.startswith("十"):
        return 10 + CN.get(s[1:], 0)
    if s.endswith("十"):
        return CN.get(s[0], 0) * 10
    return 0


def slugify(t):
    t = re.sub(r"[，。、；：！？（）“”\"'\s·—…\-]+", "", t)
    return t


# 长图文章标题定位：首片顶部区域内的中日韩文本行；不含中文的行（时间/电量等
# 状态栏、纯数字符号）与以冒号结尾的节标签（"上证指数："）均排除，再取行高最大者。
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_TITLE_TOP_ZONE = 0.35   # 标题候选只在首片顶部 35% 页高内寻找


def _find_long_image_title_line(blocks_dir, first_page):
    """通用：在长图首片顶部定位"标题行"，返回 (text, y_top, h)（归一化）或 None。

    判据（纯排版形态，与平台/项目文案无关）：
      1) 位于首片顶部区域（y_top ≤ 35% 页高）；
      2) 含中日韩文字 —— 天然排除状态栏/时间/电量等行（"11:22"、"×11 85"、"•"）；
      3) 不以 "：" / ":" 结尾 —— 排除"上证指数："这类节标签；
      4) 候选中取**最靠上**者（长图文章标题固定在首片顶部元信息之前/紧随其后，
         正文行即便行高略大也不能抢走标题），并列时取行高更大者。
    """
    pages_json = os.path.join(os.path.dirname(os.path.abspath(blocks_dir)),
                              "ocr", "pages_json", f"{first_page}.json")
    if not os.path.isfile(pages_json):
        return None
    try:
        with open(pages_json, encoding="utf-8") as f:
            lines = json.load(f).get("lines", [])
    except Exception:
        return None
    cands = []
    for ln in lines:
        t = (ln.get("text") or "").strip()
        if not t or not _CJK_RE.search(t) or t[-1] in "：:":
            continue
        try:
            y_top = 1.0 - float(ln["y"]) - float(ln["h"])
            h = float(ln["h"])
        except (KeyError, TypeError, ValueError):
            continue
        if 0 < y_top <= _TITLE_TOP_ZONE and h > 0:
            cands.append((t, y_top, h))
    if not cands:
        return None
    # 标题在长图首片最顶部；正文行字号可能略大（如么瑞 p101：标题 h=0.0174、
    # 正文 h=0.0176），按行高最大会误把正文当标题。改为优先 y_top 最小。
    return min(cands, key=lambda c: (c[1], -c[2], -len(c[0])))


def _long_image_title_from_ocr(blocks_dir, first_page):
    """通用：从首片**原始 OCR lines** 定位长图文章标题（不依赖平台/项目文案）。

    为什么需要它：detect 的 heading 判定会漏判（长图标题常只比正文大一点），且标题
    若落在图片检测区域内会被当"图内文字"剔除，blocks 里就没有这一行了——而长图首片
    顶部的原始 OCR 里标题始终存在。判据见 `_find_long_image_title_line`。

    Returns:
        标题字符串；未找到返回 ""（由调用方回退 blocks 综合判断）。
    """
    hit = _find_long_image_title_line(blocks_dir, first_page)
    return hit[0] if hit else ""


def _long_image_title_span_px(blocks_dir, first_page):
    """标题行在首片的**像素** y 范围 (y0, y1, title_h)；取不到返回 None。

    用途：丢弃与标题重复的"标题条截图"——长截图顶部那条"标题 + 作者"的横条常被
    detect 判成 image block（裁成 p{n}_f1.png），若标题文字已提取为 `# 标题`，再
    渲染这张图就是图文重复（应留字去图）。页高取自 OCR 预处理切片图
    `output/_ocr_tmp/p{n}.jpg`（与 detect 同源），保证像素坐标与 blocks 对齐。
    """
    hit = _find_long_image_title_line(blocks_dir, first_page)
    if not hit:
        return None
    _, y_top, h = hit
    jpg = os.path.join(os.path.dirname(os.path.abspath(blocks_dir)),
                       "_ocr_tmp", f"p{first_page}.jpg")
    try:
        from PIL import Image as _Image
        with _Image.open(jpg) as im:
            H = float(im.size[1])
    except Exception:
        return None
    return (y_top * H, (y_top + h) * H, h * H)


def _long_image_title(blocks_dir, first_page):
    """取长图文章标题。

    先走 `_long_image_title_from_ocr`（读首片原始 OCR，通用排版判据，最稳）；取不到
    时回退到对第一片 blocks 顶部的**综合判断**：

      - 位置：标题在第一片最顶部（正文/小节标签之前，或紧随顶部元信息之后）；
      - 字号：标题字号**不小于**页内正文行高中位（节标签/图表注字号明显更小）；
      - 加粗：常加粗（加分项，字号显著大时非加粗也认）；
      - 内容：不是时间戳（如"11:53 ③"）、不是节标签（以 `：` 结尾）。

    图片上标题顺序固定，结果只依赖图片排版，与 OCR 拆行无关。
    """
    t = _long_image_title_from_ocr(blocks_dir, first_page)
    if t:
        return t
    data = _load_blocks(blocks_dir, first_page)
    if not data:
        return ""
    blocks = [b for b in data
              if b.get("type") in ("text", "heading") and (b.get("text") or "").strip()]
    if not blocks:
        return ""
    # 页内正文行高中位数基准：用宽行（非节标签、非图注）的中位 h。
    body_hs = [b["y_bottom"] - b["y_top"] for b in blocks
               if not (b.get("text") or "").strip().endswith("：")]
    if not body_hs:
        return ""
    body_h = sorted(body_hs)[len(body_hs) // 2]  # 中位

    # 按阅读序（y 升序）遍历。先跳过时间戳，看页首第一个"内容块"：
    blocks.sort(key=lambda b: (b["y_top"], b.get("x0", 0)))
    content = [b for b in blocks
               if (b.get("text") or "").strip()
               and not re.match(r"^\s*\d{1,2}:\d{2}",
                                (b.get("text") or "").strip())]
    if not content:
        return ""
    first = content[0]
    first_t = (first.get("text") or "").strip()
    # 情形 B：标题是"居中短行"（公众号/长图顶部文章题常见样式：字号与正文相近、
    # 但页面最顶部、居中、独立一行），不依赖大字/加粗。若首块居中且较短 → 即标题。
    if first.get("centered") and 0 < len(first_t) <= 32 \
            and not first_t.endswith("："):
        return first_t

    # 情形 A：标题靠大字号/加粗。从页首起收集"标题行"（连续大字/加粗行）。
    # 判定要点：
    #  - 首行显著大于正文中位(≥1.15×)才是大字标题；
    #  - 后续行须与首行字号接近（回落 ≤10%）才并入——否则说明是标题下方的
    #    摘要/正文段(即使整段加粗，如"摘要：…"学术文摘)，标题区就此结束。
    #  - 连续多行标题(排版拆行)行高接近，不受影响。
    group = []
    first_h = None
    limit_lines = 1
    for b in content:
        t = (b.get("text") or "").strip()
        if t.endswith("："):
            break    # 到第一个节标签（正文开始），标题区结束
        h = b["y_bottom"] - b["y_top"]
        is_bold = bool(b.get("bold"))
        big = h >= body_h * 0.95
        if is_bold or big:
            if group and (h < first_h * 0.9
                          or b["y_top"] - group[-1]["y_bottom"]
                          > 1.6 * (group[-1]["y_bottom"] - group[-1]["y_top"] or 1)):
                break  # 行高回落(标题与摘要/正文的分界) / 垂直间距拉大 → 停
            if not group:
                first_h = h
                # 收首行后才决定"行数上限"
                limit_lines = 3 if first_h >= body_h * 1.15 else 1
            group.append(b)
            if len(group) > limit_lines:
                # 超过 1 行/3 行：前 N 行是标题，多余是正文
                group.pop()
                break
        elif group:
            break  # 标题区结束：遇到既不加粗也不大的文本块
    if not group:
        return ""
    # group 首行上方若已存在普通正文（非粗、非居中、非时间戳）→ group 不是
    # 页面最顶部的标题，而是正文中途被误判的大字行 → 不给标题。
    for pre in content:
        if pre["y_top"] >= group[0]["y_top"]:
            break
        pre_t = (pre.get("text") or "").strip()
        if not pre_t or pre.get("centered") or pre.get("bold"):
            continue
        return ""

    # 组内按视觉位置输出：同行(y_top 相近)按 x0 从左到右，行间按 y 从上到下
    row_tol = max((b["y_bottom"] - b["y_top"] for b in group), default=20) * 0.6
    rows = []
    for b in group:
        placed = False
        for r in rows:
            if abs(b["y_top"] - r["y_top"]) <= row_tol:
                r["items"].append(b)
                placed = True
                break
        if not placed:
            rows.append({"y_top": b["y_top"], "items": [b]})
    rows.sort(key=lambda r: r["y_top"])
    out = []
    for r in rows:
        r["items"].sort(key=lambda b: b.get("x0", 0))
        out.append("".join(b.get("text", "").strip() for b in r["items"]))
    return "".join(out)


def auto_chapters(blocks_dir):
    """从每页 blocks 的 heading 自动推导章节边界。

    长图（滚动长截图）被切成多片后，本质是同一张图、没有真实章节标题——此时按
    heading 切分只会把"图表标题"误当章节标题。每张长图独立成一个章节（无标题），
    不与其他内容混合；多张长图各占一章。
    """
    files = sorted([f for f in os.listdir(blocks_dir) if f.endswith(".json")],
                   key=lambda f: int(f[:-5]))
    pages = [int(f[:-5]) for f in files]
    minp, maxp = pages[0], pages[-1]

    # 长图切分页标记：ocr.prepare_images 写入 {源文件名: [页号,...]}，每张长图独立成章
    out_dir = os.path.dirname(os.path.abspath(blocks_dir))
    lip_file = os.path.join(out_dir, "long_image_pages.txt")
    lip_groups = []
    if os.path.isfile(lip_file):
        try:
            with open(lip_file, encoding="utf-8") as f:
                for line in f:
                    line = line.rstrip("\n")
                    if "\t" in line:
                        name, nums = line.split("\t", 1)
                        ps = [int(x) for x in nums.replace(",", " ").split() if x.strip()]
                        if ps:
                            lip_groups.append((name, sorted(set(ps))))
                    else:
                        ps = [int(x) for x in line.split() if x.strip()]
                        if ps:
                            lip_groups.append((line, sorted(set(ps))))
        except Exception:
            lip_groups = []

    # 每张长图：占全部页 → 各自独立一章。文章标题取长图第一片顶部检测到的粗体标题
    # （detect 已把"（8.24~8.30）"/"8月周总结第3期"等粗体行判为 heading）。
    long_image_pages = set()
    for _name, _ps in lip_groups:
        long_image_pages.update(_ps)
    if long_image_pages and set(pages) <= long_image_pages:
        arts = []
        for i, (_name, _ps) in enumerate(lip_groups):
            title = _long_image_title(blocks_dir, min(_ps))
            arts.append(("custom", "", f"article_{i}.md", title, min(_ps), max(_ps)))
        # 若多张长图之间页码有缝隙（非连续的 blocks），把空白页范围也收进最近章
        arts.sort(key=lambda a: a[4])
        print(f"  (整本书是 {len(lip_groups)} 张长图切分，各自独立成章)")
        return arts
    anchors = []
    # 记录每一页首个 heading（任意格式），用于"找不到第X章"时回退到"每章一节"
    first_heading_per_page = {}
    for n in pages:
        with open(os.path.join(blocks_dir, f"{n}.json"), encoding="utf-8") as f:
            data = json.load(f)
        heads = [h for h in data["blocks"] if h["type"] == "heading"]
        if not heads:
            continue
        merged = []
        for h in sorted(heads, key=lambda h: h["y_top"]):
            if merged and h["y_top"] - merged[-1]["y_bottom"] < 130 \
                    and merged[-1]["level"] == h["level"]:
                merged[-1]["text"] += " " + h["text"]
                merged[-1]["y_bottom"] = h["y_bottom"]
            else:
                merged.append(dict(h))
        first_heading_per_page[n] = merged[0]
        for h in merged:
            t = h["text"].strip()
            if re.match(r"^第[0-9]+章( |$)", t):
                anchors.append((n, "chapter", t))
                break
            if re.match(r"^第[一二三四五六七八九十]+编", t):
                anchors.append((n, "part_intro", t))
                break
            if t == "参考文献":
                anchors.append((n, "references", t))
                break
    anchors.sort()
    if not anchors:
        # 回退：书的章节不是"第X章/第X编/参考文献"格式（如"悖论系列N"等自定义标题），
        # 把每一页首个 heading 当一章，文件用 heading 文本命名。
        # 优先 level 小的（更大标题）；每页只取首个 heading。
        sorted_pages = sorted(first_heading_per_page.items(),
                              key=lambda kv: (kv[1]["level"], kv[0]))
        seen_titles = set()
        for n, h in sorted_pages:
            title = h["text"].strip()
            # 跳过页内重复标题（如重复页码）
            if title in seen_titles:
                continue
            seen_titles.add(title)
            anchors.append((n, "custom", title))
        anchors.sort()
        if not anchors:
            raise SystemExit("未找到任何章节标题页（blocks 里没有 heading），"
                             "检查 detect 是否检测到 heading block")
        print(f"  (未识别到第X章/第X编/参考文献，回退到按 heading 切分，共 {len(anchors)} 章)")

    segs = []
    first = anchors[0][0]
    if minp <= first - 1:
        segs.append(("front", minp, first - 1, "前言 · 版权 · 致谢"))
    for i, (n, kind, title) in enumerate(anchors):
        end = anchors[i + 1][0] - 1 if i + 1 < len(anchors) else maxp
        segs.append((kind, n, end, title))

    cn_num = {"一": "1", "二": "2", "三": "3", "四": "4", "五": "5",
              "六": "6", "七": "7", "八": "8", "九": "9", "十": "10"}
    chapters = []
    max_no = 0
    for kind, a, b, title in segs:
        if kind == "front":
            slug, fname, no = "00_前言", "00_前言.md", 0
        elif kind == "references":
            no = max_no + 1
            slug, fname = f"{no:02d}_参考文献", f"{no:02d}_参考文献.md"
        elif kind == "chapter":
            m = re.match(r"^第([0-9]+)章\s*(.*)$", title)
            no = int(m.group(1)) if m else 0
            name = m.group(2).strip() if m else ""
            if not name:
                name = title
            slug = f"{no:02d}_第{no}章_{slugify(name)}"
            fname = slug + ".md"
        elif kind == "part_intro":
            m = re.match(r"^第([一二三四五六七八九十]+)编\s*(.*)$", title)
            no = cn2int(m.group(1)) if m else 0
            name = m.group(2).strip() if m else ""
            if not name:
                name = title
            cn_txt = m.group(1) if m else ""
            slug = f"{no:02d}_第{cn_txt}编_{slugify(name)}" if m else f"{no:02d}_{slugify(name)}"
            fname = slug + ".md"
        else:  # custom: 自定义标题（"悖论系列N"等无第X章格式）
            max_no += 1
            no = max_no
            slug = f"{no:02d}_{slugify(title)}"
            fname = slug + ".md"
        max_no = max(max_no, no)
        chapters.append((kind, slug, fname, title, a, b))
    return chapters


def _load_blocks(blocks_dir, n):
    p = os.path.join(blocks_dir, f"{n}.json")
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)["blocks"]


def _fmt_line(text, bold, centered):
    """按 detect 判定的 加粗/居中 格式化单行文本。
    用纯 HTML（<strong> / <div style="text-align:center">）避免 markdown 解析器
    在 HTML 块内不处理 `**` 的问题。"""
    if bold:
        text = f"<strong>{text}</strong>"
    if centered:
        return f'<div style="text-align:center">{text}</div>'
    return text


def render_page(blocks_dir, n, skip_title=None, no_headings=False):
    """渲染一页 blocks 为 Markdown。

    skip_title: 若给出标题文本，渲染时跳过第一个与之相同的 heading（用于避免
        与文件头 `# {title}` 重复，如自定义章节标题书的一二级 heading 重复）。
    no_headings: 长图切分的内容没有真实标题，把所有 heading 当普通文本输出。
    段落内每行 = 一个 OCR text block（对应原图的一行视觉文字），用 `\n` 连接，
    保留原图换行结构。
    """
    blocks = _load_blocks(blocks_dir, n)
    if not blocks:
        return ""
    out = []
    skip_done = skip_title is None or no_headings
    sorted_blocks = sorted(blocks, key=lambda b: (b["y_top"], 1 if b["type"] == "image" else 0))
    merged = []
    for b in sorted_blocks:
        if (b["type"] == "heading" and merged and merged[-1]["type"] == "heading"
                and merged[-1]["level"] == b["level"]
                and b["y_top"] - merged[-1]["y_bottom"] < 130):
            merged[-1]["text"] += f" {b['text']}"
            merged[-1]["y_bottom"] = b["y_bottom"]
        else:
            merged.append(b)
    cur_para = []
    last_y = None
    for b in merged:
        if b["type"] == "image":
            if cur_para:
                out.append("".join(cur_para) + "\n\n")
                cur_para = []
            cap = b.get("caption", "").strip()
            alt = cap if cap else "插图"
            out.append(f"![{alt}](../figs/{b['file']})\n\n")
            last_y = b["y_bottom"]
        elif b["type"] == "heading":
            if not skip_done and b["text"].strip() == skip_title:
                # 跳过与文件头重复的章节标题（只跳第一个）
                skip_done = True
                last_y = b["y_bottom"]
                continue
            if no_headings:
                # 长图切分：标题当普通文本，但按几何聚类 para_start 分段落
                _bold = bool(b.get("bold"))
                _centered = bool(b.get("centered"))
                if bool(b.get("para_start")) and cur_para:
                    out.append("".join(cur_para) + "\n\n")
                    cur_para = []
                if _centered:
                    if cur_para:
                        out.append("".join(cur_para) + "\n\n")
                        cur_para = []
                    out.append(_fmt_line(b["text"], _bold, True) + "\n\n")
                else:
                    cur_para.append(_fmt_line(b["text"], _bold, False))
                last_y = b["y_bottom"]
                continue
            if cur_para:
                out.append("".join(cur_para) + "\n\n")
                cur_para = []
            level = b.get("level", 3)
            marker = "#" * level
            out.append(f"{marker} {b['text']}\n\n")
            last_y = b["y_bottom"]
        else:
            t = b["text"]
            bold = bool(b.get("bold"))
            centered = bool(b.get("centered"))
            # 段落开始：detect 已按几何聚类算出 para_start；为 True 时开新段
            if bool(b.get("para_start")) and cur_para:
                out.append("".join(cur_para) + "\n\n")
                cur_para = []
            if centered:
                # 居中行独占一段（HTML div 居中），加粗一并应用
                if cur_para:
                    out.append("".join(cur_para) + "\n\n")
                    cur_para = []
                out.append(_fmt_line(t, bold, centered) + "\n\n")
            else:
                cur_para.append(_fmt_line(t, bold, False))
            last_y = b["y_bottom"]
    if cur_para:
        out.append("".join(cur_para) + "\n\n")
    return "".join(out)


def _is_title_bar_block(page, block, drop_img_span):
    """image block 是否是"标题条截图"（应留字去图的那张）。

    drop_img_span = (page, y0, y1, title_h)：标题行的页号与像素 y 范围/行高。
    判据以"与标题行重叠"为主（长截图顶部的"标题+作者"横条必然压着标题行），
    以"图片高度 ≤ 3×标题行高（同量级）"为辅——避免把恰好与标题行有交叠的
    正文大图误删。
    """
    if not drop_img_span:
        return False
    p, y0, y1, title_h = drop_img_span
    if page != p:
        return False
    b_top = block.get("y_top")
    b_bot = block.get("y_bottom")
    if b_top is None or b_bot is None:
        return False
    if not (b_top < y1 and b_bot > y0):
        return False          # 与标题行无重叠 → 正常内容图
    img_h = b_bot - b_top
    return title_h <= 0 or img_h <= 3 * title_h


def _render_pages_cross_page(blocks_dir, pages, skip_title=None, no_headings=False,
                                skip_pages=(), drop_img_span=None):
    """跨页渲染：把 pages 列表里所有页的 blocks 合在一起按 y 排序，
    段落状态 cur_para 跨页延续——detect 标 para_start=False 的首行（切片边界
    续行）自动并入上一段，消除长图切片造成的"段中换页=两段"伪断裂。

    skip_pages: 这些页整页不参与渲染（沿用旧的 front 跳过第10/11/12页逻辑）。
    drop_img_span: (page, y0, y1, title_h)，把该页与标题行重叠的"标题条截图"丢弃
        ——标题文字已写在文件头 `# 标题`，同一张图再插一次即图文重复（留字去图）。
    """
    out = []
    all_blocks = []
    for n in pages:
        if n in skip_pages:
            continue
        b = _load_blocks(blocks_dir, n)
        if not b:
            continue
        for bb in b:
            all_blocks.append((n, bb))
    if not all_blocks:
        return ""
    # 跨页按 y 排序：同一页内按 y_top，跨页时不要把不同页的 y 混在一起直接比
    # （不同页坐标系独立），但这里 OCR y_top 是 detect 用当前页 H 算出的像素，
    # 不同页 H 相近且长图切片是按 y 顺序切的，按 (n, y_top) 排序天然就是阅读顺序。
    all_blocks.sort(key=lambda nb: (nb[0], nb[1]["y_top"]))

    # 标题合并：按**视觉位置**分组排序（先 y_top 行、同行内按 x0 从左到右），
    # 使标题文本顺序与图片一致，与 OCR 拆行无关；拼接不加空格。
    merged = []
    for n, b in all_blocks:
        # 仅合并"同层级的连续标题行"：标题(通常居左/非居中)与其后独立的作者署名行
        # (居中"知乎：…")不合并——否则标题文本会被署名污染, 无法与文件头标题做 skip。
        _same_align = (merged[-1][1].get("centered") == b.get("centered")) \
            if (merged and merged[-1][1]["type"] == "heading") else False
        if (b["type"] == "heading" and merged and merged[-1][1]["type"] == "heading"
                and merged[-1][1]["level"] == b["level"]
                and b["y_top"] - merged[-1][1]["y_bottom"] < 130
                and _same_align):
            # 并入当前 heading 组
            cur = merged[-1][1]
            cur["_group"].append(b)
            cur["y_bottom"] = max(cur["y_bottom"], b["y_bottom"])
        else:
            nb = dict(b)
            if nb.get("type") == "heading":
                nb["_group"] = []
            merged.append((n, nb))
    # 对每个 heading 组按**视觉位置**排序拼接：同行(y_top 相近)按 x0 从左到右，
    # 行间按 y 从上到下——与 _long_image_title 一致，保证正文标题与文件头一致可 skip。
    for n, b in merged:
        if b.get("type") == "heading":
            g = [b] + b.pop("_group", [])
            row_tol = max((hh.get("y_bottom", 0) - hh.get("y_top", 0) for hh in g),
                          default=20) * 0.6
            rows = []
            for hh in g:
                placed = False
                for r in rows:
                    if abs(hh["y_top"] - r["y_top"]) <= row_tol:
                        r["items"].append(hh)
                        placed = True
                        break
                if not placed:
                    rows.append({"y_top": hh["y_top"], "items": [hh]})
            rows.sort(key=lambda r: r["y_top"])
            parts = []
            for r in rows:
                r["items"].sort(key=lambda hh: hh.get("x0", 0))
                parts.append("".join(hh.get("text", "").strip() for hh in r["items"]))
            b["text"] = "".join(parts)
            b["y_bottom"] = max(hh["y_bottom"] for hh in g)
            b["bold"] = any(hh.get("bold") for hh in g)
            b["centered"] = all(hh.get("centered") for hh in g)

    # skip_done 只在"没有标题要跳过"时直接置 True；no_headings 时仍允许
    # 跳过与文件头 # {title} 重复的标题（长图标题已在文件头写 #）。
    skip_done = skip_title is None
    cur_para = []
    SENT_END = ("。", "！", "？")
    # 跨页权威段落判定：基于合并后的阅读顺序，用"文本标点 + 同页垂直间距"重算段首。
    #   - 文本标点：上一行以句末标点结尾 / 本行是节标签(以：结尾) → 段首。
    #   - 垂直间距（仅长图 no_headings 用，且只在**同一页内**相邻文本之间可比）：
    #     相邻文本块 gap/h 明显大于段内行距（OCR 拆行 gap/h≈0.2~0.6，段间距≈1.0+）
    #     → 段首。detect per-page 输出对切片首行无上下文（i=0 默认 False），
    #     故跨页处用"上一行是否句号"续判。
    # 追踪"上一个同页文本行"的位置用于 gap/h；换页/图片打断后失效。
    prev_text = ""
    prev_pg = None
    prev_bot = None
    prev_h = None

    def _page_gap_is_para(n, b):
        """同页且垂直间距明显(≥0.75×本行高) → 真段间距。仅长图模式启用。"""
        if not no_headings or prev_pg is None or n != prev_pg:
            return False
        h = b["y_bottom"] - b["y_top"]
        if h <= 0 or prev_bot is None:
            return False
        gap = b["y_top"] - prev_bot
        return gap >= 0.75 * h

    def _reset_pos():
        nonlocal prev_pg, prev_bot, prev_h
        prev_pg = None
        prev_bot = None
        prev_h = None

    def _update_pos(n, b):
        nonlocal prev_pg, prev_bot, prev_h
        prev_pg = n
        prev_bot = b["y_bottom"]
        prev_h = b["y_bottom"] - b["y_top"]

    # 通用列表识别状态：检测"连续编号行/同 bullet 行"并把它们输出为 markdown 列表。
    # 有序项(1./2./3. 或 1．2．3. 或 1、2、3)与无序项(- • ·)都会识别；
    # 编号标点统一为半角（`3．`→`3.`），markdown 预览缩进一致。
    pending_list = []
    pending_kind = None       # 'ol' / 'ul'
    pending_prev_num = None

    def _flush_pending_list():
        nonlocal pending_list, pending_kind, pending_prev_num
        if pending_list:
            out.append("\n".join(pending_list) + "\n\n")
        pending_list = []
        pending_kind = None
        pending_prev_num = None

    def _emit_list_or_para(plain, styled_html):
        """把一段文本输出：若它是列表项则并入 pending_list（连续同类项同列表），
        否则先冲刷 pending_list 再按普通段落输出。styled_html 为 None 表示纯文本。"""
        nonlocal pending_kind, pending_prev_num
        s = (plain or "").strip()
        item = None
        m = OL_RE.match(s) if s else None
        if m and styled_html is None:
            item = ("ol", int(m.group(1)), m.group(2).strip())
        else:
            m = UL_RE.match(s) if s else None
            if m and styled_html is None:
                item = ("ul", None, m.group(1).strip())
        if item is None:
            _flush_pending_list()
            if styled_html is None:
                out.append(s + "\n\n")
            else:
                out.append(styled_html + "\n\n")
            return
        kind, num, content = item
        if kind == "ol":
            line = f"{num}. {content}"
        else:
            line = f"- {content}"
        # 与上一个列表项同类且(有序编号连续 or 无序) → 并入同一列表
        if pending_kind == kind and (
                kind == "ul" or (num == (pending_prev_num or 0) + 1)):
            pending_list.append(line)
            pending_prev_num = num
            return
        # 不连续（编号跳跃 / 类型变化）→ 新起一段列表
        _flush_pending_list()
        pending_kind = kind
        pending_list.append(line)
        pending_prev_num = num

    def _push_text_segments(b, txt, bold, use_strip=True):
        """把一段文本（可能行内含多色 color_spans）切分后追加进 cur_para。

        行内混色（detect 的 color_spans=[{s,e,color_rgb,color_name}]，s/e 为字符
        区间）：黑段 col=None、彩色段用其标准色，逐段 append；_flush_para 合并
        相邻同色段，最终输出"行内多个 <span>"而非整行一色。
        """
        spans = b.get("color_spans") or []
        if not spans:
            _use = txt.strip() if use_strip else txt
            if b.get("color_hue") is not None and b.get("color_rgb"):
                col = (b["color_rgb"], b.get("color_name"))
            else:
                col = None
            if _use:
                cur_para.append([_use, bold, col])
            return
        # 有 spans：不 strip（保证字符索引与 spans 对齐）
        segs = []
        prev = 0
        n = len(txt)
        for sp in sorted(spans, key=lambda x: x["s"]):
            s = max(0, int(sp.get("s", 0)))
            e = min(n - 1, int(sp.get("e", 0)))
            if s > prev:
                segs.append((txt[prev:s], None))
            segs.append((txt[s:e + 1],
                         (sp["color_rgb"], sp.get("color_name"))))
            prev = e + 1
        if prev < n:
            segs.append((txt[prev:], None))
        for seg, col in segs:
            seg = seg.strip()
            if seg:
                cur_para.append([seg, bold, col])

    def _flush_para():
        """把 cur_para（元素 (text, bold, color_rgb)）组装成一段。
        连续同加粗且同色的行合并为一个 run；run 整体按 bold/颜色上样
        （<strong> / <span style="color:#xxxxxx">），避免 OCR 把同一视觉行拆成
        多块时输出 <strong>a</strong><strong>b</strong>（如"注：…板/块。"）。

        若该段是"列表项"（行首编号/bullet）且为纯文本 → 走 _emit_list_or_para
        归入通用列表；带样式(加粗/彩色)的段落按普通文本输出（保留样式）。
        """
        if not cur_para:
            return
        runs = []
        for txt, bld, col in cur_para:
            if runs and runs[-1][1] == bld and runs[-1][2] == col:
                runs[-1][0] += txt
            else:
                runs.append([txt, bld, col])
        has_style = any(bld or col for _, bld, col in runs)
        parts = []
        for t, bld, col in runs:
            s = t
            if col:
                # col=(rgb, name)。语义标准色优先输出名称（如 red/pink）；无则 hex
                _val = col[1] if col[1] else "#%02x%02x%02x" % tuple(col[0][:3])
                s = f'<span style="color:{_val}">{s}</span>'
            if bld:
                s = f"<strong>{s}</strong>"
            parts.append(s)
        plain = "".join(txt for txt, _, _ in cur_para)
        _emit_list_or_para(plain, None if not has_style else "".join(parts))
        cur_para.clear()

    def _md_table(b):
        rows = b.get("rows") or []
        if not rows:
            return ""
        ncol = max(len(r) for r in rows)
        def _cell_text(c):
            t = (c.get("text") or "").strip() if isinstance(c, dict) else str(c).strip()
            col = c.get("color_rgb") if isinstance(c, dict) else None
            if col:
                nm = c.get("color_name") if isinstance(c, dict) else None
                _val = nm if nm else "#%02x%02x%02x" % tuple(col[:3])
                t = f'<span style="color:{_val}">{t}</span>'
            t = t.replace("|", "\\|")
            return t
        def _line(cells):
            cells = cells + [""] * (ncol - len(cells))
            return "| " + " | ".join(_cell_text(c) for c in cells) + " |"
        lines = [_line(rows[0])]
        lines.append("| " + " | ".join("---" for _ in range(ncol)) + " |")
        for r in rows[1:]:
            lines.append(_line(r))
        return "\n".join(lines) + "\n\n"

    for n, b in merged:
        if b["type"] == "image":
            if _is_title_bar_block(n, b, drop_img_span):
                continue      # 标题条截图：标题文字已在文件头，留字去图
            _flush_para()
            _flush_pending_list()
            cap = b.get("caption", "").strip()
            alt = cap if cap else "插图"
            out.append(f"![{alt}](../figs/{b['file']})\n\n")
            prev_text = ""
            _reset_pos()
            continue
        if b["type"] == "table":
            _flush_para()
            _flush_pending_list()
            _mdt = _md_table(b)
            if _mdt:
                out.append(_mdt)
            prev_text = ""
            _reset_pos()
            continue
        if b["type"] == "heading":
            if not skip_done and b["text"].strip() == skip_title:
                skip_done = True
                prev_text = b["text"].rstrip()
                continue
            if no_headings:
                _bold = bool(b.get("bold"))
                _centered = bool(b.get("centered"))
                _t = b["text"]
                # skip_title 匹配——若合并后 heading 文本以标题开头, 剥除前缀输出剩余;
                # 解决 heading 合并把"居中标题+居中节标签"粘成 "标题写在前面" 而跳不过的 bug。
                if not skip_done and _t.startswith(skip_title):
                    rest = _t[len(skip_title):].lstrip()
                    skip_done = True
                    if not rest:
                        prev_text = _t.rstrip()
                        _update_pos(n, b)
                        continue
                    # 把剩余部分当成独立 heading 输出(保留居中/加粗)
                    if _centered:
                        _flush_para()
                        _flush_pending_list()
                        out.append(_fmt_line(rest, _bold, True) + "\n\n")
                    else:
                        _push_text_segments(b, rest, _bold, use_strip=True)
                    prev_text = rest.rstrip()
                    _update_pos(n, b)
                    continue
                _ci = _is_list_item(_t)
                _is_label_h = _t.strip().endswith("：")
                # 段首判定：与 text 分支一致——以 detect 的 para_start 为主（标点+节标签
                # 文本驱动）, 跨页/切片首行用 prev_text 句末标点补判。
                para_start = (bool(b.get("para_start", False))
                              or _is_label_h
                              or (prev_text.rstrip().endswith("：") and _ci is not None)
                              or (prev_text.endswith(SENT_END) if prev_text else False))
                if para_start and cur_para:
                    _flush_para()
                    _flush_pending_list()
                if _centered:
                    _flush_para()
                    _flush_pending_list()
                    out.append(_fmt_line(_t, _bold, True) + "\n\n")
                    _update_pos(n, b)
                else:
                    # 仅真正彩色（有明确色相）上色；黑/灰正文不上；
                    # 行内混色(color_spans)由 _push_text_segments 按段处理
                    _push_text_segments(b, _t, _bold, use_strip=True)
                    _update_pos(n, b)
                prev_text = _t.rstrip()
                continue
            _flush_para()
            _flush_pending_list()
            level = b.get("level", 3)
            marker = "#" * level
            out.append(f"{marker} {b['text']}\n\n")
            prev_text = b["text"].rstrip()
            _reset_pos()
            continue
        t = b["text"]
        # 长图文章标题已作为 # 写入文件头：正文首个与标题相同的 text（标题行不是
        # heading 而是普通加粗 text）也应跳过，避免 `# 标题` 后又重复一行标题。
        if not skip_done and t.strip() == skip_title:
            skip_done = True
            prev_text = t.rstrip()
            _update_pos(n, b)
            continue
        bold = bool(b.get("bold"))
        centered = bool(b.get("centered"))
        is_label = t.strip().endswith("：")
        _cur_item = _is_list_item(t)
        # 段首判定：优先用 detect 的 para_start（标点+节标签文本驱动, 在长图
        # 行距大(gap/h≈0.94)的截图上仍可靠——而旧版的 gap>=0.75h 阈值会把
        # 每行当段首造成"每行独立段"伪断裂）。跨页/切片首行 detect 拿不到上行
        # 上下文默认 False, 用 prev_text 句末标点补判。
        para_start = (bool(b.get("para_start", False))
                      or is_label
                      or (prev_text.rstrip().endswith("：") and _cur_item is not None)
                      or (prev_text.endswith(SENT_END) if prev_text else False))
        if para_start:
            _flush_para()
        if centered:
            _flush_para()
            _flush_pending_list()
            out.append(_fmt_line(t, bold, centered) + "\n\n")
        else:
            # 仅真正彩色（有明确色相）上色；黑/灰正文不上。
            # 行内混色(color_spans)由 _push_text_segments 按段处理；
            # 无混色时仍保持原 append(t) 语义（不 strip，保留 OCR 原文）。
            _push_text_segments(b, t, bold, use_strip=False)
        prev_text = t.rstrip()
        _update_pos(n, b)
    _flush_para()
    _flush_pending_list()
    return "".join(out)


def _write_chapter(blocks_dir, md_tmp, spec):
    kind, slug, fname, title, start, end = spec
    p = os.path.join(md_tmp, fname)
    # 空标题（如长图切分合并的单篇）不写 `#`，避免无意义的空标题
    head = ([f"# {title}\n\n"] if title else [])
    if kind == "front":
        body = _render_pages_cross_page(
            blocks_dir, list(range(start, end + 1)),
            skip_pages=(10, 11, 12))
    elif kind == "part_intro":
        body = _render_pages_cross_page(blocks_dir, [start])
    else:
        # 长图切分（slug 为空）：文章标题已从长图顶部粗体取到，正文里其余
        # heading（图表标题等）仍当普通文本，只在文件头写 # 标题、并跳过正文
        # 里与标题相同的首个 heading 避免重复。普通书则按 heading 正常渲染。
        is_long_image = (slug == "")
        drop_img_span = None
        if is_long_image:
            # 长截图顶部"标题+作者"横条常被 detect 裁成一张图；标题文字既已提取为
            # 文件头 `# 标题`，就丢弃这张"标题条截图"，做到留字去图。
            span = _long_image_title_span_px(blocks_dir, start)
            if span:
                drop_img_span = (start,) + tuple(span)
        body = _render_pages_cross_page(
            blocks_dir, list(range(start, end + 1)),
            skip_title=title, no_headings=is_long_image or not bool(title),
            drop_img_span=drop_img_span)
    with open(p, "w", encoding="utf-8") as f:
        f.write("".join(head) + body)
    return p


def _mirror_dir(src_dir, dst_dir):
    """把 src_dir 的内容镜像同步到 dst_dir：文件复制覆盖，目录递归同步，
    并删除 dst_dir 中 src_dir 没有的多余项（避免历史残留累积，如 images/images）。"""
    os.makedirs(dst_dir, exist_ok=True)
    src_names = set(os.listdir(src_dir))
    for f in src_names:
        s = os.path.join(src_dir, f)
        d = os.path.join(dst_dir, f)
        if os.path.isdir(s):
            _mirror_dir(s, d)
        else:
            shutil.copy2(s, d)
    for f in os.listdir(dst_dir):
        if f not in src_names:
            p = os.path.join(dst_dir, f)
            if os.path.isdir(p):
                shutil.rmtree(p)
            else:
                os.remove(p)


def _swap_in(tmp, dst):
    """把 tmp 内容移入 dst（覆盖同名），并删除 dst 中多出的旧文件/目录。

    支持子目录（如 images/）：用镜像同步递归合并，历史残留（如 images/images）会被清掉。
    """
    os.makedirs(dst, exist_ok=True)
    _mirror_dir(tmp, dst)
    try:
        shutil.rmtree(tmp)
    except OSError:
        pass


def _rewrite_fig_refs(md_text):
    """把 article 内的 `../figs/` 图片引用改为 `images/`。"""
    return md_text.replace("../figs/", "images/")


def _clean_stale_tmp(output_dir):
    """清理 output 下历史遗留的临时目录（.md.tmp.* / .article.tmp.* 等）。

    正常流程里临时目录在 _swap_in 中会被清理；若某次运行中途失败会残留。
    这里在每次 build 前清除它们，避免越积越多。
    """
    out = str(output_dir)
    if not os.path.isdir(out):
        return
    for name in os.listdir(out):
        p = os.path.join(out, name)
        if os.path.isdir(p) and name.startswith((".md.tmp.", ".article.tmp.",
                                                 ".blocks.tmp.", ".figs.tmp.")):
            shutil.rmtree(p, ignore_errors=True)
            print(f"  清理历史临时目录: {p}")


def build(work_dir, chapters=None):
    """组装章节 Markdown，并产出 article 目录（每章一文件 + images/）。

    Args:
        work_dir: 中间产物根目录
        chapters: 可选人工章节结构（list of dict，含 kind/slug/file/title/start/end）
    Returns:
        (md_dir, article_dir, chapters)
    """
    work = paths.work_path(work_dir)
    _clean_stale_tmp(str(work))
    blocks_dir = str(paths.blocks_dir(work))
    figs_dir = str(paths.figs_dir(work))
    md_dir = str(paths.md_dir(work))
    article_dir = str(paths.article_dir(work))
    images_out = str(paths.images_dir(work))

    if not os.path.isdir(blocks_dir):
        raise SystemExit(f"错误：未找到 blocks 目录 {blocks_dir}\n"
                         f"  请先运行布局分析（p2md detect / convert）")
    if not os.path.isdir(figs_dir):
        raise SystemExit(f"错误：未找到 figs 目录 {figs_dir}\n"
                         f"  请先运行布局分析（p2md detect / convert）")

    md_tmp = os.path.join(str(work), f".md.tmp.{os.getpid()}")
    os.makedirs(md_tmp, exist_ok=True)

    if chapters is None:
        chapters = auto_chapters(blocks_dir)
        print("检测到的章节结构（自动推导，可人工复核）：")
        for c in chapters:
            print(f"  {c[0]:10} {c[4]:>4}-{c[5]:<4}  {c[1]}")
    else:
        print("使用人工章节结构")

    written = []
    for spec in chapters:
        if isinstance(spec, dict):
            spec = (spec["kind"], spec["slug"], spec["file"], spec["title"],
                    spec["start"], spec["end"])
        p = _write_chapter(blocks_dir, md_tmp, spec)
        written.append(spec)
        print(f"  {os.path.basename(p)}  ({os.path.getsize(p) // 1024} KB)")
    _swap_in(md_tmp, md_dir)

    # ---- 产出 article 目录（每章一个 md + images/） ----
    # 每章 → article_N.md；图片引用 ../figs/ → images/；裁图复制到 content/images/
    article_tmp = os.path.join(str(work), f".article.tmp.{os.getpid()}")
    images_tmp = os.path.join(article_tmp, "images")
    os.makedirs(article_tmp, exist_ok=True)
    os.makedirs(images_tmp, exist_ok=True)

    # 复制裁图
    if os.path.isdir(figs_dir):
        for f in os.listdir(figs_dir):
            if f.endswith(".png"):
                shutil.copyfile(os.path.join(figs_dir, f), os.path.join(images_tmp, f))

    # 每章写为 article_N.md（按章节顺序编号）
    for i, spec in enumerate(written):
        # 从 md_dir 读取对应章节文件
        fname = spec[2]
        src_path = os.path.join(md_dir, fname)
        if not os.path.exists(src_path):
            continue
        with open(src_path, encoding="utf-8") as f:
            content = f.read()
        content = _rewrite_fig_refs(content)
        with open(os.path.join(article_tmp, f"article_{i}.md"), "w", encoding="utf-8") as f:
            f.write(content)
    _swap_in(article_tmp, article_dir)

    n_articles = len(written)
    n_imgs = len([f for f in os.listdir(images_out) if f.endswith(".png")]) if os.path.isdir(images_out) else 0
    print(f"\n产出 {n_articles} 章 article + {n_imgs} 张图 → {article_dir}/")
    return md_dir, article_dir, written
