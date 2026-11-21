# -*- coding: utf-8 -*-
"""OCR 引擎选择 / RapidOCR → 统一 schema 的单测（不依赖真实模型）。"""
import os
import sys

import pytest

from p2md import ocr as O


class _Result:
    def __init__(self, boxes, txts):
        self.boxes = boxes
        self.txts = txts


class _Cfg:
    def __init__(self, value=None, values=None):
        self.value = value
        self.values = values or {}

    def get(self, key, default=None):
        if self.values:
            return self.values.get(key, default)
        return self.value if self.value is not None else default

    def get_engine(self):
        return self.values.get('engine') if self.values else self.value


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
    assert lines[0]["y"] == pytest.approx(0.4)   # 1 - 60/100
    assert lines[0]["w"] == pytest.approx(0.5)
    assert lines[0]["h"] == pytest.approx(0.4)
    assert lines[1]["y"] == pytest.approx(0.0)   # 贴页底
    assert text == "第一行\n第二行\n"


def test_resolve_engine_platform_default(monkeypatch):
    monkeypatch.delenv("P2MD_ENGINE", raising=False)
    monkeypatch.delenv("P2MD_OCR_BACKEND", raising=False)
    monkeypatch.setattr(O.sys, "platform", "darwin")
    assert O.resolve_engine() == "swift"
    monkeypatch.setattr(O.sys, "platform", "linux")
    assert O.resolve_engine() == "rapidocr"


def test_resolve_engine_env_and_config(monkeypatch):
    monkeypatch.setattr(O.sys, "platform", "darwin")
    monkeypatch.setenv("P2MD_ENGINE", "rapidocr")
    assert O.resolve_engine(_Cfg(values={"engine": "swift"})) == "rapidocr"
    monkeypatch.delenv("P2MD_ENGINE", raising=False)
    assert O.resolve_engine(_Cfg(values={"engine": "rapidocr"})) == "rapidocr"
    assert O.resolve_engine(_Cfg(values={"engine": "auto"})) == "swift"


def test_resolve_engine_legacy_names(monkeypatch):
    monkeypatch.delenv("P2MD_ENGINE", raising=False)
    monkeypatch.delenv("P2MD_OCR_BACKEND", raising=False)
    monkeypatch.setattr(O.sys, "platform", "darwin")
    assert O.resolve_engine(_Cfg(values={"book.ocr_backend": "rapidocr"})) == "rapidocr"
    monkeypatch.setenv("P2MD_OCR_BACKEND", "swift")
    assert O.resolve_engine(_Cfg(values={"engine": "rapidocr"})) == "swift"


def test_resolve_engine_override_precedence(monkeypatch):
    monkeypatch.setattr(O.sys, "platform", "darwin")
    monkeypatch.setenv("P2MD_ENGINE", "rapidocr")
    assert O.resolve_engine(_Cfg(values={"engine": "rapidocr"}), override="swift") \
        == "swift"
    monkeypatch.delenv("P2MD_ENGINE", raising=False)
    assert O.resolve_engine(_Cfg(values={"engine": "rapidocr"}), override="AUTO") \
        == "swift"


def test_resolve_engine_invalid_value():
    with pytest.raises(SystemExit, match="未知 OCR 引擎"):
        O.resolve_engine(override="tesseract")


def test_cli_engine_flag():
    from p2md.cli import _parser

    parser = _parser()
    assert parser.parse_args(["ocr", "-p", ".", "--engine", "rapidocr"]).engine \
        == "rapidocr"
    assert parser.parse_args(["pipeline", "--engine", "swift"]).engine == "swift"


def test_config_p2md_namespace_and_legacy_fallback(tmp_path):
    from p2md.config import Config

    (tmp_path / "config.yaml").write_text(
        "p2md:\n"
        "  name: new\n"
        "  engine: rapidocr\n"
        "  force: true\n"
        "  start_page: null\n"
        "book:\n"
        "  name: old\n"
        "  start_page: 7\n"
        "detect:\n"
        "  min_heading_ratio: 2.0\n",
        encoding="utf-8",
    )
    cfg = Config(str(tmp_path))
    assert cfg.get_book_name() == "new"
    assert cfg.get_engine() == "rapidocr"
    assert cfg.get_force() is True
    assert cfg.get_start_page() is None      # 显式 null 不回退到 book.start_page
    assert cfg.get_p2md("min_heading_ratio") == 2.0   # 旧 detect 键兜底

    cfg._load_env = lambda: {"P2MD_ENGINE": "swift"}
    assert cfg.get_p2md("engine") == "swift"


def test_config_p2md_legacy_only(tmp_path):
    from p2md.config import Config

    (tmp_path / "config.yaml").write_text(
        "p2md:\n  force_ocr: true\n"
        "book:\n  name: old\ndetect:\n  middlewares: [a.py]\n"
        "pipeline:\n  pdf: layed\n",
        encoding="utf-8",
    )
    cfg = Config(str(tmp_path))
    assert cfg.get_book_name() == "old"
    assert cfg.get_force() is True
    assert cfg.get_p2md("middlewares") == ["a.py"]
    assert cfg.get_render_type_of_pdf() == "layed"
