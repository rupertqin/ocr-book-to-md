#!/usr/bin/env python3
"""
ocr-book-to-md - 扫描书转 Markdown 的统一入口

把 `projects/<书名>/` 项目从截图 PNG 转换为结构化章节 Markdown，
并可调用 document-factory 生成 PDF。

用法:
    python main.py init  -p ./projects/进化心理学
    python main.py all   -p ./projects/进化心理学
    python main.py ocr/detect/build/pdf -p ./projects/进化心理学

    # 安装后可直接运行
    ocr-book-to-md all -p ./projects/进化心理学
"""

import sys
from pathlib import Path

# src 布局兼容：未安装时从 src/ 目录导入
try:
    from ocr_book_to_md.cli import main
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent / "src"))
    from ocr_book_to_md.cli import main

if __name__ == '__main__':
    main()
