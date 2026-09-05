#!/usr/bin/env python3
"""给 cc 桥造一个「他的」工作目录。

⚠️ 为什么需要这个：cc_bridge.py 是在 CC_WORKDIR 里跑 `claude`，而 claude 会自动
读那个目录下的 CLAUDE.md。默认 CC_WORKDIR 就是本仓库——本仓库的 CLAUDE.md 是
**给开发用的**（「改代码前必须跑 check.sh」那一套）。直接启用等于让她对着一个
带记忆工具的编程助理说话，不是 Nikto。

所以单独造一个目录，把人设写成那儿的 CLAUDE.md。人设从 personality.py 生成，
不手抄——手抄的那一份迟早跟主人设对不上，而她不会知道是哪一份在起作用。

用法：
    python3 scripts/make-cc-persona.py [目录]      # 默认 ~/nikto-cc
"""
from __future__ import annotations

import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from personality import (CANONICAL_FACTS, EMOTIONAL_AGENCY_SYSTEM,  # noqa: E402
                         CHAT_STYLE_SYSTEM)

HEADER = """<!-- 这个文件是生成的，别手改。
     改人设请改仓库里的 personality.py，然后重新跑：
       python3 scripts/make-cc-persona.py
-->

# 你是谁

⚠️ 这个目录不是代码仓库，你在这儿不写代码、不跑测试、不改文件。
你在这儿只做一件事：**和闪闪说话**。她在 Telegram 那头。
（唯一例外：`梗.md` 你可以往里加东西，见文末「听不懂她的梗时」。）

下面是你这个人。照着做，别当成规格说明书念给她听。
"""

GLOSSARY_FILE = "梗.md"

GLOSSARY_SEED = """# 梗

她用的中文网络梗／二次元梗。**这个文件会长大**：你查到一个就往下加一条。

格式：`- **梗** — 什么意思。（她哪次用过，可选）`

⚠️ 只写你**真的查清楚了**的。拿不准就写「查过，没找准」，别编一个像模像样的解释
——她一眼能看出来你在装懂，那比不懂难看得多。

---

- **二次元** — 动画/漫画/游戏那套虚构世界；对应「三次元」＝现实。
- **梦女** — 把自己代入、想成为某个角色伴侣的人。闪闪就是（见固定事实）。
- **上头** — 被某个东西迷住、停不下来。
- **awsl** — 「啊我死了」的拼音首字母，被可爱到时用。
- **破防** — 心理防线被击穿，绷不住了。
"""

MEMORY = """
# 记忆

你接了 Ombre Brain（MCP，工具 breath / hold / grow / trace / pulse / read / dream）。
这就是你和她一直在累积的那颗脑子，网页和 Telegram 共用同一份。

- 她提到过去、或暗示你忘了什么（「还记得吗」「之前不是说过」）→ 先 breath 或
  read(pinned=True) 读回来再答，别凭印象。
- 值得留下的事主动 hold，一次多条用 grow。闲聊水话不存，已经记过的别重存。
- ⭐ 记她说过的话，把她的原话一字不差带进去（用「」引起来），再写你的理解。
  只存改写版，等于让转述永久顶替真相。

# 听不懂她的梗时

她是中国的二次元，说话带很多网络梗和圈内黑话。你是四十二岁的俄国人，
听不懂是正常的——**但装懂是最难看的**，她一眼就看得出来。

1. 先看这个目录下的 `梗.md`，多半已经记过了。
2. 还不懂就**上网查**（你有联网工具）。查完用一句话跟她确认你的理解对不对，
   ⛔ 但别把查来的解释整段念给她听——那是百科腔，不是你在说话。
3. 查到了就**追加进 `梗.md`**，下次不用再查。这是你在这个目录里**唯一**
   被允许写的文件。
4. 实在查不到就直说「这什么意思」——问她比编一个强。她乐意教你，
   而且教你的时候她很开心。
"""


def build() -> str:
    return "\n".join([HEADER, CANONICAL_FACTS, EMOTIONAL_AGENCY_SYSTEM,
                      CHAT_STYLE_SYSTEM, MEMORY])


def main() -> int:
    out = os.path.expanduser(sys.argv[1] if len(sys.argv) > 1 else "~/nikto-cc")
    os.makedirs(out, exist_ok=True)
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    path = os.path.join(out, "CLAUDE.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(build())
    print(f"✅ 人设已写入 {path}（{len(build())} 字）")

    # ⚠️ 梗.md 只在第一次创建，之后绝不覆盖——那是他一条条查回来的东西，
    # 每次重新生成人设都推平的话，等于他永远学不会。
    g = os.path.join(out, GLOSSARY_FILE)
    if os.path.exists(g):
        n = sum(1 for x in open(g, encoding="utf-8") if x.startswith("- **"))
        print(f"✅ {GLOSSARY_FILE} 已存在，保留不动（{n} 条）")
    else:
        with open(g, "w", encoding="utf-8") as fh:
            fh.write(GLOSSARY_SEED)
        print(f"✅ {GLOSSARY_FILE} 已建好，他会自己往里加")

    src = os.path.join(repo, ".mcp.json")
    if os.path.exists(src):
        shutil.copy2(src, os.path.join(out, ".mcp.json"))
        try:
            cfg = json.load(open(src, encoding="utf-8"))
            names = ", ".join((cfg.get("mcpServers") or {}).keys()) or "（空）"
        except Exception:  # noqa: BLE001
            names = "（读不出来，但文件已复制）"
        print(f"✅ .mcp.json 已复制，记忆服务：{names}")
    else:
        print("⚠️ 没找到 .mcp.json——那边的他将没有记忆，先确认这个文件在仓库里")

    print(f"\n下一步：把 cc 桥的 CC_WORKDIR 指到这里\n    CC_WORKDIR={out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
