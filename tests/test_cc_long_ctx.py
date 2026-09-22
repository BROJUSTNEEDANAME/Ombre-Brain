"""长上下文 [1m] 后缀 + 「用力档位」说明。

由来：她说「上下文太少」。实测同一个 CLI、同一个模型：
    claude-opus-4-6      窗口 180,000 ｜压缩阈值 144,000
    claude-opus-4-6[1m]  窗口 980,000 ｜压缩阈值 784,000
所以默认给模型名挂上 [1m]。风险是万一她那台机器不认，他一句话都回不了，
于是失败要摘掉后缀重试一次——但**不许靠猜错误文本**判断是不是后缀的锅
（第一版拿关键词猜，一个无关的「配置文件找不到」就把长上下文永久关掉了）。
这里把这条规则钉死：只有「摘掉就成功」才算证明。
"""
import asyncio
import importlib.util
import pathlib

_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _cc():
    import os
    os.environ.setdefault("TELEGRAM_BOT_TOKEN", "t")
    from tests.tgstub import install_all      # noqa: PLC0415
    install_all()
    spec = importlib.util.spec_from_file_location("cc_bridge", _ROOT / "cc_bridge.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class _Proc:
    def __init__(self, rc, out=b"", err=b""):
        self.returncode = rc
        self._o, self._e = out, err

    async def communicate(self):
        return self._o, self._e


_OK = ('{"type":"result","subtype":"success","result":"在。",'
       '"session_id":"s1"}').encode("utf-8")


def _spy(cc, monkeypatch, outcomes):
    """把 create_subprocess_exec 换成替身，记下每次真正发出去的 argv。
    outcomes 是每次调用返回的 _Proc（按顺序取）。"""
    seen: list[list[str]] = []
    left = list(outcomes)

    async def fake(*argv, **kw):
        seen.append(list(argv))
        return left.pop(0)

    monkeypatch.setattr(cc.asyncio, "create_subprocess_exec", fake)
    return seen


def _model_arg(argv):
    return argv[argv.index("--model") + 1]


def test_the_model_name_carries_the_1m_suffix(monkeypatch):
    cc = _cc()
    monkeypatch.setitem(cc.long_ctx, "broken", False)
    seen = _spy(cc, monkeypatch, [_Proc(0, _OK)])
    asyncio.run(cc.run_cc("在吗", None))
    assert _model_arg(seen[0]).endswith("[1m]"), seen[0]


def test_a_failure_retries_once_without_the_suffix(monkeypatch):
    """失败了要摘掉后缀再试一次——不然万一她那边不认，他一句话都回不了。"""
    cc = _cc()
    monkeypatch.setitem(cc.long_ctx, "broken", False)
    seen = _spy(cc, monkeypatch, [_Proc(1, b"", b"boom"), _Proc(0, _OK)])
    text, _ = asyncio.run(cc.run_cc("在吗", None))
    assert len(seen) == 2, seen
    assert _model_arg(seen[0]).endswith("[1m]")
    assert not _model_arg(seen[1]).endswith("[1m]"), "第二次必须是摘掉后缀的"
    assert text == "在。"


def test_only_a_successful_retry_proves_the_suffix_was_at_fault(monkeypatch):
    """摘掉就成功 → 确实是它的锅，以后不再用。"""
    cc = _cc()
    monkeypatch.setitem(cc.long_ctx, "broken", False)
    _spy(cc, monkeypatch, [_Proc(1, b"", b"boom"), _Proc(0, _OK)])
    asyncio.run(cc.run_cc("在吗", None))
    assert cc.long_ctx["broken"] is True


def test_an_unrelated_failure_does_not_disable_long_context(monkeypatch):
    """⚠️ 这条是真出过事的那一例：一个跟 [1m] 毫无关系的错误
    （「Claude configuration file not found」）被关键词猜中，
    长上下文被永久关掉。摘掉后缀也还是失败 → 不是它的锅，不许动它。"""
    cc = _cc()
    monkeypatch.setitem(cc.long_ctx, "broken", False)
    bad = b'{"type":"result","is_error":true,"result":"Claude configuration file not found"}'
    _spy(cc, monkeypatch, [_Proc(1, bad, b""), _Proc(1, bad, b"")])
    asyncio.run(cc.run_cc("在吗", None))
    assert cc.long_ctx["broken"] is False, "无关错误不许把长上下文关掉"


def test_once_broken_the_suffix_is_never_sent_again(monkeypatch):
    cc = _cc()
    monkeypatch.setitem(cc.long_ctx, "broken", True)
    seen = _spy(cc, monkeypatch, [_Proc(0, _OK)])
    asyncio.run(cc.run_cc("在吗", None))
    assert len(seen) == 1, "已经知道不认了，不要再白试一次"
    assert not _model_arg(seen[0]).endswith("[1m]")


def test_the_effort_help_says_medium_is_a_downgrade_not_a_middle_gear():
    """我在这件事上连错两次，都是拿**一道题**下定论：
    先让她切 medium，又改口说「只有 max 管用」。
    4 道题 × 5 档跑下来：low≈0 ＜ medium(17~30) ＜ 默认(116~221) ≈ high ＜ max。
    medium 在默认**下面**——她「开了 medium 还是不动脑子」是因为生效了、方向反的。
    帮助文本必须说出这一条，并且把她换回「默认」，不是推去 max。"""
    src = (_ROOT / "cc_bridge.py").read_text(encoding="utf-8")
    body = src[src.index("async def effort_cmd"):]
    body = body[:body.index("\nasync def ", 1)]
    # ⚠️ 只看**她会看到的那段回复**，不看 docstring——docstring 里故意留着
    # 「我连错两次」的旧数字当账，拿整个函数体去比会把那笔账误判成残留。
    # （CLAUDE.md 那条：断言前先想这个串在别处会不会先命中。）
    body = body[body.index("reply_text("):]
    assert "/effort 默认" in body, "要把她换回默认，不是推去 max"
    assert "降档" in body, "必须点明 medium 是降档"
    # 旧的两组错数字一个都不许留
    for stale in ("144", "176", "max 121**", "只有一档"):
        assert stale not in body, f"旧结论残留：{stale}"
    # 真实测到的那几个数要在场（不许只写结论不给证据）
    for real in ("221", "148", "116"):
        assert real in body, f"实测数字缺了：{real}"
