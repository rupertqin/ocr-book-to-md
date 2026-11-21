#!/usr/bin/env python
"""
p2md - 图片（扫描页 / 长截图）转 Markdown 的统一入口

所有参数由命令行显式传入。

用法:
    python main.py pipeline --images ./assets --work ./work --out ./out
    python main.py ocr --images ./assets --engine rapidocr --force
    python main.py detect --work ./work
    python main.py build --work ./work

    # 安装后可直接运行
    p2md pipeline --images ./assets --work ./work
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
