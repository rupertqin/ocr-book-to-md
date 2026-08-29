# -*- coding: utf-8 -*-
"""图/标题/正文三分检测（迁移自 detect_figs.py）。

核心思想：用字号（行高）区分"正文/标题/图内文字"，用颜色区分标题。
OCR 坐标 JSON（<project>/output/ocr/pages_json/）→ 结构化 blocks JSON + 裁图。
"""

import json
import os
import re
import shutil
import sys

from PIL import Image
import numpy as np

FIG_RE = re.compile(r"^\s*图\s*[0-9]+\.[0-9]+")
TBL_RE = re.compile(r"^\s*表\s*[0-9]+\.[0-9]+")
SRC_RE = re.compile(r"^\s*(注|资料来源|来源)[:：]?")
ROW_THR = 4
GAP_TOL = 12
PHOTO_MIN = 120
PHOTO_MIN_W = 120
RUN_GAP = 40


def _norm_to_px(box, W, H):
    x0 = box["x"] * W
    x1 = (box["x"] + box["w"]) * W
    y_top = (1 - box["y"] - box["h"]) * H
    y_bot = (1 - box["y"]) * H
    return x0, y_top, x1, y_bot


def _lines_to_px(lines, W, H):
    out = []
    for l in lines:
        x0, y_top, x1, y_bot = _norm_to_px(l, W, H)
        out.append({"text": l["text"], "x0": x0, "y_top": y_top,
                    "y_bottom": y_bot, "x1": x1, "h": y_bot - y_top,
                    "w": x1 - x0})
    return out


def _estimate_body_h(px):
    if not px:
        return 48.0
    page_w = max((p["x1"] for p in px), default=1)
    hs = [l["h"] for l in px
          if l["w"] > 0.85 * page_w
          and not FIG_RE.search(l["text"])
          and not TBL_RE.search(l["text"])
          and not SRC_RE.search(l["text"])]
    if len(hs) < 3:
        hs = [l["h"] for l in px
              if l["w"] > 0.7 * page_w
              and not FIG_RE.search(l["text"])
              and not TBL_RE.search(l["text"])
              and not SRC_RE.search(l["text"])]
    if len(hs) < 2:
        return 48.0
    return float(np.median(hs))


def _is_tall(line, body_h):
    return line["h"] >= 0.82 * body_h


def _line_text_color(img_rgb, box, W, H):
    if "x" in box:
        x0 = int(box["x"] * W)
        x1 = int((box["x"] + box["w"]) * W)
        y0 = int((1 - box["y"] - box["h"]) * H)
        y1 = int((1 - box["y"]) * H)
    else:
        x0, x1 = int(box["x0"]), int(box["x1"])
        y0, y1 = int(box["y_top"]), int(box["y_bottom"])
    if x1 - x0 < 10 or y1 - y0 < 8:
        return None
    reg = img_rgb[y0:y1, x0:x1]
    lum = reg.astype(np.float32).mean(axis=2)
    m = lum < 185
    if m.sum() < 25:
        return None
    px = reg[m]
    return (int(np.median(px[:, 0])), int(np.median(px[:, 1])), int(np.median(px[:, 2])))


def _col_sat(rgb):
    mx, mn = max(rgb), min(rgb)
    return (mx - mn) / mx if mx > 25 else 0.0


def _line_centered(t, W):
    """判断一行文字是否水平居中：文字 bbox 中心接近页面水平中心，
    且不是满行文字（居中行通常较短，占宽 <0.9）。"""
    xc = (t["x0"] + t["x1"]) / 2
    w = t["x1"] - t["x0"]
    page_c = W / 2
    tol = max(0.03 * W, 20)  # 中心偏差容差
    return abs(xc - page_c) <= tol and w < 0.9 * W


def _line_bold(t, img_gray, W, H, threshold_ratio=0.078):
    """用距离变换估算笔画粗细来判定是否加粗。

    密度法把"字大/颜色深/字密"都混进加粗，分不清"注/免责"(真粗) vs 正文(密但不粗)。
    改为：对单行文字做二值化→距离变换，取笔画中心处距离的高分位数作为笔画半径，
    stroke_width=2×radius，再除以行高消除字号影响。粗体笔画更厚 → 相对粗细更大。

    只用 scipy 的 distance_transform_edt（通用科学计算库），不引入 OpenCV(cv2)——
    避免与 macOS Vision 形成两套图像处理栈的重复。
    threshold_ratio: 相对笔画粗细（stroke_width/行高）阈值。实测常规正文 ~0.075，
        粗体 ~0.082+，默认 0.078 可区分；可按项目用 detect.bold_thickness_ratio 调。
    """
    x0 = max(0, int(t["x0"])); x1 = min(W, int(t["x1"]))
    y0 = max(0, int(t["y_top"])); y1 = min(H, int(t["y_bottom"]))
    if x1 - x0 < 8 or y1 - y0 < 6:
        return False
    reg = img_gray[y0:y1, x0:x1]
    if reg.size == 0:
        return False
    h = y1 - y0
    try:
        from scipy.ndimage import distance_transform_edt
        # 二值化：文字为前景(True)，背景为 0
        binary = (reg < 128)
        dist = distance_transform_edt(binary)
        nonzero = dist[dist > 0.5]
        if nonzero.size == 0:
            return False
        radius = float(np.percentile(nonzero, 80))
    except Exception:
        return False
    stroke_width = radius * 2
    relative = stroke_width / h
    return relative > threshold_ratio


def _detect_headings(px, body_h, page_w, img_rgb, img_gray, W, H,
                     exclude_ranges=(), min_heading_ratio=1.28, global_body_h=None,
                     bold_thickness_ratio=0.078):
    """检测标题。

    h_ratio 用全局正文基准 global_body_h 计算（跨页面统一），保证标题字号
    与正文字号的比较在全书中稳定有效，而非受单页正文行高估算波动影响。
    若未传 global_body_h，回退用本页 body_h。

    min_heading_ratio: 彩色标题（`##`）的最小行高比例。真实标题字号明显大于正文
    （如"悖论系列N"1.31+/"第X章"1.29+），正文里独立成行的诗歌/脚注行字号接近
    正文（1.07~1.27×）。设 1.28 可过滤后者。可经 config `detect.min_heading_ratio` 调整。

    粗体标题（如长图顶部"（8.24~8.30）"/"8月周总结第3期"/"11:53 ③"）：
    行高可能并不明显大于正文（1.03×），但笔画明显更粗。作为补充，粗体且短、
    不以句号结尾、宽度适中、周围留白的行也判为标题——这能排除正文里加粗的
    注释/免责声明（"注：...。"、"本文仅为...入市需谨慎。"都以句号结尾）。
    """
    # global_body_h 传归一化正文行高中位数（跨页统一），换算成当前页像素：*H
    base_h = (global_body_h * H) if global_body_h else body_h
    excl = [(e0 - 4, e1 + 4) for (e0, e1) in exclude_ranges]
    candidates = []
    bold_titles = set()  # 粗体标题候选（行高不足但笔画粗的短行）
    # 标题是短标题，正文段落（一整句）即使行高略大也不应被当标题。
    # 用最大字符数过滤：超过 max_heading_chars 视为正文（误判的长句不再当标题）。
    max_heading_chars = 40
    for i, line in enumerate(px):
        if FIG_RE.search(line["text"]) or TBL_RE.search(line["text"]):
            continue
        txt = line["text"].strip()
        if len(txt) > max_heading_chars:
            continue
        h_ratio = line["h"] / base_h
        w = line["w"]
        if h_ratio > 2.2:
            continue
        yc = (line["y_top"] + line["y_bottom"]) / 2
        if any(e0 <= yc <= e1 for (e0, e1) in excl):
            continue
        col = _line_text_color(img_rgb, line, W, H)
        sat = _col_sat(col) if col else 0.0
        if col and sat > 0.25 and h_ratio >= min_heading_ratio \
                and 0.10 * page_w < w <= 0.95 * page_w:
            candidates.append((i, 2))
        elif (not col or sat < 0.35) and h_ratio >= 1.18 \
                and 0.14 * page_w <= w <= 0.92 * page_w:
            candidates.append((i, 3))
        # 粗体标题（长图顶部文章标题等）：笔画粗但行高不足。粗体+短+不以句号结尾
        # +宽度适中 → 标题；正文里的加粗注释/免责声明以句号结尾被排除。
        elif _line_bold(line, img_gray, W, H, bold_thickness_ratio) \
                and len(txt) <= 15 \
                and not txt.endswith(("。", "！", "？")) \
                and 0.08 * page_w < w <= 0.95 * page_w:
            candidates.append((i, 3))
            bold_titles.add(i)
    if not candidates:
        return {}

    cands = sorted(candidates, key=lambda t: px[t[0]]["y_top"])
    groups = []
    cur = [cands[0]]
    for c in cands[1:]:
        if px[c[0]]["y_top"] - px[cur[-1][0]]["y_bottom"] < 1.4 * body_h \
                and c[1] == cur[-1][1]:
            cur.append(c)
        else:
            groups.append(cur)
            cur = [c]
    groups.append(cur)

    result = {}
    for g in groups:
        g_top = min(px[i]["y_top"] for i, _ in g)
        g_bot = max(px[i]["y_bottom"] for i, _ in g)
        # 标题候选跨 ≥3 行时判定为正文段落（OCR 把长句拆成多行、每行都略大被误判
        # 为 heading）——拒绝。真正的标题是 1~2 行，不要误伤单个超高行标题。
        if len(g) >= 3:
            continue
        is_bold_title = all(i in bold_titles for i, _ in g)
        # 粗体标题是强信号：短、粗、不以句号结尾，几乎必然是标题。长图页头 OCR 常
        # 有标题与"已付费/原创"等重叠，留白/暗区校验会误拒——对纯粗体标题组直接放行。
        if is_bold_title:
            for i, lv in g:
                result[i] = lv
            continue
        above = [l for l in px if l["y_bottom"] <= g_top]
        below = [l for l in px if l["y_top"] >= g_bot]
        # 标题在页面最上/最下时，页边即留白：此时上方/下方没有内容，视为足够留白。
        gap_up = (g_top - max((l["y_bottom"] for l in above), default=0)
                  if above else g_top + 2 * body_h)
        gap_dn = (min((l["y_top"] for l in below), default=H) - g_bot
                  if below else (H - g_bot) + 2 * body_h)
        # 标题须周围有足够留白（避免被紧贴的正文行干扰）；候选已是明显大于正文的字号。
        # 放宽：字号明显大于正文(≥min_heading_ratio*0.92)的强候选，只需 0.3×body_h 的留白，
        # 否则像"标题紧跟小徽章/正文"的合法标题会被误拒。弱候选仍要求较大留白。
        g_h_ratio = max(px[i]["h"] for i, _ in g) / base_h
        min_gap = 0.3 * body_h
        if g_h_ratio < min_heading_ratio * 0.92:
            min_gap = 1.2 * body_h
        if gap_up < min_gap or gap_dn < min_gap:
            continue
        up = img_gray[max(0, int(g_top) - 24):int(g_top), :]
        dn = img_gray[int(g_bot):min(H, int(g_bot) + 24), :]
        up_dark = int((up < 210).sum()) if up.size else 0
        dn_dark = int((dn < 210).sum()) if dn.size else 0
        if up_dark > 4 * 24 * 4 or dn_dark > 4 * 24 * 4:
            continue
        for i, lv in g:
            result[i] = lv
    return result


def _para_clusters(px, body_h, page_w):
    tall = sorted([l for l in px if _is_tall(l, body_h) and l["w"] >= 0.12 * page_w],
                  key=lambda l: l["y_top"])
    clusters = []
    cur = []
    for l in tall:
        if cur and l["y_top"] - cur[-1]["y_bottom"] < 1.6 * body_h:
            cur.append(l)
        else:
            if cur:
                clusters.append(cur)
            cur = [l]
    if cur:
        clusters.append(cur)
    paras = []
    for c in clusters:
        has_full = any(l["w"] > 0.85 * page_w for l in c)
        avg_w = sum(l["w"] for l in c) / len(c)
        half = any(l["w"] > 0.5 * page_w for l in c)
        if has_full or avg_w > 0.55 * page_w or (len(c) >= 2 and half):
            paras.append((min(l["y_top"] for l in c), max(l["y_bottom"] for l in c)))
    return paras


def _group_lines_into_paragraphs(px, body_h=None, img_gray=None):
    """把 OCR 行按段落分组，返回 {行 id: 该行是否为段落首行}。

    段落边界**纯内容驱动**——只用文本标点判定，完全不依赖像素宽度/间距/形态学，
    因此同一内容在不同宽度截图下段落结构严格一致。

    一行是段落首行，当且仅当满足任一：
      1) 节标签：去首尾空白后以全角冒号 `：` 结尾（上证指数：/周线：/日线：/
         中证1000：/综上所述：/短线：/中长线（模拟盘仅供参考）：等）→ 段首；
      2) 上一行（阅读顺序上的前一行）以句末标点（`。`/`！`/`？`）结尾
         → 段首。上一行若不以句末标点结尾（句中逗号 / 行末无标点），说明
         是同一句的软换行续行，**不**开新段。

    关键洞察：正文里真正的段间距在视觉上和"行→行"间距非常接近（都是一行的
    高度），用像素间距/比例阈值根本分不开；但作者在段末会用句号收束一句话，
    段首是新一句——所以"上一行是句末标点"是最可靠的段首信号，且完全文本驱动，
    不同宽度截图 OCR 换行虽然不同，但**同一个结构位置**的上一行是否以句号
    结尾是一致的（要么都是句号，要么都不是），因此两图段落结构天然一致。
    """
    lines = sorted(px, key=lambda l: l["y_top"])
    if not lines:
        return {}
    SENT_END = ("。", "！", "？")
    start_idx = {}
    for i, l in enumerate(lines):
        text = l["text"].strip()
        # 1) 节标签（内容驱动，宽度无关）
        if text.endswith("："):
            start_idx[id(l)] = True
            continue
        # 2) 上一行是句末标点 → 真段首
        if i > 0 and lines[i - 1]["text"].rstrip().endswith(SENT_END):
            start_idx[id(l)] = True
            continue
        # 其余（页面首行无上一行 / 上一行句中逗号或无标点=软换行续行）→ 并入当前段
        start_idx[id(l)] = False
    return start_idx


def _row_inside_para(y, paras):
    for (py0, py1) in paras:
        if py0 - 4 <= y <= py1 + 4:
            return True
    return False


def _row_thr(W):
    return max(3, int(W * 0.004))


def _scan_up_blank(img_gray, start_y, W, blank_px=12, max_dist=3000):
    y = int(start_y)
    thr = _row_thr(W)
    while y > 0 and start_y - y < max_dist:
        band = img_gray[y - 4:y, :]
        if band.size and (band < 210).sum() > thr:
            break
        y -= 4
    if y <= 0:
        return (0, int(start_y))
    content_top = y
    blanks = 0
    while y > 0 and start_y - y < max_dist:
        band = img_gray[y - 4:y, :]
        if band.size and (band < 210).sum() > thr:
            content_top = y - 4
            blanks = 0
        else:
            blanks += 4
            if blanks >= blank_px:
                break
        y -= 4
    return (max(0, content_top), int(start_y))


def _scan_down_blank(img_gray, start_y, H, W, blank_px=12, max_dist=3000):
    y = int(start_y)
    thr = _row_thr(W)
    while y < H and y - start_y < max_dist:
        band = img_gray[y:min(H, y + 4), :]
        if band.size and (band < 210).sum() > thr:
            break
        y += 4
    if y >= H:
        return (int(start_y), H)
    content_bot = y + 4
    blanks = 0
    while y < H and y - start_y < max_dist:
        band = img_gray[y:min(H, y + 4), :]
        if band.size and (band < 210).sum() > thr:
            content_bot = y + 4
            blanks = 0
        else:
            blanks += 4
            if blanks >= blank_px:
                break
        y += 4
    return (int(start_y), min(H, content_bot))


def _region_top_above(cap_top, cap_bot, paras, img_gray, W):
    above = [p for p in paras if p[1] <= cap_top + 4]
    if above:
        return (max(p[1] for p in above), cap_bot)
    return (_scan_up_blank(img_gray, cap_top, W)[0], cap_bot)


def _region_bottom_below(cap_top, cap_bot, paras, img_gray, W, H):
    below = [p for p in paras if p[0] >= cap_bot - 4]
    if below:
        return (cap_top, min(p[0] for p in below))
    return (cap_top, _scan_down_blank(img_gray, cap_bot, H, W)[1])


def _trim_para_inside(region, paras, cap=None):
    top, bot = region
    for (py0, py1) in paras:
        if py1 <= bot + 2 and py0 >= top - 2:
            if cap is not None and abs(py0 - cap["y_top"]) < 8:
                continue
            if (cap is not None and abs(py0 - cap["y_top"]) < 60
                    and (FIG_RE.search(cap["text"]) or TBL_RE.search(cap["text"]))):
                continue
            return None
    return top, bot


def _tight_crop(img_gray, W, region, x_margin=10):
    ty0, ty1 = int(round(region[0])), int(round(region[1]))
    if ty1 - ty0 < 40:
        return None
    sub = img_gray[ty0:ty1, :]
    rowsum = (sub < 210).sum(axis=1)
    thr = max(3, int(W * 0.004))
    rows = np.where(rowsum > thr)[0]
    if rows.size == 0:
        return None
    rmin, rmax = int(rows.min()), int(rows.max())
    if rmin > GAP_TOL:
        y0 = ty0 + rmin
    else:
        y0 = ty0
    if ty1 - (ty0 + rmax) > GAP_TOL:
        y1 = ty0 + rmax + 1
    else:
        y1 = ty1
    if y1 - y0 < 30:
        return None
    band = img_gray[y0:y1, :]
    colsum = (band < 210).sum(axis=0)
    cols = np.where(colsum > max(3, int((y1 - y0) * 0.004)))[0]
    if cols.size == 0:
        return None
    x0 = max(0, int(cols.min()) - x_margin)
    x1 = min(W, int(cols.max()) + x_margin + 1)
    if y1 - y0 < 30 or x1 - x0 < 30:
        return None
    return (y0, y1, x0, x1)


def _pass_a_regions(px, W, H, img_gray, body_h, paras, n=0):
    regions = []
    for cap in sorted(px, key=lambda t: t["y_top"]):
        is_fig = bool(FIG_RE.search(cap["text"]))
        is_tbl = bool(TBL_RE.search(cap["text"]))
        if not (is_fig or is_tbl):
            continue
        cap_top, cap_bot = cap["y_top"], cap["y_bottom"]
        if is_fig:
            reg = _region_top_above(cap_top, cap_bot, paras, img_gray, W)
        else:
            reg = _region_bottom_below(cap_top, cap_bot, paras, img_gray, W, H)
        trimmed = _trim_para_inside(reg, paras, cap=cap)
        if trimmed is None:
            continue
        top, bot = trimmed
        if bot - top < 50:
            continue
        tight = _tight_crop(img_gray, W, (top, bot))
        if tight is None:
            continue
        y0, y1, x0, x1 = tight
        if y1 - y0 < 50 or x1 - x0 < 50:
            continue
        kind = 'table' if is_tbl else 'fig'
        regions.append((y0, y1, x0, x1, kind, cap["text"][:40]))
    return regions


def _pass_b_regions(px, W, H, img_gray, body_h, paras, passA, heading_rows, n=0):
    regions = []
    covered = np.zeros(H, dtype=bool)
    for r in passA:
        covered[int(max(0, r[0] - 20)):int(min(H, r[1] + 20))] = True
    row_is_content = (img_gray < 210).sum(axis=1) > max(3, int(W * 0.004))
    for (py0, py1) in paras:
        row_is_content[int(max(0, py0 - 2)):int(min(H, py1 + 2))] = False
    row_is_content[covered] = False
    for l in px:
        if FIG_RE.search(l["text"]) or TBL_RE.search(l["text"]):
            row_is_content[int(l["y_top"]):int(l["y_bottom"]) + 1] = False
    for (hy0, hy1) in heading_rows:
        row_is_content[int(hy0):int(hy1) + 1] = False

    runs = []
    in_run = False
    run_start = last_content = 0
    for y in range(H):
        if row_is_content[y]:
            if not in_run:
                run_start = y
                in_run = True
            last_content = y
        elif in_run and y - last_content > RUN_GAP:
            runs.append((run_start, last_content + 1))
            in_run = False
    if in_run:
        runs.append((run_start, last_content + 1))

    for (r0, r1) in runs:
        if r1 - r0 < PHOTO_MIN:
            continue
        # 向上扩展，但遇到段落(正文)就停，避免把图表上方/下方的正文并进图区
        y0 = r0
        while y0 > 0:
            if any(_row_inside_para(y, paras) for y in range(max(0, y0 - 4), y0)):
                break
            band = img_gray[y0 - 4:y0, :]
            if (band < 210).sum() > max(3, int(W * 0.004)):
                y0 -= 4
            else:
                break
        # 向下扩展，遇到段落(正文)就停——K 线黑底图下方常紧跟"用户名/正文"，若扩进去会被 _row_inside_para 整段拒绝
        y1 = r1
        while y1 < H:
            if any(_row_inside_para(y, paras) for y in range(y1, min(y1 + 4, H))):
                break
            band = img_gray[y1:y1 + 4, :]
            if band.size and (band < 210).sum() > max(3, int(W * 0.004)):
                y1 += 4
            else:
                break
        if y1 - y0 < PHOTO_MIN:
            continue
        if any(_row_inside_para(y, paras) for y in range(y0, y1, 10)):
            continue
        tight = _tight_crop(img_gray, W, (y0, y1), x_margin=20)
        if tight is None:
            continue
        yy0, yy1, x0, x1 = tight
        if yy1 - yy0 < PHOTO_MIN or x1 - x0 < PHOTO_MIN_W:
            continue
        regions.append((yy0, yy1, x0, x1, 'photo', ''))
    return regions


def _merge_regions(a, b):
    regions = sorted(a + b, key=lambda r: r[0])
    merged = []
    for r in regions:
        if merged and r[0] < merged[-1][1] + 30 and r[2] < merged[-1][3] + 10:
            prev = merged[-1]
            kind = prev[4] if prev[4] != 'photo' else r[4]
            merged[-1] = (min(prev[0], r[0]), max(prev[1], r[1]),
                          min(prev[2], r[2]), max(prev[3], r[3]),
                          kind, prev[5] or r[5])
        else:
            merged.append(r)
    return merged


def _swap_in(tmp, dst):
    """把 tmp 内容移入 dst（覆盖同名），并删除 dst 中多出的旧文件。"""
    os.makedirs(dst, exist_ok=True)
    new_names = set(os.listdir(tmp))
    for f in new_names:
        shutil.move(os.path.join(tmp, f), os.path.join(dst, f))
    for f in os.listdir(dst):
        if f not in new_names:
            p = os.path.join(dst, f)
            if os.path.isdir(p):
                shutil.rmtree(p)
            else:
                os.remove(p)
    try:
        os.rmdir(tmp)
    except OSError:
        pass


def _process_page(n, json_dir, img_dir, blocks_tmp, fig_tmp, min_heading_ratio=1.28,
                  global_body_h=None, bold_thickness_ratio=0.078):
    jpath = os.path.join(json_dir, f"{n}.json")
    ipath = os.path.join(img_dir, f"p{n}.jpg")
    if not os.path.exists(ipath):
        ipath = os.path.join(img_dir, f"p{n}.png")
    if not (os.path.exists(jpath) and os.path.exists(ipath)):
        return None
    with open(jpath, encoding="utf-8") as f:
        data = json.load(f)
    lines = data.get("lines", [])
    img = Image.open(ipath)
    W, H = img.size
    img_gray = np.array(img.convert("L")).astype(np.float32)
    img_rgb = np.array(img.convert("RGB")).astype(np.float32)
    px = _lines_to_px(lines, W, H)
    body_h = _estimate_body_h(px)
    paras = _para_clusters(px, body_h, W)

    passA = _pass_a_regions(px, W, H, img_gray, body_h, paras, n=n)
    heading_by_line = _detect_headings(px, body_h, W, img_rgb, img_gray, W, H,
                                       exclude_ranges=[(r[0], r[1]) for r in passA],
                                       min_heading_ratio=min_heading_ratio,
                                       global_body_h=global_body_h,
                                       bold_thickness_ratio=bold_thickness_ratio)
    heading_rows = [(px[i]["y_top"], px[i]["y_bottom"]) for i in heading_by_line]
    passB = _pass_b_regions(px, W, H, img_gray, body_h, paras, passA,
                            heading_rows, n=n)
    regions = _merge_regions(passA, passB)

    # 若"photo"区域内容其实全是文字（OCR 已识别成 text），则它是正文段落而非图片——
    # 否则这段文字会被当图裁掉、正文丢失。用文字覆盖率判定：区域内被文字行(其 y 中心
    # 落在区内)覆盖的行占比 >0.6 → 判为文字区，剔除该 photo。
    filtered_regions = []
    for (fy0, fy1, fx0, fx1, kind, cap) in regions:
        if kind == 'photo':
            # 该区内 OCR 文字行的 y 覆盖区间
            text_covered = np.zeros(fy1 - fy0, dtype=bool)
            for t in px:
                if FIG_RE.search(t["text"]) or TBL_RE.search(t["text"]):
                    continue
                yc = (t["y_top"] + t["y_bottom"]) / 2
                if fy0 <= yc < fy1:
                    y0c = max(0, int(t["y_top"]) - fy0)
                    y1c = min(fy1 - fy0, int(t["y_bottom"]) - fy0)
                    if y1c > y0c:
                        text_covered[y0c:y1c] = True
            if text_covered.size and text_covered.mean() > 0.6:
                continue  # 区域大部分是文字 → 非图片，剔除
        filtered_regions.append((fy0, fy1, fx0, fx1, kind, cap))
    regions = filtered_regions

    img_boxes = []
    blocks = []
    for (fy0, fy1, fx0, fx1, kind, cap) in regions:
        # 图片（图/照片）裁剪下边界不能切到下面的图/表标题或正文：若区域内有
        # FIG_RE/TBL_RE 标题行，把 fy1 缩到该标题之上，避免"图+标题+正文"被一起裁进图片。
        if kind in ('photo', 'fig', 'table'):
            cap_cut = None
            for t in px:
                if not (FIG_RE.search(t["text"]) or TBL_RE.search(t["text"])):
                    continue
                tyt = t["y_top"]
                if fy0 - 4 <= tyt < fy1:
                    if cap_cut is None or tyt < cap_cut:
                        cap_cut = tyt
            if cap_cut is not None and cap_cut > fy0 + 20:
                fy1 = cap_cut - 4
        fig_idx = len([b for b in blocks if b["type"] == "image"]) + 1
        fname = f"p{n}_f{fig_idx}.png"
        crop = img.crop((fx0, fy0, fx1, fy1))
        crop.save(os.path.join(fig_tmp, fname))
        img_boxes.append((fy0, fy1, kind))
        blocks.append({"type": "image", "file": fname, "y_top": fy0,
                       "y_bottom": fy1, "x0": fx0, "x1": fx1,
                       "kind": kind, "caption": cap})

    # 段落分组：标记每行是否为段落首行（内容驱动的节标签 + 相对行高的大间距）
    para_start_map = _group_lines_into_paragraphs(px, body_h, img_gray=img_gray)

    for i, t in enumerate(px):
        is_caption = bool(FIG_RE.search(t["text"]) or TBL_RE.search(t["text"]))
        yc = (t["y_top"] + t["y_bottom"]) / 2
        para_start = bool(para_start_map.get(id(t), True))
        inside = None
        for (iy0, iy1, kind) in img_boxes:
            if iy0 <= yc <= iy1:
                inside = kind
                break
        # 图内文字（非图/表标题）整段跳过：这些字已作为图片内容渲染，OCR 再识别一遍
        # 会造成正文与图片内容重复。图/表标题（如"图1-1 xxx"）保留（通常在图外/图注）。
        if inside is not None and not is_caption:
            continue
        centered = bool(_line_centered(t, W))
        # 居中行多为图表标题/大字号落款，不是加粗；加粗判定对居中行豁免
        bold = (not centered) and bool(_line_bold(t, img_gray, W, H,
                                                  bold_thickness_ratio))
        if i in heading_by_line:
            lv = heading_by_line[i]
            blocks.append({"type": "heading", "level": lv, "text": t["text"],
                           "y_top": t["y_top"], "y_bottom": t["y_bottom"],
                           "x0": t["x0"], "x1": t["x1"],
                           "centered": centered, "bold": bold,
                           "para_start": para_start})
            continue
        blocks.append({"type": "text", "text": t["text"], "y_top": t["y_top"],
                       "y_bottom": t["y_bottom"],
                       "x0": t["x0"], "x1": t["x1"],
                       "centered": centered, "bold": bold,
                       "para_start": para_start})

    blocks.sort(key=lambda b: (b["y_top"], 1 if b["type"] == "image" else 0))
    with open(os.path.join(blocks_tmp, f"{n}.json"), "w", encoding="utf-8") as f:
        json.dump({"page": n, "regions": len(regions),
                   "body_h": round(body_h, 1), "n_paras": len(paras),
                   "n_headings": len(heading_rows), "blocks": blocks},
                  f, ensure_ascii=False)
    return len(regions)


def detect(project, img_dir, pages=None):
    """对项目执行布局检测。

    Args:
        project: Project 实例
        img_dir: 源图目录（通常是预处理后的 JPEG 目录，或原图目录）
        pages: 仅处理指定页（调试用，默认全部）
    Returns:
        (blocks_dir, figs_dir, total_regions)
    """
    json_dir = str(project.get_ocr_pages_json_dir())
    blocks_dir = str(project.get_blocks_dir())
    fig_dir = str(project.get_figs_dir())

    # 彩色标题（`##`）最小行高比例（跨页面统一基准）：
    # 真标题字号明显大于正文（"悖论系列N"1.31+/"第X章"1.29+），
    # 正文里独立成行的诗歌/脚注行字号接近正文（1.07~1.27×）。设 1.28 过滤后者。
    # 可配置 detect.min_heading_ratio。
    from .config import Config
    min_heading_ratio = float(Config(str(project.path)).get('detect.min_heading_ratio', 1.28))
    # 加粗判定阈值（相对笔画粗细）。可配置 detect.bold_thickness_ratio。
    bold_thickness_ratio = float(
        Config(str(project.path)).get('detect.bold_thickness_ratio', 0.078))

    if not os.path.isdir(json_dir):
        raise SystemExit(f"错误：未找到 OCR 坐标目录 {json_dir}\n"
                         f"  请先运行 OCR：python main.py ocr -p <project>")
    if not os.path.isdir(img_dir):
        raise SystemExit(f"错误：未找到截图目录 {img_dir}\n"
                         f"  请用 --images 指定正确的截图目录")

    # 计算全书统一正文基准行高（跨页面有效）：所有页 OCR 行高的中位数。
    # 标题字号与正文字号的比较用全局基准，避免单页 body_h 估算波动。
    _hs = []
    if pages is None:
        _all_pages = sorted(int(os.path.splitext(f)[0])
                            for f in os.listdir(json_dir)
                            if f.endswith(".json"))
    else:
        _all_pages = pages
    for _n in _all_pages:
        _p = os.path.join(json_dir, f"{_n}.json")
        if not os.path.exists(_p):
            continue
        try:
            with open(_p, encoding="utf-8") as _f:
                _d = json.load(_f)
            _hs.extend(ln["h"] for ln in _d.get("lines", []))
        except Exception:
            continue
    # 全局正文基准（归一化行高中位数，跨页统一）。_detect_headings 里会 ×H 换算成像素。
    global_body_h = float(np.median(_hs)) if _hs else None

    # 清理历史遗留的临时目录
    out_str = str(project.output_dir)
    for name in os.listdir(out_str):
        p = os.path.join(out_str, name)
        if os.path.isdir(p) and name.startswith((".blocks.tmp.", ".figs.tmp.",
                                                 ".md.tmp.", ".article.tmp.")):
            shutil.rmtree(p, ignore_errors=True)
            print(f"  清理历史临时目录: {p}")

    blocks_tmp = os.path.join(project.output_dir, f".blocks.tmp.{os.getpid()}")
    fig_tmp = os.path.join(project.output_dir, f".figs.tmp.{os.getpid()}")
    os.makedirs(blocks_tmp, exist_ok=True)
    os.makedirs(fig_tmp, exist_ok=True)

    if pages is None:
        pages = _all_pages
    total = 0
    for n in pages:
        r = _process_page(n, json_dir, img_dir, blocks_tmp, fig_tmp,
                          min_heading_ratio=min_heading_ratio,
                          global_body_h=global_body_h,
                          bold_thickness_ratio=bold_thickness_ratio)
        if r:
            total += r
    _swap_in(blocks_tmp, blocks_dir)
    _swap_in(fig_tmp, fig_dir)
    print(f"processed {len(pages)} pages, {total} regions")
    return blocks_dir, fig_dir, total
