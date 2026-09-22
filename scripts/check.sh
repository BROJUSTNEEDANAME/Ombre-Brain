#!/usr/bin/env bash
# 交付前必须跑这个。不是建议，是硬规矩。
#
# 由来：多次把「语法没问题但一跑就崩」的代码推给闪闪，让她在 Telegram 上
# 替我踩出来——_trace 定义晚于使用、抽函数漏了 _keep_typing，都是这样漏过去的。
# python -m py_compile 只看语法，抓不到这类错误，所以必须真的执行一遍。
set -uo pipefail
cd "$(dirname "$0")/.."
fail=0
# ⚠️ 先清 __pycache__：陈旧的 .pyc 会让这一轮读到上一次的代码。
# 今天自查改动时真踩到了——改回去了，测试却还在按改坏的版本跑。
find . -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true

# ⚠️⚠️ 交付前快照：跑测试**不许改动版本库里的文件**。
# 真事（2026-09-22）：我给桥加了「启动时刷新人设」，写的是 CC_WORKDIR/CLAUDE.md，
# 而 CC_WORKDIR 默认就是仓库目录。测试里有一处没 monkeypatch 就调了它——
# **仓库那份记着全部教训的 CLAUDE.md 被推平成 Nikto 人设（9335→78867 字），
# 而且被我 git add -A 一起提交、推上去了**。干这事的正是那个「防止覆盖」的提交。
# 靠我记得不行，让这个脚本自己发现：跑完比一遍，动了就红。
# ⚠️ 必须比**内容**，不能只比「哪些文件是 dirty 的」：我第一版就写成了
# 比 `git diff --name-only`，而那时 CLAUDE.md 本来就在改动中、前后都在列表里，
# 埋了个真会写文件的测试进去它一声不响地放过了（自己验了才发现）。
_HASH_BEFORE=$(mktemp)
git ls-files -z 2>/dev/null | xargs -0 sha1sum 2>/dev/null | sort -k2 > "$_HASH_BEFORE"

echo "▶ 语法检查"
python3 -m py_compile server.py telegram_bot.py personality.py writing_style.py \
    prompt_cache.py utils.py reply_sanitizer.py memory_guard.py health_store.py morning.py \
    claude_provider.py restore_memories.py backup_memories.py \
    scripts/_verify_claude_key.py contradiction.py stale_ledger.py \
    sweep_contradictions.py env_file.py backfill_embeddings.py || fail=1

echo "▶ 冒烟测试（真的把整条路跑一遍）"
python3 -m pytest tests/test_tg_direct_smoke.py tests/test_claude_provider.py \
    tests/test_restore_memories.py tests/test_backup_memories.py -q || fail=1

echo "▶ 相关单测"
python3 -m pytest tests/test_dedup_helpers.py tests/test_prompt_output.py \
    tests/test_personality.py tests/test_writing_style.py \
    tests/test_contradiction.py tests/test_stale_ledger.py \
    tests/test_env_file.py tests/test_web_search.py \
    tests/test_cc_persona.py tests/test_autoupdate.py \
    tests/test_memory_guard.py tests/test_cc_status.py \
    tests/test_persona_switch.py tests/test_backup_alert.py \
    tests/test_health_store.py tests/test_persona_live.py tests/test_verbatim_memory.py tests/test_meta_leak.py tests/test_eleven_tts.py \
    tests/test_adhd_manager.py tests/test_chat_store.py tests/test_coreading.py \
    tests/test_home_recovery_contract.py tests/test_prompt_cache.py tests/test_public_site.py -q || fail=1

# ⚠️ 上面两步是分开跑的，跨文件的互相污染在分步里永远看不见。
# 真事：test_cc_persona 和 test_tg_direct_smoke 各塞各的 telegram 替身进全局
# sys.modules，谁先跑谁说了算——单独跑都绿，放一起挂 91 条，而 check.sh 一直报绿。
echo "▶ 全部放进同一个进程再跑一遍（防跨文件互相污染）"
python3 -m pytest tests/test_tg_direct_smoke.py tests/test_claude_provider.py \
    tests/test_restore_memories.py tests/test_backup_memories.py \
    tests/test_dedup_helpers.py tests/test_prompt_output.py \
    tests/test_personality.py tests/test_writing_style.py \
    tests/test_contradiction.py tests/test_stale_ledger.py \
    tests/test_env_file.py tests/test_web_search.py \
    tests/test_cc_persona.py tests/test_autoupdate.py \
    tests/test_memory_guard.py tests/test_cc_status.py \
    tests/test_persona_switch.py tests/test_backup_alert.py \
    tests/test_health_store.py tests/test_persona_live.py tests/test_verbatim_memory.py tests/test_meta_leak.py tests/test_eleven_tts.py \
    tests/test_adhd_manager.py tests/test_chat_store.py tests/test_coreading.py \
    tests/test_home_recovery_contract.py tests/test_prompt_cache.py tests/test_public_site.py -q || fail=1

_HASH_AFTER=$(mktemp)
git ls-files -z 2>/dev/null | xargs -0 sha1sum 2>/dev/null | sort -k2 > "$_HASH_AFTER"
_CHANGED=$(diff "$_HASH_BEFORE" "$_HASH_AFTER" 2>/dev/null \
           | grep '^[<>]' | awk '{print $3}' | sort -u)
rm -f "$_HASH_BEFORE" "$_HASH_AFTER"
if [ -n "$_CHANGED" ]; then
    echo "❌ 跑测试把版本库里的文件改了——测试绝不许写仓库文件："
    printf '%s\n' "$_CHANGED" | sed 's/^/     /'
    echo "   → 先 git checkout -- <上面那些文件>，再去修那个写文件的测试"
    fail=1
fi

# ⚠️ 没被这个脚本跑到的测试文件要**点名**，不许静默。
# 真事：tests/ 下有 10 个文件从来没被 check.sh 跑过（文件名是一个个写死的），
# 我一直以为「600 passed」就是全部。其中 test_scoring / test_thread_emotions /
# test_feel_flow 缺 mcp、frontmatter 这类依赖才跑不了——
# 而 CLAUDE.md 自己写着「依赖缺失时不要 skip，用替身让它真的执行」。
# 这是笔旧账，但至少不许再看不见。
_UNRUN=$(comm -13 \
    <(grep -o 'tests/test_[a-z_]*\.py' "$0" | sort -u) \
    <(ls tests/test_*.py 2>/dev/null | sort -u))
if [ -n "$_UNRUN" ]; then
    echo "❓ 这些测试文件这次没跑（缺依赖的旧账，不计入上面的 passed）："
    printf '%s\n' "$_UNRUN" | sed 's/^/     /'
fi

echo "▶ 未定义名扫描（抽函数漏依赖专用）"
python3 - <<'PY' || fail=1
import ast, builtins, sys

DUNDER = {"__file__", "__name__", "__doc__", "__package__", "__spec__", "__loader__"}


def _collect(node, names):
    """把这个作用域里所有「会产生新名字」的地方都收进来。
    收得不全就会误报——一个会喊狼来了的检查等于没有检查。"""
    for x in ast.walk(node):
        if isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(x.name)
            names |= {a.arg for a in x.args.args + x.args.kwonlyargs}
            if x.args.vararg: names.add(x.args.vararg.arg)
            if x.args.kwarg: names.add(x.args.kwarg.arg)
        elif isinstance(x, ast.ClassDef):
            names.add(x.name)
        elif isinstance(x, ast.Name) and isinstance(x.ctx, ast.Store):
            names.add(x.id)
        elif isinstance(x, (ast.Import, ast.ImportFrom)):
            names |= {(a.asname or a.name).split(".")[0] for a in x.names}
        elif isinstance(x, ast.ExceptHandler) and x.name:
            names.add(x.name)
        elif isinstance(x, ast.Global):
            names |= set(x.names)
        elif isinstance(x, ast.comprehension) and isinstance(x.target, ast.Name):
            names.add(x.target.id)
        elif isinstance(x, ast.AnnAssign) and isinstance(x.target, ast.Name):
            names.add(x.target.id)
        elif isinstance(x, ast.Lambda):
            names |= {a.arg for a in x.args.args}
    return names


bad = []
for path in ("telegram_bot.py", "server.py"):
    tree = ast.parse(open(path, encoding="utf-8").read())
    top = _collect(tree, set(dir(builtins)) | DUNDER)
    for fn in tree.body:
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        local = _collect(fn, {a.arg for a in fn.args.args + fn.args.kwonlyargs})
        used = {x.id for x in ast.walk(fn)
                if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Load)}
        miss = sorted(used - local - top)
        if miss:
            bad.append(f"{path}::{fn.name} 引用了未定义的名字 {miss}")
print("\n".join(bad) if bad else "未发现未定义引用")
sys.exit(1 if bad else 0)
PY

if [ "$fail" -ne 0 ]; then
    echo "❌ 检查未通过——不许交付给她。"
    exit 1
fi
echo "✅ 全部通过"
