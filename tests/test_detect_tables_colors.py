# -*- coding: utf-8 -*-
"""detect 模块：通用表格识别 + 颜色语义化 的单元测试。

测试对象（纯函数，不需图片/OCR）：
  - _table_row_bands: 多行列对齐 → 表格行 y 带
  - _group_text_into_tables: 行网格 → type=table 块（含表头吸收、cell 颜色）
  - _rgb_to_hue / _snap_to_semantic / _cluster_rgbs / _snap_blocks_colors:
    颜色近黑/低饱和排除、语义色吸附、每页聚类、跨页一致性

表格识别输入是 OCR 文本块（不是像素），但为了直观看到"哪些行被聚成表格"，
会把构造的文本块画成页面示意并高亮识别带，导出到 tests/_generated/。
"""

import os

from PIL import Image, ImageDraw, ImageFont

from ocr_book_to_md import detect as D

_GEN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_generated")
_FONT_CACHE = {}


def _font(size):
    if size not in _FONT_CACHE:
        p = "/System/Library/Fonts/STHeiti Medium.ttc"
        _FONT_CACHE[size] = (ImageFont.truetype(p, size)
                             if os.path.exists(p) else ImageFont.load_default())
    return _FONT_CACHE[size]


def _export_table_page(name, blocks, highlights=()):
    """把文本块画成"页面"，高亮识别为表格的行带。highlights=[(y0,y1,label,color)]"""
    margin = 30
    maxx = max((b.get("x1", b["x0"] + 60) for b in blocks), default=100) + margin
    maxy = max((b.get("y_bottom", b["y_top"] + 30) for b in blocks),
               default=100) + margin
    im = Image.new("RGB", (int(maxx), int(maxy)), "white")
    dr = ImageDraw.Draw(im)
    for (y0, y1, label, color) in highlights:
        dr.rectangle([10, int(y0), int(maxx) - 10, int(y1)],
                     fill=color, outline=(30, 90, 200), width=2)
        dr.text((12, max(2, int(y0) - 20)), label, fill=(30, 90, 200),
                font=_font(18))
    fnt = _font(22)
    for b in blocks:
        x0 = int(b.get("x0", 100))
        y0 = int(b.get("y_top", 100))
        # 文字在 y0..y0+30 行内居中：font 高约 22，把 y 设为 y0+4 居中
        dr.text((x0, y0 + 4), b["text"], font=fnt, fill=(0, 0, 0))
    os.makedirs(_GEN, exist_ok=True)
    im.save(os.path.join(_GEN, name))


def _txt(text, y_top, x0, y_bottom=None, x1=None):
    """构造一个最小 text 行（px 坐标系，y 向下）。"""
    h = 30 if y_bottom is None else y_bottom - y_top
    w = 60 if x1 is None else x1 - x0
    return {"type": "text", "text": text, "y_top": y_top,
            "y_bottom": (y_top + h), "x0": x0, "x1": (x0 + w)}


def _block(text, y_top, x0, width=200, bold=False, hue=None, rgb=None, name=None):
    b = {"type": "text", "text": text, "y_top": y_top,
         "y_bottom": y_top + 30, "x0": x0, "x1": x0 + width,
         "bold": bold, "centered": False, "para_start": True,
         "color_hue": hue, "color_rgb": rgb, "color_name": name}
    return b


# ================= 表格识别 _table_row_bands =================

def test_table_row_bands_detects_grid():
    # 三行三列（每行 3 个短词，x 对齐）→ 应产出 1 个 y 带
    rows = []
    for r, y in enumerate([100, 150, 200]):
        for c, x in enumerate([200, 500, 800]):
            rows.append(_txt(f"{r}{c}", y, x, y_bottom=y + 40))
    bands = D._table_row_bands(rows, W=1200)
    assert len(bands) == 1
    y0, y1 = bands[0]
    assert y0 <= 100 and y1 >= 240


def test_table_row_bands_ignores_body_paragraph():
    # 每行只有一个宽块（正文段落样式）→ 不是表格
    rows = [_txt("这是一行铺满版心的正文段落文字测试内容", 100, 180, x1=1000),
            _txt("这是第二行正文段落继续测试内容", 150, 180, x1=1000),
            _txt("这是第三行正文段落继续测试", 200, 180, x1=1000),
            _txt("这是第四行正文段落继续", 250, 180, x1=1000)]
    bands = D._table_row_bands(rows, W=1200)
    assert bands == []


def test_table_row_bands_requires_min_rows():
    # 只有 2 行多列 → 不足 min_rows=3，不应视为表格
    rows = []
    for r, y in enumerate([100, 150]):
        for c, x in enumerate([200, 500, 800]):
            rows.append(_txt(f"{r}{c}", y, x, y_bottom=y + 40))
    assert D._table_row_bands(rows, W=1200) == []


# ================= 表格聚合 _group_text_into_tables =================

def test_group_text_into_tables_makes_table_block():
    blocks = []
    for r, y in enumerate([100, 140, 180, 220, 260]):
        for c, x in enumerate([200, 500]):
            blocks.append(_block(f"cell{r}{c}", y, x, width=150))
    # 导出页面示意：标出聚合为表格的行带
    bands = D._table_row_bands(list(blocks), W=1200)
    if bands:
        _export_table_page(
            "table_grid_two_cols.png", blocks,
            highlights=[(b0, b1, "识别为表格", (200, 240, 255)) for b0, b1 in bands])
    out = D._group_text_into_tables(blocks)
    tables = [b for b in out if b.get("type") == "table"]
    assert len(tables) == 1
    tb = tables[0]
    assert len(tb["rows"]) == 5          # 5 行
    assert all(len(r) == 2 for r in tb["rows"])  # 2 列
    # 原 text 块被移除（不重复）
    assert all(b.get("type") != "text" for b in out)


def test_group_text_into_tables_absorbs_header_row():
    # 表头行 + 3 数据行：表头在数据行正上方且 y 间隙小 → 并入首行
    blocks = []
    blocks.append(_block("姓名", 100, 200, width=100))
    blocks.append(_block("年龄", 100, 500, width=100))
    for r, y in enumerate([160, 210, 260, 310]):
        blocks.append(_block(f"张三{r}", y, 200, width=100))
        blocks.append(_block(f"{20 + r}", y, 500, width=100))
    # 导出：表头行(浅橙)并入下方数据表(浅蓝)，合并为一个表格
    _export_table_page(
        "table_header_absorb.png", blocks,
        highlights=[(100, 130, "表头行(被并入)", (255, 220, 180)),
                    (160, 340, "数据行", (200, 240, 255))])
    out = D._group_text_into_tables(blocks)
    tables = [b for b in out if b.get("type") == "table"]
    assert len(tables) == 1
    tb = tables[0]
    assert len(tb["rows"]) == 5
    first = [c["text"] for c in tb["rows"][0]]
    assert first == ["姓名", "年龄"]


def test_group_text_into_tables_skips_scattered_text():
    # 普通正文段落（每行单块）不应被聚成表格
    blocks = [_block("第一句话，很重要很关键的正文内容。", 100, 100, width=900),
              _block("第二句话，继续阐述观点。", 140, 100, width=900),
              _block("第三句话，作为段落收尾。", 180, 100, width=900)]
    out = D._group_text_into_tables(blocks)
    assert not any(b.get("type") == "table" for b in out)
    assert len(out) == 3


# ================= 颜色：色相判定 =================

def test_rgb_to_hue_rejects_near_black():
    assert D._rgb_to_hue((30, 30, 30)) is None      # 黑
    assert D._rgb_to_hue((64, 52, 47)) is None      # 近黑暖偏（扫描噪声）
    assert D._rgb_to_hue((32, 34, 44)) is None      # 深底/暗色


def test_rgb_to_hue_rejects_low_saturation_gray():
    assert D._rgb_to_hue((180, 180, 180)) is None   # 灰
    assert D._rgb_to_hue((160, 150, 145)) is None   # 灰偏暖（低饱和）


def test_rgb_to_hue_detects_primary_hues():
    h_red = D._rgb_to_hue((232, 102, 122))          # 红
    assert h_red is not None and (h_red < 10 or h_red > 350)
    h_blue = D._rgb_to_hue((117, 148, 213))         # 蓝
    assert h_blue is not None and 200 < h_blue < 240
    h_green = D._rgb_to_hue((46, 178, 84))          # 绿
    assert h_green is not None and 120 < h_green < 160


# ================= 颜色：语义色吸附 =================

def test_snap_to_semantic_picks_nearest_standard():
    # 红队 / 蓝队股票 吸附到各自的语义标准色
    assert D._snap_to_semantic((232, 102, 122)) in ("lightcoral", "indianred", "salmon", "palevioletred")
    assert D._snap_to_semantic((117, 148, 213)) == "cornflowerblue"


def test_snap_none_and_dark_returns_none():
    assert D._snap_to_semantic(None) is None
    # 灰色/近黑吸附到最近语义色也有色值；但 hue 判定层(None)不会进入吸附流程
    assert D._rgb_to_hue((64, 52, 47)) is None


# ================= 颜色：聚类 =================

def test_cluster_rgbs_groups_near_duplicates():
    # 红色系若干微差采样 → 全部并为一簇
    reds = [(232, 102, 122), (230, 105, 120), (234, 100, 124)]
    clusters = D._cluster_rgbs(reds, thresh=9.0)
    assert len(clusters) == 1
    assert len(clusters[0]) == 3


def test_cluster_rgbs_separates_distinct_hues():
    # 红 vs 蓝 应分为两簇
    colors = [(232, 102, 122), (117, 148, 213), (230, 100, 120)]
    clusters = D._cluster_rgbs(colors, thresh=9.0)
    assert len(clusters) == 2


# ================= 颜色：blocks 整体吸附 _snap_blocks_colors =================

def _mk_color_block(text, y, rgb, kind="text"):
    b = {"type": kind, "text": text, "y_top": y, "y_bottom": y + 30,
         "x0": 100, "x1": 300, "bold": False, "centered": False,
         "para_start": True, "color_hue": D._rgb_to_hue(rgb),
         "color_rgb": list(rgb)}
    return b


def test_snap_blocks_colors_assigns_semantic_names():
    blocks = [_mk_color_block("格力", 100, (232, 102, 122)),
              _mk_color_block("传音", 140, (117, 148, 213))]
    out = D._snap_blocks_colors(blocks, snap=True, cluster=True)
    names = {b.get("color_name") for b in out if b.get("color_rgb")}
    assert "cornflowerblue" in names
    # 红队吸附后一定是红系语义名
    red = [b for b in out if b["text"] == "格力"][0]
    assert red["color_name"] in D.SEMANTIC_COLORS


def test_snap_blocks_colors_keeps_gray_uncolored():
    # 无彩色（hue None / rgb 灰）→ 不上色、不加 color_name
    blocks = [_block("黑字正文", 100, 100, hue=None, rgb=None)]
    out = D._snap_blocks_colors(blocks)
    assert out[0].get("color_rgb") is None
    assert out[0].get("color_name") is None


def test_snap_blocks_colors_table_cells():
    # table cell 中的彩色同样吸附 + 标语义名；无彩 cell 保持 None
    from ocr_book_to_md import detect as Dd
    cell_red = {"text": "格力", "color_rgb": [232, 102, 122]}
    cell_black = {"text": "双汇", "color_rgb": None}
    tb = {"type": "table", "y_top": 100,
          "rows": [[cell_red, cell_black]]}
    out = Dd._snap_blocks_colors([tb])
    tbl = out[0]
    c = tbl["rows"][0][0]
    assert c["color_name"] in D.SEMANTIC_COLORS
    assert c["color_rgb"] == list(D.SEMANTIC_COLORS[c["color_name"]])
    assert tbl["rows"][0][1]["color_rgb"] is None
