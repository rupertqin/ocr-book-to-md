#!/usr/bin/env python
"""
p2md - 扫描书 / 长截图转 Markdown 的统一入口

把 `projects/<书名>/` 项目从截图或 PDF 转换为结构化章节 Markdown。

用法:
    python main.py init  -p ./projects/进化心理学
    python main.py pipeline -p ./projects/进化心理学
    python main.py ocr / detect / build -p ./projects/进化心理学

    # 安装后可直接运行
    p2md pipeline -p ./projects/进化心理学
"""

import sys
from pathlib import Path

# src 布局兼容：未安装时从 src/ 目录导入
try:
    from p2md.cli import main
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent / "src"))
    from p2md.cli import main

if __name__ == '__main__':
    main()
