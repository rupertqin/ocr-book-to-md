# -*- coding: utf-8 -*-
"""组装章节 Markdown（迁移自 build_md.py）+ 产出 document-factory 可读的 article 目录。

章节边界从 blocks 的 heading 自动推导（"第X章"/"第X编"/"参考文献"）。

产物（project/output/ 下）：
    md/                         传统章节 Markdown（保留便于复核）
    content/article_N.md        每章一篇文章（供 document-factory pdf 读取）
    content/images/             裁图（article 内的图片引用统一改为 images/）
"""

import json
import os
import re
import shutil

CN = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
      "七": 7, "八": 8, "九": 9, "十": 10}


def cn2int(s):
    if not s:
        return 0
    if len(s) == 1:
        return CN.get(s, 0)
    if s.startswith("十"):
        return 10 + CN.get(s[1:], 0)
    if s.endswith("十"):
        return CN.get(s[0], 0) * 10
    return 0


def slugify(t):
    t = re.sub(r"[，。、；：！？（）“”\"'\s·—…\-]+", "", t)
    return t


def auto_chapters(blocks_dir):
    """从每页 blocks 的 heading 自动推导章节边界。"""
    files = sorted([f for f in os.listdir(blocks_dir) if f.endswith(".json")],
                   key=lambda f: int(f[:-5]))
    pages = [int(f[:-5]) for f in files]
    minp, maxp = pages[0], pages[-1]
    anchors = []
    # 记录每一页首个 heading（任意格式），用于"找不到第X章"时回退到"每章一节"
    first_heading_per_page = {}
    for n in pages:
        with open(os.path.join(blocks_dir, f"{n}.json"), encoding="utf-8") as f:
            data = json.load(f)
        heads = [h for h in data["blocks"] if h["type"] == "heading"]
        if not heads:
            continue
        merged = []
        for h in sorted(heads, key=lambda h: h["y_top"]):
            if merged and h["y_top"] - merged[-1]["y_bottom"] < 130 \
                    and merged[-1]["level"] == h["level"]:
                merged[-1]["text"] += " " + h["text"]
                merged[-1]["y_bottom"] = h["y_bottom"]
            else:
                merged.append(dict(h))
        first_heading_per_page[n] = merged[0]
        for h in merged:
            t = h["text"].strip()
            if re.match(r"^第[0-9]+章( |$)", t):
                anchors.append((n, "chapter", t))
                break
            if re.match(r"^第[一二三四五六七八九十]+编", t):
                anchors.append((n, "part_intro", t))
                break
            if t == "参考文献":
                anchors.append((n, "references", t))
                break
    anchors.sort()
    if not anchors:
        # 回退：书的章节不是"第X章/第X编/参考文献"格式（如"悖论系列N"等自定义标题），
        # 把每一页首个 heading 当一章，文件用 heading 文本命名。
        # 优先 level 小的（更大标题）；每页只取首个 heading。
        sorted_pages = sorted(first_heading_per_page.items(),
                              key=lambda kv: (kv[1]["level"], kv[0]))
        seen_titles = set()
        for n, h in sorted_pages:
            title = h["text"].strip()
            # 跳过页内重复标题（如重复页码）
            if title in seen_titles:
                continue
            seen_titles.add(title)
            anchors.append((n, "custom", title))
        anchors.sort()
        if not anchors:
            raise SystemExit("未找到任何章节标题页（blocks 里没有 heading），"
                             "检查 detect 是否检测到 heading block")
        print(f"  (未识别到第X章/第X编/参考文献，回退到按 heading 切分，共 {len(anchors)} 章)")

    segs = []
    first = anchors[0][0]
    if minp <= first - 1:
        segs.append(("front", minp, first - 1, "前言 · 版权 · 致谢"))
    for i, (n, kind, title) in enumerate(anchors):
        end = anchors[i + 1][0] - 1 if i + 1 < len(anchors) else maxp
        segs.append((kind, n, end, title))

    cn_num = {"一": "1", "二": "2", "三": "3", "四": "4", "五": "5",
              "六": "6", "七": "7", "八": "8", "九": "9", "十": "10"}
    chapters = []
    max_no = 0
    for kind, a, b, title in segs:
        if kind == "front":
            slug, fname, no = "00_前言", "00_前言.md", 0
        elif kind == "references":
            no = max_no + 1
            slug, fname = f"{no:02d}_参考文献", f"{no:02d}_参考文献.md"
        elif kind == "chapter":
            m = re.match(r"^第([0-9]+)章\s*(.*)$", title)
            no = int(m.group(1)) if m else 0
            name = m.group(2).strip() if m else ""
            if not name:
                name = title
            slug = f"{no:02d}_第{no}章_{slugify(name)}"
            fname = slug + ".md"
        elif kind == "part_intro":
            m = re.match(r"^第([一二三四五六七八九十]+)编\s*(.*)$", title)
            no = cn2int(m.group(1)) if m else 0
            name = m.group(2).strip() if m else ""
            if not name:
                name = title
            cn_txt = m.group(1) if m else ""
            slug = f"{no:02d}_第{cn_txt}编_{slugify(name)}" if m else f"{no:02d}_{slugify(name)}"
            fname = slug + ".md"
        else:  # custom: 自定义标题（"悖论系列N"等无第X章格式）
            max_no += 1
            no = max_no
            slug = f"{no:02d}_{slugify(title)}"
            fname = slug + ".md"
        max_no = max(max_no, no)
        chapters.append((kind, slug, fname, title, a, b))
    return chapters


def _load_blocks(blocks_dir, n):
    p = os.path.join(blocks_dir, f"{n}.json")
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)["blocks"]


def render_page(blocks_dir, n, skip_title=None):
    """渲染一页 blocks 为 Markdown。

    skip_title: 若给出标题文本，渲染时跳过第一个与之相同的 heading（用于避免
        与文件头 `# {title}` 重复，如自定义章节标题书的一二级 heading 重复）。
    """
    blocks = _load_blocks(blocks_dir, n)
    if not blocks:
        return ""
    out = []
    skip_done = skip_title is None
    sorted_blocks = sorted(blocks, key=lambda b: (b["y_top"], 1 if b["type"] == "image" else 0))
    merged = []
    for b in sorted_blocks:
        if (b["type"] == "heading" and merged and merged[-1]["type"] == "heading"
                and merged[-1]["level"] == b["level"]
                and b["y_top"] - merged[-1]["y_bottom"] < 130):
            merged[-1]["text"] += f" {b['text']}"
            merged[-1]["y_bottom"] = b["y_bottom"]
        else:
            merged.append(b)
    cur_para = []
    last_y = None
    for b in merged:
        if b["type"] == "image":
            if cur_para:
                out.append("\n".join(cur_para) + "\n\n")
                cur_para = []
            cap = b.get("caption", "").strip()
            alt = cap if cap else "插图"
            out.append(f"![{alt}](../figs/{b['file']})\n\n")
            last_y = b["y_bottom"]
        elif b["type"] == "heading":
            if not skip_done and b["text"].strip() == skip_title:
                # 跳过与文件头重复的章节标题（只跳第一个）
                skip_done = True
                last_y = b["y_bottom"]
                continue
            if cur_para:
                out.append("\n".join(cur_para) + "\n\n")
                cur_para = []
            level = b.get("level", 3)
            marker = "#" * level
            out.append(f"{marker} {b['text']}\n\n")
            last_y = b["y_bottom"]
        else:
            t = b["text"]
            if last_y is not None and b["y_top"] - last_y > 60:
                if cur_para:
                    out.append("\n".join(cur_para) + "\n\n")
                    cur_para = []
            cur_para.append(t)
            last_y = b["y_bottom"]
    if cur_para:
        out.append("\n".join(cur_para) + "\n\n")
    return "".join(out)


def _write_chapter(blocks_dir, md_tmp, spec):
    kind, slug, fname, title, start, end = spec
    p = os.path.join(md_tmp, fname)
    parts = [f"# {title}\n\n"]
    if kind == "front":
        for n in range(start, end + 1):
            if n in (10, 11, 12):
                continue
            if not _load_blocks(blocks_dir, n):
                continue
            parts.append(render_page(blocks_dir, n))
    elif kind == "part_intro":
        if _load_blocks(blocks_dir, start):
            parts.append(render_page(blocks_dir, start))
    else:
        for n in range(start, end + 1):
            if not _load_blocks(blocks_dir, n):
                continue
            # 渲染时跳过与该章 title 相同的第一个 heading，避免与文件头 # {title} 重复
            parts.append(render_page(blocks_dir, n, skip_title=title))
    with open(p, "w", encoding="utf-8") as f:
        f.write("".join(parts))
    return p


def _mirror_dir(src_dir, dst_dir):
    """把 src_dir 的内容镜像同步到 dst_dir：文件复制覆盖，目录递归同步，
    并删除 dst_dir 中 src_dir 没有的多余项（避免历史残留累积，如 images/images）。"""
    os.makedirs(dst_dir, exist_ok=True)
    src_names = set(os.listdir(src_dir))
    for f in src_names:
        s = os.path.join(src_dir, f)
        d = os.path.join(dst_dir, f)
        if os.path.isdir(s):
            _mirror_dir(s, d)
        else:
            shutil.copy2(s, d)
    for f in os.listdir(dst_dir):
        if f not in src_names:
            p = os.path.join(dst_dir, f)
            if os.path.isdir(p):
                shutil.rmtree(p)
            else:
                os.remove(p)


def _swap_in(tmp, dst):
    """把 tmp 内容移入 dst（覆盖同名），并删除 dst 中多出的旧文件/目录。

    支持子目录（如 images/）：用镜像同步递归合并，历史残留（如 images/images）会被清掉。
    """
    os.makedirs(dst, exist_ok=True)
    _mirror_dir(tmp, dst)
    try:
        shutil.rmtree(tmp)
    except OSError:
        pass


def _rewrite_fig_refs(md_text):
    """把 article 内的 `../figs/` 图片引用改为 `images/`（document-factory 约定）。"""
    return md_text.replace("../figs/", "images/")


def _clean_stale_tmp(output_dir):
    """清理 output 下历史遗留的临时目录（.md.tmp.* / .article.tmp.* 等）。

    正常流程里临时目录在 _swap_in 中会被清理；若某次运行中途失败会残留。
    这里在每次 build 前清除它们，避免越积越多。
    """
    out = str(output_dir)
    if not os.path.isdir(out):
        return
    for name in os.listdir(out):
        p = os.path.join(out, name)
        if os.path.isdir(p) and name.startswith((".md.tmp.", ".article.tmp.",
                                                 ".blocks.tmp.", ".figs.tmp.")):
            shutil.rmtree(p, ignore_errors=True)
            print(f"  清理历史临时目录: {p}")


def build(project, chapters=None):
    """组装章节 Markdown，并产出 document-factory 可读的 article 目录。

    Args:
        project: Project 实例
        chapters: 可选人工章节结构（list of dict，含 kind/slug/file/title/start/end）
    Returns:
        (md_dir, article_dir, chapters)
    """
    _clean_stale_tmp(project.output_dir)
    blocks_dir = str(project.get_blocks_dir())
    figs_dir = str(project.get_figs_dir())
    md_dir = str(project.get_md_dir())
    article_dir = str(project.get_article_dir())
    images_out = str(project.get_images_out_dir())

    if not os.path.isdir(blocks_dir):
        raise SystemExit(f"错误：未找到 blocks 目录 {blocks_dir}\n"
                         f"  请先运行布局分析：python main.py detect -p <project>")
    if not os.path.isdir(figs_dir):
        raise SystemExit(f"错误：未找到 figs 目录 {figs_dir}\n"
                         f"  请先运行布局分析：python main.py detect -p <project>")

    md_tmp = os.path.join(project.output_dir, f".md.tmp.{os.getpid()}")
    os.makedirs(md_tmp, exist_ok=True)

    if chapters is None:
        chapters = auto_chapters(blocks_dir)
        print("检测到的章节结构（自动推导，可人工复核）：")
        for c in chapters:
            print(f"  {c[0]:10} {c[4]:>4}-{c[5]:<4}  {c[1]}")
    else:
        print("使用人工章节结构")

    written = []
    for spec in chapters:
        if isinstance(spec, dict):
            spec = (spec["kind"], spec["slug"], spec["file"], spec["title"],
                    spec["start"], spec["end"])
        p = _write_chapter(blocks_dir, md_tmp, spec)
        written.append(spec)
        print(f"  {os.path.basename(p)}  ({os.path.getsize(p) // 1024} KB)")
    _swap_in(md_tmp, md_dir)

    # ---- 产出 document-factory 可读的 article 目录 ----
    # 每章 → article_N.md；图片引用 ../figs/ → images/；裁图复制到 content/images/
    article_tmp = os.path.join(project.output_dir, f".article.tmp.{os.getpid()}")
    images_tmp = os.path.join(article_tmp, "images")
    os.makedirs(article_tmp, exist_ok=True)
    os.makedirs(images_tmp, exist_ok=True)

    # 复制裁图
    if os.path.isdir(figs_dir):
        for f in os.listdir(figs_dir):
            if f.endswith(".png"):
                shutil.copyfile(os.path.join(figs_dir, f), os.path.join(images_tmp, f))

    # 每章写为 article_N.md（按章节顺序编号）
    for i, spec in enumerate(written):
        # 从 md_dir 读取对应章节文件
        fname = spec[2]
        src_path = os.path.join(md_dir, fname)
        if not os.path.exists(src_path):
            continue
        with open(src_path, encoding="utf-8") as f:
            content = f.read()
        content = _rewrite_fig_refs(content)
        with open(os.path.join(article_tmp, f"article_{i}.md"), "w", encoding="utf-8") as f:
            f.write(content)
    _swap_in(article_tmp, article_dir)

    n_articles = len(written)
    n_imgs = len([f for f in os.listdir(images_out) if f.endswith(".png")]) if os.path.isdir(images_out) else 0
    print(f"\n产出 {n_articles} 章 article + {n_imgs} 张图 → {article_dir}/（可直接交给 document-factory pdf）")
    return md_dir, article_dir, written
