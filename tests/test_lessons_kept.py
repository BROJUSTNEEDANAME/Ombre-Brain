"""教训不许丢。

由来：2026-09-23 闪闪看完 CLAUDE.md 那页「我反复踩的坑」说
「一定要保留，不许动现在的设定」。

而这份文件我**真的自己推平过一次**（2026-09-22，一个没 monkeypatch 的测试
把它写成了 Nikto 人设，9335 → 78867 字，还被 git add -A 提交推上了 origin）。
check.sh 里的内容哈希护栏防的是「测试写仓库文件」那一类；这里防的是另一类：
有人（多半是下一个我）觉得这页太长，动手「精简」，把带日期带原话的条目
合并成几条漂亮的通则——那就等于删了，因为通则改变不了任何行为。

只管「不许丢」，不管「不许加」：新踩的坑照写，写完再存一份快照。
"""
import pathlib
import re

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_SNAPS = _ROOT / "docs" / "snapshots"


def _latest_snapshot() -> pathlib.Path:
    snaps = sorted(_SNAPS.glob("CLAUDE.md.*.snapshot.md"))
    assert snaps, "docs/snapshots/ 里一份 CLAUDE.md 快照都没有"
    return snaps[-1]


def _headlines(text: str) -> list[str]:
    """抽出每条教训的**加粗标题**（`- **……**`），当作这条教训的身份。
    比全文比对稳：正文措辞允许润色，标题没了才算这条教训被删了。"""
    lessons = text[text.index("## 🦊"):]
    return re.findall(r"^- \*\*(.+?)\*\*", lessons, re.M | re.S)


def test_there_is_a_snapshot_at_all():
    """她说要备份。没有快照 = 没备份，别让这个测试自己变成空壳。"""
    snap = _latest_snapshot()
    assert len(snap.read_text(encoding="utf-8")) > 5000, f"{snap.name} 小得不像真的"


def test_every_lesson_in_the_snapshot_is_still_in_claude_md():
    snap = _latest_snapshot()
    kept = _headlines((_ROOT / "CLAUDE.md").read_text(encoding="utf-8"))
    # 标题里常有强调符号/换行，比对时按「去掉空白后是否还在」算
    flat = "".join(" ".join(kept).split())
    missing = [h for h in _headlines(snap.read_text(encoding="utf-8"))
               if "".join(h.split()) not in flat]
    assert not missing, (
        f"这些教训在 {snap.name} 里有，现在的 CLAUDE.md 里没了：\n  "
        + "\n  ".join(m[:40] for m in missing))
