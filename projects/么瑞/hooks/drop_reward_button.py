# -*- coding: utf-8 -*-
"""么瑞项目 OCR 中间件：去掉长截图**底部的红色赞赏按钮**（白字"稀罕作者"那种圆角按钮）。

背景：本项目素材是公众号长截图，文章末尾压着一个红色圆角按钮（白图标 + 白字）。
它不是文章内容，但 detect 会把它识别成一个 image block 裁成插图（如 p57_f1.png），
正文里还残留"凸 稀罕作者"字样。

中间件在 OCR 之后、detect 之前运行（见 config.yaml detect.middlewares），此时：
  - 能拿到 OCR lines（用来删掉按钮上的文字行）；
  - 能改输出目录下 _ocr_tmp/p{n}.jpg（detect 用的就是这批页图）。
把按钮区域涂成周围底色后，detect 既不会裁出插图，也不会残留文字。

判据（全部相对页尺寸，不写死像素/坐标）：
  1) 只检查 **long_image_pages.txt 里每组长图的最后一页**（长截图末尾才可能有这个
     按钮；中间切片的底部是正文/图表，逐个查既没必要也更易误伤）；
  2) 页面底部 BAND 内存在成块的**红色**像素（R 高、G/B 明显低且彼此接近）；
  3) 该红块 bbox **填充率高**（按钮是实心圆角矩形；K 线蜡烛/红字是细碎红像素），
     且**宽而扁**（宽/高 ≥ MIN_ASPECT、宽 ≥ 页宽 MIN_W_RATIO、高 ≤ 页高 MAX_H_RATIO）；
  4) bbox 外围一圈是**浅色**（说明它压在页面底色上，而不是图表内部的红色区块）。
四条同时满足才判为按钮 → 涂底色 + 删除落在框内的 OCR line。

接口：def process(ocr_dir: str, project_path: str) -> None
"""

import json
import os
import re

import numpy as np
from PIL import Image

BAND = 0.15            # 只检查页面底部 15%
RED_R = 140            # 红色判据：R ≥ RED_R 且 G、B 明显低于 R
RED_G_RATIO = 0.75
RED_B_RATIO = 0.80
RED_GB_DIFF = 40       # 且 G、B 彼此接近（排除棕/橙/粉紫）
MIN_RED_RATIO = 0.002  # 红像素数下限（相对页面积）
MIN_FILL = 0.6         # bbox 内红色填充率
MIN_W_RATIO = 0.20     # bbox 宽 ≥ 页宽 20%
MAX_H_RATIO = 0.25     # bbox 高 ≤ 页高 25%
MIN_ASPECT = 2.0       # 宽/高
RING_LIGHT = 190       # "浅色"判据：min(R,G,B) ≥ RING_LIGHT
RING_LIGHT_RATIO = 0.7
PAD_RATIO = 0.02       # 涂抹外扩（按钮圆角/抗锯齿像素在其 bbox 外一点）


def _read_long_image_pages(out_dir):
    """读取 long_image_pages.txt → 每组长图的**最后一页**页号集合。

    按钮只可能出现在长截图末尾，故只查每组的末页；中间切片不查（底部是正文/图表，
    查了既无收益、又更容易把图表里的红色区块误判成按钮）。
    """
    path = os.path.join(out_dir, "long_image_pages.txt")
    last_pages = set()
    if not os.path.isfile(path):
        return last_pages
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line:
                continue
            nums = line.split("\t", 1)[1] if "\t" in line else line
            pages = [int(t) for t in re.split(r"[,\s]+", nums.strip()) if t.isdigit()]
            if pages:
                last_pages.add(max(pages))
    return last_pages


def _red_mask(arr):
    """红色像素掩码：R 高、G/B 明显低且彼此接近（如 224,80,80）。"""
    R = arr[:, :, 0].astype(np.int16)
    G = arr[:, :, 1].astype(np.int16)
    B = arr[:, :, 2].astype(np.int16)
    return ((R >= RED_R) & (G <= R * RED_G_RATIO) & (B <= R * RED_B_RATIO)
            & (np.abs(G - B) <= RED_GB_DIFF))


def _find_button(arr):
    """在页面底部找"红色实心扁块且压在浅色底上"，返回 (bbox, fill_color) 或 None。"""
    H, W = arr.shape[:2]
    red = _red_mask(arr)
    red[: int(H * (1 - BAND)), :] = False          # 只留底部 BAND
    ys, xs = np.where(red)
    if len(ys) < max(800, int(W * H * MIN_RED_RATIO)):
        return None
    x0, x1 = int(xs.min()), int(xs.max()) + 1
    y0, y1 = int(ys.min()), int(ys.max()) + 1
    bw, bh = x1 - x0, y1 - y0
    if bw < W * MIN_W_RATIO or bh < 20 or bh > H * MAX_H_RATIO:
        return None
    if bw / max(bh, 1) < MIN_ASPECT:
        return None
    if len(ys) / float(bw * bh) < MIN_FILL:
        return None                                # 不成实心块 → 不是按钮

    # 外围一圈必须浅色（按钮压在页面底色上，而不是图表内部的红色区块）
    pad = max(8, int(W * PAD_RATIO))
    ex0, ey0 = max(0, x0 - pad), max(0, y0 - pad)
    ex1, ey1 = min(W, x1 + pad), min(H, y1 + pad)
    strips = [arr[ey0:y0, ex0:ex1], arr[y1:ey1, ex0:ex1],
              arr[y0:y1, ex0:x0], arr[y0:y1, x1:ex1]]
    strips = [s.reshape(-1, 3) for s in strips if s.size]
    if not strips:
        return None
    ring = np.concatenate(strips)
    if float((ring.min(axis=1) >= RING_LIGHT).mean()) < RING_LIGHT_RATIO:
        return None
    return (ex0, ey0, ex1, ey1), np.median(ring, axis=0).astype(np.uint8)


def _drop_lines_inside(lines, rect, W, H):
    """删除中心落在 rect 内的 OCR line（rect 为像素坐标，左上原点）。"""
    ex0, ey0, ex1, ey1 = rect
    kept, removed = [], 0
    for ln in lines:
        x = float(ln.get("x", 0.0)) * W
        w = float(ln.get("w", 0.0)) * W
        y = float(ln.get("y", 0.0)) * H          # OCR 的 y 是"底边"，原点在左下
        h = float(ln.get("h", 0.0)) * H
        cx, cy = x + w / 2.0, H - (y + h / 2.0)
        if ex0 <= cx <= ex1 and ey0 <= cy <= ey1:
            removed += 1
        else:
            kept.append(ln)
    return kept, removed


def process(ocr_dir: str, project_path: str) -> None:
    out_dir = os.path.dirname(os.path.dirname(os.path.abspath(ocr_dir)))
    img_dir = os.path.join(out_dir, "_ocr_tmp")
    last_pages = _read_long_image_pages(out_dir)   # 每组长图的末页
    if not last_pages or not os.path.isdir(img_dir):
        print("  (无长图末页记录/页图目录，跳过底部赞赏按钮清理)")
        return

    n_btn = n_line = 0
    for n in sorted(last_pages):
        img_path = os.path.join(img_dir, f"p{n}.jpg")
        json_path = os.path.join(ocr_dir, f"{n}.json")
        if not os.path.isfile(img_path):
            continue
        im = Image.open(img_path).convert("RGB")
        W, H = im.size
        arr = np.asarray(im).copy()
        hit = _find_button(arr)
        if not hit:
            continue
        rect, fill = hit
        arr[rect[1]:rect[3], rect[0]:rect[2]] = fill          # 涂成周围底色
        Image.fromarray(arr).save(img_path, "JPEG", quality=95)
        n_btn += 1

        if os.path.isfile(json_path):
            with open(json_path, encoding="utf-8") as f:
                data = json.load(f)
            data["lines"], removed = _drop_lines_inside(
                data.get("lines", []), rect, W, H)
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            n_line += removed

    if n_btn:
        print(f"  已去掉 {n_btn} 处底部赞赏按钮（同时删除 {n_line} 个按钮文字 OCR line）")
    else:
        print("  未发现底部赞赏按钮")
