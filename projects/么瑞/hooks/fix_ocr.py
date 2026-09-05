# -*- coding: utf-8 -*-
"""么瑞项目 OCR 纠错钩子（per-project 追加层，仅本 project 生效）。

分层：
  - Vision 框架常见、且**零歧义**的形近错 → 内置通用层 src/ocr_book_to_md/ocr_fix.py
    （默认全项目开启，如跨行"…双底经 + 构"→"…双底结构"）。
  - 本文件只放么瑞**专属**规则。通用替换有歧义的（如"上开"可能是"上"+开始）
    也必须放这里，用上下文正则限定，避免误伤。

接口：def process(ocr_dir: str, project_path: str) -> None
遍历 pages_json/*.json，按阅读序(y 降序)处理 lines，每次修改打印 LOG 后写回。
"""
import json
import os
import re

# 么瑞专属行内纠错：正则 (pattern, 替换) 或子串 (旧, 新)。
# 例：Vision 把"上升"的"升"识成"开" → "3浪上开"。
# 注意不能裸替"上开→上升"（会误伤"表面上开始"），须限定在"数字+浪+上开"语境。
WORD_REGEXES = [
    (re.compile(r"(?<=\d)浪上开"), r"浪上升"),      # "3浪上开"→"3浪上升"
    (re.compile(r"(?<=[0-9一二三四五六七八九十])上开"), "上升"),  # "2上开"等兜底
]


def process(ocr_dir: str, project_path: str) -> None:
    n_fixed = 0
    for fname in sorted(os.listdir(ocr_dir)):
        if not fname.endswith(".json"):
            continue
        path = os.path.join(ocr_dir, fname)
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        lines = data.get("lines", [])
        if not lines:
            continue
        lines.sort(key=lambda ln: ln.get("y", 0.0), reverse=True)  # 阅读序
        changed = False
        for ln in lines:
            t = ln.get("text", "")
            if not t:
                continue
            new = t
            for rx, repl in WORD_REGEXES:
                new2 = rx.sub(repl, new)
                if new2 != new:
                    new = new2
            if new != t:
                print(f"    [ocr_fix] p{data.get('page', fname)}: 「{t}」→「{new}」")
                ln["text"] = new
                changed = True
                n_fixed += 1
        if changed:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
    print(f"  共修正 {n_fixed} 处么瑞专属 OCR 错词")
