# -*- coding: utf-8 -*-
"""OCR 引擎封装：编译并运行 ocr_batch.swift（macOS Vision），产物落盘 output/ocr/。

本模块把 run.py 的 OCR 编排逻辑迁移进包内，提供：
- 源图预处理归一化为 JPEG（规避 ImageIO 解 PNG 必崩的 SIGBUS bug）
- 分段调用 Swift OCR，捕获 SIGBUS 崩溃并二分定位、跳过继续
- OCR 缓存复用（force_ocr 控制）
"""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# 包内 swift 源文件路径（放在 src/ocr_book_to_md/ 同级或项目根）
HERE = Path(__file__).resolve().parent
SWIFT_SRC = HERE / "ocr_batch.swift"
if not SWIFT_SRC.exists():
    SWIFT_SRC = HERE.parent.parent / "ocr_batch.swift"

SEGMENT = 30          # 每次调用 Swift 的页数段（Vision 批量处理有内存累积 SIGBUS 风险）
SIGBUS = -10          # 128 + SIGBUS(10) = 138，subprocess 返回 -10


def page_done(json_dir, n):
    """该页 OCR 结果是否已存在"""
    return os.path.isfile(os.path.join(json_dir, f"{n}.json"))


def run_ocr_resilient(bin_path, img_dir, txt_dir, json_dir, start, end, reuse_broken=False):
    """分段调用 Swift OCR，捕获 SIGBUS 崩溃并二分定位崩溃页、跳过继续。

    逻辑完全沿用 run.py 的 run_ocr_resilient。
    """
    broken = os.path.join(json_dir, "broken.txt")
    skipped_pages = set()
    if reuse_broken and os.path.isfile(broken):
        with open(broken, encoding="utf-8") as f:
            skipped_pages = {int(x) for x in f.read().split() if x.strip()}

    def img_exists(p):
        return os.path.isfile(os.path.join(img_dir, f"p{p}.jpg"))

    def seg_run(a, b):
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
        failed = len(got) < len(want)
        if failed:
            print(f"  [!] 段 p{a}..p{b} 未完成：{len(want)} 页产出 {len(got)}（code {r.returncode}）")
        return new_ok, failed

    todo = [p for p in range(start, end + 1)
            if not page_done(json_dir, p) and p not in skipped_pages]
    print(f"待 OCR 页: {len(todo)} 张（已缓存跳过）")

    i = 0
    while i < len(todo):
        a = todo[i]
        b = a
        while i + 1 < len(todo) and todo[i + 1] - b <= 3 and (b - a) < SEGMENT - 1:
            b = todo[i + 1]
            i += 1
        i += 1

        _, crashed = seg_run(a, b)
        if not crashed:
            continue
        lo, hi = a, b
        while lo < hi:
            mid = (lo + hi) // 2
            mid_ok, mid_crashed = seg_run(lo, mid)
            if mid_crashed:
                hi = mid
            else:
                lo = mid + 1
        crash_page = lo
        if not page_done(json_dir, crash_page) and img_exists(crash_page):
            single_ok, single_crashed = seg_run(crash_page, crash_page)
            if single_crashed and not page_done(json_dir, crash_page):
                print(f"  [!] p{crash_page} 触发 Vision SIGBUS，已跳过（记录到 broken.txt）")
                skipped_pages.add(crash_page)
                with open(broken, "a", encoding="utf-8") as f:
                    f.write(f"{crash_page}\n")
            else:
                print(f"  [?] 批量崩溃定位到 p{crash_page}（单页正常，跳过该页续跑）")
                skipped_pages.add(crash_page)
                with open(broken, "a", encoding="utf-8") as f:
                    f.write(f"{crash_page}\n")
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
    """扫描截图目录，从文件名提取页号，返回 {页号: 文件名} 映射。"""
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


def _is_blank_image(im, uniform_ratio=0.999):
    """判定图片是否"完全空白、无实质信息"。

    把图像转灰度，统计出现最多的灰度值的占比：若 ≥uniform_ratio（默认 99.9%）
    是同一个颜色，说明该图基本是纯色（全白/全黑/纯色），没有任何文字/图片内容。
    典型场景：源图里夹的"透明白图"——按白底合成后是整页纯白，会被判定为空白并过滤。

    Args:
        im: PIL Image
        uniform_ratio: 主色占比阈值，超过即视为空白
    Returns:
        True 表示空白（应过滤）
    """
    try:
        import numpy as np
        g = np.asarray(im.convert("L")).astype(np.int64)
        if g.size == 0:
            return True
        hist = np.bincount(g.ravel(), minlength=256)
        dominant = float(hist.max()) / g.size
        return dominant >= uniform_ratio
    except Exception:
        return False


BLANK_PAGES_FILE = "blank_pages.txt"


def blank_pages_path(out):
    """空白页记录文件路径（<out>/blank_pages.txt）。"""
    return os.path.join(out, BLANK_PAGES_FILE)


def read_blank_pages(out):
    """读取空白页记录（页号列表）。文件不存在或为空返回 []。"""
    p = blank_pages_path(out)
    if not os.path.isfile(p):
        return []
    try:
        with open(p, encoding="utf-8") as f:
            return sorted({int(x) for x in f.read().split() if x.strip()})
    except Exception:
        return []


def write_blank_pages(out, page_nums):
    """把空白页号写入记录文件（覆盖），供 PDF 各流最终制作时统一过滤。"""
    p = blank_pages_path(out)
    os.makedirs(out, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(" ".join(str(x) for x in sorted(set(page_nums))))


def prepare_images(images_dir, pages, out, force=False):
    """把输入图片统一归一化为 JPEG，输出到 <out>/_ocr_tmp/。
    返回临时图片目录。完全沿用 run.py 的 prepare_images。

    自动过滤"完全空白"的图片（如透明白图）：不产出对应的 p{n}.jpg，
    并把空白页号写入 <out>/blank_pages.txt 持久化——最终做 PDF 的各流
    （img/layed/text）读取该记录统一过滤，保证原图目录 / _ocr_tmp 都不含空白页。

    Args:
        force: True 时绕过 mtime 缓存，重新处理所有页（配合 --force 全步骤无缓存）
    """
    tmp = os.path.join(out, "_ocr_tmp")
    os.makedirs(tmp, exist_ok=True)
    from PIL import Image
    converted = copied = blank = 0
    blank_pages = []
    for n, fname in sorted(pages.items()):
        src = os.path.join(images_dir, fname)
        dst = os.path.join(tmp, f"p{n}.jpg")
        if not os.path.isfile(src):
            continue
        # 先判空白（放在 mtime 缓存判断之前），保证重跑时也能清掉历史残留的空白页
        im = Image.open(src)
        if _is_blank_image(im):
            # 完全空白（纯色、无信息）→ 过滤：删掉历史 p{n}.jpg，不产出，OCR/detect 自动跳过
            if os.path.isfile(dst):
                os.remove(dst)
            blank += 1
            blank_pages.append(n)
            continue
        if not force and os.path.isfile(dst) and os.path.getmtime(dst) >= os.path.getmtime(src):
            continue
        fmt = im.format
        if fmt == "JPEG":
            shutil.copyfile(src, dst)
            copied += 1
            continue
        if im.mode == "RGBA":
            bg = Image.new("RGB", im.size, (255, 255, 255, 255))
            bg.paste(im, mask=im.split()[3])
            im = bg
        elif im.mode != "RGB":
            im = im.convert("RGB")
        im.save(dst, "JPEG", quality=95)
        converted += 1
    if blank:
        print(f"  已过滤 {blank} 张完全空白图片（无信息）：{blank_pages}")
    write_blank_pages(out, blank_pages)
    print(f"图片预处理完成：{copied} 张 JPEG 直接复制 + {converted} 张转 JPEG"
          f" + {blank} 张空白过滤（临时目录 {tmp}）")
    return tmp


def run(project, images_dir, start=None, end=None, force_ocr=False):
    """对项目执行 OCR 步骤。

    Args:
        project: Project 实例
        images_dir: 源图目录（已解析）
        start/end: 页码范围（None=自动探测）
        force_ocr: 是否忽略 OCR 缓存
    Returns:
        (img_for_ocr, start, end)：预处理后的图目录与最终范围
    """
    out = str(project.output_dir)
    os.makedirs(out, exist_ok=True)
    ocr_cache = project.get_ocr_dir()
    os.makedirs(ocr_cache, exist_ok=True)
    bin_path = os.path.join(ocr_cache, "ocr_batch")
    if not os.path.exists(bin_path):
        if not SWIFT_SRC.exists():
            raise SystemExit(f"错误：找不到 ocr_batch.swift（{SWIFT_SRC}）")
        subprocess.run(["swiftc", "-o", bin_path, str(SWIFT_SRC)], check=True)

    pages = scan_pages(images_dir)
    if not pages:
        raise SystemExit(f"错误：截图目录 {images_dir} 里找不到含数字页号的文件")
    rng = auto_page_range(pages)
    start = start if start is not None else rng[0]
    end = end if end is not None else rng[1]
    print(f"检测到 {len(pages)} 页，OCR 范围 p{start}..p{end}")

    img_for_ocr = prepare_images(images_dir, pages, out, force=force_ocr)

    if os.path.isdir(os.path.join(ocr_cache, "pages_json")) and not force_ocr:
        print("复用 OCR 缓存:", ocr_cache)
    else:
        print("运行 OCR（macOS Vision, zh-Hans + en）...")
        os.makedirs(project.get_ocr_pages_dir(), exist_ok=True)
        os.makedirs(project.get_ocr_pages_json_dir(), exist_ok=True)
        run_ocr_resilient(bin_path, img_for_ocr,
                          str(project.get_ocr_pages_dir()),
                          str(project.get_ocr_pages_json_dir()),
                          start, end, reuse_broken=False)
    return img_for_ocr, start, end
