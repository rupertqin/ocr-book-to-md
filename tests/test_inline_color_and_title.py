# -*- coding: utf-8 -*-
"""行内混色切字 + 长图标题"首段误判"修复 的单元测试。

对象：
  - detect._line_inline_colors: 合成图(黑字+彩色强调字) → 精确返回彩色字区间
  - build_md._long_image_title: 页首"居中节标签 + 直接开始正文"时，首段不被当标题
  - build_md._render_pages_cross_page: color_spans → 行内多个 color span 输出
"""

import json
import os
import tempfile

import numpy as np

from ocr_book_to_md import detect as D
from ocr_book_to_md import build_md as BM


def _make_row_img(text, colored_idx, fg_green=(60, 179, 113),
                  W=700, H=240):
    """生成"等宽字"合成行：colored_idx(可多个)位置的字符为绿色，其余黑。
    每个字宽 14px、高 24。返回 (img_float32_rgb, box_px)。"""
    img = np.full((H, W, 3), 255, dtype=np.float32)
    cw = 14
    x0 = 100
    for i, ch in enumerate(text):
        x = x0 + i * cw
        col = fg_green if i in colored_idx else (30, 30, 30)
        img[120:144, x:x + cw] = col
    box = {"x0": x0, "x1": x0 + cw * len(text),
           "y_top": 120, "y_bottom": 144}
    return img, box


def test_inline_colors_detects_only_colored_chars():
    text = "甲乙丙丁戊"
    img, box = _make_row_img(text, {2})     # 仅"丙"绿色
    runs = D._line_inline_colors(img, box, text, 700, 240)
    assert runs == [(2, 2, (60, 179, 113))]


def test_inline_colors_contiguous_green_run():
    text = "强调带头大哥重要"
    img, box = _make_row_img(text, {2, 3, 4})   # 中间三字绿
    runs = D._line_inline_colors(img, box, text, 700, 240)
    assert len(runs) == 1
    s, e, rgb = runs[0]
    assert s == 2 and e == 4
    assert rgb[1] > rgb[0] and rgb[1] > rgb[2]  # 绿系


def test_inline_colors_all_black_no_run():
    text = "全黑字正文行"
    img, box = _make_row_img(text, set())
    runs = D._line_inline_colors(img, box, text, 700, 240)
    assert runs == []


def test_block_color_spans_render_inline():
    """detect 产出的 color_spans → md 只对彩色段包 span，其余不上色。"""
    text = "一二三四五六七八九十"
    # 中间"三四五六"(索引2..5)为绿段，模拟 detect 吸附后输出
    rgb = list(D.SEMANTIC_COLORS["mediumseagreen"])
    block = {"type": "text", "text": text, "y_top": 100, "y_bottom": 130,
             "x0": 100, "x1": 800, "bold": False, "centered": False,
             "para_start": True, "color_hue": None, "color_rgb": None,
             "color_name": None,
             "color_spans": [{"s": 2, "e": 5, "color_rgb": rgb,
                              "color_name": "mediumseagreen"}]}
    with tempfile.TemporaryDirectory() as tmp:
        bd = os.path.join(tmp, "blocks")
        os.makedirs(bd)
        with open(os.path.join(bd, "1.json"), "w", encoding="utf-8") as f:
            json.dump({"page": 1, "blocks": [block]}, f, ensure_ascii=False)
        md = BM._render_pages_cross_page(bd, [1], no_headings=True)
    assert ('一二<span style="color:mediumseagreen">三四五六</span>七八九十'
            in md)
    # 整段只有一个 span，没有把首尾黑字染色
    assert md.count("<span") == 1


def test_long_image_title_takes_top_centered_label():
    """长图文章标题可以是**居中短行**（公众号常见样式：字号不大但页面顶部、
    居中、独立成行）。复现真实结构：居中标签(y100) → 普通正文(y160) →
    正文首段(y200, 字号略大被误判 big)。首段不该当标题，回退取顶部居中短行。
    """
    with tempfile.TemporaryDirectory() as tmp:
        bd = os.path.join(tmp, "blocks")
        os.makedirs(bd)
        blocks = [
            {"type": "text", "text": "点金术加强版", "y_top": 100,
             "y_bottom": 128, "x0": 400, "x1": 700,
             "centered": True, "bold": False, "color_hue": None,
             "color_rgb": None, "color_name": None},
            {"type": "text", "text": "兄弟们好，很多朋友都是因点金术加的我。",
             "y_top": 160, "y_bottom": 188, "x0": 100, "x1": 900,
             "centered": False, "bold": False, "color_hue": None,
             "color_rgb": None, "color_name": None},
            {"type": "text", "text": "大盘岌岌可危。此，我也筛选了一些基本面健康的老登股，",
             "y_top": 200, "y_bottom": 231, "x0": 100, "x1": 1000,
             "centered": False, "bold": False, "color_hue": None,
             "color_rgb": None, "color_name": None},
            {"type": "text", "text": "接下去一塌糊涂的市场做个准备。",
             "y_top": 231, "y_bottom": 262, "x0": 100, "x1": 700,
             "centered": False, "bold": False, "color_hue": None,
             "color_rgb": None, "color_name": None},
        ]
        with open(os.path.join(bd, "1.json"), "w", encoding="utf-8") as f:
            json.dump({"page": 1, "blocks": blocks}, f, ensure_ascii=False)
        title = BM._long_image_title(bd, 1)
    assert title == "点金术加强版"


def test_long_image_title_no_centered_label_no_title():
    """页首直接是普通正文（既无大字号标题也无居中短行）→ 不给标题。"""
    with tempfile.TemporaryDirectory() as tmp:
        bd = os.path.join(tmp, "blocks")
        os.makedirs(bd)
        blocks = [
            {"type": "text", "text": "兄弟们好，很多朋友都是因点金术加的我。",
             "y_top": 100, "y_bottom": 128, "x0": 100, "x1": 900,
             "centered": False, "bold": False, "color_hue": None,
             "color_rgb": None, "color_name": None},
            {"type": "text", "text": "大盘岌岌可危。此，我也筛选了一些基本面健康的老登股，",
             "y_top": 200, "y_bottom": 231, "x0": 100, "x1": 1000,
             "centered": False, "bold": False, "color_hue": None,
             "color_rgb": None, "color_name": None},
        ]
        with open(os.path.join(bd, "1.json"), "w", encoding="utf-8") as f:
            json.dump({"page": 1, "blocks": blocks}, f, ensure_ascii=False)
        title = BM._long_image_title(bd, 1)
    assert title == ""


def test_long_image_title_keeps_real_top_title():
    """真正的大标题位于页面最顶部 → 正常生成 # 标题。"""
    with tempfile.TemporaryDirectory() as tmp:
        bd = os.path.join(tmp, "blocks")
        os.makedirs(bd)
        blocks = [
            {"type": "text", "text": "一周内参", "y_top": 100,
             "y_bottom": 140, "x0": 300, "x1": 800,
             "centered": False, "bold": True, "color_hue": None,
             "color_rgb": None, "color_name": None},
            {"type": "text", "text": "正文从这里开始，内容继续。",
             "y_top": 200, "y_bottom": 231, "x0": 100, "x1": 900,
             "centered": False, "bold": False, "color_hue": None,
             "color_rgb": None, "color_name": None},
        ]
        with open(os.path.join(bd, "1.json"), "w", encoding="utf-8") as f:
            json.dump({"page": 1, "blocks": blocks}, f, ensure_ascii=False)
        title = BM._long_image_title(bd, 1)
    assert title == "一周内参"


def test_long_image_title_does_not_swallow_body():
    """大字标题(y100,h45)后紧跟普通正文(y160,h28)——正文行高≈页内中位，若按
    0.95×中位判 big 会被整页吞掉。标题必须停在首行，不得吞正文。"""
    with tempfile.TemporaryDirectory() as tmp:
        bd = os.path.join(tmp, "blocks")
        os.makedirs(bd)
        blocks = [
            {"type": "text", "text": "2026/3/9-一周内参", "y_top": 100,
             "y_bottom": 145, "x0": 200, "x1": 900,
             "centered": False, "bold": True, "color_hue": None,
             "color_rgb": None, "color_name": None},
            {"type": "text", "text": "现在是周一凌晨，杭州天气转暖，遥远的中东战事正酣。",
             "y_top": 180, "y_bottom": 208, "x0": 100, "x1": 1000,
             "centered": False, "bold": False, "color_hue": None,
             "color_rgb": None, "color_name": None},
            {"type": "text", "text": "第二行正文，继续补充说明事件的影响与机会。",
             "y_top": 208, "y_bottom": 236, "x0": 100, "x1": 1000,
             "centered": False, "bold": False, "color_hue": None,
             "color_rgb": None, "color_name": None},
        ]
        with open(os.path.join(bd, "1.json"), "w", encoding="utf-8") as f:
            json.dump({"page": 1, "blocks": blocks}, f, ensure_ascii=False)
        title = BM._long_image_title(bd, 1)
    # 只保留标题行，不吞正文
    assert title == "2026/3/9-一周内参"
    assert "周一凌晨" not in title
