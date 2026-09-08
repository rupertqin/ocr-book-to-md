# -*- coding: utf-8 -*-
"""detect 模块：图像采样相关函数的合成图单元测试。

不依赖项目真实书页；用 numpy/PIL 现造小图：
  - _line_text_color / _text_color_hue: 红字/黑字/深底 的像素取样
  - _line_bold: 粗 vs 细笔画（几何矩形模拟）判定
  - _para_clusters: 深色背景上的 OCR 行（图内文字）不判为正文段

每个测试运行时会把构造的合成图**导出**到 tests/_generated/*.png，
方便人工打开查看真实效果（该目录已在 .gitignore，不入库）。
"""

import os

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from ocr_book_to_md import detect as D

_FONT = None
_GEN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "_generated")


def _export(name, arr_rgb, boxes=(), labels=()):
    """把合成图保存为 PNG（画上采样框与文字标注，便于肉眼核对）。"""
    os.makedirs(_GEN_DIR, exist_ok=True)
    a = np.clip(arr_rgb, 0, 255).astype(np.uint8)
    im = Image.fromarray(a, "RGB" if a.ndim == 3 else "L")
    if a.ndim == 2:
        im = im.convert("RGB")
    dr = ImageDraw.Draw(im)
    for bx in boxes:
        dr.rectangle([bx["x0"], bx["y_top"], bx["x1"], bx["y_bottom"]],
                     outline=(0, 0, 255), width=2)
    try:
        lbl_font = ImageFont.truetype(
            "/System/Library/Fonts/STHeiti Medium.ttc", 16)
    except Exception:
        lbl_font = None
    for i, lbl in enumerate(labels):
        dr.text((10, 8 + i * 22), lbl, fill=(200, 30, 30), font=lbl_font)
    im.save(os.path.join(_GEN_DIR, name))


def _font(size=28):
    global _FONT
    cand = ["/System/Library/Fonts/STHeiti Medium.ttc",
            "/System/Library/Fonts/STHeiti Light.ttc",
            "/System/Library/Fonts/Arial.ttf"]
    for p in cand:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def _text_img(text, fg, bg=(255, 255, 255), x0=100, y0=100,
              size=28, pad=6):
    """渲染一行文字，返回 (np_img_rgb_float32, dict_box_px)。"""
    font = _font(size)
    # 量文字实际大小，避免裁剪
    im_tmp = Image.new("RGB", (10, 10), bg)
    dr = ImageDraw.Draw(im_tmp)
    bbox = dr.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    W = x0 + tw + pad + 60
    H = y0 + th + pad + 60
    im = Image.new("RGB", (W, H), bg)
    dr = ImageDraw.Draw(im)
    dr.text((x0 - bbox[0], y0 - bbox[1]), text, font=font, fill=fg)
    img = np.array(im).astype(np.float32)
    box = {"x0": x0, "y_top": y0, "x1": x0 + tw + pad,
           "y_bottom": y0 + th + pad}
    return img, box


# ================= 文字颜色取样 =================

def test_line_text_color_red_text():
    img, box = _text_img("红利资产", (232, 102, 122))
    _export("text_color_red.png", img, boxes=[box], labels=["红字采样框(蓝)"])
    W, H = img.shape[1], img.shape[0]
    rgb = D._line_text_color(img, box, W, H)
    assert rgb is not None
    # 中位色应为红系：R 明显大于 G/B
    r, g, b = rgb
    assert r > 180 and g < 150 and b < 150


def test_line_text_color_black_text_returns_dark():
    img, box = _text_img("普通正文", (30, 30, 30))
    _export("text_color_black.png", img, boxes=[box], labels=["黑字采样框"])
    W, H = img.shape[1], img.shape[0]
    rgb = D._line_text_color(img, box, W, H)
    assert rgb is not None
    assert max(rgb) < 110  # 近黑
    # 黑字 → hue None（_text_color_hue）
    hue, _ = D._text_color_hue(img, box, W, H)
    assert hue is None


def test_line_text_color_empty_area_returns_none():
    # 区域里没有暗像素（纯白）→ None
    img = np.full((200, 300, 3), 255, dtype=np.float32)
    box = {"x0": 100, "y_top": 100, "x1": 200, "y_bottom": 150}
    _export("text_color_white_empty.png", img, boxes=[box],
            labels=["空白采样框"])
    assert D._line_text_color(img, box, 300, 200) is None


def test_text_color_hue_red_detected():
    img, box = _text_img("红色标注", (232, 102, 122))
    _export("text_color_red_label.png", img, boxes=[box], labels=["红字"])
    W, H = img.shape[1], img.shape[0]
    hue, rgb = D._text_color_hue(img, box, W, H)
    assert hue is not None
    assert (hue < 10 or hue > 350)  # 红系


def test_text_color_hue_blue_detected():
    img, box = _text_img("蓝色说明", (100, 149, 237))
    _export("text_color_blue.png", img, boxes=[box], labels=["蓝字"])
    W, H = img.shape[1], img.shape[0]
    hue, _ = D._text_color_hue(img, box, W, H)
    assert hue is not None and 200 < hue < 240


# ================= 加粗判定（合成笔画） =================

def _stroke_img(stroke_w, y0=100, H=None, bg=255):
    """画一条竖直黑色笔画（宽 stroke_w、高贯穿文字框），模拟真实字形笔画。

    竖条置于页面中央(x≈300)且两侧留白，保证 distance transform 的
    "最近背景"在条两侧（而非窗口边界），距离度量才准确。
    """
    if H is None:
        H = y0 + 80
    gray = np.full((H, 700), bg, dtype=np.float32)
    x_c = 300
    gray[y0:y0 + 40, x_c:x_c + stroke_w] = 0.0
    return gray


def _stroke_box(y0=100, stroke_w=2, x_c=300):
    return {"x0": x_c - 150, "y_top": y0, "x1": x_c + 150,
            "y_bottom": y0 + 40}  # OCR 框：笔画在框中央，两侧留白


def test_line_bold_thick_vs_thin():
    W, H = 700, 260
    # 细笔画（常规正文，2px 条 → radius≈1 → 相对粗细 2/40=0.05）
    thin = _stroke_img(2)
    t_thin = _stroke_box(stroke_w=2)
    _export("bold_thin2px.png", thin, boxes=[t_thin],
            labels=["细笔画2px(应判不粗)"])
    assert D._line_bold(t_thin, thin, W, H) is False
    # 粗笔画（加粗文字）
    thick = _stroke_img(14)
    t_thick = _stroke_box(stroke_w=14)
    _export("bold_thick14px.png", thick, boxes=[t_thick],
            labels=["粗笔画14px(应判粗)"])
    assert D._line_bold(t_thick, thick, W, H) is True


def test_line_bold_uses_base_h_normalization():
    """同一绝对笔画粗细，字号尺度大 → 相对细，判为不粗。"""
    W, H = 700, 260
    # 4px 笔画：默认用框高 40 → 0.1 > 0.078(粗)；base_h=60 → 0.067(不粗)
    mid = _stroke_img(4)
    t_mid = _stroke_box(stroke_w=4)
    _export("bold_mid4px_baseh.png", mid, boxes=[t_mid],
            labels=["4px 笔画：默认判粗 / base_h=60 判不粗"])
    assert D._line_bold(t_mid, mid, W, H) is True
    assert D._line_bold(t_mid, mid, W, H, base_h=60) is False
    # base_h 小 → 相对更粗 → True
    assert D._line_bold(t_mid, mid, W, H, base_h=16) is True


def test_line_bold_empty_region_false():
    gray = np.full((200, 300), 255, dtype=np.float32)
    t = {"x0": 100, "y_top": 100, "x1": 200, "y_bottom": 130}
    _export("bold_empty.png", gray, boxes=[t], labels=["空白(应判不粗)"])
    assert D._line_bold(t, gray, 300, 200) is False


# ================= 深底文字不判正文段 =================

def _page_img_with_dark_band(H=600, W=700):
    """白底；中部一条深色带（模拟 K 线图深底），带内写浅色文字。"""
    img = np.full((H, W, 3), 255, dtype=np.float32)
    img[300:500, 150:600] = (30, 30, 45)   # 深色带
    # 深色带内画几行浅灰/白色文字，模拟 K 线图内数字
    font = _font(24)
    im = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8))
    dr = ImageDraw.Draw(im)
    dr.text((190, 335), "1790.87", font=font, fill=(255, 255, 255))
    dr.text((200, 380), "MACD", font=font, fill=(200, 210, 230))
    img = np.array(im).astype(np.float32)
    return img


def test_para_clusters_skips_dark_background_lines():
    """深底上(图内)的 OCR 行 → _para_clusters 不应把它判成正文段。"""
    H, W = 600, 700
    img_rgb = _page_img_with_dark_band(H, W)
    gray = img_rgb.mean(axis=2)
    # 行: 深底带内的"白字"(低亮, 因 OCR 会给白色文字的 bbox)
    dark_lines = [
        {"text": "1790.87", "x0": 200.0, "x1": 300.0,
         "y_top": 330.0, "y_bottom": 360.0, "h": 30, "w": 100},
    ]
    # 普通白底正文（宽行）
    body_lines = [
        {"text": "这是一个普通的正文段落用来撑满版心宽度测试数据内容",
         "x0": 60.0, "x1": 640.0, "y_top": 60.0, "y_bottom": 92.0,
         "h": 32, "w": 580},
    ]
    boxes = [{"x0": 60, "y_top": 60, "x1": 640, "y_bottom": 92},
             {"x0": 200, "y_top": 330, "x1": 300, "y_bottom": 360}]
    _export("page_darkband_lines.png", img_rgb, boxes=boxes,
            labels=["蓝框=白底正文行", "蓝框=深底图内文字(不应成段)"])
    px = dark_lines + body_lines
    paras = D._para_clusters(px, body_h=32.0, page_w=W, img_gray=gray)
    # 只应产生一个正文段(白底宽行)；深底行(暗占比高)被跳过
    assert len(paras) == 1
    py0, py1 = paras[0]
    assert py0 >= 50 and py1 <= 110  # 只在白底正文处
