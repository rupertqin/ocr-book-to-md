# -*- coding: utf-8 -*-
"""p2md —— 图片（扫描页 / 长截图）转结构化 Markdown（本地、无 LLM）。

三段流水线（每步产物落盘、JSON 协议解耦）：
    ① ocr     OCR → <work>/ocr/（macOS 用 Swift Vision，其他平台用 RapidOCR）
    ② detect  图/标题/正文三分检测 → <work>/blocks + <work>/figs
    ③ build   组装章节 Markdown   → <work>/content/

接受图片输入，产出 Markdown 与裁图。
"""

from .api import (
    ConvertError, ConvertResult, build_stage,
    convert, convert_image, convert_images,
)

__version__ = "0.5.0"

__all__ = [
    "ConvertError",
    "ConvertResult",
    "build_stage",
    "convert",
    "convert_image",
    "convert_images",
    "__version__",
]
