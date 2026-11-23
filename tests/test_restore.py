# -*- coding: utf-8 -*-
"""restore 模块的单元测试（text / layed / raw 三种 Markdown 还原方式）。"""

import json
import os

import pytest
from PIL import Image, ImageDraw

from p2md import restore


def _scan(path, size=(500, 700)):
    """生成一张"有内容"的页图（纯白图会被空白页过滤掉）。"""
    im = Image.new("RGB", size, "white")
    ImageDraw.Draw(im).rectangle([40, 40, size[0] - 40, size[1] - 40], fill="black")
    im.save(path)
    return path


def _make_work(tmp, pages, content=True):
    """构造一个最小 work 目录：blocks / ocr/pages_json / _ocr_tmp / figs / content。

    pages: {页号: {"headings": [...], "lines": [...], "image": bool}}
    """
    work = os.path.join(tmp, "work")
    for sub in ("blocks", "ocr/pages_json", "_ocr_tmp", "figs", "content"):
        os.makedirs(os.path.join(work, sub), exist_ok=True)

    for n, spec in pages.items():
        blocks = []
        y = 100
        for i, h in enumerate(spec.get("headings", [])):
            blocks.append({"type": "heading", "level": 2 if i == 0 else 3,
                           "text": h, "y_top": y, "y_bottom": y + 40})
            y += 60
        for ln in spec.get("lines", []):
            blocks.append({"type": "text", "text": ln, "y_top": y,
                           "y_bottom": y + 40, "x0": 10, "x1": 1000,
                           "centered": False, "bold": False})
            y += 50
        if spec.get("image"):
            blocks.append({"type": "image", "file": "p%d_f1.png" % n,
                           "y_top": 200, "y_bottom": 600,
                           "x0": 100, "x1": 1000})
            Image.new("RGB", (900, 400), "gray").save(
                os.path.join(work, "figs", "p%d_f1.png" % n))

        with open(os.path.join(work, "blocks", "%d.json" % n), "w",
                  encoding="utf-8") as f:
            json.dump({"page": n, "blocks": blocks}, f, ensure_ascii=False)

        # OCR 行：Vision 的归一化坐标（y 是底边）
        lines = [{"text": t, "x": 0.02, "y": 0.80 - i * 0.03,
                  "w": 0.9, "h": 0.025}
                 for i, t in enumerate(spec.get("lines", []))]
        with open(os.path.join(work, "ocr", "pages_json", "%d.json" % n), "w",
                  encoding="utf-8") as f:
            json.dump({"page": n, "lines": lines}, f, ensure_ascii=False)

        Image.new("RGB", (1000, 1400), "white").save(
            os.path.join(work, "_ocr_tmp", "p%d.jpg" % n), format="JPEG")

    if content:
        with open(os.path.join(work, "content", "article_0.md"), "w",
                  encoding="utf-8") as f:
            f.write("# 第1章\n\n正文\n")
    return work


def test_estimate_text_width_mm_full_and_half_width():
    # 10 个全角 = 10.5em；5 个半角 = 2.75em；合计 13.25em
    w = restore.estimate_text_width_mm("汉字十个汉字十个汉字" + "abcde", 10.0)
    assert w == pytest.approx(13.25 * 10.0 * 25.4 / 72.0)


def test_parse_punct_groups_string_and_dict():
    assert restore.parse_punct_groups(None) == []
    one = restore.parse_punct_groups("-0.1em")
    assert len(one) == 1 and one[0][1] == "-0.1em" and one[0][2] == ""
    two = restore.parse_punct_groups({"、。": "-0.1em", "，": "-0.2em"})
    assert [g[2] for g in two] == ["", "-b"]
    assert two[1][1] == "-0.2em"


def test_slice_windows_single_page_when_fits():
    boxes = [(100, 200), (300, 400)]
    assert restore.slice_windows(boxes, 900, 1200) == [(0.0, 900)]


def test_slice_windows_cuts_between_lines():
    # 页高 2000，一页只能放 1000 → 切口必须落在行间隙（行底 950 或 1200）
    boxes = [(100, 400), (500, 950), (1000, 1200), (1300, 1500), (1600, 1900)]
    windows = restore.slice_windows(boxes, 2000, 1000)
    assert len(windows) >= 2
    cuts = [round(top + h) for top, h in windows[:-1]]
    for cut in cuts:
        for t, b in boxes:
            assert not (t < cut < b), f"切口 {cut} 落在行内 {(t, b)}"
    assert windows[0][0] == 0.0
    assert pytest.approx(windows[-1][0] + windows[-1][1]) == 2000
    assert all(h <= 1000 for _t, h in windows)


def test_page_img_size_prefers_ocr_tmp(tmp_path):
    work = _make_work(str(tmp_path), {1: {"lines": ["正文"]}})
    assert restore.page_img_size(str(work), 1) == (1000, 1400)
    assert restore.page_img_size(str(work), 99) == (restore.DEF_W, restore.DEF_H)


def test_sorted_page_images_orders_by_page_number(tmp_path):
    d = tmp_path / "scans"
    d.mkdir()
    for name in ("p10.png", "p2.png", "p1.png", "cover.jpg", "no_number.png"):
        _scan(d / name, (120, 120))
    names = [n for n, _p in restore.sorted_page_images(d)]
    assert names[:3] == ["p1.png", "p2.png", "p10.png"]
    assert names[-1] == "no_number.png"


def test_prepare_normal_requires_articles(tmp_path):
    work = _make_work(str(tmp_path), {1: {"lines": ["正文"]}}, content=False)
    with pytest.raises(SystemExit):
        restore.prepare_normal(str(work))
    open(os.path.join(work, "content", "article_0.md"), "w").close()
    assert restore.prepare_normal(str(work)).name == "content"


def test_prepare_layed_writes_chapters_and_sections(tmp_path):
    work = _make_work(str(tmp_path), {
        1: {"headings": ["第1章 开场"], "lines": ["第一行正文。", "第二行正文。"],
            "image": True},
        2: {"lines": ["第三行正文。"]},
    })
    count, out = restore.prepare_layed(
        str(work), page_margin="1cm 1.5cm", font_family="Serif")

    assert count == 1
    assert out.name == "content_layed"
    md = (out / "article_0.md").read_text(encoding="utf-8")
    assert md.startswith("# 第1章 开场")
    assert md.count('<section class="layed-page">') == 2      # 每页一个 section
    assert "../figs/p1_f1.png" in md                          # 图片相对引用
    assert "<style>" in md and ".layed-page" in md


def test_prepare_layed_keeps_text_inside_section(tmp_path):
    """超宽行会被压字号，不排到 section 外（否则 overflow:hidden 会裁字）。"""
    long_line = "很" * 45
    work = _make_work(str(tmp_path), {1: {"lines": [long_line]}})
    _count, out = restore.prepare_layed(
        str(work), page_margin="1cm 1.5cm", font_family="Serif")
    md = (out / "article_0.md").read_text(encoding="utf-8")
    for seg in md.split("<p style=")[1:]:
        style = seg.split('">', 1)[0]
        left = float(style.split("left:")[1].split("mm")[0])
        font = float(style.split("font-size:")[1].split("pt")[0])
        width = restore.estimate_text_width_mm(long_line, font)
        assert left + width <= 180.5, "行宽溢出 section"


def test_prepare_image_single_article_without_blocks(tmp_path):
    """image 不做 OCR：没有 blocks 时全书一篇，每页一个 section。"""
    work = _make_work(str(tmp_path), {1: {"lines": ["正文"]}})
    scans = tmp_path / "scans"
    scans.mkdir()
    for name in ("p1.png", "p2.png"):
        _scan(scans / name)

    count, out = restore.prepare_image(
        str(work), page_margin="1cm 1.5cm", assets_dir=str(scans))
    assert count == 1
    md = (out / "article_0.md").read_text(encoding="utf-8")
    assert md.count('<section class="img-page">') == 2
    assert 'src="images/p1.png"' in md and 'src="images/p2.png"' in md
    assert (out / "images" / "p1.png").is_file()


def test_prepare_image_skips_cover(tmp_path):
    work = _make_work(str(tmp_path), {1: {"lines": ["正文"]}})
    scans = tmp_path / "scans"
    scans.mkdir()
    for name in ("cover.jpg", "p1.png"):
        _scan(scans / name)
    _count, out = restore.prepare_image(
        str(work), page_margin="1cm 1.5cm", assets_dir=str(scans))
    md = (out / "article_0.md").read_text(encoding="utf-8")
    assert "cover.jpg" not in md and "images/p1.png" in md
