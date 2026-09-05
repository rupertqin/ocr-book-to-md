# -*- coding: utf-8 -*-
"""OCR 纠错内置通用层（全 project 共享）。

同一 OCR 框架（macOS Vision）对中文字形常出**相同**的形近字误识（如 升→开、
结→经），这些与具体项目无关。本模块内置一套通用纠错规则，**默认对所有项目生效**，
把 pages_json 里每行的 OCR 错字改成正确词，供 text 流（detect→blocks→md）与
layed 流（直接读 lines）共用。

分层：
  1) 内置通用规则（本文件 GLOBAL_*，默认全项目开启，config detect.ocr_fix_global:false 可关）
  2) per-project 追加（config detect.ocr_fix 指向项目脚本，追加项目专属纠错）

规则只做"确定的错误组合 → 正确词"的整词替换，避免无脑单字替换误伤正确文字。

LOG：每次修正打印 [ocr_fix] p{n} 旧文本 → 新文本，便于核对改动。

跨行拆词：OCR 会把"结构"的"结"（误为"经"）放上一行行尾、"构"放下一行行首，
此时单行替换无法命中。CROSS_LINE_FIXES 用 (锚定词, 行尾误字, 下一行首字, 应改字)
处理：某行含锚定词且以误字结尾、下一行以对应首字开头 → 改行尾误字。
"""

import json
import os

# ---- 内置通用规则（形近字高频错，数据驱动，可按新发现追加）----
#
# ⚠ 安全红线：全局默认层会作用到**所有项目**，规则必须零歧义——
#   错误子串在任意中文语境里都不应是正确文本的一部分。
#   例如 "上开"→"上升" 会误伤 "表面上开始"（"上"+开始）——不可入全局！
#   有歧义的规则请放 per-project 脚本（detect.ocr_fix），由项目人工把关。
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


def run_global_fix(project, log=print) -> int:
    """运行内置通用 OCR 纠错（全 project）。config detect.ocr_fix_global=false 可关。

    Returns:
        修正的字符/行数（0 = 未启用或未修正）。
    """
    from .config import Config
    cfg = Config(str(project.path))
    enabled = cfg.get("detect.ocr_fix_global", True)
    if not enabled:
        return 0
    ocr_dir = str(project.get_ocr_pages_json_dir())
    if not os.path.isdir(ocr_dir):
        return 0
    log("  (OCR 内置通用纠错: 形近字修正)")
    total = fix_pages(ocr_dir, GLOBAL_WORD_FIXES, GLOBAL_CROSS_LINE_FIXES, log=log)
    log(f"    共修正 {total} 处")
    return total
