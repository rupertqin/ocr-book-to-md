#!/usr/bin/env python3
"""
一键转换：书的截图目录 → 带图片的 Markdown 集合

用法:
    python3 run.py                          # 默认 data/images → out/
    python3 run.py --images 别书 --out 别处
    python3 run.py --force-ocr              # 忽略 OCR 缓存，强制重跑

流程（子脚本各自负责路径默认值与输入存在性检查，本文件只做编排）:
    ① 若无 OCR 缓存 → 编译并运行 ocr_batch.swift，产物进 <out>/ocr/
    ② detect_figs.py --img-dir <images> --out-dir <out>   → out/blocks + out/figs
    ③ build_md.py   --out-dir <out>                        → out/md

输出（--out 目录为自包含集合，可直接拷贝/发布）:
    out/
    ├── md/       章节 Markdown（正文 + 标题 + 图片引用，最终交付）
    ├── figs/     插图与表格裁图（被 md 引用）
    ├── blocks/   每页结构化元素 JSON（中间）
    └── ocr/      OCR 产物（中间，可删除；删后重跑自动重新生成）
"""
import argparse, os, re, shutil, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))

def pick_python():
    """子脚本解释器：优先用户 PATH 里的 python（用户偏好），
    但要求能 import PIL/numpy（detect_figs.py 依赖）；都不行才回退项目环境。"""
    for cand in ("python", "python3"):
        exe = shutil.which(cand)
        if not exe:
            continue
        try:
            r = subprocess.run([exe, "-c", "import PIL, numpy"],
                               capture_output=True, timeout=15)
            if r.returncode == 0:
                print(f"子脚本解释器: {exe} (PATH 优先)")
                return exe
        except Exception:
            pass
    managed = "/Users/rupert/.workbuddy/binaries/python/envs/default/bin/python3"
    if os.path.exists(managed):
        print(f"PATH 中的 python 缺少 PIL/numpy，回退项目环境: {managed}")
        return managed
    return sys.executable

PY = pick_python()

def run(cmd, **kw):
    print(">>", " ".join(cmd))
    subprocess.run(cmd, check=True, **kw)

SEGMENT = 30          # 每次调用 Swift 的页数段（Vision 批量处理有内存累积 SIGBUS 风险）
SIGBUS = -10          # 128 + SIGBUS(10) = 138，subprocess 返回 -10

def page_done(json_dir, n):
    """该页 OCR 结果是否已存在"""
    return os.path.isfile(os.path.join(json_dir, f"{n}.json"))

def run_ocr_resilient(bin_path, img_dir, txt_dir, json_dir, start, end, reuse_broken=False):
    """分段调用 Swift OCR，捕获 SIGBUS 崩溃并二分定位崩溃页、跳过继续。

    Vision 框架对少量图片有底层 SIGBUS bug（单页/大批量都可能崩，无法靠 Swift
    侧规避）。这里用 Python 外层兜底：每段 SEGMENT 页调一次二进制，段内 SIGBUS
    就用二分法定位到"崩在哪一页"，把该页记入 <out>/ocr/broken.txt 并跳过，
    从下一页继续，直到全部页都跑完或全被跳过。

    reuse_broken=True 时复用上次的 broken.txt（跳过已知崩溃页）；False（默认）
    则忽略旧记录，重新尝试所有缺失页（适合内存好转后补页）。
    """
    broken = os.path.join(json_dir, "broken.txt")
    skipped_pages = set()
    if reuse_broken and os.path.isfile(broken):
        with open(broken, encoding="utf-8") as f:
            skipped_pages = {int(x) for x in f.read().split() if x.strip()}

    def img_exists(p):
        return os.path.isfile(os.path.join(img_dir, f"p{p}.jpg"))

    def seg_run(a, b):
        """跑 [a,b]，返回 (成功页列表, 是否异常)。
        异常 = 段内"有源图且未处理"的页无任何产出（SIGBUS 或 Vision 静默失败）；
        源图缺失的页（ocr_batch 会 [skip]）不算失败。"""
        want = [p for p in range(a, b + 1)
                if not page_done(json_dir, p) and img_exists(p)]
        if not want:
            return [], False
        r = subprocess.run([bin_path, img_dir, txt_dir, json_dir, str(a), str(b)],
                           capture_output=True, text=True)
        sys.stdout.write(r.stdout); sys.stdout.flush()
        sys.stderr.write(r.stderr); sys.stderr.flush()
        got = [p for p in want if page_done(json_dir, p)]
        new_ok = sorted(got)
        # 只要还有本应处理的页没产出，无论退出码，都视为失败（可能静默失败）
        failed = len(got) < len(want)
        if failed:
            print(f"  [!] 段 p{a}..p{b} 未完成：{len(want)} 页产出 {len(got)}（code {r.returncode}）")
        return new_ok, failed

    # 待处理页：范围里还没结果、且不在已知崩溃清单里
    todo = [p for p in range(start, end + 1)
            if not page_done(json_dir, p) and p not in skipped_pages]
    print(f"待 OCR 页: {len(todo)} 张（已缓存跳过）")

    i = 0
    while i < len(todo):
        a = todo[i]
        # 向前凑够 SEGMENT 页
        b = a
        while i + 1 < len(todo) and todo[i + 1] - b <= 3 and (b - a) < SEGMENT - 1:
            b = todo[i + 1]
            i += 1
        i += 1  # 移到下一段首

        _, crashed = seg_run(a, b)
        if not crashed:
            continue
        # 崩溃：二分定位崩溃页
        lo, hi = a, b
        while lo < hi:
            mid = (lo + hi) // 2
            # 跑左半段；若左半本身有已缓存页，Swift 会跳过，无碍
            mid_ok, mid_crashed = seg_run(lo, mid)
            if mid_crashed:
                hi = mid           # 崩溃页在 [lo, mid]
            else:
                lo = mid + 1       # 左半正常，崩溃页在 (mid, hi]
        crash_page = lo
        if not page_done(json_dir, crash_page) and img_exists(crash_page):
            # 确认该页自身确实崩（单页跑一次，若也崩则记为坏页跳过）
            single_ok, single_crashed = seg_run(crash_page, crash_page)
            if single_crashed and not page_done(json_dir, crash_page):
                print(f"  [!] p{crash_page} 触发 Vision SIGBUS，已跳过（记录到 broken.txt）")
                skipped_pages.add(crash_page)
                with open(broken, "a", encoding="utf-8") as f:
                    f.write(f"{crash_page}\n")
            else:
                # 单页不崩 → 是批量内存累积，推进一页再继续
                print(f"  [?] 批量崩溃定位到 p{crash_page}（单页正常，跳过该页续跑）")
                skipped_pages.add(crash_page)
                with open(broken, "a", encoding="utf-8") as f:
                    f.write(f"{crash_page}\n")
        # 下一段从 crash_page 之后重新收集待处理页
        todo = [p for p in range(start, end + 1)
                if not page_done(json_dir, p) and p not in skipped_pages]
        i = 0

    with open(broken, "w", encoding="utf-8") as f:
        f.write(" ".join(str(x) for x in sorted(skipped_pages)))
    if skipped_pages:
        print(f"本次 OCR 跳过（Vision 崩溃）: {sorted(skipped_pages)}")
    else:
        print("OCR 全部完成，无崩溃页。")

PAGE_RE = re.compile(r"(\d+)")

def scan_pages(images_dir):
    """扫描截图目录，从文件名提取页号，返回 {页号: 文件名} 映射。
    支持任意命名格式（p3.png / screenshot_page_3.png / img_003.jpg…），
    只要求文件名里含一个数字且可排序。无匹配返回 {}。"""
    pages = {}
    if not os.path.isdir(images_dir):
        return pages
    for f in os.listdir(images_dir):
        m = PAGE_RE.search(f)
        if m:
            pages[int(m.group(1))] = f
    return pages

def auto_page_range(pages):
    """从 {页号: 文件名} 映射探测页码范围 (min, max)，空映射返回 None"""
    return (min(pages), max(pages)) if pages else None

def prepare_images(images_dir, pages, out):
    """把输入图片统一归一化为 JPEG，输出到 <out>/_ocr_tmp/。
    返回临时图片目录；Swift 从该目录读取（统一 p{n}.jpg 命名）。

    工程背景：macOS 的 ImageIO 解码「任何 PNG」都必崩（SIGBUS，与磁盘/Alpha/内容
    无关，已多重对照实验证实），而解码 JPEG 绝对稳定。故这里把源图（PNG 或 JPEG
    伪装）统一归一到 JPEG，彻底消除对 ImageIO PNG 解码的依赖。已是 JPEG 的页直接
    复制（保持像素不变，避免二次有损压缩改变图区检测），真 PNG 才转 JPEG。
    pages: {页号: 源文件名} 映射，支持任意命名格式。"""
    tmp = os.path.join(out, "_ocr_tmp")
    os.makedirs(tmp, exist_ok=True)
    from PIL import Image
    import shutil as _sh
    converted = copied = 0
    for n, fname in sorted(pages.items()):
        src = os.path.join(images_dir, fname)
        dst = os.path.join(tmp, f"p{n}.jpg")
        if not os.path.isfile(src):
            continue
        if os.path.isfile(dst) and os.path.getmtime(dst) >= os.path.getmtime(src):
            continue
        # 已是 JPEG（含 .png 扩展名但内容为 JPEG 的页）：直接复制，保持像素不变
        fmt = Image.open(src).format
        if fmt == "JPEG":
            _sh.copyfile(src, dst)
            copied += 1
            continue
        # 非 JPEG（真 PNG / 其他）：ImageIO 解 PNG 必崩，统一转 JPEG 规避
        im = Image.open(src)
        if im.mode == "RGBA":
            bg = Image.new("RGB", im.size, (255, 255, 255, 255))
            bg.paste(im, mask=im.split()[3])  # 合成白底，丢弃透明通道
            im = bg
        elif im.mode != "RGB":
            im = im.convert("RGB")
        im.save(dst, "JPEG", quality=95)
        converted += 1
    print(f"图片预处理完成：{copied} 张 JPEG 直接复制 + {converted} 张转 JPEG（临时目录 {tmp}）")
    return tmp

def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--images", default="data/images",
                    help="书的截图目录（默认 data/images；文件名任意，自动提取数字页号）")
    ap.add_argument("--out", default="out",
                    help="输出目录（默认 out/，生成 md/ + figs/ 集合）")
    ap.add_argument("--start", type=int, help="起始页（默认自动探测）")
    ap.add_argument("--end", type=int, help="结束页")
    ap.add_argument("--force-ocr", action="store_true", help="忽略 ocr 缓存，强制重跑 OCR")
    args = ap.parse_args()

    out = os.path.abspath(args.out)
    os.makedirs(out, exist_ok=True)
    ocr_cache = os.path.join(out, "ocr")
    os.makedirs(ocr_cache, exist_ok=True)
    bin_path = os.path.join(ocr_cache, "ocr_batch")
    if not os.path.exists(bin_path):
        run(["swiftc", "-o", bin_path, os.path.join(HERE, "ocr_batch.swift")])

    # 自动扫描源图目录：从文件名提取数字页号 → {页号: 文件名}，任意命名均可
    pages = scan_pages(args.images)
    if not pages:
        sys.exit(f"错误：截图目录 {args.images} 里找不到含数字页号的文件")
    rng = auto_page_range(pages)
    start = args.start if args.start is not None else rng[0]
    end = args.end if args.end is not None else rng[1]
    print(f"检测到 {len(pages)} 页，OCR 范围 p{start}..p{end}")

    # 统一预处理：所有源图 → JPEG 临时目录（规避 ImageIO 解 PNG 必崩；
    # 同时把任意命名的源图归一化为 p{n}.jpg，供 ocr_batch 与 detect_figs 共用）
    img_for_ocr = prepare_images(args.images, pages, out)

    # ---------- ① OCR：无缓存才跑，产物固定进 <out>/ocr/ ----------
    if os.path.isdir(os.path.join(ocr_cache, "pages_json")) and not args.force_ocr:
        print("复用 OCR 缓存:", ocr_cache)
    else:
        print("运行 OCR（macOS Vision, zh-Hans + en）...")
        os.makedirs(os.path.join(ocr_cache, "pages"), exist_ok=True)
        os.makedirs(os.path.join(ocr_cache, "pages_json"), exist_ok=True)
        run_ocr_resilient(bin_path, img_for_ocr,
                           os.path.join(ocr_cache, "pages"),
                           os.path.join(ocr_cache, "pages_json"),
                           start, end, reuse_broken=False)

    # ---------- ② 检测 + ③ 组装（路径/检查内化在子脚本） ----------
    # detect_figs 读图：若原图目录用 pN.png 命名（可被 detect_figs 直接读且像素无损），
    # 用原图目录；否则用预处理目录（统一 pN.jpg 命名 + JPEG）。
    if all(pages.get(n) == f"p{n}.png" for n in pages):
        detect_img = args.images
    else:
        detect_img = img_for_ocr
    run([PY, os.path.join(HERE, "detect_figs.py"),
         "--img-dir", detect_img, "--out-dir", out])
    run([PY, os.path.join(HERE, "build_md.py"), "--out-dir", out])

    md_dir = os.path.join(out, "md")
    figs_dir = os.path.join(out, "figs")
    n_md = len([f for f in os.listdir(md_dir) if f.endswith(".md")])
    n_figs = len(os.listdir(figs_dir))
    print(f"\n完成：{n_md} 章 Markdown + {n_figs} 张图 → {out}/")
    print("  md/    章节 Markdown（最终交付）")
    print("  figs/  插图与表格裁图")
    print("  blocks/ 布局中间产物")
    print("  ocr/   OCR 中间产物（可删除；删后重跑自动重新生成）")

if __name__ == "__main__":
    main()
