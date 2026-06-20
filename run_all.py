# -*- coding: utf-8 -*-
"""
一个服务里同时跑两样（让 Ombre Brain 和 Telegram bot 住在一起）：
  1. Ombre Brain MCP 服务器 (server.py) —— 前台主进程
  2. Telegram bot (telegram_bot.py) —— 后台子进程（仅当配置了 TELEGRAM_BOT_TOKEN）

把 Render 上 ombre-brain 服务的 startCommand 指到这个文件，大脑和 bot 就共用
同一套环境变量、一起运行。bot 起不来也绝不影响大脑：它是独立子进程，崩了主进程照常。
"""

import os
import subprocess
import sys

# 只有配置了 token 才拉起 bot；起不来也吞掉异常，保证大脑照常服务
if os.environ.get("TELEGRAM_BOT_TOKEN"):
    try:
        subprocess.Popen([sys.executable, "telegram_bot.py"])
        print("[run_all] Telegram bot 已在后台启动", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[run_all] Telegram bot 启动失败（不影响大脑）: {exc}", flush=True)
else:
    print("[run_all] 未配置 TELEGRAM_BOT_TOKEN，只跑 Ombre Brain", flush=True)

# 前台运行 MCP 服务器（替换当前进程，让 Render 正常托管这个 web 服务）
os.execvp(sys.executable, [sys.executable, "server.py"])
