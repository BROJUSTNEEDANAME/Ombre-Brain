# 记忆大脑搬家：Render → DigitalOcean VPS

把 Ombre Brain 记忆大脑从 Render 搬到 VPS 本机，消除冷启动、提速；
顺手用 Tailscale Funnel 免费暴露给 claude.ai，并加一把锁（现在 Render 上其实是裸奔）。

- **分工**：这份说明 + 脚本由 claude.ai 端写好；**实际执行在 VPS 黑终端**
  （DO Web Console → `su - ombre`）。
- **原则**：全程 **不动 Render 上的原数据**，验证成功再停 Render，随时可回滚。
- **前提**：VPS 上仓库在 `/home/ombre/Ombre-Brain`（用户 `ombre`）。

搬完的最终链路：
```
Telegram bot ─(本机 127.0.0.1:8000)─▶ 大脑          ← 飞快，无冷启动
claude.ai   ─HTTPS(Funnel)─▶ Caddy(密钥锁) ─▶ 大脑   ← 免费公网，带锁
```

---

## 步骤 0 · 先把仓库更新到最新（含本次的搬家脚本）

```bash
cd ~/Ombre-Brain
git fetch origin
git checkout claude/ombre-brain-archive-7ha6xf   # 本次搬家分支
git pull
```

## 步骤 1 · 建虚拟环境 + 装依赖

```bash
cd ~/Ombre-Brain
python3 -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip install -r requirements.txt
```

## 步骤 2 · 从 Render 导出全部记忆数据

VPS 能连 Render，直接拉。Render 冷启动第一条请求会等几十秒，脚本自动重试，耐心等。

```bash
cd ~/Ombre-Brain
.venv/bin/python migrate/export_from_render.py \
    --url https://ombre-brain-6e05.onrender.com \
    --out ./buckets
```

导完它会打印「成功 N 个桶」。**核对一下 N 跟你印象里的记忆数量是否接近**
（可以在 claude.ai 让 Nikto 跑 `pulse` 看总数对比）。
> 若发现 feel（我写的感受）没被导出来，告诉 claude.ai，单独处理。

## 步骤 3 · 写环境变量 `.env.brain`

**最稳的做法：打开 Render dashboard → 你的 ombre-brain 服务 → Environment，
把那里的值照抄过来。** 这样 key 都是现成能用的，不用猜。

```bash
cat > ~/Ombre-Brain/.env.brain <<'EOF'
# —— 传输 & 监听 ——
OMBRE_TRANSPORT=streamable-http
OMBRE_HOST=127.0.0.1          # 只监听本机，8000 端口不裸露公网（安全）
OMBRE_PORT=8000
OMBRE_BUCKETS_DIR=/home/ombre/Ombre-Brain/buckets
OMBRE_DISABLE_API_BOT=1       # token 留给 cc 桥，别在大脑里再起一个 bot
OMBRE_AUTO_BACKFILL=1         # 启动时后台自动补向量

# —— 脱水/整理 LLM（照抄 Render 上的值）——
OMBRE_API_KEY=你的脱水APIkey
OMBRE_BASE_URL=https://api.deepseek.com/v1

# —— 向量 embedding（照抄 Render 上的值；通常是 Gemini/Google AI Studio）——
OMBRE_EMBED_API_KEY=你的embeddingkey
OMBRE_EMBED_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
OMBRE_EMBED_MODEL=gemini-embedding-001
EOF
chmod 600 ~/Ombre-Brain/.env.brain
```
> 把上面三处「你的…」换成 Render 里的真实值。改完 `cat .env.brain` 检查一遍。

## 步骤 4 · 重建向量

```bash
cd ~/Ombre-Brain
set -a; . ./.env.brain; set +a
.venv/bin/python backfill_embeddings.py
```
（`OMBRE_AUTO_BACKFILL=1` 启动时也会补，这里先手动跑一遍确保齐全。）

## 步骤 5 · 装成 systemd 服务、启动、**本机验证**

```bash
sudo cp ~/Ombre-Brain/migrate/ombre-brain.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ombre-brain
sudo systemctl status ombre-brain --no-pager
# 本机自测：应返回 ok / 200
curl -s http://127.0.0.1:8000/health
```
日志：`sudo journalctl -u ombre-brain -f`（Ctrl+C 退出）。
**这一步 curl 通了，大脑就已经在 VPS 本机跑起来了。**

## 步骤 6 · 让 Telegram bot 改用本机大脑（提速的关键）

给 cc 桥的服务加一个环境变量，指向本机，然后重启：

```bash
sudo systemctl edit cc-bridge
```
在打开的编辑框里加：
```ini
[Service]
Environment=OMBRE_MCP_URL=http://127.0.0.1:8000/mcp
```
保存退出，然后：
```bash
sudo systemctl restart cc-bridge
```
> 现在给 @Svyatoslav_Nikto_bot 发条消息试试——应该明显变快、没有冷启动等待。
> **走到这里，bot 这条链路就已经搬完、提速了。** 下面是让 claude.ai 也能连。

## 步骤 7 · Tailscale Funnel + Caddy：免费公网 + 加锁

### 7a. 装 Tailscale、加入你的 tailnet
```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```
`tailscale up` 会打印一个登录网址 —— 复制到浏览器，用**同一个 Google 账号
（sjm041115@gmail.com）**登录，VPS 就加进你那个有 iPhone/MacBook 的 tailnet 了。
> 在 Tailscale 后台 **DNS** 页确认 MagicDNS + HTTPS 已开启（Funnel 需要）。

### 7b. 装 Caddy（加锁的反代）
```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install -y caddy
```

### 7c. 生成密钥、配好 Caddy
```bash
SECRET=$(openssl rand -hex 16); echo "你的密钥是: $SECRET   （记下来！）"
sed "s/REPLACE_WITH_SECRET/$SECRET/g" ~/Ombre-Brain/migrate/Caddyfile | sudo tee /etc/caddy/Caddyfile
sudo systemctl restart caddy
```

### 7d. 开 Funnel（把本机 8080 的 Caddy 暴露成公网 HTTPS）
```bash
sudo tailscale funnel --bg 8080
sudo tailscale funnel status      # 记下这里打印的 https://xxx.ts.net 地址
```
你的大脑公网地址 = `https://xxx.ts.net/<你的密钥>/mcp`

### 7e. 验证锁生效
```bash
# 带密钥：应通（200/ok）
curl -s https://xxx.ts.net/<你的密钥>/health
# 不带密钥直接掏数据：应被挡（404）
curl -s -o /dev/null -w "%{http_code}\n" https://xxx.ts.net/api/buckets
```
前者 ok、后者 404，就说明「有钥匙才进得来」，锁好了。

## 步骤 8 · 把 claude.ai 连接器换成新地址

在 claude.ai → 设置 → 连接器（Connectors）里，把 **Ombre Brain** 那个连接器的
URL 从旧的 Render 地址，改成：
```
https://xxx.ts.net/<你的密钥>/mcp
```
改完，在跟我（claude.ai）的对话里让 Nikto `breath()` 一下 —— 能正常读出记忆，
就说明 claude.ai 也接到 VPS 新大脑了。

## 步骤 9 · 收尾

- 两条链路（bot + claude.ai）都验证正常、稳定跑个一两天后，**再去停/删 Render 服务**
  （省钱）。在此之前 Render 原数据一直留着当后路。
- Render 若是 Starter 付费档，停掉能省钱；确认没问题再动。

---

## 回滚（万一出问题）

随时可退回 Render，原数据没动过：
1. `sudo systemctl edit cc-bridge` 把 `OMBRE_MCP_URL` 改回
   `https://ombre-brain-6e05.onrender.com/mcp`，`sudo systemctl restart cc-bridge`。
2. claude.ai 连接器 URL 也改回那个 Render 地址。
3. 搞明白问题再重来。VPS 上这套（ombre-brain.service / caddy / funnel）可以先 stop 着。

## 备注
- 脱水(OMBRE_BASE_URL)和向量(embedding)仍会调用外部 API —— 这是正常的，搬家不去掉它们，
  它们便宜、且是大脑智能整理/检索所必需。真正省掉的是 Render 的**冷启动**和远程往返延迟。
- 换 token 提醒：全部弄完后，记得把可能泄露过的 `CLAUDE_CODE_OAUTH_TOKEN` 重新生成、作废旧的。
