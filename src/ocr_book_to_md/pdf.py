# -*- coding: utf-8 -*-
"""PDF 集成：调用 document-factory 的 `pdf` 命令生成 PDF。

ocr-book-to-md 的 `build` 已经把章节产出为 `output/content/article_N.md` +
`output/content/images/`，这是 document-factory `pdf` 命令的标准输入格式。
本模块负责以子进程方式调用 document-factory 的 CLI，把 `-p` 指向本项目目录。
"""

import os
import subprocess
import sys
from pathlib import Path


def _find_document_factory(config=None) -> Path:
    """定位 document-factory 的 main.py。

    优先级：config.pdf.document_factory > 环境变量 OCR_TO_MD_DF > 常见路径。
    config 可为 None（imgpdf 场景），此时直接走环境变量/默认猜测。
    """
    df = config.get_document_factory() if config is not None else ""
    if not df:
        df = os.environ.get("OCR_TO_MD_DF", "")
    if not df:
        # 默认猜测：document-factory 与本项目同级目录
        here = Path(__file__).resolve().parent.parent.parent  # workspace root
        cand = here.parent / "document-factory"
        if (cand / "main.py").exists():
            df = str(cand)
    if not df:
        raise SystemExit(
            "错误：找不到 document-factory。请配置 config.yaml 的 pdf.document_factory "
            "或环境变量 OCR_TO_MD_DF 指向其 main.py 所在目录"
        )
    df_path = Path(df).resolve()
    main_py = df_path / "main.py"
    if not main_py.exists():
        raise SystemExit(f"错误：{main_py} 不存在，无法调用 document-factory")
    return df_path


def make_pdf(project, config, cover_images=None, input_dir=None, output=None):
    """调用 document-factory 的 pdf 命令，把本项目的内容目录转成 PDF。

    Args:
        project: Project 实例
        config: Config 实例（可为 None，此时跳过 config 相关的标题等参数）
        cover_images: 额外的 --cover-image 列表（路径相对项目根）。传 None 时
            从 config.pdf.cover_images 读取；否则用传入的列表。
        input_dir: document-factory 的输入目录（-i）。text 流默认读 output/content/；
            img 流传独立的 output/content_img/，避免污染 text 的 content。
        output: 显式输出 PDF 路径（-o）。img 流传 <书名>.img.unc.pdf，使
            document-factory 生成未压缩版时即带 .img 后缀，compress 后得到
            <书名>.img.pdf（保留 .img，不覆盖 text 的 <书名>.pdf）。
    Returns:
        subprocess 返回结果。
    """
    df_dir = _find_document_factory(config)
    main_py = df_dir / "main.py"

    # 传给 document-factory 的命令行参数
    cmd = [sys.executable, str(main_py), "pdf", "-p", str(project.path)]
    if input_dir:
        cmd += ["-i", str(input_dir)]

    if config is not None:
        title = config.get_pdf_title()
        if title:
            cmd += ["-t", title]
        subtitle = config.get_pdf_subtitle()
        if subtitle:
            cmd += ["-s", subtitle]
        statement = config.get_pdf_statement()
        if statement:
            cmd += ["--statement", statement]
        engine = config.get_pdf_engine()
        if engine:
            cmd += ["--engine", engine]
        chunk = config.get_pdf_chunk_articles()
        if chunk:
            cmd += ["--chunk-articles", str(chunk)]
        jobs = config.get_pdf_jobs()
        if jobs:
            cmd += ["-j", str(jobs)]
        mode = config.get_pdf_mode()
        cmd += ["-m", mode]

    # 显式 output 优先；否则用 config.pdf.output
    if output:
        cmd += ["-o", output]
    elif config is not None:
        output = config.get_pdf_output()
        if output:
            cmd += ["-o", output]

    # cover_images：优先命令行传入，否则读 config.pdf.cover_images
    if cover_images is None and config is not None:
        cover_images = config.get("pdf.cover_images", None)
    if cover_images:
        if isinstance(cover_images, str):
            cover_images = [cover_images]
        for rel in cover_images:
            cmd += ["--cover-image", str(rel)]

    print(">>", " ".join(cmd))
    r = subprocess.run(cmd, cwd=str(df_dir))
    if r.returncode != 0:
        raise SystemExit(f"document-factory pdf 执行失败（code {r.returncode}）")
    return r
