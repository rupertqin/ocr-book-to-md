# -*- coding: utf-8 -*-
"""工作目录内的产物路径派生（纯函数）。

所有阶段以 ``work_dir`` 为唯一中间产物根目录，
调用方只需传一个路径，子目录由此统一派生。
"""

from pathlib import Path


def work_path(work_dir: str | Path) -> Path:
    return Path(work_dir).expanduser().resolve()


def ocr_dir(work_dir: str | Path) -> Path:
    return work_path(work_dir) / "ocr"


def ocr_pages_dir(work_dir: str | Path) -> Path:
    return ocr_dir(work_dir) / "pages"


def ocr_pages_json_dir(work_dir: str | Path) -> Path:
    return ocr_dir(work_dir) / "pages_json"


def ocr_broken_file(work_dir: str | Path) -> Path:
    return ocr_dir(work_dir) / "broken.txt"


def ocr_tmp_dir(work_dir: str | Path) -> Path:
    return work_path(work_dir) / "_ocr_tmp"


def single_input_dir(work_dir: str | Path) -> Path:
    """单张图片输入的工作副本目录。"""
    return work_path(work_dir) / "_single_input"


def blocks_dir(work_dir: str | Path) -> Path:
    return work_path(work_dir) / "blocks"


def figs_dir(work_dir: str | Path) -> Path:
    return work_path(work_dir) / "figs"


def md_dir(work_dir: str | Path) -> Path:
    return work_path(work_dir) / "md"


def content_dir(work_dir: str | Path) -> Path:
    return work_path(work_dir) / "content"


def article_dir(work_dir: str | Path) -> Path:
    return content_dir(work_dir)


def images_dir(work_dir: str | Path) -> Path:
    return content_dir(work_dir) / "images"
