# -*- coding: utf-8 -*-
"""API / CLI 显式参数契约测试。"""

import sys
from pathlib import Path

import pytest

from p2md import api, build_md, cli, config_file, detect, middleware, ocr


def test_api_passes_all_explicit_options(monkeypatch, tmp_path):
    images = tmp_path / "images"
    images.mkdir()
    work = tmp_path / "work"
    calls = {}

    def fake_ocr(images_dir, work_dir, start, end, **kwargs):
        calls["ocr"] = (images_dir, work_dir, start, end, kwargs)
        return str(images), start, end

    def fake_mw(work_dir, **kwargs):
        calls["mw"] = (work_dir, kwargs)

    def fake_detect(work_dir, img_dir, pages, **kwargs):
        calls["detect"] = (work_dir, img_dir, pages, kwargs)
        return "blocks", "figs", 0

    def fake_build(work_dir, chapters=None):
        calls["build"] = (work_dir, chapters)
        article = Path(work_dir) / "content"
        article.mkdir(parents=True, exist_ok=True)
        return "md", str(article), []

    monkeypatch.setattr(ocr, "run", fake_ocr)
    monkeypatch.setattr(middleware, "run_all", fake_mw)
    monkeypatch.setattr(detect, "detect", fake_detect)
    monkeypatch.setattr(build_md, "build", fake_build)

    result = api.convert_images(
        str(images), work_dir=str(work), engine="rapidocr", force=True,
        start=3, end=9, split_long_image_max_ratio=4.5,
        min_heading_ratio=1.1, heading_page_base=False,
        middlewares=["a.py"], ocr_fix_global=False, log=lambda _msg: None,
    )

    assert calls["ocr"][0] == str(images.resolve())
    assert calls["ocr"][1] == str(work.resolve())
    assert calls["ocr"][2:4] == (3, 9)
    assert calls["ocr"][4]["engine"] == "rapidocr"
    assert calls["ocr"][4]["force"] is True
    assert calls["ocr"][4]["split_long_image_max_ratio"] == 4.5
    assert calls["mw"][1]["middlewares"] == ["a.py"]
    assert calls["mw"][1]["ocr_fix_global"] is False
    assert calls["detect"][3]["min_heading_ratio"] == 1.1
    assert calls["detect"][3]["heading_page_base"] is False
    assert result.work_root == work.resolve()


def test_api_ignores_config_files(monkeypatch, tmp_path):
    images = tmp_path / "images"
    images.mkdir()
    (tmp_path / "config.yaml").write_text("p2md:\n  engine: tesseract\n", encoding="utf-8")
    (tmp_path / ".env").write_text("P2MD_ENGINE=tesseract\n", encoding="utf-8")
    seen = {}

    monkeypatch.setattr(
        ocr, "run",
        lambda images_dir, work_dir, start, end, **kwargs: (
            seen.setdefault("engine", kwargs["engine"]), images_dir, start, end)[1:],
    )
    monkeypatch.setattr(middleware, "run_all", lambda *a, **k: None)
    monkeypatch.setattr(detect, "detect", lambda *a, **k: ("b", "f", 0))

    def fake_build(work_dir, chapters=None):
        article = Path(work_dir) / "content"
        article.mkdir(parents=True, exist_ok=True)
        return "md", str(article), []

    monkeypatch.setattr(build_md, "build", fake_build)

    api.convert_images(str(images), work_dir=str(tmp_path / "work"),
                       log=lambda _msg: None)
    assert seen["engine"] is None


def test_api_rejects_missing_images(tmp_path):
    with pytest.raises(api.ConvertError, match="图片目录或文件不存在"):
        api.convert_images(str(tmp_path / "missing"))


def test_config_file_loader_aliases_and_base(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "p2md:\n"
        "  assets_dir: assets\n"
        "  work: work\n"
        "  output: final\n"
        "  start_page: 3\n"
        "  engine: rapidocr\n",
        encoding="utf-8",
    )
    values, base = config_file.load_config(str(path))
    assert values["images_dir"] == "assets"
    assert values["work_dir"] == "work"
    assert values["out_dir"] == "final"
    assert values["start"] == 3
    assert values["engine"] == "rapidocr"
    assert base == tmp_path


def test_cli_config_applies_and_flags_override(monkeypatch, tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "assets_dir: assets\n"
        "work: work\n"
        "output: final\n"
        "engine: rapidocr\n"
        "force: true\n"
        "bold_thickness_ratio: 0.082\n"
        "middlewares: [hooks/a.py]\n",
        encoding="utf-8",
    )
    captured = {}

    def fake_convert(images_dir, **kwargs):
        captured.clear()
        captured["images"] = images_dir
        captured.update(kwargs)

    monkeypatch.setattr(api, "convert_images", fake_convert)

    monkeypatch.setattr(sys, "argv", ["p2md", "pipeline", "--config", str(path)])
    cli.main()
    assert captured["images"] == str(tmp_path / "assets")
    assert captured["work_dir"] == str(tmp_path / "work")
    assert captured["out_dir"] == str(tmp_path / "final")
    assert captured["engine"] == "rapidocr"
    assert captured["force"] is True
    assert captured["bold_thickness_ratio"] == 0.082
    assert captured["middlewares"] == [str(tmp_path / "hooks/a.py")]

    monkeypatch.setattr(
        sys, "argv",
        ["p2md", "pipeline", "--config", str(path), "--engine", "swift", "--no-force"])
    cli.main()
    assert captured["engine"] == "swift"
    assert captured["force"] is False
