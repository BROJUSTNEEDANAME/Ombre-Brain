# -*- coding: utf-8 -*-
"""把 systemd 服务用的那份环境变量读进当前进程。

由来：手动跑 backfill_embeddings.py 报「missing API key」、跑
sweep_contradictions.py 报「Missing credentials」——key 都在服务的
EnvironmentFile 里，shell 里没有。同一个错踩了两次，抽出来共用。

只认 KEY=VALUE，不执行任何东西（不是 source，不会跑命令）。
已经存在的环境变量不覆盖——命令行显式传的优先。
"""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path

CANDIDATES = (".env.apibot", ".env", ".env.brain")


def service_env_file(service: str) -> str:
    """问 systemd 这个服务用的是哪份配置文件。拿不到返回空串。"""
    try:
        out = subprocess.run(
            ["systemctl", "show", service, "-p", "EnvironmentFile", "--value"],
            capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:  # noqa: BLE001
        return ""
    # 形如 "-/home/ombre/Ombre-Brain/.env.apibot (ignore_errors=yes)"
    return out.lstrip("-").split(" ")[0].strip()


def parse(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key or not key.replace("_", "").isalnum():
            continue
        value = value.strip()
        if value[:1] in ("'", '"') and value[-1:] == value[:1] and len(value) >= 2:
            value = value[1:-1]
        else:
            value = value.split(" #")[0].strip()
        out[key] = value
    return out


def load(*, repo_dir: str | None = None, services=("ombre-apibot", "ombre-brain"),
         override: bool = False) -> str:
    """加载并写进 os.environ，返回真正用了哪个文件（没找到返回空串）。"""
    base = Path(repo_dir or os.path.dirname(os.path.abspath(__file__)))
    paths = []
    for svc in services:
        p = service_env_file(svc)
        if p:
            paths.append(Path(p))
    paths += [base / name for name in CANDIDATES]

    used = ""
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for key, value in parse(text).items():
            if override or not os.environ.get(key):
                os.environ[key] = value
        used = used or str(path)
    return used


__all__ = ["load", "parse", "service_env_file", "CANDIDATES"]
