# -*- coding: utf-8 -*-
"""build_md 模块的单元测试。"""

import json
import os
import tempfile

from p2md import build_md


def _make_blocks_dir(tmp, pages):
    """构造一个含 heading 的 blocks 目录。"""
    blocks_dir = os.path.join(tmp, "blocks")
    os.makedirs(blocks_dir, exist_ok=True)
    for n, heads in pages.items():
        data = {"page": n, "blocks": []}
        for h in heads:
            data["blocks"].append({"type": "heading", "level": 2,
                                   "text": h, "y_top": 100, "y_bottom": 140})
        with open(os.path.join(blocks_dir, f"{n}.json"), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    return blocks_dir


def test_auto_chapters_detects_chapter_boundaries():
    with tempfile.TemporaryDirectory() as tmp:
        pages = {
            1: ["第1章 通往进化心理学"],
            2: [],
            3: ["参考文献"],
        }
        blocks_dir = _make_blocks_dir(tmp, pages)
        chapters = build_md.auto_chapters(blocks_dir)
        kinds = [c[0] for c in chapters]
        assert "chapter" in kinds
        assert "references" in kinds


def test_rewrite_fig_refs():
    md = "![图1.1](../figs/p1_f1.png)\n正文"
    out = build_md._rewrite_fig_refs(md)
    assert "images/p1_f1.png" in out
    assert "../figs/" not in out


def test_fmt_line_escapes_html():
    assert build_md._fmt_line("A<B & C", True, True) == \
        '<div style="text-align:center"><strong>A&lt;B &amp; C</strong></div>'


def test_cn2int():
    assert build_md.cn2int("一") == 1
    assert build_md.cn2int("十") == 10
    assert build_md.cn2int("十三") == 13
    assert build_md.cn2int("二十") == 20
    assert build_md.cn2int("二十一") == 21
    assert build_md.cn2int("九十九") == 99
