# -*- coding: utf-8 -*-
"""OCR 引擎选择 / RapidOCR → 统一 schema 的单测（不依赖真实模型）。"""
import pytest

from p2md import ocr as O


class _Result:
    def __init__(self, boxes, txts):
        self.boxes = boxes
        self.txts = txts


def test_convert_rapidocr_result_empty():
    assert O.convert_rapidocr_result(None, 100, 200) == ([], "")
    assert O.convert_rapidocr_result(_Result(None, None), 100, 200) == ([], "")


def test_convert_rapidocr_result_schema_and_order():
    result = _Result(
        [[[80, 20], [180, 20], [180, 60], [80, 60]],
         [[10, 90], [70, 90], [70, 120], [10, 120]]],
        ["第一行", "第二行"],
    )
    lines, text = O.convert_rapidocr_result(result, 200, 100)
    assert [ln["text"] for ln in lines] == ["第一行", "第二行"]
    assert lines[0]["x"] == pytest.approx(0.4)
    assert lines[0]["y"] == pytest.approx(0.4)
    assert lines[0]["w"] == pytest.approx(0.5)
    assert lines[0]["h"] == pytest.approx(0.4)
    assert lines[1]["y"] == pytest.approx(0.0)
    assert text == "第一行\n第二行\n"


def test_resolve_engine_platform_default(monkeypatch):
    monkeypatch.setattr(O.sys, "platform", "darwin")
    assert O.resolve_engine() == "swift"
    monkeypatch.setattr(O.sys, "platform", "linux")
    assert O.resolve_engine() == "rapidocr"


def test_resolve_engine_ignores_environment(monkeypatch):
    monkeypatch.setattr(O.sys, "platform", "darwin")
    monkeypatch.setenv("P2MD_ENGINE", "rapidocr")
    assert O.resolve_engine() == "swift"


def test_resolve_engine_explicit_override(monkeypatch):
    monkeypatch.setattr(O.sys, "platform", "darwin")
    assert O.resolve_engine("rapidocr") == "rapidocr"
    assert O.resolve_engine("AUTO") == "swift"


def test_resolve_engine_invalid_value():
    with pytest.raises(SystemExit, match="未知 OCR 引擎"):
        O.resolve_engine("tesseract")


def test_cli_explicit_flags():
    from p2md.cli import _parser

    parser = _parser()
    args = parser.parse_args([
        "ocr", "--images", "img", "--engine", "rapidocr", "--force",
        "--split-long-image-max-ratio", "4.5",
    ])
    assert args.images == "img"
    assert args.engine == "rapidocr"
    assert args.force is True
    assert args.split_long_image_max_ratio == 4.5

    pipe = parser.parse_args([
        "pipeline", "--images", "img", "--work", "w", "--out", "o",
        "--middleware", "a.py", "--middleware", "b.py",
    ])
    assert pipe.work == "w"
    assert pipe.out == "o"
    assert pipe.middleware == ["a.py", "b.py"]


def test_cli_has_no_project_or_init():
    from p2md.cli import _parser

    with pytest.raises(SystemExit):
        _parser().parse_args(["init", "-p", "."])
    with pytest.raises(SystemExit):
        _parser().parse_args(["ocr", "-p", "."])
