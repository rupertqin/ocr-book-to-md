# -*- coding: utf-8 -*-
"""OCR 引擎封装：统一产物 schema，按平台选择本地 OCR 后端。

- macOS（默认）：编译并运行 ocr_batch.swift（Apple Vision）；分段调用、SIGBUS 二分兜底。
- 其他平台（默认）：RapidOCR（onnxruntime 小模型，自带中文），输出与 Vision 同 schema。
- 两者都输出 ``<work>/ocr/pages_json/<n>.json``：``{"page": n, "lines": [{text,x,y,w,h}]}``，
  x/y/w/h 归一化、原点左下（与 Vision 的 boundingBox 一致）。

通用能力：源图预处理为 JPEG（规避 ImageIO 解 PNG 的 SIGBUS）、超长图按段落切分、
空白页过滤、OCR 缓存复用（force 控制）。
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from . import defaults, paths

# 包内 swift 源文件路径（与 __init__.py 同级，随 wheel 一起分发）
HERE = Path(__file__).resolve().parent
SWIFT_SRC = HERE / "ocr_batch.swift"
if not SWIFT_SRC.exists():
    SWIFT_SRC = HERE.parent.parent / "ocr_batch.swift"

SEGMENT = 30          # 每次调用 Swift 的页数段（Vision 批量处理有内存累积 SIGBUS 风险）
SIGBUS = -10          # 128 + SIGBUS(10) = 138，subprocess 返回 -10


def _atomic_write_text(path, text: str) -> None:
    """同目录临时文件 + os.replace，避免中断留下半截文本。"""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-", text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def _atomic_write_json(path, data) -> None:
    _atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))


def page_done(json_dir, n, img_dir=None):
    """该页 OCR 结果是否可复用。

    仅当 JSON 存在且不早于对应页图 mtime 时才算完成，避免源图更新后复用旧文本。
    """
    json_path = os.path.join(json_dir, f"{n}.json")
    if not os.path.isfile(json_path):
        return False
    if img_dir:
        img_path = os.path.join(img_dir, f"p{n}.jpg")
        if os.path.isfile(img_path):
            try:
                if os.path.getmtime(json_path) < os.path.getmtime(img_path):
                    return False
            except OSError:
                return False
    return True


def _clean_ocr_results(json_dir, txt_dir, valid_pages, img_dir):
    """删除陈旧页的 JSON/TXT，返回失效页号集合。"""
    stale = set()
    if os.path.isdir(json_dir):
        for name in os.listdir(json_dir):
            if not name.endswith(".json"):
                continue
            stem = name[:-5]
            if not stem.isdigit():
                continue
            n = int(stem)
            if n not in valid_pages:
                stale.add(n)
                continue
            img_path = os.path.join(img_dir, f"p{n}.jpg")
            json_path = os.path.join(json_dir, name)
            if not os.path.isfile(img_path):
                stale.add(n)
                continue
            try:
                if os.path.getmtime(json_path) < os.path.getmtime(img_path):
                    stale.add(n)
            except OSError:
                stale.add(n)

    for n in stale:
        for path in (os.path.join(json_dir, f"{n}.json"),
                     os.path.join(txt_dir, f"{n}.txt")):
            if os.path.isfile(path):
                try:
                    os.remove(path)
                except OSError:
                    pass

    if os.path.isdir(txt_dir):
        for name in os.listdir(txt_dir):
            if not name.endswith(".txt"):
                continue
            stem = name[:-4]
            if stem.isdigit() and (int(stem) in stale or int(stem) not in valid_pages):
                try:
                    os.remove(os.path.join(txt_dir, name))
                except OSError:
                    pass
    return stale


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
        return (force or not page_done(json_dir, p, img_dir)) and img_exists(p)

    def seg_run(a, b):
        want = [p for p in range(a, b + 1) if need_ocr(p)]
        if not want:
            return [], False
        r = subprocess.run([bin_path, img_dir, txt_dir, json_dir, str(a), str(b)],
                           capture_output=True, text=True)
        sys.stdout.write(r.stdout); sys.stdout.flush()
        sys.stderr.write(r.stderr); sys.stderr.flush()
        got = [p for p in want if page_done(json_dir, p, img_dir)]
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
            if single_crashed and not page_done(json_dir, crash_page, img_dir):
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

    _atomic_write_text(broken, " ".join(str(x) for x in sorted(skipped_pages)))
    if skipped_pages:
        print(f"本次 OCR 跳过（Vision 崩溃）: {sorted(skipped_pages)}")
    else:
        print("OCR 全部完成，无崩溃页。")


VALID_ENGINES = ("swift", "rapidocr")


def resolve_engine(override=None):
    """选择 OCR 引擎：只接受显式参数，否则按平台默认。

    - ``override``：CLI ``--engine`` / 库调用传入的显式选择。
    - ``auto`` 或 ``None``：macOS → Swift Vision，其他 → RapidOCR。
    """
    value = override
    if value is None:
        value = "auto"
    normalized = str(value).strip().lower()
    if normalized in VALID_ENGINES:
        return normalized
    if normalized in ("", "auto"):
        return "swift" if sys.platform == "darwin" else "rapidocr"
    raise SystemExit(
        f"错误：未知 OCR 引擎 {value!r}，可选 auto / swift / rapidocr"
    )


def convert_rapidocr_result(result, width, height):
    """把 RapidOCR 输出转成与 Swift Vision 相同的 lines schema。

    RapidOCR 的 box 是像素坐标、原点左上，四点顺序为左上/右上/右下/左下。
    Vision 的 ``y`` 是 bbox 底边、原点左下，故 ``y = 1 - ymax/H``。
    返回 ``(lines, text)``，lines 按从上到下、从左到右排序。
    """
    if result is None or getattr(result, "boxes", None) is None \
            or getattr(result, "txts", None) is None:
        return [], ""
    lines = []
    for box, text in zip(result.boxes, result.txts):
        pts = [[float(p[0]), float(p[1])] for p in box]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        x0, x1 = min(xs) / width, max(xs) / width
        top, bottom = min(ys) / height, max(ys) / height
        lines.append({
            "text": str(text),
            "x": round(max(0.0, x0), 6),
            "y": round(max(0.0, 1.0 - bottom), 6),
            "w": round(min(1.0, x1 - x0), 6),
            "h": round(min(1.0, bottom - top), 6),
            "_top": round(top, 6),
        })
    lines.sort(key=lambda ln: (ln["_top"], ln["x"]))
    for ln in lines:
        ln.pop("_top", None)
    return lines, "\n".join(ln["text"] for ln in lines) + ("\n" if lines else "")


def run_ocr_rapidocr(img_dir, txt_dir, json_dir, start, end, force=False):
    """用 RapidOCR 逐页识别 p{n}.jpg，产出与 Vision 完全相同的 JSON/TXT。"""
    try:
        from rapidocr import RapidOCR
        from PIL import Image
    except ImportError as exc:
        raise SystemExit(
            "错误：当前选择 RapidOCR，但未安装。请 pip install 'p2md[rapidocr]'，"
            "或改用 --engine swift / engine: swift"
        ) from exc

    if force:
        for d in (json_dir, txt_dir):
            if os.path.isdir(d):
                for name in os.listdir(d):
                    fp = os.path.join(d, name)
                    if os.path.isfile(fp):
                        try:
                            os.remove(fp)
                        except OSError:
                            pass
        broken = os.path.join(json_dir, "broken.txt")
        if os.path.isfile(broken):
            try:
                os.remove(broken)
            except OSError:
                pass

    engine = RapidOCR()
    todo = [n for n in range(start, end + 1)
            if (force or not page_done(json_dir, n, img_dir))
            and os.path.isfile(os.path.join(img_dir, f"p{n}.jpg"))]
    print(f"RapidOCR 待识别页: {len(todo)} 张" + ("" if force else "（已缓存跳过）"))
    for n in todo:
        img_path = os.path.join(img_dir, f"p{n}.jpg")
        with Image.open(img_path) as im:
            width, height = im.size
        result = engine(str(img_path))
        lines, text = convert_rapidocr_result(result, width, height)
        _atomic_write_text(os.path.join(txt_dir, f"{n}.txt"), text)
        _atomic_write_json(os.path.join(json_dir, f"{n}.json"),
                           {"page": n, "lines": lines})
        print(f"  p{n}: {len(lines)} 行")
    _atomic_write_text(os.path.join(json_dir, "broken.txt"), "")


IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif"}
PAGE_RE = re.compile(r"(\d+)")
# _ocr_tmp 下的成品页图名：p{页号}.jpg（用于清理页号重排后不再使用的历史切片）
TMP_PAGE_RE = re.compile(r"^p(\d+)\.jpg$")


def _natural_name_key(name):
    """文件名的"自然排序"键：其中的数字按**数值**比较。

    纯字符串比较会把 '9-5.jpg' 排到 '9-25.jpg' 之后（'5' > '2'），日期命名的
    长截图素材（9-5 / 9-12 / 9-19 / 9-25）顺序就乱了，成书章节顺序随之错乱。
    数字补零成等宽字符串，既保证数值序，又避免 tuple 里 int/str 混比 TypeError。
    """
    return tuple(
        f"{int(x):010d}" if x.isdigit() else x.lower()
        for x in re.split(r"(\d+)", name) if x
    )


def scan_pages(assets_dir):
    """扫描截图目录，从文件名提取页号，返回 {页号: 文件名} 映射。

    页号取文件名里**第一个数字**；同页号（如 "8-28.png"/"8-29.jpg" 都抽到 8，
    或 "9-5.jpg"/"9-12.jpg" 都抽到 9）冲突时，按文件名**自然顺序**（数字按数值比）
    把后一个文件顺延到下一个可用页号，避免 dict 覆盖丢失文件。
    冲突时的排序必须用自然顺序而不是字符串顺序，否则 "9-5.jpg" 会排到
    "9-12.jpg" 之后，章节顺序错乱。无冲突项目（如 page_180/182/184）页号与
    处理顺序都不变。
    """
    pages = {}
    if not os.path.isdir(assets_dir):
        return pages
    # 仅图片为页源
    items = []
    for f in os.listdir(assets_dir):
        if Path(f).suffix.lower() not in IMAGE_EXTS:
            continue
        m = PAGE_RE.search(f)
        if m:
            items.append((int(m.group(1)), f))
    # 首数字为主序（无冲突项目顺序完全不变），同名首数字用自然序打平
    items.sort(key=lambda t: (t[0], _natural_name_key(t[1])))
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
        且列均灰度偏离背景主色；或**浅灰指示条**（列均灰 ≥205、几乎无深色
        像素段、整列偏离背景）。
    滚动条检测基于结构：它是与内容有白缝分隔、位于右侧的窄灰竖条——因此除
    宽度/位置外还必须满足"左侧有白缝"与"列内暗像素段数极少"，否则正文/图表
    最右侧的列会被误判成滚动条而被裁掉（行末字被切半 → OCR 错字 + 假分段）。
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
    del gx, gy, mag
    hist = np.bincount(gray.astype(np.int64).ravel(), minlength=256)
    bg = int(hist.argmax())
    # 滚动条列：列均灰度 150-210（灰带）+ bimodal（深+浅都>15%）+
    # 结构性：整列"暗像素段"数量少（滚动条=轨道+滑块，暗像素集中在少数几段；
    # K 线图内容列=大量分散的蜡烛，暗像素段很多）。
    dr = (gray < 70).mean(axis=0); lr = (gray > 210).mean(axis=0)
    col_mean = gray.mean(axis=0)
    col_ink = (gray < 200).mean(axis=0)      # 明显偏暗（墨迹）像素占比
    col_off = (gray < bg - 6).mean(axis=0)   # 偏离背景的像素占比（白缝≈0）
    dark_mask = (gray < 70)
    # 每列暗像素的连续段数（行维）：统计"非暗→暗"的跳变次数
    col_dark_runs = np.zeros(W, dtype=int)
    if dark_mask.shape[0] > 1:
        starts = dark_mask[1:] & ~dark_mask[:-1]
        col_dark_runs = starts.sum(axis=0)
        col_dark_runs += dark_mask[0]  # 首行是暗也算一段开始
        del starts, dark_mask
    # 滚动条列候选（两类）：
    #   ① 深/中灰滚动条：列均灰 150-210（灰带）+ bimodal（深+浅都>15%，轨道+滑块）
    #      + 暗像素段数少（滑块是连续一两段；K 线图内容列是大量分散蜡烛）。
    #   ② **浅灰滚动指示条**（iOS 式浅色条，浮在白底/浅底内容右侧）：列均灰 ≥205、
    #      几乎没有深色像素段，且"有偏暗像素"或"整列明显偏离背景"。只认 ① 会把
    #      这种浅条留在图里（成图右侧多出一条灰竖条）。
    sb_col = (((dr > 0.15) & (lr > 0.15) & (col_mean > 150) & (col_mean < 210)
               & (col_dark_runs < 100))
              | ((col_mean >= 205) & (col_ink < 0.6) & (col_dark_runs < 15)
                 & ((col_ink >= 0.03) | (col_off >= 0.5))))
    # 滚动条段：连续的 sb_col，取最右段，且需同时满足：
    #   ① 宽度 W*[0.5%, 8%]、距右边 ≤ W*10%；
    #   ② **左侧有白缝**——滚动条是独立于内容的细条，与内容之间留白；
    #   ③ 列内暗像素"段数"极少——轨道+滑块是一两段连续暗区。
    # ②③ 是防误判的关键：正文/图表最右侧的几列同样"灰调、明暗混合、贴近右边缘"，
    # 但它们是整列文字或蜡烛线（暗像素段数几十上百），且左邻就是内容（没有白缝）。
    # 把这种列当滚动条裁掉会**切掉行末文字**——行末字被切掉右半边后 OCR 会把
    # 「少」认成「！」、「概」认成「相」，正文出现错字，还会因伪句末标点把一句话
    # 误切成两段（段首判定是"上一行以句末标点结尾"）。
    w_min = max(3, int(W * 0.005)); w_max = max(w_min + 1, int(W * 0.08))
    dist_max = int(W * 0.10)
    gutter = max(3, int(W * 0.005))
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
            if not (w_min <= w <= w_max and (W - 1 - re) <= dist_max):
                continue
            if int(col_dark_runs[rs:re+1].max()) >= 15:
                continue        # ③ 暗像素段太多 → 文字/图表列，不是滚动条
            if rs > 0:
                _gut = col_off[max(0, rs - gutter):rs]
                if _gut.size and float(_gut.max()) >= 0.03:
                    continue    # ② 左侧紧邻内容（无白缝）→ 不是滚动条
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
    lines = []
    for name in sorted(long_image_pages):
        nums = sorted(set(long_image_pages[name]))
        lines.append(f"{name}\t{','.join(str(x) for x in nums)}")
    _atomic_write_text(p, "\n".join(lines) + ("\n" if lines else ""))


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
    """把空白页号写入记录文件（覆盖），供后续流程统一过滤。"""
    p = blank_pages_path(out)
    _atomic_write_text(p, " ".join(str(x) for x in sorted(set(page_nums))))


def _alloc_page_numbers(preferred, count, used):
    """为一张长图的 count 个连续切片选起始页号。

    优先从 preferred（该素材在 assets 里的原始页号）开始，让页号尽量贴近原始编号；
    若区间 [preferred, preferred+count) 与**已占用页号** used（普通页或其他长图切片）相交，
    则把**整段**后移到 max(used)+1。

    为什么要整段后移而不是逐个跳过占用号：逐个跳号会把一张长图的切片页号打散，
    跨页渲染时切片顺序错乱。为什么绝不能与 used 重叠：同一批页被两个"长图组"
    同时登记后，build_md.auto_chapters 会逐组各自成一篇文章，于是同一批页被切成
    两篇（重复正文，且后一篇的标题会取到页中间的正文句）。
    """
    if count <= 0:
        return preferred
    if any((preferred + i) in used for i in range(count)):
        return (max(used) + 1) if used else preferred
    return preferred


def prepare_images(assets_dir, pages, out, force=defaults.FORCE,
                   max_ratio=defaults.SPLIT_LONG_IMAGE_MAX_RATIO,
                   max_h=defaults.SPLIT_SLICE_MAX_H,
                   min_h=defaults.SPLIT_SLICE_MIN_H,
                   slice_ratio=defaults.SPLIT_SLICE_RATIO):
    """把输入图片统一归一化为 JPEG，输出到 <out>/_ocr_tmp/。
    返回 (临时图片目录, 展开后的 pages 字典)。完全沿用 run.py 的 prepare_images。

    处理三件事：
    1. 自动过滤"完全空白"的图片（如透明白图）：不产出对应的 p{n}.jpg，
       并把空白页号写入 <out>/blank_pages.txt 持久化。
    2. 超长图（高宽比 >= max_ratio，如滚动长截图）内容感知切分为多片：
       在空白行处切，避免切断内部图表；每片登记为新页（连续页号），
       扩入 pages，使 OCR/detect/build 按多页处理。切片页号由
       `_alloc_page_numbers` 分配，保证**各组页号互不相交**（相交会让
       auto_chapters 把同一批页切成两篇重复文章）。
    3. 按当前素材**重建** <out>/long_image_pages.txt（不 merge 历史），
       并清掉页号重排后不再使用的历史切片图，避免"幽灵章节/幽灵页"。

    Args:
        force: True 时绕过 mtime 缓存，重新处理所有页（配合 --force 全步骤无缓存）
        max_ratio: 超过该高宽比即视为超长图，触发切分
        max_h / min_h: 切分后每片高度区间（px）
        slice_ratio: 切片高度上限相对图片宽度的比例（约=内容区高/宽）。
            超长图每片高 ≤ max(slice_ratio*W, min_h)，使各片宽度受限
            （scale=content_w/W）→ 图片宽度全页一致，不出现"页面宽度不一"。
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
    # 已占用页号：当前所有普通页 + 本轮已切出的长图切片。切片页号必须避开它们，
    # 否则会覆盖普通页的 pN.jpg，或让同一批页属于两个长图组而重复成章。
    used_pages = set(pages)
    slice_pages = set()
    for n, fname in sorted(pages.items()):
        fname_key = fname
        src = os.path.join(assets_dir, fname)
        dst = os.path.join(tmp, f"p{n}.jpg")
        if not os.path.isfile(src):
            continue
        # 先判空白（放在 mtime 缓存判断之前），保证重跑时也能清掉历史残留的空白页
        with Image.open(src) as opened:
            # 预处理：边缘检测裁四边空白 + 去掉右侧灰色滚动条（避免误识别为内容）
            im = _trim_image(opened).copy()
        if _is_blank_image(im):
            # 仅当 p{n}.jpg 确属本文件时才删：页号 n 可能已被别的长图切片占用，
            # 那种情况下 dst 是别人的切片，删了就丢内容。
            if new_pages.get(n) == fname and os.path.isfile(dst):
                os.remove(dst)
            blank += 1
            blank_pages.append(n)
            continue

        W, H = im.size
        # 超长图 → 内容感知切分
        if H > min_h and H / W >= max_ratio:
            rgb = _to_rgb(im)
            gray = np.asarray(rgb.convert("L"))
            # 切片高度上限同时受调用方 max_h 与图片宽度 × slice_ratio 约束，
            # 并保证不低于 min_h。
            _slice_max_h = max(min_h, min(max_h, int(W * slice_ratio)))
            cuts = _find_split_points(gray, max_h=_slice_max_h, min_h=min_h)
            if cuts:
                # 清掉本文件可能存在的旧切片/旧单页（只删属于本文件 fname 的页，
                # 避免误删别的长图（如 8-28 的切片）被 8-29 的清理误删）
                for pn in [p for p, f in new_pages.items() if f == fname]:
                    old = os.path.join(tmp, f"p{pn}.jpg")
                    if os.path.isfile(old):
                        os.remove(old)
                    del new_pages[pn]
                pts = [0] + cuts + [H]
                # 合并过小的切片（多为尾部空白残留）：<200px 并入前一片，避免无意义的小页
                merged = []
                for y0, y1 in zip(pts[:-1], pts[1:]):
                    if merged and y1 - merged[-1][1] < 200:
                        merged[-1] = (merged[-1][0], y1)
                    else:
                        merged.append((y0, y1))
                merged = [seg for seg in merged if seg[1] - seg[0] >= 200]
                # 当前长图的原始页号已让给切片，不再视为"被普通页占用"；
                # 但已被别的长图切片占用时必须保留，避免两组相交。
                if n not in slice_pages:
                    used_pages.discard(n)
                # 连续编号：优先从 n 开始；与已分配页号相交则整段后移（见 helper 注释）
                _pn = _alloc_page_numbers(n, len(merged), used_pages)
                for y0, y1 in merged:
                    crop = rgb.crop((0, y0, W, y1))
                    crop.save(os.path.join(tmp, f"p{_pn}.jpg"), "JPEG", quality=95)
                    new_pages[_pn] = fname_key
                    long_image_pages.setdefault(fname_key, []).append(_pn)
                    used_pages.add(_pn)
                    slice_pages.add(_pn)
                    _pn += 1
                if merged:
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
    # 清掉页号重排后不再使用的历史切片图：OCR 是按 p{start}..p{end} 区间跑的，
    # 残留的旧 p{n}.jpg 会被当成有效页 OCR，产出"幽灵页"。
    for _f in os.listdir(tmp):
        _m = TMP_PAGE_RE.match(_f)
        if _m and int(_m.group(1)) not in new_pages:
            try:
                os.remove(os.path.join(tmp, _f))
            except OSError:
                pass
    # **按当前素材重建**（不再 merge 历史）long_image_pages：素材被删除/改名或
    # 页号被重排后，旧分组残留就会变成"幽灵章节"（auto_chapters 是逐组各自成篇，
    # 会凭空多出重复/无源篇目）。
    write_long_image_pages(out, long_image_pages)
    print(f"图片预处理完成：{copied} 张 JPEG 直接复制 + {converted} 张转 JPEG"
          f" + {blank} 张空白过滤 + {sliced} 张长图切分（临时目录 {tmp}）")
    return tmp, new_pages


def run(images_dir, work_dir, start=None, end=None, force=defaults.FORCE,
        engine=defaults.ENGINE,
        split_long_image_max_ratio=defaults.SPLIT_LONG_IMAGE_MAX_RATIO,
        split_slice_max_h=defaults.SPLIT_SLICE_MAX_H,
        split_slice_min_h=defaults.SPLIT_SLICE_MIN_H,
        split_slice_ratio=defaults.SPLIT_SLICE_RATIO):
    """执行 OCR 步骤。

    Args:
        images_dir: 源图片目录
        work_dir: 中间产物根目录
        start/end: 页码范围（None=自动探测）
        force: 是否忽略 OCR 缓存
        engine: OCR 引擎（auto/swift/rapidocr），None=按平台默认
        split_*: 超长图切分参数
    Returns:
        (img_for_ocr, start, end)：预处理后的图目录与最终范围
    """
    out = paths.work_path(work_dir)
    out.mkdir(parents=True, exist_ok=True)
    ocr_cache = paths.ocr_dir(out)
    ocr_cache.mkdir(parents=True, exist_ok=True)

    engine = resolve_engine(engine)
    bin_path = None
    if engine == "swift":
        bin_path = str(ocr_cache / "ocr_batch")
        stale = (not os.path.exists(bin_path)
                 or (SWIFT_SRC.exists()
                     and os.path.getmtime(bin_path) < os.path.getmtime(SWIFT_SRC)))
        if stale:
            if not SWIFT_SRC.exists():
                raise SystemExit(f"错误：找不到 ocr_batch.swift（{SWIFT_SRC}）")
            subprocess.run(["swiftc", "-o", bin_path, str(SWIFT_SRC)], check=True)

    source = Path(images_dir).expanduser().resolve()
    if source.is_file():
        if source.suffix.lower() not in IMAGE_EXTS:
            raise SystemExit(f"错误：不支持的图片文件 {source}")
        staged = paths.single_input_dir(out)
        try:
            if staged.exists():
                shutil.rmtree(staged)
            staged.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, staged / f"p1{source.suffix.lower()}")
        except Exception as exc:
            raise SystemExit(f"错误：单张图片准备失败 {source}: {exc}") from exc
        images_dir = str(staged)
    elif not source.is_dir():
        raise SystemExit(f"错误：图片目录或文件不存在 {source}")
    else:
        images_dir = str(source)

    pages = scan_pages(images_dir)
    if not pages:
        raise SystemExit(f"错误：目录 {images_dir} 里没有图片输入")
    print(f"图片页: {len(pages)}")

    rng = auto_page_range(pages)
    start = start if start is not None else rng[0]
    end = end if end is not None else rng[1]
    print(f"共 {len(pages)} 页，OCR 范围 p{start}..p{end}")

    img_for_ocr, pages = prepare_images(
        images_dir, pages, str(out), force=force,
        max_ratio=split_long_image_max_ratio, max_h=split_slice_max_h,
        min_h=split_slice_min_h, slice_ratio=split_slice_ratio)

    # 切分后页号可能扩展（长图→多片），用展开后的 pages 重算范围
    rng2 = auto_page_range(pages)
    if start is None or (rng2 and rng2[1] > end):
        end = rng2[1] if rng2 else end
    if rng2:
        start = min(start, rng2[0]) if start is not None else rng2[0]

    pages_dir = paths.ocr_pages_dir(out)
    json_dir = paths.ocr_pages_json_dir(out)
    pages_dir.mkdir(parents=True, exist_ok=True)
    json_dir.mkdir(parents=True, exist_ok=True)
    stale = _clean_ocr_results(str(json_dir), str(pages_dir),
                               set(pages), img_for_ocr)
    if stale:
        shown = " ".join(str(p) for p in sorted(stale)[:10])
        print(f"  已失效并重跑 OCR 缓存 {len(stale)} 页: {shown}"
              f"{' ...' if len(stale) > 10 else ''}")
    if engine == "swift":
        print("运行 OCR（macOS Vision, zh-Hans + en，逐页复用缓存）...")
        run_ocr_resilient(bin_path, img_for_ocr, str(pages_dir), str(json_dir),
                          start, end, reuse_broken=False, force=force)
    else:
        print("运行 OCR（RapidOCR, 中文小模型，逐页复用缓存）...")
        run_ocr_rapidocr(img_for_ocr, str(pages_dir), str(json_dir),
                         start, end, force=force)
    return img_for_ocr, start, end
