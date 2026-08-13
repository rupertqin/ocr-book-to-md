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


def _detect_headings(px, body_h, page_w, img_rgb, img_gray, W, H, exclude_ranges=()):
    excl = [(e0 - 4, e1 + 4) for (e0, e1) in exclude_ranges]
    candidates = []
    for i, line in enumerate(px):
        if FIG_RE.search(line["text"]) or TBL_RE.search(line["text"]):
            continue
        h_ratio = line["h"] / body_h
        w = line["w"]
        if h_ratio > 2.2:
            continue
        yc = (line["y_top"] + line["y_bottom"]) / 2
        if any(e0 <= yc <= e1 for (e0, e1) in excl):
            continue
        col = _line_text_color(img_rgb, line, W, H)
        sat = _col_sat(col) if col else 0.0
        if col and sat > 0.25 and h_ratio >= 1.06 and 0.10 * page_w < w <= 0.95 * page_w:
            candidates.append((i, 2))
        elif (not col or sat < 0.35) and h_ratio >= 1.18 \
                and 0.14 * page_w <= w <= 0.92 * page_w:
            candidates.append((i, 3))
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
        above = [l for l in px if l["y_bottom"] <= g_top]
        below = [l for l in px if l["y_top"] >= g_bot]
        gap_up = g_top - (max((l["y_bottom"] for l in above), default=0))
        gap_dn = (min((l["y_top"] for l in below), default=H) - g_bot)
        if gap_up < 0.6 * body_h or gap_dn < 0.6 * body_h:
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
        y0 = r0
        while y0 > 0:
            band = img_gray[y0 - 4:y0, :]
            if (band < 210).sum() > max(3, int(W * 0.004)):
                y0 -= 4
            else:
                break
        y1 = r1
        while y1 < H:
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


def _process_page(n, json_dir, img_dir, blocks_tmp, fig_tmp):
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
                                       exclude_ranges=[(r[0], r[1]) for r in passA])
    heading_rows = [(px[i]["y_top"], px[i]["y_bottom"]) for i in heading_by_line]
    passB = _pass_b_regions(px, W, H, img_gray, body_h, paras, passA,
                            heading_rows, n=n)
    regions = _merge_regions(passA, passB)

    img_boxes = []
    blocks = []
    for (fy0, fy1, fx0, fx1, kind, cap) in regions:
        fig_idx = len([b for b in blocks if b["type"] == "image"]) + 1
        fname = f"p{n}_f{fig_idx}.png"
        crop = img.crop((fx0, fy0, fx1, fy1))
        crop.save(os.path.join(fig_tmp, fname))
        img_boxes.append((fy0, fy1, kind))
        blocks.append({"type": "image", "file": fname, "y_top": fy0,
                       "y_bottom": fy1, "kind": kind, "caption": cap})

    for i, t in enumerate(px):
        is_caption = bool(FIG_RE.search(t["text"]) or TBL_RE.search(t["text"]))
        yc = (t["y_top"] + t["y_bottom"]) / 2
        in_para = _row_inside_para(yc, paras)
        inside = None
        for (iy0, iy1, kind) in img_boxes:
            if iy0 <= yc <= iy1:
                inside = kind
                break
        if i in heading_by_line:
            lv = heading_by_line[i]
            blocks.append({"type": "heading", "level": lv, "text": t["text"],
                           "y_top": t["y_top"], "y_bottom": t["y_bottom"]})
            continue
        if inside is None:
            blocks.append({"type": "text", "text": t["text"], "y_top": t["y_top"],
                           "y_bottom": t["y_bottom"]})
        else:
            if not in_para or is_caption:
                continue
            blocks.append({"type": "text", "text": t["text"], "y_top": t["y_top"],
                           "y_bottom": t["y_bottom"]})

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

    if not os.path.isdir(json_dir):
        raise SystemExit(f"错误：未找到 OCR 坐标目录 {json_dir}\n"
                         f"  请先运行 OCR：python main.py ocr -p <project>")
    if not os.path.isdir(img_dir):
        raise SystemExit(f"错误：未找到截图目录 {img_dir}\n"
                         f"  请用 --images 指定正确的截图目录")

    blocks_tmp = os.path.join(project.output_dir, f".blocks.tmp.{os.getpid()}")
    fig_tmp = os.path.join(project.output_dir, f".figs.tmp.{os.getpid()}")
    os.makedirs(blocks_tmp, exist_ok=True)
    os.makedirs(fig_tmp, exist_ok=True)

    if pages is None:
        pages = sorted(int(os.path.splitext(f)[0])
                       for f in os.listdir(json_dir)
                       if f.endswith(".json"))
    total = 0
    for n in pages:
        r = _process_page(n, json_dir, img_dir, blocks_tmp, fig_tmp)
        if r:
            total += r
    _swap_in(blocks_tmp, blocks_dir)
    _swap_in(fig_tmp, fig_dir)
    print(f"processed {len(pages)} pages, {total} regions")
    return blocks_dir, fig_dir, total
