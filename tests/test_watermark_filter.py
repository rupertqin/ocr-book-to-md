# -*- coding: utf-8 -*-
"""ocr_fix 斜排水印行滤除 的单元测试。"""

import json
import os
import tempfile

from p2md import ocr_fix as F


def _page(lines, body_n=20):
    """构造页面：先放 body_n 行正常正文，再追加给定 lines。"""
    rows = [(f"这是第{i}行正常的正文内容，用来撑满版心宽度测试用。",
             0.02, 0.9) for i in range(body_n)]
    rows += [(t, h, w) for (t, h, w) in lines]
    return {"lines": [{"text": t, "y": 1.0 - i * 0.03, "h": h, "w": w}
                      for i, (t, h, w) in enumerate(rows)]}


def _call(data):
    """跑一次水印过滤，返回移除行数。"""
    n = F._filter_watermark_lines(data, lambda *a, **k: None)
    return n


def test_watermark_giant_bbox_short_text_removed():
    """斜排水印：行高是正文 5+ 倍 + 短文本 + 重复 → 滤除。"""
    body = _page([
        ("闲鱼嘘嘘敦敦", 0.27, 0.42),
        ("闲鱼嘘嘘敦敦", 0.26, 0.42),
    ])
    n = _call(body)
    assert n == 2
    texts = [l["text"] for l in body["lines"]]
    assert "闲鱼嘘嘘敦敦" not in texts
    assert len(body["lines"]) == 20


def test_watermark_known_terms_removed():
    """短文本 + 命中水印词 → 滤除。"""
    body = _page([
        ("闲鱼嘘飯", 0.03, 0.3),
    ])
    n = _call(body)
    assert n == 1
    assert "闲鱼嘘飯" not in [l["text"] for l in body["lines"]]


def test_normal_text_not_removed():
    """正常正文/短标题（无巨大 bbox、无重复、无水印词）→ 保留。"""
    body = _page([
        ("这是正常段落内容，用来测试水印不过滤正常文字。", 0.02, 0.9),
        ("标题", 0.03, 0.2),
        ("13:44", 0.015, 0.08),
    ])
    n = _call(body)
    assert n == 0
    assert len(body["lines"]) == 23


def test_pages_not_modified_when_no_watermark():
    data = {"lines": [{"text": "普通正文行不短但是正常长度内容足够", "y": 0.9,
                        "h": 0.02, "w": 0.9}]}
    n = _call(data)
    assert n == 0
