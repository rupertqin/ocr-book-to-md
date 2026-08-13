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
import argparse, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable

def run(cmd, **kw):
    print(">>", " ".join(cmd))
    subprocess.run(cmd, check=True, **kw)

def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--images", default="data/images",
                    help="书的截图目录（默认 data/images）")
    ap.add_argument("--out", default="out",
                    help="输出目录（默认 out/，生成 md/ + figs/ 集合）")
    ap.add_argument("--start", type=int, help="起始页（默认自动探测）")
    ap.add_argument("--end", type=int, help="结束页")
    ap.add_argument("--force-ocr", action="store_true", help="忽略 ocr 缓存，强制重跑 OCR")
    args = ap.parse_args()

    out = os.path.abspath(args.out)
    os.makedirs(out, exist_ok=True)
    ocr_cache = os.path.join(out, "ocr")

    # ---------- ① OCR：无缓存才跑，产物固定进 <out>/ocr/ ----------
    if os.path.isdir(os.path.join(ocr_cache, "pages_json")) and not args.force_ocr:
        print("复用 OCR 缓存:", ocr_cache)
    else:
        print("运行 OCR（macOS Vision, zh-Hans + en）...")
        os.makedirs(os.path.join(ocr_cache, "pages"), exist_ok=True)
        os.makedirs(os.path.join(ocr_cache, "pages_json"), exist_ok=True)
        bin_path = os.path.join(ocr_cache, "ocr_batch")
        if not os.path.exists(bin_path):
            run(["swiftc", "-o", bin_path, os.path.join(HERE, "ocr_batch.swift")])
        start = args.start if args.start is not None else 3
        end = args.end if args.end is not None else 99999
        run([bin_path, args.images,
             os.path.join(ocr_cache, "pages"),
             os.path.join(ocr_cache, "pages_json"),
             str(start), str(end)])

    # ---------- ② 检测 + ③ 组装（路径/检查内化在子脚本） ----------
    run([PY, os.path.join(HERE, "detect_figs.py"),
         "--img-dir", args.images, "--out-dir", out])
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
