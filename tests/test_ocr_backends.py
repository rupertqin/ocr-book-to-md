# -*- coding: utf-8 -*-
"""OCR 后端选择 / RapidOCR → 统一 schema 的单测（不依赖真实模型）。"""
import os
import sys

import pytest

from p2md import ocr as O


class _Result:
    def __init__(self, boxes, txts):
        self.boxes = boxes
        self.txts = txts


class _Cfg:
    def __init__(self, value=None):
        self.value = value

    def get(self, key, default=None):
        return self.value if self.value is not None else default


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


def test_resolve_ocr_backend_platform_default(monkeypatch):
    monkeypatch.delenv("P2MD_OCR_BACKEND", raising=False)
    monkeypatch.setattr(O.sys, "platform", "darwin")
    assert O.resolve_ocr_backend() == "swift"
    monkeypatch.setattr(O.sys, "platform", "linux")
    assert O.resolve_ocr_backend() == "rapidocr"


def test_resolve_ocr_backend_env_and_config(monkeypatch):
    monkeypatch.setattr(O.sys, "platform", "darwin")
    monkeypatch.setenv("P2MD_OCR_BACKEND", "rapidocr")
    assert O.resolve_ocr_backend(_Cfg("swift")) == "rapidocr"
    monkeypatch.delenv("P2MD_OCR_BACKEND", raising=False)
    assert O.resolve_ocr_backend(_Cfg("rapidocr")) == "rapidocr"
    assert O.resolve_ocr_backend(_Cfg("auto")) == "swift"
