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


def run_ocr_resilient(bin_path, img_dir, txt_dir, json_dir, start, end,
                      reuse_broken=False, force=False):
    """分段调用 Swift OCR，捕获 SIGBUS 崩溃并二分定位崩溃页、跳过继续。

    逻辑完全沿用 run.py 的 run_ocr_resilient。
    Args:
        force: True 时忽略已有 json（强制重跑所有页，配合 --force）
    """
    broken = os.path.join(json_dir, "broken.txt")
    skipped_pages = set()
    if reuse_broken and os.path.isfile(broken):
        with open(broken, encoding="utf-8") as f:
            skipped_pages = {int(x) for x in f.read().split() if x.strip()}

    # force 时：清空目标 OCR 目录（json_dir 与 txt_dir）下的所有文件，
    # 让 Swift 二进制不会再"跳过(已存在)"。逐文件 os.remove，不删目录本身。
    if force:
        for d in (json_dir, txt_dir):
            if not os.path.isdir(d):
                continue
            for name in os.listdir(d):
                fpath = os.path.join(d, name)
                if os.path.isfile(fpath):
                    try:
                        os.remove(fpath)
                    except OSError:
                        pass
        if os.path.isfile(broken):
            try:
                os.remove(broken)
            except OSError:
                pass
        skipped_pages.clear()

    def img_exists(p):
        return os.path.isfile(os.path.join(img_dir, f"p{p}.jpg"))

    def need_ocr(p):
        """该页是否需要 OCR：force 时强制，否则 json 已存在则跳过。"""
        return (force or not page_done(json_dir, p)) and img_exists(p)

    def seg_run(a, b):
        want = [p for p in range(a, b + 1) if need_ocr(p)]
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

    todo = [p for p in range(start, end + 1) if need_ocr(p) and p not in skipped_pages]
    print(f"待 OCR 页: {len(todo)} 张" + ("" if force else "（已缓存跳过）"))

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
        if need_ocr(crash_page):
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
                if need_ocr(p) and p not in skipped_pages]
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

    按 (页号, 文件名) 排序，保证处理顺序稳定；同页号（文件名里数字相同，如
    "8-28.png"/"8-29.jpg" 都抽到 8）冲突时，按文件名顺序把后一个文件放到
    下一个可用页号，避免 dict 覆盖丢失文件。无冲突项目（如 page_180/182/184）
    页号不变，排序不受影响。
    """
    pages = {}
    if not os.path.isdir(images_dir):
        return pages
    items = []
    for f in os.listdir(images_dir):
        m = PAGE_RE.search(f)
        if m:
            items.append((int(m.group(1)), f))
    items.sort(key=lambda t: (t[0], t[1]))
    used = set()
    for pn, f in items:
        while pn in used:  # 冲突：顺延到下一个未用页号
            pn += 1
        pages[pn] = f
        used.add(pn)
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


def _find_split_points(gray, max_h=2600, min_h=1400, blank_ratio=0.003):
    """在**段落边界**处找切点，避免把连续段落/图表拦腰切断。

    长图排版规律：一段文字内行距固定、段与段之间留白更宽（如正文行内 gap≈35px、
    段间 gap≈110px，双峰清晰）。先识别"文本行"并统计行间距，定出**段间 gap**
    （明显大于行内中位），再贪心地在 [last+min_h, last+max_h] 窗口内**优先选段间
    gap 中心**下刀；窗口内无段界时退回选任意宽空白带；仍无（整段图超 max_h）才
    取窗口中部。这样切点几乎总是落在段落之间，切完的片就是完整段落。

    Args:
        gray: 灰度 numpy 数组 (H, W)
        max_h / min_h: 每片高度区间
        blank_ratio: 空白行判定阈值（该行非背景像素数 < W*blank_ratio 视为空白）
    Returns:
        切点 y 坐标列表（每片起点，不含 0 与 H）
    """
    import numpy as np
    H, W = gray.shape
    content = (gray < 200).sum(axis=1)
    blank_thr = max(2, int(W * blank_ratio))
    is_blank = content < blank_thr

    # 内容行 run：分隔出文字行/图。文字行高通常 20~90px，图更高。
    runs = []
    y = 0
    while y < H:
        if not is_blank[y]:
            s = y
            while y < H and not is_blank[y]:
                y += 1
            if y - s >= 8:
                runs.append((s, y))
        else:
            y += 1

    # 文本行（高度像一行字）间的 gap：段内行距 vs 段间留白双峰。
    # 用"两侧都是文本行"的 gap 估行内中位；段界 gap = 明显更宽（>1.8×行内中位）。
    texts = [(s, e) for (s, e) in runs if 20 <= e - s <= 90]
    line_gaps = [texts[i][0] - texts[i - 1][1]
                 for i in range(1, len(texts))
                 if 3 < texts[i][0] - texts[i - 1][1] < 300]
    line_mid = None
    if line_gaps:
        m0 = float(np.median(line_gaps))
        small = [g for g in line_gaps if g < m0]
        line_mid = float(np.median(small)) if small else m0
    # 候选落刀点：段间 gap 中心（文本行之间的大留白）+ 一般空白带中心
    def _cut_points():
        pts = []
        # 段界：文本行间 gap 明显大于行内中位
        if line_mid:
            for i in range(1, len(texts)):
                gp = texts[i][0] - texts[i - 1][1]
                if gp > line_mid * 1.8 and 10 <= gp <= 600:
                    pts.append((gp, (texts[i - 1][1] + texts[i][0]) // 2, "seg"))
        # 一般空白带（含图间隙等，>=15px）
        gap_bands = []
        y = 0
        while y < H:
            if is_blank[y]:
                s = y
                while y < H and is_blank[y]:
                    y += 1
                if y - s >= 15:
                    gap_bands.append((y - s, (s + y) // 2))
            else:
                y += 1
        for w, c in gap_bands:
            pts.append((w, c, "band"))
        return pts

    pts = _cut_points()
    cuts = []
    last = 0
    while H - last > max_h:
        lo = last + min_h
        hi = min(last + max_h, H)
        # 窗口内候选：段界优先（即使窄也优先，因为它保证不切段），空白带其次按宽
        seg_in = [(abs(c - (lo + hi) // 2), c) for (w, c, t) in pts
                  if t == "seg" and lo <= c <= hi]
        if seg_in:
            cut = min(seg_in)[1]
        else:
            band_in = [(-w, -(abs(c - (lo + hi) // 2)), c)
                       for (w, c, t) in pts if t == "band" and lo <= c <= hi]
            if band_in:
                cut = min(band_in)[2]
            else:
                # 整段图超 max_h 且无空白带：取窗口中部（不可避免切图/文字）
                cut = lo + (hi - lo) // 2
        cuts.append(cut)
        last = cut
    return cuts


def _to_rgb(im):
    """RGBA/其他模式 → RGB（透明合成白底）。"""
    from PIL import Image
    if im.mode == "RGBA":
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.split()[3])
        return bg
    if im.mode != "RGB":
        return im.convert("RGB")
    return im


def _trim_image(im, pad_ratio=0.01):
    """智能 trim：用边缘检测找内容包围盒，裁掉四边空白；再去掉右侧灰色滚动条。

    所有阈值都**相对图片尺寸**（比例），不依赖固定像素，对分辨率/边距变化鲁棒：
      - pad = max(2, min(W,H)*pad_ratio)
      - 滚动条宽度区间 = W*[0.5%, 8%]
      - 滚动条到右边缘距离上限 = W*10%
      - 滚动条内部 = 深+浅两端像素都占该列高 >15%（bimodal 轨道+滑块），
        且列均灰度偏离背景主色。
    滚动条检测基于结构：它是与内容有白缝分隔、位于右侧的窄灰竖条。
    """
    W, H = im.size
    if W < 120 or H < 120:
        return im
    import numpy as np
    rgb = _to_rgb(im)
    gray = np.asarray(rgb.convert("L")).astype(np.float32)
    pad = max(2, int(min(W, H) * pad_ratio))

    # ---- 1) Sobel 边缘 + 滚动条列识别（滚动条列不算"内容"）----
    gx = np.zeros_like(gray); gy = np.zeros_like(gray)
    gx[:, 1:-1] = gray[:, 2:] - gray[:, :-2]
    gy[1:-1, :] = gray[2:, :] - gray[:-2, :]
    mag = np.sqrt(gx * gx + gy * gy)
    edge = mag > 30
    hist = np.bincount(gray.astype(np.int64).ravel(), minlength=256)
    bg = int(hist.argmax())
    # 滚动条列：列均灰度 150-210（灰带）+ bimodal（深+浅都>15%）+
    # 结构性：整列"暗像素段"数量少（滚动条=轨道+滑块，暗像素集中在少数几段；
    # K 线图内容列=大量分散的蜡烛，暗像素段很多）。
    dr = (gray < 70).mean(axis=0); lr = (gray > 210).mean(axis=0)
    col_mean = gray.mean(axis=0)
    dark_mask = (gray < 70)
    # 每列暗像素的连续段数（行维）：统计"非暗→暗"的跳变次数
    col_dark_runs = np.zeros(W, dtype=int)
    if dark_mask.shape[0] > 1:
        starts = dark_mask[1:] & ~dark_mask[:-1]
        col_dark_runs = starts.sum(axis=0)
        col_dark_runs += dark_mask[0]  # 首行是暗也算一段开始
    sb_col = ((dr > 0.15) & (lr > 0.15) & (col_mean > 150) & (col_mean < 210)
             & (col_dark_runs < 100))
    # 滚动条段：连续的 sb_col，最右段且宽度 W*[0.5%, 8%]、距右边 ≤ W*10%
    w_min = max(3, int(W * 0.005)); w_max = max(w_min + 1, int(W * 0.08))
    dist_max = int(W * 0.10)
    sb_mask = np.zeros(W, dtype=bool)
    _idx = np.where(sb_col)[0]
    if len(_idx) >= 3:
        _runs = []; _start = _prev = int(_idx[0])
        for i in _idx[1:]:
            i = int(i)
            if i - _prev > 1:
                _runs.append((_start, _prev)); _start = i
            _prev = i
        _runs.append((_start, _prev))
        for (rs, re) in reversed(_runs):
            w = re - rs + 1
            if w_min <= w <= w_max and (W - 1 - re) <= dist_max:
                sb_mask[rs:re+1] = True
                break
    # 内容 = 边缘或与背景差异明显，但**排除滚动条列**（滚动条不是图内容）
    content = (edge | (np.abs(gray - bg) > 50))
    if sb_mask.any():
        content[:, sb_mask] = False

    # ---- 2) 裁四边空白 ----
    row_c = content.any(axis=1); col_c = content.any(axis=0)
    ys = np.where(row_c)[0]; xs = np.where(col_c)[0]
    if len(ys) == 0 or len(xs) == 0:
        return im
    y0 = max(0, int(ys.min()) - pad); y1 = min(H, int(ys.max()) + 1 + pad)
    x0 = max(0, int(xs.min()) - pad); x1 = min(W, int(xs.max()) + 1 + pad)
    # 若识别到滚动条：强制裁到滚动条左边界（右侧的一切——滚动条 + 右边框/阴影都裁掉）
    if sb_mask.any():
        sb_left = int(np.where(sb_mask)[0].min())
        if sb_left > x0:
            x1 = min(x1, sb_left)
    return rgb.crop((x0, y0, x1, y1))


BLANK_PAGES_FILE = "blank_pages.txt"
LONG_IMAGE_PAGES_FILE = "long_image_pages.txt"


def long_image_pages_path(out):
    """长图切分页记录文件路径（<out>/long_image_pages.txt）。"""
    return os.path.join(out, LONG_IMAGE_PAGES_FILE)


def read_long_image_pages(out):
    """读取长图切分页记录：{源文件名: [页号,...]}。文件不存在或为空返回 {}。

    格式：每行 `源文件名<TAB>页号1,页号2,...`（文件名可含空格，用制表符分隔）。
    """
    p = long_image_pages_path(out)
    result = {}
    if not os.path.isfile(p):
        return result
    try:
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.rstrip("\n")
                if not line:
                    continue
                if "\t" in line:
                    name, pages = line.split("\t", 1)
                else:
                    name, pages = line, line  # 兼容旧格式（一行全是页号）
                nums = [int(x) for x in pages.replace(",", " ").split() if x.strip()]
                if nums:
                    result[name] = sorted(set(nums))
    except Exception:
        return {}
    return result


def write_long_image_pages(out, long_image_pages):
    """把长图切分页记录写入文件（覆盖）。long_image_pages: {源文件名: [页号,...]}。"""
    p = long_image_pages_path(out)
    os.makedirs(out, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        for name in sorted(long_image_pages):
            nums = sorted(set(long_image_pages[name]))
            f.write(f"{name}\t{','.join(str(x) for x in nums)}\n")


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


def prepare_images(images_dir, pages, out, force=False,
                   max_ratio=3.0, max_h=2600, min_h=1400, slice_ratio=3.0):
    """把输入图片统一归一化为 JPEG，输出到 <out>/_ocr_tmp/。
    返回 (临时图片目录, 展开后的 pages 字典)。完全沿用 run.py 的 prepare_images。

    处理两件事：
    1. 自动过滤"完全空白"的图片（如透明白图）：不产出对应的 p{n}.jpg，
       并把空白页号写入 <out>/blank_pages.txt 持久化。
    2. 超长图（高宽比 >= max_ratio，如滚动长截图）内容感知切分为多片：
       在空白行处切，避免切断内部图表；每片登记为新页（连续页号），
       扩入 pages，使 OCR/detect/build 按多页处理。

    Args:
        force: True 时绕过 mtime 缓存，重新处理所有页（配合 --force 全步骤无缓存）
        max_ratio: 超过该高宽比即视为超长图，触发切分
        max_h / min_h: 切分后每片高度区间（px）
        slice_ratio: 切片高度上限相对图片宽度的比例（约=内容区高/宽）。
            超长图每片高 ≤ max(slice_ratio*W, min_h)，这样在 layed 流里各片
            都是宽度受限（scale=content_w/W）→ 图片宽度全页一致，不出现"页面宽度不一"。
    Returns:
        (tmp, pages)：tmp 为预处理图目录，pages 为展开（含切片子页）后的页号映射
    """
    tmp = os.path.join(out, "_ocr_tmp")
    os.makedirs(tmp, exist_ok=True)
    from PIL import Image
    import numpy as np
    converted = copied = blank = sliced = 0
    blank_pages = []
    # 长图切分页：{源文件名: [切片页号]}——每张长图单独成章，不与其他混合
    long_image_pages = {}
    new_pages = dict(pages)
    for n, fname in sorted(pages.items()):
        src = os.path.join(images_dir, fname)
        dst = os.path.join(tmp, f"p{n}.jpg")
        if not os.path.isfile(src):
            continue
        # 先判空白（放在 mtime 缓存判断之前），保证重跑时也能清掉历史残留的空白页
        im = Image.open(src)
        # 预处理：边缘检测裁四边空白 + 去掉右侧灰色滚动条（避免误识别为内容）
        im = _trim_image(im)
        if _is_blank_image(im):
            if os.path.isfile(dst):
                os.remove(dst)
            blank += 1
            blank_pages.append(n)
            continue

        W, H = im.size
        # 超长图 → 内容感知切分
        if H > min_h and H / W >= max_ratio:
            rgb = _to_rgb(im)
            gray = np.asarray(rgb.convert("L"))
            # 切片高度上限按图片宽度 × slice_ratio（≈内容区高/宽），保证 layed 里各片
            # 都是宽度受限 → 图片宽度全页一致。
            _slice_max_h = max(int(W * slice_ratio), min_h)
            cuts = _find_split_points(gray, max_h=_slice_max_h, min_h=min_h)
            if cuts:
                # 清掉本文件可能存在的旧切片（只删属于本文件 fname 的页，
                # 避免误删别的长图（如 8-28 的切片）被 8-29 的清理误删）
                for pn in list(new_pages):
                    if pn >= n and new_pages.get(pn) == fname:
                        old = os.path.join(tmp, f"p{pn}.jpg")
                        if os.path.isfile(old):
                            os.remove(old)
                pts = [0] + cuts + [H]
                # 合并过小的切片（多为尾部空白残留）：<200px 并入前一片，避免无意义的小页
                merged = []
                for y0, y1 in zip(pts[:-1], pts[1:]):
                    if merged and y1 - merged[-1][1] < 200:
                        merged[-1] = (merged[-1][0], y1)
                    else:
                        merged.append((y0, y1))
                # 连续重编号：从 n 开始；若 n 已被别的文件切片占用（如 8-29 的 n=9
                # 被 8-28 切片占用），则从最大已用页+1 起，避免两张长图切片页号重叠。
                _pn = n
                if new_pages.get(n) not in (None, fname):
                    _pn = max(new_pages) + 1 if new_pages else n
                # 只删除本文件原来的单页 p{n}.jpg（若该页属于本文件）
                if new_pages.get(n) == fname:
                    del new_pages[n]
                _kept = 0
                for y0, y1 in merged:
                    if y1 - y0 < 200:
                        continue
                    crop = rgb.crop((0, y0, W, y1))
                    crop.save(os.path.join(tmp, f"p{_pn}.jpg"), "JPEG", quality=95)
                    new_pages[_pn] = fname
                    long_image_pages.setdefault(fname, []).append(_pn)
                    _pn += 1
                    _kept += 1
                if _kept > 0:
                    sliced += 1
                continue

        if not force and os.path.isfile(dst) and os.path.getmtime(dst) >= os.path.getmtime(src):
            continue
        fmt = im.format
        if fmt == "JPEG":
            shutil.copyfile(src, dst)
            copied += 1
            continue
        rgb = _to_rgb(im)
        rgb.save(dst, "JPEG", quality=95)
        converted += 1
    if blank:
        print(f"  已过滤 {blank} 张完全空白图片（无信息）：{blank_pages}")
    if sliced:
        print(f"  已切分 {sliced} 张超长图（空白处切分，避免切到图表），"
              f"页数 {len(pages)} → {len(new_pages)}")
    write_blank_pages(out, blank_pages)
    write_long_image_pages(out, long_image_pages)
    print(f"图片预处理完成：{copied} 张 JPEG 直接复制 + {converted} 张转 JPEG"
          f" + {blank} 张空白过滤 + {sliced} 张长图切分（临时目录 {tmp}）")
    return tmp, new_pages


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

    # 超长图切分参数（可配置）
    from .config import Config
    _cfg = Config(str(project.path))
    _max_ratio = float(_cfg.get('book.split_long_image_max_ratio', 3.0))
    _max_h = int(_cfg.get('book.split_slice_max_h', 2600))
    _min_h = int(_cfg.get('book.split_slice_min_h', 1400))
    _slice_ratio = float(_cfg.get('book.split_slice_ratio', 3.0))

    img_for_ocr, pages = prepare_images(
        images_dir, pages, out, force=force_ocr,
        max_ratio=_max_ratio, max_h=_max_h, min_h=_min_h,
        slice_ratio=_slice_ratio)

    # 切分后页号可能扩展（长图→多片），用展开后的 pages 重算范围
    rng2 = auto_page_range(pages)
    if start is None or (rng2 and rng2[1] > end):
        end = rng2[1] if rng2 else end
    if rng2:
        start = min(start, rng2[0]) if start is not None else rng2[0]

    if os.path.isdir(os.path.join(ocr_cache, "pages_json")) and not force_ocr:
        print("复用 OCR 缓存:", ocr_cache)
    else:
        print("运行 OCR（macOS Vision, zh-Hans + en）...")
        os.makedirs(project.get_ocr_pages_dir(), exist_ok=True)
        os.makedirs(project.get_ocr_pages_json_dir(), exist_ok=True)
        run_ocr_resilient(bin_path, img_for_ocr,
                          str(project.get_ocr_pages_dir()),
                          str(project.get_ocr_pages_json_dir()),
                          start, end, reuse_broken=False, force=force_ocr)
    return img_for_ocr, start, end
