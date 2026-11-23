# -*- coding: utf-8 -*-
"""p2md —— 图片（扫描页 / 长截图）转结构化 Markdown（本地、无 LLM）。

三段流水线（每步产物落盘、JSON 协议解耦）：
    ① ocr     OCR → <work>/ocr/（macOS 用 Swift Vision，其他平台用 RapidOCR）
    ② detect  图/标题/正文三分检测 → <work>/blocks + <work>/figs
    ③ build   组装章节 Markdown   → <work>/content/

产物到 Markdown 为止；``type`` 选还原方式：``normal``（默认，纯文本流）/
``layed``（版式还原，按 OCR 坐标定位）/ ``image``（每页原图一页，不做 OCR）。
"""

from .api import (
    ConvertError, ConvertResult, build_stage,
    convert, convert_image, convert_images,
)
from .restore import MD_TYPES, organize, prepare_image, prepare_layed, prepare_normal

__version__ = "0.6.0"

__all__ = [
    "ConvertError",
    "ConvertResult",
    "MD_TYPES",
    "build_stage",
    "convert",
    "convert_image",
    "convert_images",
    "organize",
    "prepare_layed",
    "prepare_image",
    "prepare_normal",
    "__version__",
]
