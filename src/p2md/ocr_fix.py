# -*- coding: utf-8 -*-
"""OCR 纠错内置通用层（全库共享）。

同一 OCR 框架（macOS Vision）对中文字形常出**相同**的形近字误识（如 升→开、
结→经），与具体调用方无关。本模块内置一套通用纠错规则，把 pages_json 里每行的
OCR 错字改成正确词，供后续 detect → build 流程共用。

内置规则（本文件 GLOBAL_*）默认开启，可用 ``ocr_fix_global=False`` 关闭；
调用方专属纠错请通过 ``middlewares`` 脚本实现。

规则只做"确定的错误组合 → 正确词"的整词替换，避免无脑单字替换误伤正确文字。

LOG：每次修正打印 [ocr_fix] p{n} 旧文本 → 新文本，便于核对改动。

跨行拆词：OCR 会把"结构"的"结"（误为"经"）放上一行行尾、"构"放下一行行首，
此时单行替换无法命中。CROSS_LINE_FIXES 用 (锚定词, 行尾误字, 下一行首字, 应改字)
处理：某行含锚定词且以误字结尾、下一行以对应首字开头 → 改行尾误字。
"""

import json
import os

from . import defaults, paths

# ---- 内置通用规则（形近字高频错，数据驱动，可按新发现追加）----
#
# ⚠ 安全红线：全局默认层会作用到**所有项目**，规则必须零歧义——
#   错误子串在任意中文语境里都不应是正确文本的一部分。
#   例如 "上开"→"上升" 会误伤 "表面上开始"（"上"+开始）——不可入全局！
#   有歧义的规则请放调用方 middlewares 脚本，由调用方人工把关。
#
# 行内词替换：错误子串 -> 正确子串。
GLOBAL_WORD_FIXES = [
    # （当前无零歧义行内规则；有确凿项在此追加）
]

# 跨行拆词修复：OCR 把词拆行且行尾字形近误识。规则带锚定词强上下文，
# 锚定词必须是"该语境下不会出现在别的书/别处"或无害（不命中即不触发）。
# 元素 = (锚定词, 行尾误字, 下一行首字, 行尾应改为)
GLOBAL_CROSS_LINE_FIXES = [
    # "…双底经" + "构，…" → "…双底结构，…"（结->经 形近；仅当锚定词"双底/双顶"且
    # 行尾"经"、下行首"构"同时成立才触发，其余书无此语境不会误伤）
    ("双底", "经", "构", "结"),
    ("双顶", "经", "构", "结"),
]


def _apply_word_fixes(t, word_fixes):
    new = t
    for bad, good in word_fixes:
        if bad in new:
            new = new.replace(bad, good)
    return new


def _filter_watermark_lines(data, log, h_ratio=4.0, max_len=12,
                            header_pages=None):
    """滤除"异常/噪音行"：斜排水印误识行 + （长图首片的）状态栏残渣。返回滤除行数。

    一、斜排水印（Vision 对斜排半透明水印会给异常大的 bbox：行高是正文行高的数倍
    甚至 10+ 倍，且文本短、无句读）：
      - 该行 h ≥ h_ratio × 页正文行高(h 中位)——正文行高差异小，中位稳；
      - 文本短（≤max_len）且不是数字/时间戳、不以句号/冒号结尾；
      - 同页中与它相似的水印文本反复出现（水印整页平铺）或文本是重复短词
        （斜排水印常被拆成"闲鱼嘘嘘敦敦"这类无意义短词）。

    二、状态栏残渣（截长截图带进来的时间、电量、信号等），**仅对 header_pages 里
    的页**（长图各组的首片）生效：位于页面最顶部（y_top ≤ 4% 页高）且**完全不含
    中日韩文字**的极短行，如 "11:22"、"×11 85"、"09:49"、"•"、"<"。判据纯形态、
    与平台无关；限定首片是为了不误伤普通页顶部可能出现的图表参数标注。

    Args:
        header_pages: 允许做状态栏滤除的页号集合（长图各组首片）；None = 不做该项。
    """
    lines = data.get("lines", [])
    if not lines:
        return 0
    hs = [ln.get("h", 0) for ln in lines if (ln.get("text") or "").strip()]
    if not hs:
        return 0
    med_h = float(sorted(hs)[len(hs) // 2])
    if med_h <= 0:
        return 0
    import re as _re
    text_cnt = {}
    for ln in lines:
        t = (ln.get("text") or "").strip()
        if t:
            text_cnt[t] = text_cnt.get(t, 0) + 1
    kept = []
    n_removed = 0
    for ln in lines:
        t = (ln.get("text") or "").strip()
        h = ln.get("h", 0)
        is_word_wm = bool(_re.search(
            r"(闲鱼|嘘嘘|敦敦|嘘嚓|嘘飯|嘘敦|鱼嘘|响彭敦|CamScanner|扫描全能王|仅供内部|禁止外传)", t))
        # 罕见水印字片段（斜排水印被 OCR 拆散, 正文几乎不含这些字; 含字形变体
        # 嘘0x5653/嘘0x5618/魚0x9b5a/鱼0x9c7c/響/响/敦0x6566/尋0x5c0b 等）
        # → 行内含任一 + 超高 + 极短(≤4) 即滤, 不会误删含正常词汇的行。
        rare_wm = (h >= med_h * h_ratio and 1 <= len(t) <= 4
                   and bool(_re.search(r"[嘘嘘魚鱼响響彭敦敦尋寻咸咸]", t)))
        # 弱水印形态：超大 bbox + 短文本 + 无句读 + 同页重复 ≥2
        #（斜排整词水印如"闲鱼嘘嘘敦敦"会被 OCR 成同一文本多次识别）。
        weak_wm = (h >= med_h * h_ratio and len(t) <= max_len
                   and not _re.match(r"^[\d\s:：.．-]+$", t)
                   and not t.endswith(("。", "！", "？", "："))
                   and text_cnt.get(t, 0) >= 2)
        # 页面顶部状态栏残渣：仅长图首片 + 最顶部 + 完全不含中日韩文字 + 极短
        try:
            y_top = 1.0 - float(ln.get("y", 0.0)) - float(h)
        except (TypeError, ValueError):
            y_top = 1.0
        top_status = (header_pages is not None
                      and data.get("page") in header_pages
                      and len(t) <= 10 and y_top <= 0.04
                      and not _re.search(r"[\u4e00-\u9fff]", t))
        if t and (is_word_wm or rare_wm or weak_wm or top_status):
            n_removed += 1
            tag = "hdr" if top_status and not (is_word_wm or rare_wm or weak_wm) else "wm"
            log(f"    [{tag}] 滤除噪音行: 「{t}」")
            continue
        kept.append(ln)
    if n_removed:
        data["lines"] = kept
    return n_removed


def _fix_lines_in_page(data, word_fixes, cross_line_fixes, page_label, log):
    """对一页 lines 应用纠错，返回修正数。修改直接写回 data["lines"]。"""
    lines = data.get("lines", [])
    if not lines:
        return 0
    # 阅读序：OCR y 为左下原点，y 降序 = 从上到下（跨行修复依赖相邻顺序）
    lines.sort(key=lambda ln: ln.get("y", 0.0), reverse=True)
    n_fixed = 0
    for idx, ln in enumerate(lines):
        t = ln.get("text", "")
        if not t:
            continue
        new = _apply_word_fixes(t, word_fixes)
        if idx + 1 < len(lines):
            nxt = lines[idx + 1].get("text", "").lstrip()
            for anchor, tail_wrong, nxt_head, tail_right in cross_line_fixes:
                if (anchor in new and new.rstrip().endswith(tail_wrong)
                        and nxt.startswith(nxt_head)):
                    cut = len(new.rstrip()) - len(tail_wrong)
                    new = new[:cut] + tail_right
                    break
        if new != t:
            ln["text"] = new
            n_fixed += 1
            log(f"    [ocr_fix] {page_label}: 「{t}」→「{new}」")
    return n_fixed


def fix_pages(ocr_dir, word_fixes, cross_line_fixes, log=print):
    """对 pages_json 全部页应用纠错规则。返回修正总数。

    Args:
        ocr_dir: output/ocr/pages_json/ 目录
        word_fixes: [(错误子串, 正确子串), ...]
        cross_line_fixes: [(锚定词, 行尾误字, 下行首字, 应改字), ...]
        log: 打印函数（默认 print），每次修正会调用
    """
    total = 0
    for fname in sorted(os.listdir(ocr_dir)):
        if not fname.endswith(".json"):
            continue
        path = os.path.join(ocr_dir, fname)
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        page_label = f"p{data.get('page', fname)}"
        n = _fix_lines_in_page(data, word_fixes, cross_line_fixes,
                               page_label, log)
        if n:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            total += n
    return total


def run_global_fix(work_dir, enabled=defaults.OCR_FIX_GLOBAL, log=print) -> int:
    """运行内置通用 OCR 纠错。

    Returns:
        修正的字符/行数（0 = 未启用或未修正）。
    """
    if not enabled:
        return 0
    work = paths.work_path(work_dir)
    ocr_dir = str(paths.ocr_pages_json_dir(work))
    if not os.path.isdir(ocr_dir):
        return 0
    log("  (OCR 内置通用纠错: 形近字修正 + 水印行滤除 + 长图首片状态栏滤除)")
    total = fix_pages(ocr_dir, GLOBAL_WORD_FIXES, GLOBAL_CROSS_LINE_FIXES, log=log)
    # 长图各组首片页号：状态栏残渣（时间/电量/信号）只会在长截图首片出现，故限定
    # 首片做该项滤除，避免误伤普通页顶部可能出现的图表参数标注。
    header_pages = None
    try:
        from .ocr import read_long_image_pages
        lip = read_long_image_pages(str(work))
        if lip:
            header_pages = {min(ps) for ps in lip.values() if ps}
    except Exception:
        header_pages = None
    # 斜排水印行滤除（通用：Vision 对斜排半透明水印给超大 bbox + 短文本特征）
    wm = 0
    for fname in sorted(os.listdir(ocr_dir)):
        if not fname.endswith(".json"):
            continue
        path = os.path.join(ocr_dir, fname)
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        n = _filter_watermark_lines(data, log, header_pages=header_pages)
        if n:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            wm += n
    if wm:
        log(f"    滤除噪音行 {wm} 处")
    log(f"    共修正 {total} 处")
    return total + wm
