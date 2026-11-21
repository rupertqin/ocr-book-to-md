# -*- coding: utf-8 -*-
"""build_md 模块：markdown 表格渲染 + 颜色 span 输出的单元测试。"""

import json
import os
import tempfile

from p2md import build_md as BM


def _write(blocks_dir, n, blocks):
    p = os.path.join(blocks_dir, f"{n}.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"page": n, "blocks": blocks}, f, ensure_ascii=False)


def _table_block(rows):
    return {"type": "table", "y_top": 100, "rows": rows}


def _cell(text, rgb=None, name=None):
    c = {"text": text, "color_rgb": rgb}
    if name:
        c["color_name"] = name
    return c


def test_render_table_with_color_and_plain_cells():
    with tempfile.TemporaryDirectory() as tmp:
        bd = os.path.join(tmp, "blocks")
        os.makedirs(bd)
        rows = [
            [_cell("红队"), _cell("黄队"), _cell("蓝队")],
            [_cell("格力电器", [240, 128, 128], "lightcoral"),
             _cell("双汇发展", None),
             _cell("传音控股", [100, 149, 237], "cornflowerblue")],
        ]
        _write(bd, 1, [_table_block(rows)])
        md = BM._render_pages_cross_page(bd, [1], no_headings=True)
    assert "| 红队 | 黄队 | 蓝队 |" in md
    assert "| --- | --- | --- |" in md
    assert 'color:lightcoral">格力电器</span>' in md
    assert 'color:cornflowerblue">传音控股</span>' in md
    # 黑字 cell 不上色
    assert "| 双汇发展 |" in md
    assert "双汇发展</span>" not in md


def test_render_text_color_semantic_name():
    with tempfile.TemporaryDirectory() as tmp:
        bd = os.path.join(tmp, "blocks")
        os.makedirs(bd)
        block = {"type": "text", "text": "断头铡不可谓不快",
                 "y_top": 100, "y_bottom": 130, "x0": 100, "x1": 500,
                 "bold": False, "centered": False, "para_start": True,
                 "color_hue": 140.0, "color_rgb": [46, 178, 84],
                 "color_name": "seagreen"}
        _write(bd, 1, [block])
        md = BM._render_pages_cross_page(bd, [1], no_headings=True)
    assert '<span style="color:seagreen">断头铡不可谓不快</span>' in md


def test_render_text_black_no_span():
    with tempfile.TemporaryDirectory() as tmp:
        bd = os.path.join(tmp, "blocks")
        os.makedirs(bd)
        block = {"type": "text", "text": "普通黑色正文",
                 "y_top": 100, "y_bottom": 130, "x0": 100, "x1": 500,
                 "bold": False, "centered": False, "para_start": True,
                 "color_hue": None, "color_rgb": None, "color_name": None}
        _write(bd, 1, [block])
        md = BM._render_pages_cross_page(bd, [1], no_headings=True)
    assert "普通黑色正文" in md
    assert "<span" not in md


def test_table_pipe_escaped():
    rows = [[_cell("含 | 竖线"), _cell("B")]]
    b = _table_block(rows)
    # 直接经渲染主循环
    with tempfile.TemporaryDirectory() as tmp:
        bd = os.path.join(tmp, "blocks")
        os.makedirs(bd)
        _write(bd, 1, [b])
        md = BM._render_pages_cross_page(bd, [1], no_headings=True)
    assert r"含 \| 竖线" in md
