# -*- coding: utf-8 -*-
"""PDF 集成：以库方式调用 document-factory 生成 PDF。

ocr-book-to-md 的 `build` 产出 `output/content/article_N.md` + `images/`，
直接以 document-factory 的库函数 `create_pdf` 渲染（不再走子进程 CLI）。

依赖：需先安装 document-factory 包（pip install -e <document-factory 目录> 或
已发布到 PyPI 后 pip install document-factory），然后正常 import。

create_pdf 参数通过 **kwargs 透传，document-factory 新增参数无需在此重复定义。
"""

from document_factory.project import Project as DFProject
from document_factory.pdf_generator import create_pdf, compress_pdf

# config.yaml 的 pdf.* 键 → create_pdf 参数名 的映射（仅在未显式透传时补充）
_CONFIG_TO_CREATE = {
    'title': 'pdf.title',
    'subtitle': 'pdf.subtitle',
    'statement': 'pdf.statement',
    'engine': 'pdf.engine',
    'body_font_size': 'pdf.body_font_size',
    'chunk_articles': 'pdf.chunk_articles',
    'chunk_word_num': 'pdf.chunk_word_num',
    'chunk_size_mb': 'pdf.chunk_size_mb',
    'jobs': 'pdf.jobs',
    'numbered': 'pdf.numbered',
    'show_cover': 'pdf.show_cover',
    'show_date': 'pdf.show_date',
    'watermark': 'pdf.watermark',
    'mode': 'pdf.mode',
    'newest_volume_num': 'pdf.newest_volume_num',
    'cover_images': 'pdf.cover_images',
    'publications': 'pdf.publications',
}


def make_pdf(project, config, input_dir=None, output=None, compress=None, **kwargs):
    """以库方式调用 document-factory 生成 PDF。

    Args:
        project: Project 实例（ocr-book-to-md 的项目，路径下含 config.yaml + output/content）
        config: Config 实例（可为 None，此时标题等走 document_factory 自己读 config.yaml）
        input_dir: 内容输入目录。text 流默认 output/content/；img 流传 output/content_img/。
        output: 输出 PDF 路径。text 流默认 output/normal/<书名>.unc.pdf；img 流传 <书名>.img.unc.pdf。
        compress: 是否压缩。None 跟随 config.pdf.compress；False 禁用。
        **kwargs: 透传给 create_pdf 的任意参数（如 watermark、show_date、numbered、
            publications、shuffle/sand 等），优先级高于 config。document-factory
            新增参数时直接在此传，无需在此模块重复定义。
    Returns:
        document_factory create_pdf 返回的 PDF 路径列表。
    """
    df_project = DFProject(str(project.path))

    # 组装 create_pdf 参数：config 读取为基础，**kwargs 覆盖
    create_kwargs = {}
    if config is not None:
        for create_key, cfg_key in _CONFIG_TO_CREATE.items():
            if create_key in kwargs:
                continue
            v = config.get(cfg_key, None)
            if v is not None:
                create_kwargs[create_key] = v
    create_kwargs.update(kwargs)

    # cover_images：kwargs 未传时用 config；且要支持相对路径列表
    if 'cover_images' not in create_kwargs and config is not None:
        ci = config.get('pdf.cover_images', None)
        if ci:
            create_kwargs['cover_images'] = ci
    ci = create_kwargs.get('cover_images')
    if ci and isinstance(ci, str):
        create_kwargs['cover_images'] = [ci]

    # 显式 input_dir / output 覆盖
    if input_dir:
        create_kwargs['input_dir'] = input_dir
    if output is None:
        out_dir = project.output_dir / "normal"
        out_dir.mkdir(parents=True, exist_ok=True)
        output = str(out_dir / f"{project.path.name}.unc.pdf")
    create_kwargs['output_file'] = output

    # title 默认用项目名
    if not create_kwargs.get('title'):
        create_kwargs['title'] = project.path.name

    generated = create_pdf(df_project, **create_kwargs)
    if not generated:
        print("  ⚠ document-factory 未生成任何 PDF")
        return generated

    # 压缩：compress 参数 > config.pdf.compress
    if compress is False:
        return generated
    do_compress = compress
    if do_compress is None and config is not None:
        do_compress = config.get('pdf.compress', False)
    if do_compress:
        from document_factory.config import Config as DFConfig
        resolution = int(DFConfig(str(project.path)).get('compress.resolution', 100))
        for p in generated:
            compress_pdf(str(p), resolution=resolution)
    return generated
