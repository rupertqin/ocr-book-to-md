# -*- coding: utf-8 -*-
"""长图切片页号分配（ocr._alloc_page_numbers）的单元测试。

背景（真实 bug）：一张长图被内容感知切成多片后，切片按**连续页号**登记进
long_image_pages.txt；若两个长图组的页号区间相交，同一批页会被两组同时登记，
build_md.auto_chapters 逐组各自成篇，于是同一批页被切成两篇文章——正文重复，
且后一篇的标题会取到页中间的正文句。

因此分配规则必须是：
  1) 能放就放原始页号（让页号贴近素材原名，编号稳定、可复现）；
  2) 区间与别人相交 → **整段**后移到 max(已用)+1（不逐个跳号，避免切片页号
     被打散导致跨页渲染顺序错乱）。
"""

import numpy as np
from PIL import Image, ImageDraw

from p2md import ocr as O


def test_prefers_original_page_number_when_free():
    assert O._alloc_page_numbers(8, 5, set()) == 8
    # 远处已占用的页号不影响：区间 [8,13) 与 {20,21} 不相交
    assert O._alloc_page_numbers(8, 5, {20, 21}) == 8


def test_shifts_whole_range_when_overlapping():
    # [8,13) 碰到已用的 9 → 整段后移到 max(used)+1 = 10
    assert O._alloc_page_numbers(8, 5, {9}) == 10
    # 末片相碰也要整段后移（不能只挪冲突的那一片）
    assert O._alloc_page_numbers(8, 5, {12}) == 13
    assert O._alloc_page_numbers(13, 10, set(range(13, 23))) == 23


def test_shifted_range_is_disjoint_and_contiguous():
    used = {13, 14, 15}
    start = O._alloc_page_numbers(13, 3, used)
    span = set(range(start, start + 3))
    assert start == 16
    assert span == {16, 17, 18}
    assert not (span & used)


def test_zero_slices_keeps_preferred():
    # 没有切片可写时不占用页号（调用方也不会登记）
    assert O._alloc_page_numbers(5, 0, {5}) == 5


def test_two_long_images_end_up_disjoint():
    """复现 8-28 / 8-29 两个素材都解析到页号 8、9 的情形：两组的切片页号必须互不相交。"""
    used = set()
    a = O._alloc_page_numbers(8, 5, used)          # 8..12
    used.update(range(a, a + 5))
    b = O._alloc_page_numbers(9, 9, used)          # 9..17 与 8..12 相交 → 13..21
    used.update(range(b, b + 9))

    assert a == 8
    assert b == 13
    ga, gb = set(range(a, a + 5)), set(range(b, b + 9))
    assert not (ga & gb)
    assert len(used) == 5 + 9


def test_empty_used_never_shifts():
    for n in (1, 8, 100):
        assert O._alloc_page_numbers(n, 4, set()) == n


def test_scan_pages_orders_date_named_assets_chronologically(tmp_path):
    """日期命名的长图素材必须按自然顺序排（9-5 在 9-12 之前）。

    字符串序里 '9-5' > '9-12'，会把最早的一期排到最后，成书章节顺序错乱。
    """
    for n in ["9-25.jpg", "9-5.jpg", "8-29.jpg", "9-12.jpg", "8-28.png", "9-19.jpg"]:
        (tmp_path / n).write_bytes(b"x")
    pages = O.scan_pages(str(tmp_path))
    assert list(pages.values()) == ["8-28.png", "8-29.jpg", "9-5.jpg",
                                    "9-12.jpg", "9-19.jpg", "9-25.jpg"]
    assert sorted(pages) == [8, 9, 10, 11, 12, 13]


def test_scan_pages_unchanged_for_unique_page_numbers(tmp_path):
    """无冲突项目（page_180/182/184）页号与处理顺序完全不变。"""
    for n in ["page_184.png", "page_180.png", "page_182.png"]:
        (tmp_path / n).write_bytes(b"x")
    assert O.scan_pages(str(tmp_path)) == {
        180: "page_180.png", 182: "page_182.png", 184: "page_184.png"}


def test_trim_keeps_right_margin_text_columns():
    """右边缘的"文字/图表密集列"不能被误判成手机滚动条而裁掉。

    真实 bug：长截图最右侧的正文列在统计上恰好"灰调、明暗混合、贴近右边缘"，
    被 sb 检测选中后 `_trim_image` 会把图裁到那里，切掉行末文字——行末字只剩
    左半边，Vision 会把「少」认成「！」，正文出现错字；伪句末标点还会把一句话
    误切成两段（段首判定 = 上一行以句末标点结尾）。
    """
    W, H = 400, 600
    im = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(im)
    for y in range(8, H - 8, 20):
        d.rectangle([40, y, 359, y + 9], fill=(20, 20, 20))     # 密集带（非候选）
        d.rectangle([360, y, 380, y + 5], fill=(20, 20, 20))    # 窄带（旧规则误判为滚动条）
    ink = np.asarray(im.convert("L")) < 128
    cols = np.where(ink.any(axis=0))[0]
    ink_w = int(cols.max()) - int(cols.min()) + 1

    out = O._trim_image(im)
    assert out.size[0] >= ink_w, "右侧墨迹被裁掉，行末字会被切半"


def test_trim_removes_light_scroll_badge():
    """内容右侧的**浅灰滚动指示条**要被裁掉，且不能伤到内容。

    这类条比"深灰滚动条"浅得多（如灰度 238 浮在白底上），旧判据（列均灰
    150-210）认不出，会残留在成图右侧成为一条灰竖条。
    """
    W, H = 400, 400
    im = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(im)
    d.rectangle([40, 60, 300, 340], fill=(20, 20, 20))         # 内容
    d.rectangle([360, 120, 372, 260], fill=(238, 238, 238))    # 浅灰滚动条
    out = O._trim_image(im)

    # 内容完整保留
    assert out.size[0] >= 300 - 40 + 1
    # 浅条已被裁掉：最右几列应是纯白
    a = np.asarray(out.convert("L")).astype(np.float32)
    assert float(a[:, -3:].mean()) > 250, "右侧浅灰滚动条没被裁掉"


def _jpeg_with_content(path, size=(60, 60)):
    """造一张非空白小图（纯白会被判定为空白页而过滤掉）。"""
    from PIL import Image, ImageDraw
    im = Image.new("RGB", size, "white")
    ImageDraw.Draw(im).rectangle([8, 8, size[0] - 8, size[1] - 8], fill="black")
    im.save(path, "JPEG", quality=90)


def test_lip_rebuild_drops_phantom_groups(tmp_path):
    """long_image_pages.txt 必须按当前素材重建：

    素材已删除的旧分组要清掉，否则 auto_chapters 会凭空生成一个"幽灵章节"。
    """
    assets = tmp_path / "assets"
    assets.mkdir()
    _jpeg_with_content(assets / "a.jpg")
    out = tmp_path / "out"
    (out / "_ocr_tmp").mkdir(parents=True)
    O.write_long_image_pages(str(out), {"gone.png": [7, 8]})

    _, new_pages = O.prepare_images(str(assets), {1: "a.jpg"}, str(out), force=True)
    groups = O.read_long_image_pages(str(out))

    assert groups == {}
    assert set(new_pages) == {1}
