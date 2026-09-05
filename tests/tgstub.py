"""telegram / openai 的测试替身，两个测试文件共用一份。

存在的理由（真踩过）：原本 test_tg_direct_smoke 和 test_cc_persona 各塞各的
替身进**全局** sys.modules，而且都写着「已经有 telegram 就不管了」。
于是谁先跑谁说了算：cc 那份没有 BotCommand，等 telegram_bot 再 import 就炸，
一次挂 91 条。单独跑每个文件都是绿的——最难发现的那种。

所以这里只有一条规矩：**补齐缺的属性，绝不因为模块已存在就整块跳过。**
"""
import sys
import types


def _mod(name):
    m = sys.modules.get(name)
    if m is None:
        m = types.ModuleType(name)
        sys.modules[name] = m
    return m


def _fill(m, **attrs):
    """只补没有的属性——别人已经放好的替身不覆盖。"""
    for k, v in attrs.items():
        if not hasattr(m, k):
            setattr(m, k, v)


def install_telegram():
    tg = _mod("telegram")
    _fill(tg,
          Update=type("Update", (), {"ALL_TYPES": []}),
          BotCommand=lambda *a, **k: None)

    _fill(_mod("telegram.constants"),
          ChatAction=types.SimpleNamespace(TYPING="typing", RECORD_VOICE="rv"))
    _fill(_mod("telegram.error"), TelegramError=Exception)

    ext = _mod("telegram.ext")
    for n in ("Application", "ApplicationBuilder", "CommandHandler",
              "MessageHandler"):
        _fill(ext, **{n: type(n, (), {})})
    _fill(ext,
          ContextTypes=type("ContextTypes", (), {"DEFAULT_TYPE": object}),
          filters=types.SimpleNamespace(PHOTO=1, VOICE=2, TEXT=4, COMMAND=8))
    return tg


def install_openai():
    m = _mod("openai")

    class _C:
        def __init__(self, *a, **k):
            self.chat = types.SimpleNamespace(completions=self)

        async def create(self, **kw):
            raise AssertionError("测试里应当被替换掉")

    _fill(m, AsyncOpenAI=_C)
    return m


def install_all():
    install_openai()
    install_telegram()
