#!/usr/bin/env bash
# 交付前必须跑这个。不是建议，是硬规矩。
#
# 由来：多次把「语法没问题但一跑就崩」的代码推给闪闪，让她在 Telegram 上
# 替我踩出来——_trace 定义晚于使用、抽函数漏了 _keep_typing，都是这样漏过去的。
# python -m py_compile 只看语法，抓不到这类错误，所以必须真的执行一遍。
set -uo pipefail
cd "$(dirname "$0")/.."
fail=0

echo "▶ 语法检查"
python3 -m py_compile server.py telegram_bot.py personality.py writing_style.py \
    prompt_cache.py utils.py reply_sanitizer.py morning.py \
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
    tests/test_cc_persona.py tests/test_autoupdate.py -q || fail=1

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
