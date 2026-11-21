# -*- coding: utf-8 -*-
"""长图标题定位回归：正文行行高略大时不能抢走页顶标题。"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from p2md.build_md import _find_long_image_title_line  # noqa: E402


def _make_project(tmp_path, lines):
    out = tmp_path / "output"
    (out / "ocr" / "pages_json").mkdir(parents=True)
    (out / "blocks").mkdir(parents=True)
    (out / "ocr" / "pages_json" / "1.json").write_text(
        json.dumps({"page": 1, "lines": lines}, ensure_ascii=False), encoding="utf-8")
    (out / "blocks" / "1.json").write_text(
        json.dumps({"page": 1, "blocks": []}, ensure_ascii=False), encoding="utf-8")
    return str(out / "blocks")


def _line(text, y_top, h, w=0.5):
    # pages_json 的坐标是底边原点：y = 1 - y_top - h
    return {"text": text, "x": 0.0, "y": round(1 - y_top - h, 6), "w": w, "h": h}


def test_topmost_title_wins_over_taller_body_line(tmp_path):
    """么瑞 p101 现象：标题 h=0.0174，正文 h=0.0176（略大）却在下方。"""
    blocks_dir = _make_project(tmp_path, [
        _line("11月周复盘第2期（11.16~11.22）", 0.0058, 0.0174, 0.52),
        _line("隔夜美股大跌，纳指跌幅达到了惊人的5.28%", 0.0769, 0.0161, 0.98),
        _line("个多月跌幅达到20%之后，这份坚定就会有人先开始松动。再叠加纳指ETF", 0.1147, 0.0176, 0.98),
    ])
    hit = _find_long_image_title_line(blocks_dir, 1)
    assert hit is not None
    assert hit[0] == "11月周复盘第2期（11.16~11.22）"
    assert abs(hit[1] - 0.0058) < 1e-4
    assert abs(hit[2] - 0.0174) < 1e-4


def test_topmost_title_wins_over_much_taller_body_line(tmp_path):
    """奥特之父 p44 现象：正文行比标题高 18%，仍不能抢走标题。"""
    blocks_dir = _make_project(tmp_path, [
        _line("实战：股海老蟹带你一文读懂支撑位，阻力位", 0.0087, 0.0262, 0.71),
        _line("知乎：奥特之父", 0.0436, 0.0206, 0.20),
        _line("支撑位，阻力位，是大家经常面对的一个难题，尤其是波段选手", 0.0669, 0.0218, 0.93),
    ])
    hit = _find_long_image_title_line(blocks_dir, 1)
    assert hit is not None
    assert hit[0] == "实战：股海老蟹带你一文读懂支撑位，阻力位"


def test_header_label_and_digits_excluded(tmp_path):
    """冒号结尾的节标签、纯数字行被排除；无合格候选返回 None。"""
    blocks_dir = _make_project(tmp_path, [
        _line("上证指数：", 0.0050, 0.0180),
        _line("3764.15", 0.0300, 0.0090),
    ])
    assert _find_long_image_title_line(blocks_dir, 1) is None


def test_outside_top_zone_excluded(tmp_path):
    blocks_dir = _make_project(tmp_path, [
        _line("正文里的中文行", 0.50, 0.0300),
    ])
    assert _find_long_image_title_line(blocks_dir, 1) is None
