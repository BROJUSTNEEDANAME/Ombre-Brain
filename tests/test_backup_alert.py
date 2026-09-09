"""cc 桥的自动备份 + 失败报警。

由来：记忆桶以前只有手动 /backup，没有定时。她的服务器这几天出过 .git 权限、
跑旧代码的事——万一 buckets/ 悄悄出问题，她会一声不响丢掉全部记忆。
思路学自 Jade3551/Sora-mem 的 ops/（但不抄代码，它绑 PostgreSQL，我们是文件）。

这个文件盯两条最容易写错的地方：
1. 一个 0 字节 / 打不开的假备份，必须当失败——它比没备份更坏（给你错觉）。
2. 「这轮成没成」和「库里有没有新鲜备份」是两条独立判断——
   只看前者，会在任务悄悄停摆时完全沉默。
"""
import os
import tarfile
import time

import pytest


def _cc(monkeypatch, tmp_path):
    import sys
    for name in ("telegram", "telegram.constants", "telegram.error", "telegram.ext"):
        sys.modules.pop(name, None)
    import importlib.util
    import types
    os.environ.setdefault("TELEGRAM_BOT_TOKEN", "t")
    from tests.tgstub import install_all
    install_all()
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location("cc_bridge", root / "cc_bridge.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    monkeypatch.setattr(m, "BUCKETS_DIR", str(tmp_path / "buckets"))
    monkeypatch.setattr(m, "BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setattr(m, "ALLOWED_CHAT_IDS", {1})
    return m


class _Ctx:
    def __init__(self):
        self.alerts = []
        bot = self

        class _B:
            async def send_message(_s, chat_id, text, **kw):
                self.alerts.append(text)
        self.bot = _B()


def _make_buckets(m, content=b"real memory here" * 20):
    os.makedirs(m.BUCKETS_DIR, exist_ok=True)
    with open(os.path.join(m.BUCKETS_DIR, "a.md"), "wb") as f:
        f.write(content)


def test_a_good_backup_raises_no_alert(monkeypatch, tmp_path):
    import asyncio
    m = _cc(monkeypatch, tmp_path)
    _make_buckets(m)
    ctx = _Ctx()
    asyncio.run(m.auto_backup(ctx))
    assert ctx.alerts == [], f"好备份不该报警：{ctx.alerts}"
    # 真的落了一份，而且验得过
    files = os.listdir(m.BACKUP_DIR)
    assert files and m._verify_backup(os.path.join(m.BACKUP_DIR, files[0])) == ""


def test_a_zero_byte_backup_is_treated_as_failure(monkeypatch, tmp_path):
    """核心那条：假备份比没备份更坏。"""
    m = _cc(monkeypatch, tmp_path)
    os.makedirs(m.BACKUP_DIR)
    bad = os.path.join(m.BACKUP_DIR, "buckets-empty.tar.gz")
    open(bad, "wb").close()                       # 0 字节
    assert m._verify_backup(bad) != ""


def test_a_truncated_backup_is_caught(monkeypatch, tmp_path):
    """写到一半断电那种——文件在，但打不开。"""
    m = _cc(monkeypatch, tmp_path)
    os.makedirs(m.BACKUP_DIR)
    bad = os.path.join(m.BACKUP_DIR, "buckets-trunc.tar.gz")
    with open(bad, "wb") as f:
        f.write(b"\x1f\x8b\x08" + b"garbage that is not a real gzip" * 5)
    assert m._verify_backup(bad) != ""


def test_an_empty_shell_tar_is_caught(monkeypatch, tmp_path):
    """能打开、但里面没有真文件——打了个空壳。"""
    m = _cc(monkeypatch, tmp_path)
    os.makedirs(m.BACKUP_DIR)
    shell = os.path.join(m.BACKUP_DIR, "buckets-shell.tar.gz")
    with tarfile.open(shell, "w:gz") as tar:
        pass
    assert m._verify_backup(shell) != ""


def test_missing_buckets_dir_alerts(monkeypatch, tmp_path):
    """大脑不在这台机器上 / 路径变了——_do_backup 返回 None，必须报警。"""
    import asyncio
    m = _cc(monkeypatch, tmp_path)          # 没建 buckets
    ctx = _Ctx()
    asyncio.run(m.auto_backup(ctx))
    assert any("没找到 buckets" in a for a in ctx.alerts)


def test_a_crashing_backup_alerts_and_does_not_kill_the_job(monkeypatch, tmp_path):
    import asyncio
    m = _cc(monkeypatch, tmp_path)

    def boom():
        raise OSError("disk full")
    monkeypatch.setattr(m, "_do_backup", boom)
    ctx = _Ctx()
    asyncio.run(m.auto_backup(ctx))          # 不许抛出来
    assert any("自动备份失败" in a for a in ctx.alerts)


def test_a_stale_backup_alerts_even_when_this_run_succeeded(monkeypatch, tmp_path):
    """第二道独立判断：这轮成功了，但库里最新那份还是很旧——
    （比如任务前几轮悄悄没跑成）——也得报。这是只看「这轮成没成」抓不到的。"""
    import asyncio
    m = _cc(monkeypatch, tmp_path)
    _make_buckets(m)
    monkeypatch.setattr(m, "BACKUP_STALE_H", 100)
    ctx = _Ctx()
    asyncio.run(m.auto_backup(ctx))
    # 这轮刚生成的是新鲜的，把它的时间戳改老，模拟「库里只有旧的」
    for f in glob_backups(m):
        os.utime(f, (time.time() - 500 * 3600, time.time() - 500 * 3600))
    ctx2 = _Ctx()
    # 让这一轮的 _do_backup 不再生成新文件，只走「看最新有多旧」
    monkeypatch.setattr(m, "_do_backup", lambda: None)
    # buckets 目录还在，所以 None 会触发「没找到 buckets」——换成返回旧文件路径
    monkeypatch.setattr(m, "_do_backup", lambda: glob_backups(m)[0])
    asyncio.run(m.auto_backup(ctx2))
    assert any("小时前" in a for a in ctx2.alerts), f"旧备份没报警：{ctx2.alerts}"


def test_no_backup_at_all_alerts(monkeypatch, tmp_path):
    import asyncio
    m = _cc(monkeypatch, tmp_path)
    monkeypatch.setattr(m, "_do_backup", lambda: None)
    os.makedirs(m.BUCKETS_DIR, exist_ok=True)   # 让 None 不是因为缺目录
    ctx = _Ctx()
    asyncio.run(m.auto_backup(ctx))
    assert any("一份记忆备份都没有" in a for a in ctx.alerts)


def glob_backups(m):
    import glob
    return glob.glob(os.path.join(m.BACKUP_DIR, "buckets-*.tar.gz"))


def test_a_failing_alert_never_kills_the_backup_job(monkeypatch, tmp_path):
    """报警本身发不出去（网络抖、被 Telegram 限流）也不能把定时任务带崩——
    那样连「备份挂了」都传不出来，而任务下一轮也不再跑。"""
    import asyncio
    m = _cc(monkeypatch, tmp_path)          # 没建 buckets → 必然触发报警路径

    class _BadCtx(_Ctx):
        def __init__(self):
            super().__init__()
            outer = self

            class _B:
                async def send_message(_s, chat_id, text, **kw):
                    raise RuntimeError("telegram down")
            self.bot = _B()

    # 不许抛出来——抛了就是 job 崩了
    asyncio.run(m.auto_backup(_BadCtx()))
