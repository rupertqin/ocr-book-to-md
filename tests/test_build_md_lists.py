# -*- coding: utf-8 -*-
"""build_md 模块：通用列表识别 的单元测试。

测试对象：
  - OL_RE / UL_RE / _is_list_item: 行首编号/bullet 判定（含全角 ． 无空格）
  - _render_pages_cross_page 输出中的 markdown 有序/无序列表：
      * 连续编号行 → 一个有序列表、统一半角编号
      * 导语"…："+ 列表项 → 导语独立段、列表项不粘连
      * 带样式行不做列表转换
      * 图片/标题边界正确收尾列表

列表识别输入是 OCR 文本行、输出是 md 文本。为直观看到"哪些连续编号行被
识别成一个列表"，测试运行时会画出"文本行页面示意 + 列表高亮"，导出到
tests/_generated/。
"""

import json
import os
import tempfile

from PIL import Image, ImageDraw, ImageFont

from ocr_book_to_md import build_md as BM

_GEN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_generated")
_FONT_CACHE = {}


def _font(size):
    if size not in _FONT_CACHE:
        p = "/System/Library/Fonts/STHeiti Medium.ttc"
        _FONT_CACHE[size] = (ImageFont.truetype(p, size)
                             if os.path.exists(p) else ImageFont.load_default())
    return _FONT_CACHE[size]


def _export_list_page(name, lines, highlights=()):
    """把文本行画成页面，高亮"识别为一个列表"的行带。lines=[(y_top, text)]"""
    margin = 40
    maxy = max((y for y, _ in lines), default=100) + margin
    im = Image.new("RGB", (900, int(maxy)), "white")
    dr = ImageDraw.Draw(im)
    for (y0, y1, label) in highlights:
        dr.rectangle([20, int(y0), 880, int(y1)], fill=(200, 240, 255),
                     outline=(30, 90, 200), width=2)
        dr.text((24, max(2, int(y0) - 22)), label, fill=(30, 90, 200),
                font=_font(18))
    fnt = _font(24)
    for y, text in lines:
        # y 设为 y+2 让文字在 y..y+30 行内居中（font 24）
        dr.text((60, y + 2), text, font=fnt, fill=(0, 0, 0))
    os.makedirs(_GEN, exist_ok=True)
    im.save(os.path.join(_GEN, name))


def _tb(text, y_top, bold=False, x0=100, width=400, centered=False,
        para_start=True):
    """构造 text 块（y 向下）。"""
    return {"type": "text", "text": text, "y_top": y_top,
            "y_bottom": y_top + 30, "x0": x0, "x1": x0 + width,
            "bold": bold, "centered": centered, "para_start": para_start,
            "color_hue": None, "color_rgb": None, "color_name": None}


def _write(blocks_dir, n, blocks):
    p = os.path.join(blocks_dir, f"{n}.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"page": n, "blocks": blocks}, f, ensure_ascii=False)


# ================= 基础判定 =================

def test_is_list_item_ordered():
    assert BM._is_list_item("1. 投多少？") == ("ol", 1, "投多少？")
    # 全角点 + 无空格（CJK）也能识别
    assert BM._is_list_item("3．我的标的一直死气沉沉") == ("ol", 3, "我的标的一直死气沉沉")
    assert BM._is_list_item("2、有了几只新的标的怎么办") == ("ol", 2, "有了几只新的标的怎么办")


def test_is_list_item_not_decimal():
    # 小数 3.5倍 / 1.2节 不能当列表项
    assert BM._is_list_item("3.5倍收益很可观。") is None
    assert BM._is_list_item("1.2 节介绍背景。") is None


def test_is_list_item_unordered():
    assert BM._is_list_item("- 无序项") == ("ul", None, "无序项")
    assert BM._is_list_item("• 圆点项") == ("ul", None, "圆点项")


def test_is_list_item_plain_text():
    assert BM._is_list_item("这是普通正文行内容。") is None


# ================= 端到端：渲染输出 =================

def test_consecutive_numbered_lines_form_single_list():
    """用户案例：1./2./3．三行 应成为连续有序列表且编号统一半角。"""
    with tempfile.TemporaryDirectory() as tmp:
        bd = os.path.join(tmp, "blocks")
        os.makedirs(bd)
        blocks = [
            _tb("1. 投多少？", 100),
            _tb("2. 有了几只后又出现新的标的怎么办？", 130),
            _tb("3．我的标的一直死气沉沉/下跌咋办？", 160),
        ]
        _export_list_page(
            "list_ordered_user_case.png",
            [(b["y_top"], b["text"]) for b in blocks],
            highlights=[(100, 190, "识别为同一有序列表(蓝框)")])
        _write(bd, 1, blocks)
        md = BM._render_pages_cross_page(bd, [1], no_headings=True)
    assert "1. 投多少？\n2. 有了几只后又出现新的标的怎么办？\n3. 我的标的一直死气沉沉/下跌咋办？" in md
    # 三项之间不应有拆分它们的空行
    assert "投多少？\n\n2." not in md


def test_para_after_list_terminates_it():
    """列表后跟普通正文 → 列表项先收尾，正文另起一段。"""
    with tempfile.TemporaryDirectory() as tmp:
        bd = os.path.join(tmp, "blocks")
        os.makedirs(bd)
        blocks = [
            _tb("1. 第一项问题？", 100),
            _tb("2. 第二项问题？", 130),
            _tb("列表后的正文段落，继续讲解内容。", 180, width=800),
        ]
        _export_list_page(
            "list_terminated_by_para.png",
            [(b["y_top"], b["text"]) for b in blocks],
            highlights=[(100, 160, "有序列表(蓝框)"),
                        (180, 210, "其后正文(不并入)")])
        _write(bd, 1, blocks)
        md = BM._render_pages_cross_page(bd, [1], no_headings=True)
    assert "1. 第一项问题？\n2. 第二项问题？" in md
    assert "2. 第二项问题？\n\n列表后的正文段落" in md


def test_guideline_label_then_first_item_not_glued():
    """导语 '学完…：' + 1.… → 导语独立段，首列表项从自己起。"""
    with tempfile.TemporaryDirectory() as tmp:
        bd = os.path.join(tmp, "blocks")
        os.makedirs(bd)
        blocks = [
            _tb("学完本章之后，你将能够：", 100, width=700),
            _tb("1.描述进化的三种产物。", 150),
            _tb("2. 界定心理机制概念。", 190),
        ]
        _export_list_page(
            "list_after_label.png",
            [(b["y_top"], b["text"]) for b in blocks],
            highlights=[(150, 220, "导语后的列表项(蓝框)")])
        _write(bd, 1, blocks)
        md = BM._render_pages_cross_page(bd, [1], no_headings=True)
    assert "你将能够：\n\n1." in md
    assert "1. 描述进化的三种产物。\n2. 界定心理机制概念。" in md


def test_styled_list_item_stays_paragraph():
    """带加粗/彩色样式 → 不做列表转换（保留样式按普通文本输出）。"""
    with tempfile.TemporaryDirectory() as tmp:
        bd = os.path.join(tmp, "blocks")
        os.makedirs(bd)
        blocks = [
            _tb("1. 加粗标题样式的行。", 100, bold=True),
            _tb("2. 加粗标题样式的行二。", 130, bold=True),
        ]
        _write(bd, 1, blocks)
        md = BM._render_pages_cross_page(bd, [1], no_headings=True)
    # 加粗行按 <strong> 输出，不转换成裸列表行
    assert "<strong>1. 加粗标题样式的行。</strong>" in md
    assert "1. 加粗标题样式的行。\n2. 加粗标题样式的行二。" not in md


def test_image_boundary_flushes_list():
    """列表项后紧跟图片 → 列表先输出，图片跟在列表之后。"""
    with tempfile.TemporaryDirectory() as tmp:
        bd = os.path.join(tmp, "blocks")
        os.makedirs(bd)
        blocks = [
            _tb("1. 投多少？", 100),
            _tb("2. 出现新的标的怎么办？", 130),
            {"type": "image", "file": "p1_f1.png", "y_top": 200,
             "y_bottom": 400, "x0": 100, "x1": 900},
            _tb("正文在图片之后。", 450, width=800),
        ]
        _write(bd, 1, blocks)
        md = BM._render_pages_cross_page(bd, [1], no_headings=True)
    i_list = md.find("1. 投多少？")
    i_img = md.find("p1_f1.png")
    i_body = md.find("正文在图片之后")
    assert 0 <= i_list < i_img < i_body


def test_unordered_list_output():
    with tempfile.TemporaryDirectory() as tmp:
        bd = os.path.join(tmp, "blocks")
        os.makedirs(bd)
        blocks = [
            _tb("• 第一个要点。", 100),
            _tb("- 第二个要点。", 130),
            _tb("• 第三个要点。", 160),
        ]
        _write(bd, 1, blocks)
        md = BM._render_pages_cross_page(bd, [1], no_headings=True)
        # 三种 bullet 形式统一为 md "- "
        assert "- 第一个要点。\n- 第二个要点。\n- 第三个要点。" in md
