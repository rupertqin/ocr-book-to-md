# -*- coding: utf-8 -*-
"""无子命令直接转换：终端输出、同目录写出、通配符批量。"""

import sys
from pathlib import Path

from PIL import Image, ImageDraw

from p2md import api, cli


def _make_image(path: Path, name: str):
    im = Image.new("RGB", (60, 60), "white")
    ImageDraw.Draw(im).rectangle([8, 8, 52, 52], fill="black")
    im.save(path)
    path.with_suffix(".md").unlink(missing_ok=True)
    return name


def _fake_convert(calls):
    def fake(image_path, **kwargs):
        work = Path(kwargs["work_dir"])
        content = work / "content"
        content.mkdir(parents=True, exist_ok=True)
        md = content / "article_0.md"
        md.write_text(f"# {Path(image_path).stem}\n\n正文\n", encoding="utf-8")
        calls.append((image_path, kwargs))
        return api.ConvertResult(markdowns=[md], pages=1)
    return fake


def test_direct_prints_markdown(monkeypatch, tmp_path, capsys):
    image = tmp_path / "one.jpg"
    _make_image(image, "one")
    calls = []
    monkeypatch.setattr(api, "convert_image", _fake_convert(calls))
    monkeypatch.setattr(sys, "argv", ["p2md", str(image)])

    cli.main()
    out = capsys.readouterr().out
    assert "# one" in out
    assert not (tmp_path / "one.md").exists()
    assert len(calls) == 1


def test_direct_o_writes_next_to_source(monkeypatch, tmp_path):
    image = tmp_path / "one.jpg"
    _make_image(image, "one")
    calls = []
    monkeypatch.setattr(api, "convert_image", _fake_convert(calls))
    monkeypatch.setattr(sys, "argv", ["p2md", "-o", str(image)])

    cli.main()
    assert (tmp_path / "one.md").read_text(encoding="utf-8").startswith("# one")
    assert len(calls) == 1


def test_direct_supports_glob(monkeypatch, tmp_path, capsys):
    for name in ("01.jpg", "02.jpg"):
        _make_image(tmp_path / name, name)
    calls = []
    monkeypatch.setattr(api, "convert_image", _fake_convert(calls))
    monkeypatch.setattr(sys, "argv", ["p2md", str(tmp_path / "*.jpg")])

    cli.main()
    out = capsys.readouterr().out
    assert "# 01" in out
    assert "# 02" in out
    assert len(calls) == 2


def test_direct_positional_dir_runs_book_pipeline(monkeypatch, tmp_path):
    """无子命令 + 目录 = 整本管线（convert_images），不是逐张。"""
    assets = tmp_path / "book"
    assets.mkdir()
    _make_image(assets / "01.jpg", "01")
    captured = {}

    def fake_convert(images_dir, **kwargs):
        captured["images"] = images_dir
        captured.update(kwargs)

    monkeypatch.setattr(api, "convert_images", fake_convert)
    monkeypatch.setattr(
        sys, "argv", ["p2md", str(assets), "--out", str(tmp_path / "out")])
    cli.main()
    assert captured["images"] == str(assets)
    assert captured["out_dir"] == str(tmp_path / "out")
