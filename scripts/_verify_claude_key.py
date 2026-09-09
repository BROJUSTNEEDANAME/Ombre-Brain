"""真调一次 Anthropic API，确认 key 能用。enable-claude.sh 专用。

输出恰好一行：OK|<他说的话>  或  FAIL|<原因>。
调用方据此决定要不要重启 bot——所以这里只许打印这一行，别多话。
"""
import os
import sys


def main() -> None:
    model = os.environ.get("OMBRE_CLAUDE_MODEL", "claude-opus-5")
    key = (os.environ.get("OMBRE_ANTHROPIC_KEY")
           or os.environ.get("ANTHROPIC_API_KEY", "")).strip()
    if not key:
        print("FAIL|环境里没有 OMBRE_ANTHROPIC_KEY")
        return
    try:
        import anthropic  # noqa: PLC0415
    except ModuleNotFoundError:
        print("FAIL|没装 anthropic：pip install -r requirements-telegram.txt")
        return
    try:
        client = anthropic.Anthropic(api_key=key, timeout=30.0, max_retries=0)
        resp = client.messages.create(
            model=model, max_tokens=8,
            messages=[{"role": "user", "content": "说「在」"}])
        text = "".join(b.text for b in resp.content
                       if getattr(b, "type", "") == "text").strip()
        print("OK|" + (text or "(空)"))
    except Exception as e:  # noqa: BLE001
        print("FAIL|" + f"{type(e).__name__}: {e}"[:400])


if __name__ == "__main__":
    main()
    sys.exit(0)
