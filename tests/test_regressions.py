# -*- coding: utf-8 -*-
"""审查发现的回归场景：配置、缓存、章节文件、错误呈现。"""

import json
import os
import sys
from pathlib import Path

import pytest

from p2md import api, cli, config_file, detect, middleware, ocr, paths


def test_unknown_config_key_rejected(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("assets_dir: assets\nwrok: work\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="未知配置键: wrok"):
        config_file.load_config(str(path))


def test_cli_loads_chapters_file_from_config_dir(monkeypatch, tmp_path):
    chapters = [{"kind": "chapter", "file": "a.md", "title": "A",
                 "start": 1, "end": 2}]
    (tmp_path / "chapters.json").write_text(
        json.dumps(chapters, ensure_ascii=False), encoding="utf-8")
    path = tmp_path / "config.yaml"
    path.write_text(
        "assets_dir: assets\nwork: work\nchapters: chapters.json\n",
        encoding="utf-8")
    captured = {}

    def fake_convert(images_dir, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(api, "convert_images", fake_convert)
    monkeypatch.setattr(sys, "argv", ["p2md", "pipeline", "--config", str(path)])
    cli.main()
    assert captured["chapters"] == chapters


def test_ocr_uses_per_page_cache_not_directory_shortcut(monkeypatch, tmp_path):
    images = tmp_path / "images"
    images.mkdir()
    work = tmp_path / "work"
    json_dir = paths.ocr_pages_json_dir(work)
    json_dir.mkdir(parents=True)
    (json_dir / "1.json").write_text('{"page": 1, "lines": []}', encoding="utf-8")

    monkeypatch.setattr(ocr, "scan_pages", lambda _d: {1: "p1.jpg"})
    monkeypatch.setattr(ocr, "prepare_images",
                        lambda *_a, **_k: (str(images), {1: "p1.jpg"}))
    monkeypatch.setattr(ocr, "resolve_engine", lambda _e: "rapidocr")
    called = {}
    monkeypatch.setattr(
        ocr, "run_ocr_rapidocr",
        lambda *_a, **_k: called.setdefault("called", True))

    ocr.run(str(images), str(work), engine="rapidocr")
    assert called["called"] is True


def test_missing_middleware_is_not_counted(tmp_path):
    work = paths.work_path(tmp_path / "work")
    paths.ocr_pages_json_dir(work).mkdir(parents=True)
    count = middleware.run_middlewares(
        str(work), [str(tmp_path / "missing.py")], base_dir=str(tmp_path))
    assert count == 0


def test_detect_passes_heading_plain_ratio(monkeypatch, tmp_path):
    work = paths.work_path(tmp_path / "work")
    img_dir = paths.ocr_tmp_dir(work)
    json_dir = paths.ocr_pages_json_dir(work)
    img_dir.mkdir(parents=True)
    json_dir.mkdir(parents=True)
    (json_dir / "1.json").write_text('{"page": 1, "lines": []}', encoding="utf-8")
    captured = {}

    def fake_process(_n, *_args, **kwargs):
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(detect, "_process_page", fake_process)
    monkeypatch.setattr(detect, "_swap_in", lambda *_a, **_k: None)
    detect.detect(str(work), str(img_dir), heading_plain_ratio=1.05,
                  heading_page_base=False, bold_use_page_h=False)
    assert captured["plain_heading_ratio"] == 1.05


def test_cli_reports_convert_error_without_traceback(monkeypatch, capsys):
    def fake_convert(_images, **_kwargs):
        raise api.ConvertError("boom")

    monkeypatch.setattr(api, "convert_images", fake_convert)
    monkeypatch.setattr(sys, "argv", ["p2md", "pipeline", "--images", "."])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1
    assert "错误: boom" in capsys.readouterr().err


def test_page_done_invalidates_when_image_newer(tmp_path):
    img_dir = tmp_path / "img"
    json_dir = tmp_path / "json"
    img_dir.mkdir()
    json_dir.mkdir()
    img = img_dir / "p1.jpg"
    result = json_dir / "1.json"
    img.write_text("img")
    result.write_text("{}")
    base = 1_700_000_000
    os.utime(result, (base, base))
    os.utime(img, (base + 10, base + 10))
    assert not ocr.page_done(str(json_dir), 1, str(img_dir))
    os.utime(result, (base + 20, base + 20))
    assert ocr.page_done(str(json_dir), 1, str(img_dir))


def test_clean_ocr_results_removes_stale_pages(tmp_path):
    img_dir = tmp_path / "img"
    json_dir = tmp_path / "json"
    txt_dir = tmp_path / "txt"
    for d in (img_dir, json_dir, txt_dir):
        d.mkdir()
    (img_dir / "p1.jpg").write_text("img")
    (json_dir / "1.json").write_text("{}")
    (json_dir / "2.json").write_text("{}")
    (txt_dir / "1.txt").write_text("old")
    (txt_dir / "2.txt").write_text("old")
    base = 1_700_000_000
    os.utime(json_dir / "1.json", (base, base))
    os.utime(img_dir / "p1.jpg", (base + 10, base + 10))

    stale = ocr._clean_ocr_results(str(json_dir), str(txt_dir), {1}, str(img_dir))
    assert stale == {1, 2}
    assert not (json_dir / "1.json").exists()
    assert not (json_dir / "2.json").exists()
    assert not (txt_dir / "1.txt").exists()
    assert not (txt_dir / "2.txt").exists()


def test_collect_result_mirrors_output(tmp_path):
    from p2md.paths import article_dir

    work = tmp_path / "work"
    article = article_dir(work)
    (article / "images").mkdir(parents=True)
    (article / "article_0.md").write_text("new")
    (article / "images" / "a.png").write_text("new")
    out = tmp_path / "output"
    (out / "images").mkdir(parents=True)
    (out / "article_1.md").write_text("stale")
    (out / "images" / "old.png").write_text("stale")

    result = api._collect_result(work, article, str(out))
    assert (out / "article_0.md").read_text() == "new"
    assert (out / "images" / "a.png").read_text() == "new"
    assert not (out / "article_1.md").exists()
    assert not (out / "images" / "old.png").exists()
    assert result.markdowns == [out / "article_0.md"]


def test_detect_pages_keeps_other_blocks(tmp_path, monkeypatch):
    work = paths.work_path(tmp_path / "work")
    img_dir = paths.ocr_tmp_dir(work)
    json_dir = paths.ocr_pages_json_dir(work)
    blocks_dir = paths.blocks_dir(work)
    fig_dir = paths.figs_dir(work)
    for d in (img_dir, json_dir, blocks_dir, fig_dir):
        d.mkdir(parents=True, exist_ok=True)
    (json_dir / "2.json").write_text('{"page": 2, "lines": []}', encoding="utf-8")
    (img_dir / "p2.jpg").write_text("img")
    (blocks_dir / "1.json").write_text("old")
    (fig_dir / "1.png").write_text("old")

    def fake_process(n, _json_dir, _img_dir, blocks_tmp, fig_tmp, **_kwargs):
        (Path(blocks_tmp) / f"{n}.json").write_text("new")
        (Path(fig_tmp) / f"{n}.png").write_text("new")
        return 0

    monkeypatch.setattr(detect, "_process_page", fake_process)

    def fail_swap(*_a, **_k):
        raise AssertionError("partial detect must not mirror-replace directories")

    monkeypatch.setattr(detect, "_swap_in", fail_swap)
    detect.detect(str(work), str(img_dir), pages=[2],
                  heading_page_base=False, bold_use_page_h=False)
    assert (blocks_dir / "1.json").read_text() == "old"
    assert (blocks_dir / "2.json").read_text() == "new"
