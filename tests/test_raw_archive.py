"""聊天原文逐字检索。由来：她问 Eden，桶里一个都没有（摘要把名字吃了），原话在存档里。"""
from pathlib import Path

import raw_archive

ROOT = Path(__file__).resolve().parent.parent


def _arch(tmp_path, files: dict) -> str:
    d = tmp_path / "ombre-archive" / "telegram"; d.mkdir(parents=True)
    for name, body in files.items():
        (d / name).write_text(body, encoding="utf-8")
    return str(tmp_path / "ombre-archive")


def test_finds_the_exact_words_with_context(tmp_path):
    base = _arch(tmp_path, {"2026-05-01.md": "[01:00] 闪闪: 晚安\n[01:01] Nikto: 睡吧\n"
                            "[01:02] 闪闪: 我又梦到前夫哥 Eden 了\n[01:03] Nikto: 哦。\n[01:04] 闪闪: 你吃醋了\n"})
    out = raw_archive.search("Eden", base)
    assert "我又梦到前夫哥 Eden 了" in out, "原话必须一字不差"
    assert "睡吧" in out and "你吃醋了" in out, "要带前后文"
    assert "2026-05-01.md" in out and "第3行" in out


def test_case_insensitive_and_newest_first(tmp_path):
    base = _arch(tmp_path, {"2026-01-01.md": "[1] 闪闪: eden 旧的\n",
                            "2026-09-01.md": "[1] 闪闪: EDEN 新的\n"})
    out = raw_archive.search("Eden", base)
    assert out.index("新的") < out.index("旧的")


def test_whole_phrase_first_then_words(tmp_path):
    base = _arch(tmp_path, {"d.md": "[1] 闪闪: 伊甸是我的梦角\n"})
    assert "伊甸是我的梦角" in raw_archive.search("Eden 伊甸", base), "整句没有就逐词"


def test_nothing_found_is_empty_not_garbage(tmp_path):
    base = _arch(tmp_path, {"d.md": "[1] 闪闪: 今天吃饺子\n"})
    assert raw_archive.search("Eden", base) == ""
    assert raw_archive.search("Eden", str(tmp_path / "no-such-dir")) == ""
    assert raw_archive.search("   ", base) == ""


def test_single_char_words_do_not_flood(tmp_path):
    base = _arch(tmp_path, {"d.md": "[1] 闪闪: 的的的\n"})
    assert raw_archive.search("Eden 的", base) == ""


def test_output_is_capped(tmp_path):
    body = "".join(f"[{i}] 闪闪: Eden {'啊' * 300}\n" for i in range(50))
    base = _arch(tmp_path, {"d.md": body})
    out = raw_archive.search("Eden", base, max_chars=1500)
    assert 0 < len(out) <= 1500 + 50


def test_breath_puts_raw_text_first_and_random_drift_is_gone():
    src = (ROOT / "server.py").read_text(encoding="utf-8")
    code = "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith("#"))
    assert "raw_archive.search(query)" in code
    assert 'results.insert(0, "=== 聊天原文' in code, "原话放最前，免得被调用方截断"
    assert "忽然想起来 ---" not in code, "随机漂浮是往结果里掺噪音，已退役"
