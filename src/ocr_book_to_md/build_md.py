# -*- coding: utf-8 -*-
"""组装章节 Markdown（迁移自 build_md.py）+ 产出 document-factory 可读的 article 目录。

章节边界从 blocks 的 heading 自动推导（"第X章"/"第X编"/"参考文献"）。

产物（project/output/ 下）：
    md/                         传统章节 Markdown（保留便于复核）
    content/article_N.md        每章一篇文章（供 document-factory pdf 读取）
    content/images/             裁图（article 内的图片引用统一改为 images/）
"""

import json
import os
import re
import shutil

CN = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
      "七": 7, "八": 8, "九": 9, "十": 10}


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


def _long_image_title(blocks_dir, first_page):
    """取长图文章标题：对第一片顶部的文本块做**综合判断**识别标题区。

    不依赖 detect 的 heading 判定（heading 对长图常因行高/加粗边界漏判），而直接
    分析第一片最顶部的文本块，用**多个信号综合**（单一信号都不是必须的）：

      - 位置：标题在第一片最顶部（正文/小节标签之前，或紧随顶部元信息之后）；
      - 字号：标题字号**不小于**页内正文行高中位（节标签/图表注字号明显更小）；
      - 加粗：常加粗（加分项，字号显著大时非加粗也认）；
      - 内容：不是时间戳（如"11:53 ③"）、不是节标签（以 `：` 结尾）。

    图片上标题顺序固定，结果只依赖图片排版，与 OCR 拆行无关。
    """
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

    # 按阅读序（y 升序）遍历，定位标题区：顶部连续的非时间戳、非节标签块，
    # 字号 >= 正文中位（>=0.95×）或加粗，即综合判定为标题。
    blocks.sort(key=lambda b: (b["y_top"], b.get("x0", 0)))
    group = []
    for b in blocks:
        t = (b.get("text") or "").strip()
        if re.match(r"^\s*\d{1,2}:\d{2}", t):
            continue  # 时间戳/元信息
        if t.endswith("："):
            break    # 到第一个节标签（正文开始），标题区结束
        h = b["y_bottom"] - b["y_top"]
        is_bold = bool(b.get("bold"))
        big = h >= body_h * 0.95
        if is_bold or big:
            if group and b["y_top"] - group[-1]["y_bottom"] > 1.6 * (group[-1]["y_bottom"] - group[-1]["y_top"] or 1):
                break  # 与已收标题相距过大（如跳到正文），停止
            group.append(b)
        elif group:
            break  # 标题区结束：遇到既不加粗也不大的文本块
    if not group:
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


def _render_pages_cross_page(blocks_dir, pages, skip_title=None, no_headings=False,
                                skip_pages=()):
    """跨页渲染：把 pages 列表里所有页的 blocks 合在一起按 y 排序，
    段落状态 cur_para 跨页延续——detect 标 para_start=False 的首行（切片边界
    续行）自动并入上一段，消除长图切片造成的"段中换页=两段"伪断裂。

    skip_pages: 这些页整页不参与渲染（沿用旧的 front 跳过第10/11/12页逻辑）。
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
        if (b["type"] == "heading" and merged and merged[-1][1]["type"] == "heading"
                and merged[-1][1]["level"] == b["level"]
                and b["y_top"] - merged[-1][1]["y_bottom"] < 130):
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

    def _flush_para():
        """把 cur_para（列表元素 (text, bold)）组装成一段。
        连续同加粗的行合并为一个 <strong>…</strong>，避免 OCR 把同一视觉行拆成多块
        时输出 <strong>a</strong><strong>b</strong>（如"注：…板/块。"、免责声明跨行）。
        """
        if not cur_para:
            return
        runs = []
        for txt, bld in cur_para:
            if runs and runs[-1][1] == bld:
                runs[-1][0] += txt
            else:
                runs.append([txt, bld])
        out.append("".join(
            f"<strong>{t}</strong>" if bld else t for t, bld in runs) + "\n\n")
        cur_para.clear()

    for n, b in merged:
        if b["type"] == "image":
            _flush_para()
            cap = b.get("caption", "").strip()
            alt = cap if cap else "插图"
            out.append(f"![{alt}](../figs/{b['file']})\n\n")
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
                if bool(b.get("para_start")) and cur_para:
                    _flush_para()
                if _centered:
                    _flush_para()
                    out.append(_fmt_line(b["text"], _bold, True) + "\n\n")
                    _update_pos(n, b)
                else:
                    cur_para.append([b["text"].strip(), _bold])
                    _update_pos(n, b)
                prev_text = b["text"].rstrip()
                continue
            _flush_para()
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
        # 段首判定：节标签 / 上一行句末标点 / 同页垂直间距明显
        is_label = t.strip().endswith("：")
        para_start = (is_label
                      or (prev_text.endswith(SENT_END) if prev_text else False)
                      or _page_gap_is_para(n, b))
        if para_start:
            _flush_para()
        if centered:
            _flush_para()
            out.append(_fmt_line(t, bold, centered) + "\n\n")
        else:
            cur_para.append([t, bold])
        prev_text = t.rstrip()
        _update_pos(n, b)
    _flush_para()
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
        body = _render_pages_cross_page(
            blocks_dir, list(range(start, end + 1)),
            skip_title=title, no_headings=is_long_image or not bool(title))
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
    """把 article 内的 `../figs/` 图片引用改为 `images/`（document-factory 约定）。"""
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


def build(project, chapters=None):
    """组装章节 Markdown，并产出 document-factory 可读的 article 目录。

    Args:
        project: Project 实例
        chapters: 可选人工章节结构（list of dict，含 kind/slug/file/title/start/end）
    Returns:
        (md_dir, article_dir, chapters)
    """
    _clean_stale_tmp(project.output_dir)
    blocks_dir = str(project.get_blocks_dir())
    figs_dir = str(project.get_figs_dir())
    md_dir = str(project.get_md_dir())
    article_dir = str(project.get_article_dir())
    images_out = str(project.get_images_out_dir())

    if not os.path.isdir(blocks_dir):
        raise SystemExit(f"错误：未找到 blocks 目录 {blocks_dir}\n"
                         f"  请先运行布局分析：python main.py detect -p <project>")
    if not os.path.isdir(figs_dir):
        raise SystemExit(f"错误：未找到 figs 目录 {figs_dir}\n"
                         f"  请先运行布局分析：python main.py detect -p <project>")

    md_tmp = os.path.join(project.output_dir, f".md.tmp.{os.getpid()}")
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

    # ---- 产出 document-factory 可读的 article 目录 ----
    # 每章 → article_N.md；图片引用 ../figs/ → images/；裁图复制到 content/images/
    article_tmp = os.path.join(project.output_dir, f".article.tmp.{os.getpid()}")
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
    print(f"\n产出 {n_articles} 章 article + {n_imgs} 张图 → {article_dir}/（可直接交给 document-factory pdf）")
    return md_dir, article_dir, written
