# -*- coding: utf-8 -*-
"""项目管理模块 - 每个 `projects/<书名>/` 文件夹是一个独立项目。

项目目录约定:
    projects/<书名>/
    ├── config.yaml          # 项目配置（唯一配置来源）
    ├── .env                 # 敏感信息
    ├── assets/              # 输入素材：截图（png/jpg…）或 PDF（.pdf，自动渲染成页图/提取文字）
    └── output/              # 全部产物
        ├── ocr/             # ① OCR 产物
        ├── blocks/          # ② 布局中间产物
        ├── figs/            # ② 裁图
        └── content/         # ③ 章节 Markdown + images
"""

from pathlib import Path
from typing import Optional


class Project:
    """项目目录管理，封装 config/.env/数据/输出 路径。"""

    def __init__(self, path: str = ".", output_dir: str | None = None,
                 assets_dir: str | None = None):
        """``path`` 是项目根（放 config.yaml / .env）。

        ``output_dir`` / ``assets_dir`` 可覆盖默认的 ``<path>/output`` 与 ``<path>/assets``：
        被别的项目当库调用时，中间产物要落在调用方指定的工作目录里，而不是污染项目根。
        """
        self.path = Path(path).resolve()
        self.config_file = self.path / "config.yaml"
        self.env_file = self.path / ".env"
        self.assets_dir = Path(assets_dir).resolve() if assets_dir else self.path / "assets"
        self.output_dir = Path(output_dir).resolve() if output_dir else self.path / "output"

    def __repr__(self):
        return f"Project({self.path})"

    def exists(self) -> bool:
        return self.path.exists()

    def get_input_dir(self) -> Path:
        """输入素材目录（assets/）：图片与 PDF 统一在这里解析。

        若想用 assets 之外的目录，在 config.yaml 配 book.assets_dir 覆盖（CLI
        组合优先级：--assets > book.assets_dir > 此处默认 assets/）。
        """
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
        """最终章节 Markdown 目录：output/content/"""
        return self.output_dir / "content"

    def get_article_dir(self) -> Path:
        return self.output_dir / "content"

    def get_images_out_dir(self) -> Path:
        """章节 Markdown 引用的裁图目录：output/content/images/"""
        return self.output_dir / "content" / "images"

    def get_content_img_dir(self) -> Path:
        """原图片流目录（保留兼容）：output/content_img/"""
        return self.output_dir / "content_img"

    def get_broken_file(self) -> Path:
        return self.output_dir / "ocr" / "broken.txt"
