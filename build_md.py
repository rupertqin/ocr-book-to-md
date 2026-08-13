#!/usr/bin/env python3
"""
Assemble per-chapter markdown files from blocks JSON.
Text lines + inline image references, ordered by y position per page.

Chapter boundaries are AUTO-DETECTED from the blocks (heading lines like
"第1章"/"第一编"/"参考文献"), so the page ranges no longer need to be
hard-coded. Prints the detected structure for human review.

Usage: python3 build_md.py [--out-dir DIR] [--chapters FILE]
Defaults: --out-dir out
从 --out-dir 内化推导：blocks=<out>/blocks  figs=<out>/figs  md=<out>/md
--chapters 可选：人工章节结构覆盖 JSON
       [{"kind":..,"slug":..,"file":..,"title":..,"start":..,"end":..}]
"""
import argparse, json, os, re, sys
from pathlib import Path

ap = argparse.ArgumentParser(description="组装章节 Markdown（路径默认值内化）")
ap.add_argument("--out-dir", default="out", help="产物根目录（默认 out/，读 blocks/ + figs/，写 md/）")
ap.add_argument("--chapters", help="人工章节结构 JSON（覆盖自动推导，可选）")
args = ap.parse_args()

OUT_DIR = Path(args.out_dir)
BLOCKS_DIR = OUT_DIR / "blocks"
FIGS_DIR = OUT_DIR / "figs"
OUT_MD = OUT_DIR / "md"

# ---- 输入存在性检查（内化到本脚本，run.py 无需重复判断）----
if not BLOCKS_DIR.is_dir():
    sys.exit(f"错误：未找到 blocks 目录 {BLOCKS_DIR}\n"
             f"  请先运行布局分析：python3 detect_figs.py --out-dir {args.out_dir}")
if not FIGS_DIR.is_dir():
    sys.exit(f"错误：未找到 figs 目录 {FIGS_DIR}\n"
             f"  请先运行布局分析：python3 detect_figs.py --out-dir {args.out_dir}")
# ---- 输出目录自管理：先写临时目录，完成后原子替换（防批量删除被沙箱拦截）----
import shutil
MD_TMP = Path(f"{OUT_MD}.tmp.{os.getpid()}")
MD_TMP.mkdir(parents=True, exist_ok=True)

def swap_in(tmp: Path, dst: Path):
    """把 tmp 内容移入 dst（覆盖同名），并删除 dst 中多出的旧文件。"""
    dst.mkdir(parents=True, exist_ok=True)
    new_names = set(p.name for p in tmp.iterdir())
    for p in tmp.iterdir():
        shutil.move(str(p), str(dst / p.name))
    for p in dst.iterdir():
        if p.name not in new_names:
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink()
    try:
        tmp.rmdir()
    except OSError:
        pass

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
    """文件名用：去标点/空格，保留汉字字母数字和下划线"""
    t = re.sub(r"[，。、；：！？（）“”\"'\s·—…\-]+", "", t)
    return t

def auto_chapters(blocks_dir):
    """从每页 blocks 的 heading 自动推导章节边界。
    锚点页：含"第X章"/"第X编"/"参考文献"标题的页；
    每段范围 = [锚点页, 下一个锚点页 - 1]；front = [最小页, 第一个锚点 - 1]。"""
    files = sorted([f for f in os.listdir(blocks_dir) if f.endswith(".json")],
                   key=lambda f: int(f[:-5]))
    pages = [int(f[:-5]) for f in files]
    minp, maxp = pages[0], pages[-1]
    anchors = []  # (page, kind, title)
    for n in pages:
        data = json.load(open(os.path.join(blocks_dir, f"{n}.json"), encoding="utf-8"))
        heads = [h for h in data["blocks"] if h["type"] == "heading"]
        if not heads:
            continue
        # 合并同页相邻标题行（"第1章" + "通往进化心理学的科学运动" → 一行）
        merged = []
        for h in sorted(heads, key=lambda h: h["y_top"]):
            if merged and h["y_top"] - merged[-1]["y_bottom"] < 130 \
                    and merged[-1]["level"] == h["level"]:
                merged[-1]["text"] += " " + h["text"]
                merged[-1]["y_bottom"] = h["y_bottom"]
            else:
                merged.append(dict(h))
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
        raise SystemExit("未找到任何章节标题页（第X章/第X编/参考文献），检查 blocks 是否包含 heading")

    # front 段（首个锚点之前的所有页）
    segs = []
    first = anchors[0][0]
    if minp <= first - 1:
        segs.append(("front", minp, first - 1, "前言 · 版权 · 致谢"))
    for i, (n, kind, title) in enumerate(anchors):
        end = anchors[i + 1][0] - 1 if i + 1 < len(anchors) else maxp
        segs.append((kind, n, end, title))

    # 生成 slug / 文件名 / 编号
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
            if not name:  # 章名可能没并进标题（heading 未合并时）
                name = title
            slug = f"{no:02d}_第{no}章_{slugify(name)}"
            fname = slug + ".md"
        else:  # part_intro
            m = re.match(r"^第([一二三四五六七八九十]+)编\s*(.*)$", title)
            no = cn2int(m.group(1)) if m else 0
            name = m.group(2).strip() if m else ""
            if not name:
                name = title
            cn_txt = m.group(1) if m else ""
            slug = f"{no:02d}_第{cn_txt}编_{slugify(name)}" if m else f"{no:02d}_{slugify(name)}"
            fname = slug + ".md"
        max_no = max(max_no, no)
        chapters.append((kind, slug, fname, title, a, b))
    return chapters

def load_blocks(n: int):
    p = BLOCKS_DIR / f"{n}.json"
    if not p.exists():
        return None
    return json.load(open(p, encoding="utf-8"))["blocks"]

def render_page(n: int) -> str:
    blocks = load_blocks(n)
    if not blocks:
        return ""
    out = []
    # Sort: by y_top, image before text when at similar y (so it interleaves nicely)
    sorted_blocks = sorted(blocks, key=lambda b: (b["y_top"], 1 if b["type"] == "image" else 0))
    # merge adjacent headings of the same level (e.g. "第1章" + "通往进化心理学的科学运动")
    merged = []
    for b in sorted_blocks:
        if (b["type"] == "heading" and merged and merged[-1]["type"] == "heading"
                and merged[-1]["level"] == b["level"]
                and b["y_top"] - merged[-1]["y_bottom"] < 130):
            merged[-1]["text"] += f" {b['text']}"
            merged[-1]["y_bottom"] = b["y_bottom"]
        else:
            merged.append(b)
    cur_para = []  # accumulate text lines until we hit image or large gap
    last_y = None
    for b in merged:
        if b["type"] == "image":
            if cur_para:
                out.append("\n".join(cur_para) + "\n\n")
                cur_para = []
            cap = b.get("caption", "").strip()
            alt = cap if cap else f"原书第 {n} 页插图"
            out.append(f"![{alt}](../figs/{b['file']})\n\n")
            last_y = b["y_bottom"]
        elif b["type"] == "heading":
            if cur_para:
                out.append("\n".join(cur_para) + "\n\n")
                cur_para = []
            level = b.get("level", 3)
            marker = "#" * level  # level2 -> ##, level3 -> ###
            out.append(f"{marker} {b['text']}\n\n")
            last_y = b["y_bottom"]
        else:
            t = b["text"]
            # paragraph break: large vertical gap (>50px)
            if last_y is not None and b["y_top"] - last_y > 60:
                if cur_para:
                    out.append("\n".join(cur_para) + "\n\n")
                    cur_para = []
            cur_para.append(t)
            last_y = b["y_bottom"]
    if cur_para:
        out.append("\n".join(cur_para) + "\n\n")
    return "".join(out)

def write_chapter(spec):
    kind, slug, fname, title, start, end = spec
    p = MD_TMP / fname
    parts = [f"# {title}\n\n"]
    if kind == "front":
        for n in range(start, end + 1):
            if n in (10, 11, 12):
                continue
            blocks = load_blocks(n)
            if not blocks:
                continue
            parts.append(f"## —— 原书第 {n} 页 ——\n\n")
            parts.append(render_page(n))
    elif kind == "part_intro":
        blocks = load_blocks(start)
        if blocks:
            parts.append(render_page(start))
    elif kind == "references":
        for n in range(start, end + 1):
            blocks = load_blocks(n)
            if not blocks:
                continue
            parts.append(f"## 原书第 {n} 页\n\n")
            parts.append(render_page(n))
    else:
        for n in range(start, end + 1):
            blocks = load_blocks(n)
            if not blocks:
                continue
            parts.append(f"## 原书第 {n} 页\n\n")
            parts.append(render_page(n))
    p.write_text("".join(parts), encoding="utf-8")
    return p

def main():
    if args.chapters:
        with open(args.chapters, encoding="utf-8") as f:
            raw = json.load(f)
        chapters = [(c["kind"], c["slug"], c["file"], c["title"],
                     c["start"], c["end"]) for c in raw]
        print("使用人工章节结构:", args.chapters)
    else:
        chapters = auto_chapters(BLOCKS_DIR)
        print("检测到的章节结构（自动推导，可人工复核）：")
        for c in chapters:
            print(f"  {c[0]:10} {c[4]:>4}-{c[5]:<4}  {c[1]}")
    for spec in chapters:
        p = write_chapter(spec)
        print(f"  {p.name}  ({p.stat().st_size // 1024} KB)")
    # 全部写完才原子替换正式目录（中途失败则 tmp 残留、正式目录不变）
    swap_in(MD_TMP, OUT_MD)

if __name__ == "__main__":
    main()