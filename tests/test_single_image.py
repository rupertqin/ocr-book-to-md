# -*- coding: utf-8 -*-
"""单张图片输入：CLI / API / OCR 暂存页号。"""

import sys
from pathlib import Path

from PIL import Image, ImageDraw

from p2md import api, cli, ocr, paths


def _make_image(path: Path):
    im = Image.new("RGB", (60, 60), "white")
    ImageDraw.Draw(im).rectangle([8, 8, 52, 52], fill="black")
    im.save(path)


def test_convert_image_alias_passes_file_path(monkeypatch, tmp_path):
    image = tmp_path / "one.png"
    _make_image(image)
    captured = {}

    def fake_convert(images_dir, **kwargs):
        captured["images"] = images_dir
        captured.update(kwargs)

    monkeypatch.setattr(api, "convert_images", fake_convert)
    api.convert_image(str(image), work_dir=str(tmp_path / "work"))
    assert captured["images"] == str(image)


def test_cli_accepts_single_image(monkeypatch, tmp_path):
    image = tmp_path / "one.jpg"
    _make_image(image)
    captured = {}

    def fake_convert(images_dir, **kwargs):
        captured["images"] = images_dir
        captured.update(kwargs)

    monkeypatch.setattr(api, "convert_images", fake_convert)
    monkeypatch.setattr(
        sys, "argv",
        ["p2md", "pipeline", "--image", str(image),
         "--work", str(tmp_path / "work"), "--out", str(tmp_path / "output")])
    cli.main()
    assert captured["images"] == str(image.resolve())
    assert captured["work_dir"] == str((tmp_path / "work").resolve())
    assert captured["out_dir"] == str((tmp_path / "output").resolve())


def test_ocr_run_stages_single_image_as_page_one(monkeypatch, tmp_path):
    image = tmp_path / "one.png"
    _make_image(image)
    work = tmp_path / "work"
    seen = {}

    monkeypatch.setattr(ocr, "resolve_engine", lambda _engine: "rapidocr")

    def fake_run_ocr(img_dir, _txt_dir, _json_dir, _start, _end, force=False):
        seen["img_dir"] = img_dir
        assert (Path(img_dir) / "p1.jpg").is_file()

    monkeypatch.setattr(ocr, "run_ocr_rapidocr", fake_run_ocr)

    img_dir, start, end = ocr.run(str(image), str(work), engine="rapidocr")
    assert Path(seen["img_dir"]) == paths.ocr_tmp_dir(work)
    assert (paths.single_input_dir(work) / "p1.png").is_file()
    assert Path(img_dir) == paths.ocr_tmp_dir(work)
    assert (work / "_ocr_tmp" / "p1.jpg").is_file()
    assert (start, end) == (1, 1)
