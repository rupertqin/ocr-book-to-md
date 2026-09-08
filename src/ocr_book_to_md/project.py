# -*- coding: utf-8 -*-
"""项目管理模块 - 每个 `projects/<书名>/` 文件夹是一个独立项目（对齐 document-factory）。

项目目录约定:
    projects/<书名>/
    ├── config.yaml          # 项目配置（唯一配置来源）
    ├── .env                 # 敏感信息
    ├── assets/              # 输入素材：截图（png/jpg…）或 PDF（.pdf，自动渲染成页图/提取文字）
    │   └── (历史 data/images/ 仍兼容，config book.images_dir 可覆盖)
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
        self.assets_dir = self.path / "assets"
        # 兼容：历史项目用 data/images，config book.images_dir 可显式覆盖
        self.data_dir = self.path / "data"
        self.images_dir = self.path / "data" / "images"
        self.output_dir = self.path / "output"

    def __repr__(self):
        return f"Project({self.path})"

    def exists(self) -> bool:
        return self.path.exists()

    def get_input_dir(self) -> Path:
        """输入素材目录：优先 assets，历史 data/images 存在时兼容回退。"""
        if self.assets_dir.is_dir() and any(self.assets_dir.iterdir()):
            return self.assets_dir
        if self.images_dir.is_dir():
            return self.images_dir
        return self.assets_dir

    def ensure(self) -> None:
        self.path.mkdir(parents=True, exist_ok=True)
        self.assets_dir.mkdir(parents=True, exist_ok=True)
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
