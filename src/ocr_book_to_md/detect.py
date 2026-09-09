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


def _rgb_to_hue(rgb):
    """RGB → 色相(0~360)。近黑/深底/低饱和(灰/噪声)返回 None。

    判据要排除两类误判：
      1) 近黑文字带轻微暖/冷色偏（扫描/JPEG 噪声），如 [64,52,47] —— 整体太暗(mx<0.35)；
      2) 反白/深底采样出"暗背景色"（如 K 线黑底的 [32,34,44]）。
    真彩色强调（红/蓝/绿标题、表头）亮度高(mx≥0.35)且饱和(diff/mx≥0.25)。"""
    if rgb is None:
        return None
    r, g, b = rgb[0] / 255.0, rgb[1] / 255.0, rgb[2] / 255.0
    mx, mn = max(r, g, b), min(r, g, b)
    diff = mx - mn
    if mx < 0.35 or diff / (mx or 1) < 0.25:  # 太暗(近黑/深底) 或 低饱和(灰/噪声)
        return None
    if mx == r:
        h = 60 * (((g - b) / diff) % 6)
    elif mx == g:
        h = 60 * ((b - r) / diff + 2)
    else:
        h = 60 * ((r - g) / diff + 4)
    return round(h % 360, 1)


def _text_color_hue(img_rgb, box, W, H):
    """取样一行文字的主色相（排除黑/白/灰）。返回 (hue, rgb) 或 (None, None)。
    用于通用颜色识别：文字用彩色时（如红/蓝队股票）色相差异大。"""
    rgb = _line_text_color(img_rgb, box, W, H)
    if rgb is None:
        return None, None
    h = _rgb_to_hue(rgb)
    return h, rgb


def _char_width_units(ch):
    """估算单个字符在水平方向占的宽度（相对全角=1）。中文/全角=1，
    半角字母数字≈0.55，空格≈0.3。用于行内把文本近似投影到像素 x。"""
    o = ord(ch)
    if ch == " ":
        return 0.3
    if o == 0xFF0C or o == 0xFF1A:  # 全角逗号/冒号
        return 0.8
    if 0x20 <= o <= 0x7E:           # ASCII
        return 0.55
    return 1.0


def _line_inline_colors(img_rgb, box, text, W, H, sat_thr=0.35, min_count=5):
    """行内逐字颜色切分：找出该行文本中真正彩色的连续字符段。

    背景：OCR 行级取样取"中位色"，行内若有黑字+几个彩色强调字（如"…带头大哥
    带头跑路，"，其中强调句为绿色），中位被多数黑字拉黑，整行不上色；或被彩色
    拉走把整行染上。这里按字符像素聚合——字符区域暗像素的 sat 中位 ≥ sat_thr
    视为彩色字（抗锯齿边缘像素 sat 较低会被中位忽略），连续彩色字合成一段。
    返回 [(char_start, char_end, rgb), ...]（闭区间），无彩色段返回 []。
    """
    if not text:
        return []
    if "x" in box:
        x0 = int(box["x"] * W)
        x1 = int((box["x"] + box["w"]) * W)
        y0 = int((1 - box["y"] - box["h"]) * H)
        y1 = int((1 - box["y"]) * H)
    else:
        x0, x1 = int(box["x0"]), int(box["x1"])
        y0, y1 = int(box["y_top"]), int(box["y_bottom"])
    if x1 - x0 < 10 or y1 - y0 < 8:
        return []
    reg = img_rgb[y0:y1, x0:x1]
    if reg.size == 0:
        return []
    rch = reg[:, :, 0]; gch = reg[:, :, 1]; bch = reg[:, :, 2]
    lum = (rch + gch + bch) / 3.0
    dark = lum < 185
    mx = np.maximum(np.maximum(rch, gch), bch)
    mn = np.minimum(np.minimum(rch, gch), bch)
    sat = np.where(mx > 25, (mx - mn) / np.maximum(mx, 1), 0.0)
    if not dark.any():
        return []
    # 字符宽度模型 → 每字符像素列范围
    widths = [_char_width_units(c) for c in text]
    total = sum(widths) or 1.0
    x_range = x1 - x0
    char_starts = []
    acc = 0.0
    for wd in widths:
        char_starts.append(x0 + acc / total * x_range)
        acc += wd
    char_starts.append(x1)
    # 字符判定：区域内"暗像素 sat 中位"≥sat_thr 且 暗像素数足够
    char_colored = []
    for i in range(len(text)):
        a = max(x0, int(char_starts[i]))
        b = min(x1, int(char_starts[i + 1]))
        if b <= a:
            char_colored.append(False)
            continue
        la = a - x0; lb = b - x0
        seg_dark = dark[:, la:lb]
        if not seg_dark.any():
            char_colored.append(False)
            continue
        seg_sat = sat[:, la:lb]
        sats = seg_sat[seg_dark]  # 字符内暗像素 sat 集合
        if sats.size < min_count:
            char_colored.append(False)
            continue
        char_colored.append(float(np.median(sats)) >= sat_thr)
    # 合并连续彩色字
    runs = []
    i = 0
    n = len(char_colored)
    while i < n:
        if not char_colored[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and char_colored[j + 1]:
            j += 1
        a = max(0, int(char_starts[i]) - x0)
        b = min(x1 - x0, int(char_starts[j + 1]) - x0)
        if b > a:
            seg_dark = dark[:, a:b]
            seg_reg = reg[:, a:b]
            pts = seg_reg[seg_dark]
            if len(pts):
                rgb = (int(np.median(pts[:, 0])), int(np.median(pts[:, 1])),
                       int(np.median(pts[:, 2])))
                runs.append((i, j, rgb))
        i = j + 1
    return runs


# ---- 语义标准色板（扩大的 CSS 命名色，覆盖各色相/明暗） ----
# 最终 md 中彩色文字吸附到这些标准色，语义明确且跨页一致。
SEMANTIC_COLORS = {
    # 红
    "darkred": (0x8B, 0x00, 0x00), "firebrick": (0xB2, 0x22, 0x22),
    "red": (0xFF, 0x00, 0x00), "indianred": (0xCD, 0x5C, 0x5C),
    "lightcoral": (0xF0, 0x80, 0x80), "salmon": (0xFA, 0x80, 0x72),
    "darksalmon": (0xE9, 0x96, 0x7A),
    # 粉
    "hotpink": (0xFF, 0x69, 0xB4), "deeppink": (0xFF, 0x14, 0x93),
    "pink": (0xFF, 0xC0, 0xCB), "lightpink": (0xFF, 0xB6, 0xC1),
    "palevioletred": (0xDB, 0x70, 0x93),
    # 橙
    "coral": (0xFF, 0x7F, 0x50), "tomato": (0xFF, 0x63, 0x47),
    "orangered": (0xFF, 0x45, 0x00), "darkorange": (0xFF, 0x8C, 0x00),
    "orange": (0xFF, 0xA5, 0x00),
    # 黄 / 金黄
    "gold": (0xFF, 0xD7, 0x00), "yellow": (0xFF, 0xFF, 0x00),
    "lightyellow": (0xFF, 0xFF, 0xE0), "goldenrod": (0xDA, 0xA5, 0x20),
    "darkgoldenrod": (0xB8, 0x86, 0x0B), "khaki": (0xF0, 0xE6, 0x8C),
    "palegoldenrod": (0xEE, 0xE8, 0xAA),
    # 绿
    "limegreen": (0x32, 0xCD, 0x32), "green": (0x00, 0x80, 0x00),
    "darkgreen": (0x00, 0x64, 0x00), "seagreen": (0x2E, 0x8B, 0x57),
    "mediumseagreen": (0x3C, 0xB3, 0x71), "forestgreen": (0x22, 0x8B, 0x22),
    "lightgreen": (0x90, 0xEE, 0x90), "palegreen": (0x98, 0xFB, 0x98),
    "olive": (0x80, 0x80, 0x00), "olivedrab": (0x6B, 0x8E, 0x23),
    # 青 / 蓝绿
    "teal": (0x00, 0x80, 0x80), "darkcyan": (0x00, 0x8B, 0x8B),
    "cyan": (0x00, 0xFF, 0xFF), "mediumturquoise": (0x48, 0xD1, 0xCC),
    "lightcyan": (0xE0, 0xFF, 0xFF), "paleturquoise": (0xAF, 0xEE, 0xEE),
    # 蓝
    "dodgerblue": (0x1E, 0x90, 0xFF), "royalblue": (0x41, 0x69, 0xE1),
    "steelblue": (0x46, 0x82, 0xB4), "cornflowerblue": (0x64, 0x95, 0xED),
    "lightblue": (0xAD, 0xD8, 0xE6), "skyblue": (0x87, 0xCE, 0xEB),
    "lightskyblue": (0x87, 0xCE, 0xFA), "cadetblue": (0x5F, 0x9E, 0xA0),
    "blue": (0x00, 0x00, 0xFF), "mediumblue": (0x00, 0x00, 0xCD),
    "darkblue": (0x00, 0x00, 0x8B), "navy": (0x00, 0x00, 0x80),
    # 紫
    "mediumpurple": (0x93, 0x70, 0xDB), "rebeccapurple": (0x66, 0x33, 0x99),
    "purple": (0x80, 0x00, 0x80), "indigo": (0x4B, 0x00, 0x82),
    "blueviolet": (0x8A, 0x2B, 0xE2), "violet": (0xEE, 0x82, 0xEE),
    "orchid": (0xDA, 0x70, 0xD6), "plum": (0xDD, 0xA0, 0xDD),
    "slateblue": (0x6A, 0x5A, 0xCD), "mediumslateblue": (0x7B, 0x68, 0xEE),
    # 棕
    "brown": (0xA5, 0x2A, 0x2A), "sienna": (0xA0, 0x52, 0x2D),
    "chocolate": (0xD2, 0x69, 0x1E), "sandybrown": (0xF4, 0xA4, 0x60),
    "peru": (0xCD, 0x85, 0x3F), "tan": (0xD2, 0xB4, 0x8C),
}

_SEM_LAB_CACHE = None


def _lab(rgb):
    """sRGB→Lab(D65)。用于语义色吸附的感知距离。"""
    r, g, b = (c / 255.0 for c in rgb)
    def _f(t):
        return t ** (1 / 3) if t > 0.008856 else (7.787 * t + 16 / 116)
    r = ((r + 0.055) / 1.055) ** 2.4 if r > 0.04045 else r / 12.92
    g = ((g + 0.055) / 1.055) ** 2.4 if g > 0.04045 else g / 12.92
    b = ((b + 0.055) / 1.055) ** 2.4 if b > 0.04045 else b / 12.92
    X = 0.4124 * r + 0.3576 * g + 0.1805 * b
    Y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    Z = 0.0193 * r + 0.1192 * g + 0.9505 * b
    fx, fy, fz = _f(X / 0.95047), _f(Y / 1.0), _f(Z / 1.08883)
    return (116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz))


def _sem_lab_table():
    global _SEM_LAB_CACHE
    if _SEM_LAB_CACHE is None:
        _SEM_LAB_CACHE = {name: _lab(rgb)
                          for name, rgb in SEMANTIC_COLORS.items()}
    return _SEM_LAB_CACHE


def _snap_to_semantic(rgb):
    """把采样色吸附到最近的语义标准色。返回标准色名。"""
    if rgb is None:
        return None
    l = _lab(rgb)
    best, bd = None, 1e18
    for name, pl in _sem_lab_table().items():
        d = sum((a - b) ** 2 for a, b in zip(l, pl))
        if d < bd:
            bd, best = d, name
    return best


def _cluster_rgbs(rgbs, thresh=9.0):
    """按感知距离把相近颜色分成若干簇（B 步）。返回 [[rgb,...],...]。"""
    if not rgbs:
        return []
    labs = [_lab(r) for r in rgbs]
    clusters = []  # 每簇: {'sum': lab和, 'n': 数, 'members': [rgb]}
    for rgb, lab in zip(rgbs, labs):
        placed = False
        for cl in clusters:
            c = tuple(s / cl["n"] for s in cl["sum"])
            d2 = sum((a - b) ** 2 for a, b in zip(lab, c))
            if d2 < thresh * thresh:
                cl["sum"] = tuple(s + v for s, v in zip(cl["sum"], lab))
                cl["n"] += 1
                cl["members"].append(rgb)
                placed = True
                break
        if not placed:
            clusters.append({"sum": lab, "n": 1, "members": [rgb]})
    return [cl["members"] for cl in clusters]


def _snap_blocks_colors(blocks, snap=True, cluster=True, thresh=9.0):
    """对 blocks 内所有彩色（text/heading/table cell）做每页"聚类→语义色吸附"。
    聚类(B)：感知距离相近色归并；吸附(A)：簇代表色吸到最近的语义标准色，
    并把簇内所有成员的 color_rgb 写为该标准色值、新增 color_name。
    黑/灰/无彩不受影响。返回新 blocks。"""
    def _iter_colored(blks):
        for b in blks:
            if b.get("type") in ("text", "heading"):
                if b.get("color_hue") is not None and b.get("color_rgb"):
                    yield b
                # 行内混色段：每个 span 独立参与聚类吸附
                for sp in b.get("color_spans") or []:
                    if isinstance(sp, dict) and sp.get("color_rgb"):
                        yield sp
            elif b.get("type") == "table":
                for r in b.get("rows") or []:
                    for c in r:
                        if isinstance(c, dict) and c.get("color_rgb"):
                            yield c
    colored = list(_iter_colored(blocks))
    if not colored:
        return blocks
    rgbs = [tuple(c["color_rgb"]) for c in colored]
    clusters = _cluster_rgbs(rgbs, thresh) if cluster else [[r] for r in dict.fromkeys(rgbs)]
    for members in clusters:
        if not members:
            continue
        # 簇代表色：Lab 质心 → 取最近实际成员
        labs = [_lab(r) for r in members]
        cc = tuple(sum(l[i] for l in labs) / len(labs) for i in range(3))
        rep = min(members, key=lambda r: sum((a - b) ** 2 for a, b in zip(_lab(r), cc)))
        if snap:
            name = _snap_to_semantic(rep)
            std_rgb = list(SEMANTIC_COLORS[name])
        else:
            name, std_rgb = None, list(rep)
        # 簇成员 rgb → 标准色映射
        rset = set(members)
        for c in colored:
            if tuple(c["color_rgb"]) in rset:
                c["color_rgb"] = std_rgb
                if name:
                    c["color_name"] = name
    return blocks


# 图底/图缘"刻度/指标 tab"文字特征：紧贴图 bbox 外缘、横向在图范围内、短行且
# 不以句号/冒号结尾（K 线底部指标栏、价格轴标签、行情字段），不是正文段落。
_EDGE_TXT_END = ("。", "！", "？", "：")
_EDGE_TXT_MAX = 24


def _expand_regions_to_edge_text(regions, px, body_h, tol=0.8,
                                 exclude_bands=()):
    """把紧贴图区上/下缘、横向对齐的"图内刻度文字"行并入图区（扩展 bbox）。

    现象：K线/行情 APP 截图底部指标 tab（BI 成交量/MACD/KDJ…）与顶部行情字段
    常落在图 bbox 外几像素，OCR 识别成 text 泄漏成正文行并被上色。
    这里在**裁图前**扩展 region bbox，之后 `inside` 逻辑自然把这些行当图内容跳过。
    判据（全部满足才并入）：
      - 行 y 距 region 上/下缘 ≤ tol×body_h（紧贴）；
      - 行 x 与 region x 显著重叠（非横向跨出图外的正文长行）；
      - 短行、不以句末/冒号结尾（正文句以句号结尾被拒绝）；
      - 行不落在正文/表格段（exclude_bands）内——表格紧贴 K 线图时若把表格行
        当"图内刻度"吸收，会把整张表吞进图、下方真图丢失。
    """
    if not regions or not px:
        return regions
    out = []
    for (fy0, fy1, fx0, fx1, kind, cap) in regions:
        # 循环扩展：吸收后可能露出更外围的刻度行
        changed = True
        while changed:
            changed = False
            for t in px:
                txt = (t.get("text") or "").strip()
                if not txt or txt.endswith(_EDGE_TXT_END):
                    continue
                if len(txt) > _EDGE_TXT_MAX:
                    continue
                if FIG_RE.search(txt) or TBL_RE.search(txt):
                    continue
                tyc = (t["y_top"] + t["y_bottom"]) / 2
                if any(a <= tyc <= b for (a, b) in exclude_bands):
                    continue
                tx0, tx1 = t["x0"], t["x1"]
                if tx1 <= fx0 or tx0 >= fx1:
                    continue
                overlap = min(tx1, fx1) - max(tx0, fx0)
                if overlap < 0.3 * (tx1 - tx0):
                    continue
                if (tx1 - tx0) > 0.85 * (fx1 - fx0):
                    continue
                ty0, ty1 = t["y_top"], t["y_bottom"]
                near_top = ty1 <= fy0 and (fy0 - ty1) <= tol * body_h
                near_bot = ty0 >= fy1 and (ty0 - fy1) <= tol * body_h
                if near_top:
                    fy0 = min(fy0, ty0)
                    changed = True
                    break
                if near_bot:
                    fy1 = max(fy1, ty1)
                    changed = True
                    break
        out.append((fy0, fy1, fx0, fx1, kind, cap))
    return out


def _line_centered(t, W):
    """判断一行文字是否水平居中：文字 bbox 中心接近页面水平中心，
    且行**明显窄于版心**（居中行较短；占满版心的正文宽行即使中心恰在页中
    也不是"居中"——如 PDF/公众号正文每行 x0≈左边距、w≈0.65~0.7W 铺满版心，
    其 xc 也≈0.5 会被误判，实为段首缩进/普通正文段落）。
    """
    xc = (t["x0"] + t["x1"]) / 2
    w = t["x1"] - t["x0"]
    page_c = W / 2
    tol = max(0.03 * W, 20)  # 中心偏差容差
    return abs(xc - page_c) <= tol and w <= 0.62 * W


def _line_bold(t, img_gray, W, H, threshold_ratio=0.078, base_h=None):
    """用距离变换估算笔画粗细来判定是否加粗。

    密度法把"字大/颜色深/字密"都混进加粗，分不清"注/免责"(真粗) vs 正文(密但不粗)。
    改为：对单行文字做二值化→距离变换，取笔画中心处距离的高分位数作为笔画半径，
    stroke_width=2×radius，再除以"字号尺度"消除字号影响。粗体笔画更厚 → 相对更粗。

    只用 scipy 的 distance_transform_edt（通用科学计算库），不引入 OpenCV(cv2)——
    避免与 macOS Vision 形成两套图像处理栈的重复。

    threshold_ratio: 相对笔画粗细（stroke_width/字号）阈值。实测常规正文 ~0.075，
        粗体 ~0.082+，默认 0.078 可区分；可按项目用 detect.bold_thickness_ratio 调。

    base_h: 归一化用的"字号尺度"。默认取本行高 h；当 OCR 把同一视觉行拆成多段时
        （如行尾两字被拆成独立行），各段 OCR bbox 高度不一，stroke/段高 会抖动，
        加粗判定对同一行不一致。此时传页级基准（该页正文中位行高 body_h），
        加粗 = 笔画绝对粗细 / 页面字号，与 OCR 段高无关——"注：…4号板块。"被拆成
        "…4号板"+"块。" 两段仍能一致判粗。
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
    norm = (base_h if base_h else h)
    if norm <= 0:
        return False
    relative = stroke_width / norm
    return relative > threshold_ratio


def _detect_headings(px, body_h, page_w, img_rgb, img_gray, W, H,
                     exclude_ranges=(), min_heading_ratio=1.28, global_body_h=None,
                     bold_thickness_ratio=0.078, plain_heading_ratio=1.18,
                     page_heading_base=False, bold_base_h=None):
    """检测标题。

    h_ratio 默认用全局正文基准 global_body_h（跨页面统一）计算；page_heading_base=True
    时改用本页 body_h——多张不同字号长图混排时，全书归一化行高中位数会被大字号图
    主导，导致小字号图标题相对比例被稀释，此时应让标题与该页自己的正文比。

    min_heading_ratio: 彩色标题（`##`）的最小行高比例。真实标题字号明显大于正文
    （如"悖论系列N"1.31+/"第X章"1.29+），正文里独立成行的诗歌/脚注行字号接近
    正文（1.07~1.27×）。设 1.28 可过滤后者。可经 config `detect.min_heading_ratio` 调整。

    plain_heading_ratio: 非彩色标题的最小行高比例（默认 1.18）。正文独立成行的
    小标题/图表题字号略大但不悬殊；真正章节标题字号更突出。可经 config
    `detect.heading_plain_ratio` 调整（长图文章标题常只比正文大 ~1.1×）。

    粗体标题（如长图顶部"（8.24~8.30）"/"8月周总结第3期"/"11:53 ③"）：
    行高可能并不明显大于正文（1.03×），但笔画明显更粗。作为补充，粗体且短、
    不以句号结尾、宽度适中、周围留白的行也判为标题——这能排除正文里加粗的
    注释/免责声明（"注：...。"、"本文仅为...入市需谨慎。"都以句号结尾）。
    """
    # global_body_h 传归一化正文行高中位数（跨页统一），换算成当前页像素：*H
    if page_heading_base:
        base_h = body_h
    else:
        base_h = (global_body_h * H) if global_body_h else body_h
    excl = [(e0 - 4, e1 + 4) for (e0, e1) in exclude_ranges]
    candidates = []
    bold_titles = set()  # 粗体标题候选（行高不足但笔画粗的短行）
    # 标题是短标题，正文段落（一整句）即使行高略大也不应被当标题。
    # 用最大字符数过滤：超过 max_heading_chars 视为正文（误判的长句不再当标题）。
    max_heading_chars = 40
    # 页面最顶部文本行的 y_top：长图文章标题往往是该页/该片第一行，字号只比正文
    # 大 ~1.1×（低于 1.18 通用阈值）。page_heading_base 时对"页面顶部首个文本"用
    # 更低的 plain_heading_ratio，避免把正文里字号略大的行（如列表项）误判成标题。
    _text_rows = [(l["y_top"], l["y_bottom"]) for l in px
                  if l["text"].strip()
                  and not FIG_RE.search(l["text"]) and not TBL_RE.search(l["text"])]
    top_text_y = min((t for t, _ in _text_rows), default=0)
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
        # 低行高阈值仅放宽给"页面顶部首个文本行"（长图文章标题常只比正文大 ~1.1×，
        # 低于通用 1.18）；仅 page_heading_base=True（么瑞等多长图场景）时启用，
        # 其余文本一律 >=1.18，防正文行/列表项误判为标题。
        _is_top = page_heading_base and abs(line["y_top"] - top_text_y) < 0.5 * body_h
        _eff_ratio = plain_heading_ratio if _is_top else 1.18
        # 非彩色标题行高判定：elif 链保持"上一分支条件整体不满足才落到下一分支"，
        # 低行高的粗体标题仍能落到最后的粗体分支。
        if col and sat > 0.25 and h_ratio >= min_heading_ratio \
                and 0.10 * page_w < w <= 0.95 * page_w:
            candidates.append((i, 2))
        elif (not col or sat < 0.35) and h_ratio >= _eff_ratio \
                and 0.14 * page_w <= w <= (0.96 if page_heading_base else 0.92) * page_w:
            candidates.append((i, 3))
        # 粗体标题（长图顶部文章标题等）：笔画粗但行高不足。粗体+短+不以句号结尾
        # +宽度适中 → 标题；正文里的加粗注释/免责声明以句号结尾被排除。
        elif _line_bold(line, img_gray, W, H, bold_thickness_ratio,
                        base_h=bold_base_h) \
                and len(txt) <= 15 \
                and not txt.endswith(("。", "！", "？")) \
                and 0.08 * page_w < w <= 0.95 * page_w:
            candidates.append((i, 3))
            bold_titles.add(i)
    if not candidates:
        return {}

    # 去重：同一行可能同时命中"行高(plain)"与"粗体"两条候选（各 append 一次），
    # 只保留最高等级（level 数字小的优先，如 2 < 3），避免组内重复导致误拒。
    _by_i: dict[int, int] = {}
    for _i, _lv in candidates:
        if _i not in _by_i or _lv < _by_i[_i]:
            _by_i[_i] = _lv
    candidates = list(_by_i.items())

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


def _para_clusters(px, body_h, page_w, img_gray=None):
    """几何聚类正文段（用于图区检测时把正文区域排除在"图内容"外）。

    关键：**深色背景上的 OCR 行是图内文字（K线图白字/水印），不是正文**。
    若把它们也判成正文段，_pass_b_regions 会把这些行从图内容里挖掉，导致图区被
    截短、图内文字泄漏成正文（"图切出去了 + 又识别成文字"不应并存）。
    故候选行若其包围盒内暗像素占比高（背景深色，如 >0.6），视为图内文字，跳过。
    """
    def _dark_ratio(l):
        if img_gray is None:
            return 0.0
        x0 = max(0, int(l["x0"])); x1 = min(page_w, int(l["x1"]))
        y0 = max(0, int(l["y_top"])); y1 = int(l["y_bottom"])
        if x1 - x0 < 8 or y1 - y0 < 6:
            return 0.0
        reg = img_gray[y0:y1, x0:x1]
        if reg.size == 0:
            return 0.0
        return float((reg < 200).mean())

    tall = sorted([l for l in px
                   if _is_tall(l, body_h) and l["w"] >= 0.12 * page_w
                   and _dark_ratio(l) < 0.6],   # 跳过深色底(图内文字)
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
        # 宽行(>0.5 页宽)计数：正文段落至少 2 行整宽(排版连续正文行)；
        # 图内指标标签簇(如"DIFF:…DEA:…" + "(12,26,9)" + "MACD")往往只有 1 行宽 +
        # 其余窄标签 → 不判正文，避免把图内文字当正文段挖掉、截断浅底分时副图。
        n_wide = sum(1 for l in c if l["w"] > 0.5 * page_w)
        # 簇只有 1 行宽条(如分时图顶部的 "2026-06-08 均价 130.82 数值 137.89"
        # 日期/数值信息条, w≈0.6W)：若其下方紧贴连续图形内容(浅底折线/副图),
        # 说明是**图的顶部字段条**而不是正文——判 para 会把行下方紧贴的整张
        # 分时图 run 起点(落在该行 +2px 容差)误拒, 图被漏掉、字段文字泄漏。
        _touching_graph = False
        if len(c) == 1 and img_gray is not None:
            # 孤立单行宽条(如分时图顶部 "2026-06-08 均价 130.82 数值 137.89"):
            # 向下扫 3×body_h, 若存在 ≥2 行暗像素>W×0.05(折线/图形穿过),
            # 说明它紧贴/位于图顶 → 不判正文(应作为图内容, 字段文字并入图)。
            _l0 = c[0]
            _y_bot = int(_l0["y_bottom"])
            _y_end = min(int(_l0["y_bottom"] + 3 * body_h), img_gray.shape[0])
            if _y_end > _y_bot + 4:
                _seg = img_gray[_y_bot:_y_end, :]
                _thr = max(4, int(page_w * 0.05))
                _n_lines = int(((_seg < 200).sum(axis=1) > _thr).sum())
                if _n_lines >= 2:
                    _touching_graph = True
        if has_full or avg_w > 0.55 * page_w or n_wide >= 2:
            # 段落下界取 cluster 内第一个"宽行"顶部：正文段落首行总是较宽的正文行，
            # 避免把紧贴图底/图缘的窄标签行（指标 tab、价格轴，w≈0.2~0.3 页宽）当作
            # 段落起点——那些行应作为图内容（passB 会并入图上/下缘），不挖出正文段。
            # 例外：单行宽条且下方紧贴图形(图顶部字段条, 如分时图"2026-06-08 均价…")
            # → 不判正文(否则其下紧贴的整张图 run 会被误拒/字段泄漏)。
            if not (len(c) == 1 and _touching_graph):
                _wide_rows = [l for l in c if l["w"] > 0.45 * page_w]
                if _wide_rows:
                    y0 = min(l["y_top"] for l in _wide_rows)
                else:
                    y0 = min(l["y_top"] for l in c)
                paras.append((y0, max(l["y_bottom"] for l in c)))
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


def _row_thr_soft(W):
    """浅灰/浅色文字行的 content 阈值（比 _row_thr 宽松：图内浅色字段/标签
    灰度 ~200-245，深色阈值 210 抓不到，导致浅底 App 截图被拆碎、字段漏成文字）。"""
    return max(6, int(W * 0.010))


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


def _table_row_bands(px, W, body_h=None, min_rows=3):
    """找出"多行多列、x 对齐成网格"的表格行的 y 区间（图检测前用）。

    与 _group_text_into_tables 同判据（行聚合→列簇→连续 ≥min_rows 行覆盖 ≥2 列），
    但只返回 y 段列表——供 _pass_b_regions 把表格行像素从图内容中排除：表格与其
    下方紧贴的 K 线图无空白粘连时，若表格行参与 run，会把 run 顶部拖到正文段附近
    而被整段拒绝，K 线图丢失（如"建仓表 + K 线图"）。
    """
    import statistics
    tbs = [l for l in px if (l.get("text") or "").strip()
           and not FIG_RE.search(l["text"]) and not TBL_RE.search(l["text"])]
    if len(tbs) < 4:
        return []
    def _cy(b): return (b["y_top"] + b["y_bottom"]) / 2
    rows = []
    for b in tbs:
        placed = False
        for r in rows:
            if abs(_cy(b) - _cy(r[-1])) < (r[-1]["y_bottom"] - r[-1]["y_top"]) * 0.7:
                r.append(b); placed = True; break
        if not placed:
            rows.append([b])
    xs = sorted(b["x0"] for r in rows for b in r)
    col_repr = []
    for x in xs:
        if col_repr and x - col_repr[-1] <= 30:
            continue
        col_repr.append(x)
    if len(col_repr) < 2:
        return []
    col_repr = [statistics.median([b["x0"] for r in rows for b in r
                                   if abs(b["x0"] - c) <= 15])
                for c in col_repr]
    def _row_cols(r):
        cols = set()
        for b in r:
            ci = min(range(len(col_repr)), key=lambda i: abs(b["x0"] - col_repr[i]))
            if abs(b["x0"] - col_repr[ci]) <= 20:
                cols.add(ci)
        return cols
    def _flush():
        nonlocal cur
        if len(cur) < min_rows:
            cur = []
            return
        band_rows = [rows[k] for k in cur]
        cur = []
        # 真表格每行文字高度一致且≈正文行高；若带内出现远超正文的大字行
        # (h>1.5×body_h, 如盘口截图的红色大字价格"102.14低" h≈1.8×正文) → 这是
        # 图上大字信息条而非表格, 不作表带(否则会挖空、把整张浅底 App 盘口图截断)。
        if body_h:
            _hmax = max((b["y_bottom"] - b["y_top"]) for r_ in band_rows for b in r_)
            if _hmax > 1.5 * body_h:
                return
        bands.append((min(b[0]["y_top"] for b in band_rows),
                      max(b[-1]["y_bottom"] for b in band_rows)))

    bands = []
    cur = []
    # 表格的"视觉行"须垂直连续：相邻行中心距超过行高 ×2.5 就断开（图上散布的
    # 指标标签/日期 tab/价格刻度各行距 200~500px, 不是同一表格; 真表格行距≈1.2×行高）
    for i, r in enumerate(rows):
        if len(_row_cols(r)) >= 2:
            if cur:
                _prev = rows[cur[-1]]
                _cy_prev = (_prev[0]["y_top"] + _prev[-1]["y_bottom"]) / 2
                _cy_cur = (r[0]["y_top"] + r[-1]["y_bottom"]) / 2
                _h = max((_prev[-1]["y_bottom"] - _prev[0]["y_top"]),
                         (r[-1]["y_bottom"] - r[0]["y_top"]), 1)
                if _cy_cur - _cy_prev > 2.5 * _h:
                    # 垂直不连续 → 收掉当前表带
                    _flush()
            cur.append(i)
        else:
            _flush()
            cur = []
    _flush()
    return bands


def _pass_b_regions(px, W, H, img_gray, body_h, paras, passA, heading_rows,
                    table_bands=(), n=0):
    regions = []
    # 表格段视为"扩展障碍块"：从图内容排除、并阻止 up/down 扩展把紧贴的表格文字
    # 吞进图区。但"整段拒绝"（_row_inside_para 判定）只用真正文 paras——表格行
    # 已从 content 排除、不会自身成为 run；若把它也当拒绝块，紧贴表格下方的
    # K 线图 run 起点会因 ±4px 容差被判"在段内"而整段丢失。
    block = list(paras) + list(table_bands)
    covered = np.zeros(H, dtype=bool)
    for r in passA:
        covered[int(max(0, r[0] - 20)):int(min(H, r[1] + 20))] = True
    row_is_content = (
        (img_gray < 210).sum(axis=1) > max(3, int(W * 0.004))
    ) | (
        # 浅底 App 截图/网页截图里大量浅灰文字/色值字段(灰度 200-245)——用
        # _row_thr_soft 宽松阈值也计 content, 防止图内空白行(<210 不够)把图切碎、
        # 字段文字漏出到正文；正文白底段会被 paras/宽行过滤剔除(后续 _row_inside_para)。
        (img_gray < 240).sum(axis=1) > _row_thr_soft(W)
    )
    for (py0, py1) in block:
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
        # 向上扩展，遇到障碍块(正文/表格)就停，避免把图表上方/下方的文字并进图区
        y0 = r0
        while y0 > 0:
            if any(_row_inside_para(y, block) for y in range(max(0, y0 - 4), y0)):
                break
            band = img_gray[y0 - 4:y0, :]
            if (band < 210).sum() > max(3, int(W * 0.004)):
                y0 -= 4
            else:
                break
        # 向下扩展，遇到障碍块(正文/表格)就停——K 线黑底图下方常紧跟"用户名/正文"，
        # 若扩进去会被 _row_inside_para 整段拒绝
        y1 = r1
        while y1 < H:
            if any(_row_inside_para(y, block) for y in range(y1, min(y1 + 4, H))):
                break
            band = img_gray[y1:y1 + 4, :]
            if band.size and (band < 210).sum() > max(3, int(W * 0.004)):
                y1 += 4
            else:
                break
        if y1 - y0 < PHOTO_MIN:
            continue
        # 整段拒绝仅针对真正文段：统计 run 与正文段的**实际重叠**占比——run 起点
        # 紧贴正文段底边(±几 px 容差)不算"在段内"，否则分时图顶部若有一行图内字段
        # ("2026-06-08 均价…")被判 para, 其下紧贴的整张图 run 会被误拒(此前 bug)。
        _ov = 0
        _samples = 0
        for y in range(y0, y1, 10):
            _samples += 1
            for (py0, py1) in paras:
                if py0 <= y < py1:
                    _ov += 1
                    break
        if _samples and _ov / _samples > 0.3:
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


def _group_text_into_tables(blocks, min_rows=3):
    """把"多行多列、x 对齐成网格"的 text 块聚合成一个 table 块（通用表格识别）。

    对检测到的正文段之间的列对齐短词区（如三列表"红队/黄队/蓝队"每格一个股票名、
    术语对照表等）识别为表格。判定：
      1) 行聚合：y 中心相近(≤0.7×行高)的块归同一视觉行；
      2) 列聚类：把各行内块的 x0 归入页面稳定列簇(间距≥30px 分开)；
      3) 表区：连续 ≥min_rows 行、每行覆盖 ≥2 个稳定列 → 表格；
    命中后把区内 text 块替换为一个 type=table 块（rows=[[{text,color_rgb},...],...]，
    每行按 x 排；首行若是表头(每格短)也照常并入第一行）。原有块被移除，避免重复。
    普通正文（每行单块/单列）不会命中。
    """
    import statistics
    tbs = [b for b in blocks if b.get("type") in ("text", "heading")
           and (b.get("text") or "").strip()]
    if len(tbs) < 4:
        return blocks
    # 行聚合
    def _cy(b): return (b["y_top"] + b["y_bottom"]) / 2
    rows = []  # list of blocks（同一视觉行）
    for b in tbs:
        placed = False
        for r in rows:
            if abs(_cy(b) - _cy(r[-1])) < (r[-1]["y_bottom"] - r[-1]["y_top"]) * 0.7:
                r.append(b); placed = True; break
        if not placed:
            rows.append([b])
    # 页面 x0 列簇（稳定列中心）
    xs = sorted(b["x0"] for r in rows for b in r)
    col_repr = []
    for x in xs:
        if col_repr and x - col_repr[-1] <= 30:
            continue
        # 用该 x 附近块的 x0 均值（一个近似簇）
        col_repr.append(x)
    col_repr = [statistics.median([b["x0"] for r in rows for b in r
                                   if abs(b["x0"] - c) <= 15])
                for c in col_repr]
    # 每行覆盖哪些列
    def _row_cols(r):
        cols = set()
        for b in r:
            ci = min(range(len(col_repr)), key=lambda i: abs(b["x0"] - col_repr[i]))
            if abs(b["x0"] - col_repr[ci]) <= 20:
                cols.add(ci)
        return cols
    # 连续表格行（覆盖≥2列）
    regions = []  # list of (row_idx_list)
    cur = []
    for i, r in enumerate(rows):
        if len(_row_cols(r)) >= 2:
            cur.append(i)
        else:
            if len(cur) >= min_rows:
                regions.append(cur)
            cur = []
    if len(cur) >= min_rows:
        regions.append(cur)
    if not regions:
        return blocks
    # 收集命中块 id
    used = set()
    new_tables = []
    for ridx_list in regions:
        first_i = ridx_list[0]
        # 上方紧邻的表头行：多格(x0 可归入数据行的列区间)、y 间隙小 → 并入首行
        hdr_row = None
        if first_i > 0:
            up = rows[first_i - 1]
            up_h = up[0]["y_bottom"] - up[0]["y_top"]
            first_h = rows[first_i][0]["y_bottom"] - rows[first_i][0]["y_top"]
            # 表头在数据行上方紧邻：数据首行上沿 - 表头下沿 的间隙很小
            gap = rows[first_i][0]["y_top"] - up[0]["y_bottom"]
            if len(up) >= 2 and 0 <= gap <= max(up_h, first_h) * 1.8:
                drow = sorted(rows[first_i], key=lambda b: b["x0"])
                if len(drow) >= 2:
                    hdr_row = up
        idxs = ([first_i - 1] if hdr_row is not None else []) + list(ridx_list)
        cells_rows = []
        for i in idxs:
            r = sorted(rows[i], key=lambda b: b["x0"])
            cell = []
            for b in r:
                used.add(id(b))
                # 仅真正彩色（色相非空=非黑/灰）才保留颜色；灰/黑正文不上色
                _cr = b.get("color_rgb")
                if b.get("color_hue") is None:
                    _cr = None
                cell.append({"text": b.get("text", "").strip(),
                             "color_rgb": _cr})
            cells_rows.append(cell)
        y_top = min(rows[i][0]["y_top"] for i in idxs)
        new_tables.append({"type": "table", "rows": cells_rows, "y_top": y_top})
    kept = [b for b in blocks if id(b) not in used]
    # 把 table 按 y 插入（替代被移除文本块的位置）
    for tb in new_tables:
        kept.append(tb)
    return kept


def _process_page(n, json_dir, img_dir, blocks_tmp, fig_tmp, min_heading_ratio=1.28,

                  global_body_h=None, bold_thickness_ratio=0.078,
                  plain_heading_ratio=1.18, page_heading_base=True,
                  bold_use_page_h=True, group_body_h=None):
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
    # 标题/加粗的字号基准：优先用该页所属长图组的像素正文行高中位（同组切片字号一致，
    # 比单页 body_h 稳，p17/p28 这类"正文样本少"页不拖偏）；无组时回退本页 body_h。
    font_base_h = group_body_h if (group_body_h and page_heading_base) else body_h
    paras = _para_clusters(px, body_h, W, img_gray=img_gray)

    passA = _pass_a_regions(px, W, H, img_gray, body_h, paras, n=n)
    heading_by_line = _detect_headings(px, font_base_h, W, img_rgb, img_gray, W, H,
                                       exclude_ranges=[(r[0], r[1]) for r in passA],
                                       min_heading_ratio=min_heading_ratio,
                                       global_body_h=global_body_h,
                                       bold_thickness_ratio=bold_thickness_ratio,
                                       plain_heading_ratio=plain_heading_ratio,
                                       page_heading_base=page_heading_base,
                                       bold_base_h=(font_base_h if bold_use_page_h else None))
    heading_rows = [(px[i]["y_top"], px[i]["y_bottom"]) for i in heading_by_line]
    table_bands = _table_row_bands(px, W, body_h=body_h)
    passB = _pass_b_regions(px, W, H, img_gray, body_h, paras, passA,
                            heading_rows, table_bands=table_bands, n=n)
    regions = _merge_regions(passA, passB)

    # 若"photo"区域内容其实全是正文段落（OCR 已识别成 text），则不是图片——
    # 否则这段文字会被当图裁掉、正文丢失。判据要看"宽行"（行宽≈铺满版心）覆盖：
    # 真正文每行都贯穿版心（如 0.55×W），而图内字段文字（行情截图/表项）行很窄
    # （0.1~0.3×W）。用"宽行覆盖占比 >0.55"剔除正文；字段型截图保留为图。
    _wide_min = 0.4 * W
    filtered_regions = []
    for (fy0, fy1, fx0, fx1, kind, cap) in regions:
        if kind == 'photo':
            # 该区内"宽行"（行宽≥0.4 页宽，正文特征）的 y 覆盖区间
            wide_covered = np.zeros(fy1 - fy0, dtype=bool)
            for t in px:
                if FIG_RE.search(t["text"]) or TBL_RE.search(t["text"]):
                    continue
                if t["x1"] - t["x0"] < _wide_min:
                    continue
                yc = (t["y_top"] + t["y_bottom"]) / 2
                if fy0 <= yc < fy1:
                    y0c = max(0, int(t["y_top"]) - fy0)
                    y1c = min(fy1 - fy0, int(t["y_bottom"]) - fy0)
                    if y1c > y0c:
                        wide_covered[y0c:y1c] = True
            if wide_covered.size and wide_covered.mean() > 0.55:
                continue  # 区域大部分是贯穿版心的正文 → 非图片，剔除
        filtered_regions.append((fy0, fy1, fx0, fx1, kind, cap))
    regions = filtered_regions
    # 图底/图缘刻度文字（指标 tab、价格轴标签）扩展进图区：之后 inside 逻辑跳过，
    # 避免这些行漏成正文并被上色。须在裁图/inside 判定之前执行。
    # 表格紧贴 K 线图时表格行不得被吸收（否则整表吞进图），作 exclude_bands 排除。
    regions = _expand_regions_to_edge_text(regions, px, body_h,
                                           exclude_bands=list(paras) + list(table_bands))

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
                                                  bold_thickness_ratio,
                                                  base_h=(body_h if bold_use_page_h else None)))
        # 通用颜色识别：文字主色相（黑/白/灰为 None）。彩色文字（红/蓝队等）用色相区分。
        _hue, _rgb = _text_color_hue(img_rgb, t, W, H)
        # 行内混色切分：同一行既有黑字又有彩色强调字（如"…带头大哥带头跑路，"中
        # 后半句为绿）。若彩色段未覆盖整行 → 记 color_spans=[{s,e,color_rgb}]，
        # build 按段输出（只给彩段上色）；全覆盖则沿用整行 color_rgb 即可。
        _spans = None
        _runs = _line_inline_colors(img_rgb, t, t["text"], W, H)
        if _runs:
            nch = len(t["text"])
            cov = 0
            for s, e, _ in _runs:
                cov += max(0, min(e, nch - 1) - max(s, 0) + 1)
            if not (len(_runs) == 1 and _runs[0][0] == 0
                    and _runs[0][1] >= nch - 1) and cov < nch * 0.92:
                _spans = [{"s": s, "e": e, "color_rgb": list(rgb)}
                          for (s, e, rgb) in _runs]
        if i in heading_by_line:
            lv = heading_by_line[i]
            blocks.append({"type": "heading", "level": lv, "text": t["text"],
                           "y_top": t["y_top"], "y_bottom": t["y_bottom"],
                           "x0": t["x0"], "x1": t["x1"],
                           "centered": centered, "bold": bold,
                           "para_start": para_start,
                           # 行内混色(有 color_spans)时整行不再统一上色，交给 build 按段渲染
                           "color_hue": None if _spans else _hue,
                           "color_rgb": None if _spans else (list(_rgb) if _rgb else None),
                           "color_spans": _spans})
            continue
        blocks.append({"type": "text", "text": t["text"], "y_top": t["y_top"],
                       "y_bottom": t["y_bottom"],
                       "x0": t["x0"], "x1": t["x1"],
                       "centered": centered, "bold": bold,
                       "para_start": para_start,
                       # 行内混色(有 color_spans)时整行不再统一上色，交给 build 按段渲染
                       "color_hue": None if _spans else _hue,
                       "color_rgb": None if _spans else (list(_rgb) if _rgb else None),
                       "color_spans": _spans})

    blocks = _group_text_into_tables(blocks)
    blocks = _snap_blocks_colors(blocks, snap=True, cluster=True)
    blocks.sort(key=lambda b: (b["y_top"], 1 if b["type"] == "image" else 0))
    with open(os.path.join(blocks_tmp, f"{n}.json"), "w", encoding="utf-8") as f:
        json.dump({"page": n, "regions": len(regions),
                   "body_h": round(body_h, 1), "n_paras": len(paras),
                   "n_headings": len(heading_rows), "blocks": blocks},
                  f, ensure_ascii=False)
    return len(regions)


def _group_body_h_map(json_dir, img_dir, pages):
    """按"长图分组"计算各页统一的像素正文行高中位基准。

    长图被切成多片后，同组切片**字号（像素）一致**但切片高 H 不同——OCR 归一化行高
    h 是相对各自页高的，跨片不可直接比；像素行高 = h×H 才可比。这里把每页的像素
    正文行高收集起来，按 long_image_pages.txt 的页分组，取**组内中位**作为该组所有
    页共用的字号基准。

    Returns:
        {页号: 组像素正文行高中位}。只包含属于长图组的页；非长图页不含在返回里。
    """
    out = {}
    try:
        from .ocr import read_long_image_pages
        import os as _os
        # json_dir = <out>/ocr/pages_json，long_image_pages.txt 在 <out>/ 根
        _out = _os.path.dirname(_os.path.dirname(_os.path.abspath(json_dir)))
        groups = read_long_image_pages(_out)
    except Exception:
        groups = {}
    # 页 → 所属长图组
    page_group = {}
    for _name, _ps in groups.items():
        for _p in _ps:
            page_group[_p] = _name
    if not page_group:
        return out

    # 每页像素正文行高（h×H），仅用宽行近似正文
    page_px_med = {}
    for n in pages:
        gname = page_group.get(n)
        if gname is None:
            continue
        jp = os.path.join(json_dir, f"{n}.json")
        if not os.path.exists(jp):
            continue
        try:
            with open(jp, encoding="utf-8") as f:
                dd = json.load(f)
            ip = os.path.join(img_dir, f"p{n}.jpg")
            if not os.path.exists(ip):
                ip = os.path.join(img_dir, f"p{n}.png")
            if not os.path.exists(ip):
                continue
            H = Image.open(ip).size[1]
        except Exception:
            continue
        hs = [ln["h"] * H for ln in dd.get("lines", [])
              if (ln.get("text") or "").strip()]
        if hs:
            page_px_med[n] = (gname, float(np.median(hs)))

    # 组内聚合成组中位 → 组内每页共用
    group_vals = {}
    for n, (gname, med) in page_px_med.items():
        group_vals.setdefault(gname, []).append(med)
    group_med = {g: float(np.median(v)) for g, v in group_vals.items() if v}
    for n, (gname, _med) in page_px_med.items():
        if gname in group_med:
            out[n] = group_med[gname]
    return out


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
    _cfg = Config(str(project.path))
    min_heading_ratio = float(_cfg.get('detect.min_heading_ratio', 1.28))
    # 加粗判定阈值（相对笔画粗细）。可配置 detect.bold_thickness_ratio。
    bold_thickness_ratio = float(_cfg.get('detect.bold_thickness_ratio', 0.078))
    # 非彩色标题最小行高比例（默认 1.18）。长图文章标题常只比正文大 ~1.1×，
    # 需调低才能识别。可配置 detect.heading_plain_ratio。
    plain_heading_ratio = float(_cfg.get('detect.heading_plain_ratio', 1.18))
    # 标题行高基准：true(默认)=本页所属**长图组**的像素正文行高中位；false=全书归一化中位数。
    # 长图被切成多片（同字号、切片高不同），归一化行高跨片不可比；多张不同字号长图混排时
    # 全书基准又会被大字号图主导。按"页→长图分组"聚合同组切片的像素行高最稳。
    page_heading_base = bool(_cfg.get('detect.heading_page_base', True))
    # 加粗归一化：true(默认)=除以该页所属**长图组**的像素正文中位行高；false=除以本行 OCR 高。
    # OCR 把同一视觉行拆成多段时各段高不同，除以段高会使加粗判定不一致；
    # 用组级字号尺度归一化则加粗 = 笔画绝对粗细/组字号，与 OCR 段高无关。
    bold_use_page_h = bool(_cfg.get('detect.bold_use_page_h', True))

    if not os.path.isdir(json_dir):
        raise SystemExit(f"错误：未找到 OCR 坐标目录 {json_dir}\n"
                         f"  请先运行 OCR：python main.py ocr -p <project>")
    if not os.path.isdir(img_dir):
        raise SystemExit(f"错误：未找到截图目录 {img_dir}\n"
                         f"  请用 --assets 指定正确的素材目录（或先跑 OCR）")

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

    # 长图组级字号基准：长图被切成多片（同字号、切片高不同），归一化行高跨片不可比；
    # 只有把像素行高按"页→长图组"聚合成组中位，才是该组统一的字号尺度。
    # 组内切片用同基准，避免单页正文样本少（p17/p28）导致字号估算波动。
    # 非长图页（long_image_pages.txt 未记录）保持本页 body_h。
    group_body_map = {}
    if page_heading_base or bold_use_page_h:
        group_body_map = _group_body_h_map(json_dir, img_dir, pages)

    for n in pages:
        r = _process_page(n, json_dir, img_dir, blocks_tmp, fig_tmp,
                          min_heading_ratio=min_heading_ratio,
                          global_body_h=global_body_h,
                          bold_thickness_ratio=bold_thickness_ratio,
                          plain_heading_ratio=plain_heading_ratio,
                          page_heading_base=page_heading_base,
                          bold_use_page_h=bold_use_page_h,
                          group_body_h=group_body_map.get(n))
        if r:
            total += r
    _swap_in(blocks_tmp, blocks_dir)
    _swap_in(fig_tmp, fig_dir)
    print(f"processed {len(pages)} pages, {total} regions")
    return blocks_dir, fig_dir, total
