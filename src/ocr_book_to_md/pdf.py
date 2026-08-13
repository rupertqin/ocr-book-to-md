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


def _find_document_factory(config) -> Path:
    """定位 document-factory 的 main.py。

    优先级：config.pdf.document_factory > 环境变量 OCR_TO_MD_DF > 常见路径。
    """
    df = config.get_document_factory()
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


def make_pdf(project, config):
    """调用 document-factory 的 pdf 命令，把本项目 output/content/ 转成 PDF。

    返回生成的 PDF 路径列表（在 stdout 打印）。
    """
    df_dir = _find_document_factory(config)
    main_py = df_dir / "main.py"

    # 传给 document-factory 的命令行参数
    cmd = [sys.executable, str(main_py), "pdf", "-p", str(project.path)]
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
    output = config.get_pdf_output()
    if output:
        cmd += ["-o", output]

    print(">>", " ".join(cmd))
    r = subprocess.run(cmd, cwd=str(df_dir))
    if r.returncode != 0:
        raise SystemExit(f"document-factory pdf 执行失败（code {r.returncode}）")
    return r
