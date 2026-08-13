# -*- coding: utf-8 -*-
"""ocr-book-to-md —— 扫描书转 Markdown 包。

把一个 `projects/<书名>/` 项目从截图 PNG 转换为结构化的章节 Markdown，
并可直接交给 document-factory 的 PDF 流程生成 PDF。

三段流水线（每步产物落盘、JSON 协议解耦）：
    ① ocr     Swift (macOS Vision) OCR  → output/ocr/
    ② detect  图/标题/正文三分检测       → output/blocks + output/figs
    ③ build   组装章节 Markdown         → output/content/article_N.md + images/
    ④ pdf     调用 document-factory     → 生成 PDF（可选）
"""

__version__ = "0.1.0"
