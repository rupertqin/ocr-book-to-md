# -*- coding: utf-8 -*-
"""p2md —— 扫描书 / 长截图转结构化 Markdown（本地、无 LLM）。

三段流水线（每步产物落盘、JSON 协议解耦）：
    ① ocr     OCR → output/ocr/（macOS 用 Swift Vision，其他平台用 RapidOCR）
    ② detect  图/标题/正文三分检测 → output/blocks + output/figs
    ③ build   组装章节 Markdown   → output/content/article_N.md + images/

功能边界：只产出 Markdown 与裁图，不生成 PDF，不依赖 document-factory。
"""

from .api import ConvertError, ConvertResult, convert, convert_images

__version__ = "0.2.0"

__all__ = [
    "ConvertError",
    "ConvertResult",
    "convert",
    "convert_images",
    "__version__",
]
