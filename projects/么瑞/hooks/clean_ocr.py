# -*- coding: utf-8 -*-
"""么瑞项目 OCR 中间件：清理文章头部的多余内容（仅本 project 生效）。

由 ocr-book-to-md 在 OCR 产出 pages_json 后、text/layed 分流前调用（见 config.yaml
detect.middleware）。删除匹配的 OCR line，使 text 流（detect→blocks→md）和 layed 流
（直接读 lines）的 md 都干净。

清理对象 = 公众号文章头部的一行"元信息"，不同期（8月/9月…）形态略有差异，故用
**模式规则**而非枚举具体文本。保留标题行（如"9月周总结第0期（08.31~09.06）"）。

模式清单（每一期都可核对补全，规则针对本 project 的公众号头部，正文不含这些）：
  1) 时间戳行：如 "09:49" / "11:53 ③"
  2) 带圈 emoji 碎片：如 "③0"
  3) 付费标签：如 "已付费"
  4) 作者行：以 "原创" 开头（如 "原创 么瑞 么瑞与小A 2026年8月29日 09:28 陕西6听全文"）
  5) 地域+阅读人数：如 "陕西61人" / "陕西6听全文"
  6) 引导语："听全文"

接口：def process(ocr_dir: str, project_path: str) -> None
遍历 pages_json/*.json，删除文本命中任一规则的 line 后写回。
标题行（含"周总结"或形如 （…~…）日期）受保护不删。
"""

import json
import os
import re

# 命中即删除的头部元信息规则（按行整体判断）
STRIP_RULES = [
    re.compile(r"^\s*\d{1,2}:\d{2}"),   # 时间戳：09:49 / 11:53 ③
    re.compile(r"^③"),                   # 带圈 emoji 碎片：③0
    re.compile(r"已付费"),
    re.compile(r"^原创"),                # 作者行
    re.compile(r"^陕西"),                # 地域+阅读人数：陕西61人
    re.compile(r"听全文"),
    re.compile(r"^[^\w\u4e00-\u9fff]{1,3}$"),  # 纯符号残渣：OCR 把装饰/图标识成 "<"、"→" 等
]

# 标题保护：含"周总结"或形如（…~…）的日期区间 → 不删
_TITLE_RE = re.compile(r"周总结|（\d+[.\d]*\s*~\s*\d+[.\d]*）")


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
            txt = ln.get("text", "").strip()
            if not txt:
                continue
            if _TITLE_RE.search(txt):
                kept.append(ln)  # 标题/日期行保留
                continue
            if any(r.search(txt) for r in STRIP_RULES):
                n_removed += 1
            else:
                kept.append(ln)
        data["lines"] = kept
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    print(f"  已清理 {n_removed} 个头部 OCR line")
