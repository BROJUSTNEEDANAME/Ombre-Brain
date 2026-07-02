# Ombre Brain 迁移 / 恢复指南（换机器、换账号、换 AI 都能用）

> 一句话安心：**最值钱、最不可替代的部分——你的记忆和人设——是纯文件，不绑定任何账号、任何 AI。**
> AI（Claude / Gemini / 别的）只是一个可以随时换的"引擎"。你这套系统当初就是先跑在 Gemini 上的。

---

## 一、什么绑账号、什么不绑（看清楚就不慌）

| 东西 | 绑什么 | 换了会怎样 |
|------|--------|-----------|
| **记忆数据**（`buckets/` 里的 Markdown + `embeddings.db`） | **谁都不绑，就是文件** | 复制走就行，永远能读 |
| **人设**（`CLAUDE.md` / `CLAUDE_PROMPT.md` / `persona`） | 不绑，文件 | 复制走就行 |
| **代码**（整个仓库） | 你的 GitHub | 你自己的号，clone 就有 |
| DDL/流水账/设置（`~/ombre-data/`） | 不绑，文件 | 复制走就行 |
| VPS（跑 bot 和大脑的机器） | DigitalOcean 账号 + 付费 | 换机器：在新机重装即可（见下） |
| **AI 引擎**（现在是 Claude Code + Max 订阅） | 你的 Anthropic/Claude 账号 | **可换**：换 Claude 号、或换成别的 LLM |
| Telegram bot | @BotFather 的 bot token | 换 token 即可 |
| claude.ai 连接器 | 你的 claude.ai 账号 | 换号重新填连接器地址 |

**结论：只要你手里有 ①记忆备份（buckets）+ ②这个 GitHub 仓库，你就能在任何机器、任何 AI 上把整套系统复活。** 账号只是外壳。

---

## 二、你的"底牌"备份（务必留着）

1. **记忆备份**：给 Telegram bot 发 `/backup`，他会把 `buckets-*.tar.gz` 发给你。**下载存好**（网盘/电脑/U 盘都行）。这份里是你全部记忆 + DDL + 流水账。bot 每 7 天也会自动发一份到聊天里。
2. **代码**：整个仓库在你的 GitHub（`BROJUSTNEEDANAME/Ombre-Brain`）。想更保险，把它下载一份 zip 存本地。

有这两样 = 你随时能重建，谁也拿不走。

---

## 三、怎么在一台新机器上完整复活（换机器 / 换 VPS）

```bash
# 1. 拿到代码
git clone https://github.com/BROJUSTNEEDANAME/Ombre-Brain.git
cd Ombre-Brain
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# 2. 放回记忆（把 /backup 下载的 tar 解开）
tar xzf buckets-XXXX.tar.gz          # 解出 buckets/（和 ombre-data/）
#  → 得到 ./buckets  和  ./ombre-data

# 3. 填环境变量 .env.brain（照 migrate/README_MIGRATION.md）：
#    OMBRE_API_KEY / OMBRE_BASE_URL（脱水LLM）
#    OMBRE_EMBED_*（向量，Gemini 免费）
#    OMBRE_BUCKETS_DIR 指向上面的 buckets

# 4. 起大脑 + bot（照 migrate/ 里的 systemd 单元）
#    向量如缺：.venv/bin/python backfill_embeddings.py
```
细节全在 `migrate/README_MIGRATION.md`（搬家指南）里。

---

## 四、换一个 AI（不用 Claude 了也能跑）

这套的记忆（大脑）是**模型无关**的：
- **大脑本身**：脱水/整理用任何 OpenAI 兼容 API（DeepSeek / Gemini / 本地 Ollama…都行，`config.yaml` 里改 `base_url`）；向量用任何 embedding 提供方。**跟 Claude 一点关系都没有。**
- **聊天引擎**：现在的 bot 走 Claude Code（`cc_bridge.py`）。想换别的 LLM：仓库里还留着 `telegram_bot.py`——那是**走 Anthropic API 版**；再早的版本甚至跑在 **Gemini** 上。也就是说，把"引擎"换成任何能聊天的模型都行，**记忆照样接着用**。

**所以：换 AI，只是换个说话的嘴；脑子（记忆+人设）一直是你的那份文件。**

---

## 五、最省心的建议
- 每隔一阵 `/backup` 存一份到你自己的地方（不只 Telegram）。
- 这个 `RESTORE.md` 和仓库一起留着。
- 真出事时，把这份指南 + 记忆备份丢给任何一个 AI，它都能照着帮你把系统重建起来。
