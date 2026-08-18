# -*- coding: utf-8 -*-
"""项目管理模块 - 每个 `projects/<书名>/` 文件夹是一个独立项目（对齐 document-factory）。

项目目录约定:
    projects/<书名>/
    ├── config.yaml          # 项目配置（唯一配置来源）
    ├── .env                 # 敏感信息
    ├── data/images/         # 输入截图
    └── output/              # 全部产物
        ├── ocr/             # ① OCR 产物
        ├── blocks/          # ② 布局中间产物
        ├── figs/            # ② 裁图
        └── content/         # ③ 章节 Markdown + images（供 document-factory 读取）
"""

from pathlib import Path
from typing import Optional


class Project:
    """项目目录管理，封装 config/.env/数据/输出 路径。"""

    def __init__(self, path: str = "."):
        self.path = Path(path).resolve()
        self.config_file = self.path / "config.yaml"
        self.env_file = self.path / ".env"
        self.data_dir = self.path / "data"
        self.images_dir = self.path / "data" / "images"
        self.output_dir = self.path / "output"

    def __repr__(self):
        return f"Project({self.path})"

    def exists(self) -> bool:
        return self.path.exists()

    def ensure(self) -> None:
        self.path.mkdir(parents=True, exist_ok=True)
        self.images_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    # ---- output 子目录 ----
    def get_ocr_dir(self) -> Path:
        return self.output_dir / "ocr"

    def get_ocr_pages_dir(self) -> Path:
        return self.output_dir / "ocr" / "pages"

    def get_ocr_pages_json_dir(self) -> Path:
        return self.output_dir / "ocr" / "pages_json"

    def get_blocks_dir(self) -> Path:
        return self.output_dir / "blocks"

    def get_figs_dir(self) -> Path:
        return self.output_dir / "figs"

    def get_md_dir(self) -> Path:
        """章节 Markdown 的传统目录（build 中间产物，保留便于复核）。"""
        return self.output_dir / "md"

    def get_content_dir(self) -> Path:
        """text 流（文字版）document-factory 读取的输入目录：output/content/"""
        return self.output_dir / "content"

    def get_article_dir(self) -> Path:
        return self.output_dir / "content"

    def get_images_out_dir(self) -> Path:
        """text 流图片目录：output/content/images/"""
        return self.output_dir / "content" / "images"

    def get_content_img_dir(self) -> Path:
        """img 流（原图片）独立的 document-factory 输入目录：output/content_img/
        （与 text 流的 output/content/ 隔离，避免互相污染）"""
        return self.output_dir / "content_img"

    def get_broken_file(self) -> Path:
        return self.output_dir / "ocr" / "broken.txt"
