# -*- coding: utf-8 -*-
"""么瑞项目 OCR 中间件：清理文章头部的多余内容（仅本 project 生效）。

由 ocr-book-to-md 在 OCR 产出 pages_json 后、text/layed 分流前调用（见 config.yaml
detect.middleware）。删除匹配的 OCR line，使 text 流（detect→blocks→md）和 layed 流
（直接读 lines）的 md 都干净（公众号头部"已付费/作者署名/阅读时间/时间戳"）。

接口：def process(ocr_dir: str, project_path: str) -> None
遍历 pages_json/*.json，删除文本含任一模式的 line 后写回。
这些模式是么瑞专属的头部标识（作者名/付费标签/阅读时间），正文不会出现。
"""

import json
import os

# 要清理的头部行（子串匹配）。正文不会出现这些词，安全。
STRIP_PATTERNS = ["已付费", "原创 么瑞", "听全文", "11:"]


def process(ocr_dir: str, project_path: str) -> None:
    n_removed = 0
    for fname in sorted(os.listdir(ocr_dir)):
        if not fname.endswith(".json"):
            continue
        path = os.path.join(ocr_dir, fname)
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        lines = data.get("lines", [])
        kept = []
        for ln in lines:
            txt = ln.get("text", "")
            if txt and any(p in txt for p in STRIP_PATTERNS):
                n_removed += 1
            else:
                kept.append(ln)
        data["lines"] = kept
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    print(f"  已清理 {n_removed} 个头部 OCR line")
