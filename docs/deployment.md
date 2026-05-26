# 部署指南

## 环境要求

- Linux 服务器（或任何能持续运行的机器）
- Python 3.12+
- 能访问 Telegram API 的网络（中国大陆需要代理）
- 能访问 SD WebUI 服务的网络（通常为内网）

## 部署步骤

### 1. 准备代码

```bash
git clone <repo-url> /opt/sd_telegram_bot
cd /opt/sd_telegram_bot
```

### 2. 安装依赖

```bash
uv sync
```

### 3. 配置环境变量

```bash
cp .env.example .env
vim .env
```

必填项：
- `TELEGRAM_TOKEN`：从 @BotFather 获取
- `PROXY_URL`：代理地址，格式 `socks5://127.0.0.1:10808` 或 `http://127.0.0.1:10809`
- `DEEPSEEK_API_KEY`：中译英功能需要
- `SD_API_BASE`：确认 SD WebUI 地址正确

### 4. 验证 SD API 连通性

```bash
curl -s --connect-timeout 5 http://10.126.126.1:7860/sdapi/v1/sd-models | head -c 200
```

应返回模型列表 JSON。

### 5. 启动 Bot

#### 方式一：systemd 服务（推荐）

创建 `/etc/systemd/system/sd-telegram-bot.service`：

```ini
[Unit]
Description=SD Telegram Bot
After=network.target

[Service]
Type=simple
User=your_user
WorkingDirectory=/opt/sd_telegram_bot
Environment="PATH=/home/your_user/.local/bin:/usr/local/bin:/usr/bin:/bin"
ExecStart=/home/your_user/.local/bin/uv run python bot.py
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now sd-telegram-bot
sudo systemctl status sd-telegram-bot
```

查看日志：

```bash
journalctl -u sd-telegram-bot -f
```

#### 方式二：screen / tmux

```bash
screen -S sd-bot
uv run python bot.py
# Ctrl+A D 分离
```

重新连接：

```bash
screen -r sd-bot
```

### 6. 验证 Bot 运行

在 Telegram 中找到 Bot，发送 `/start`，确认收到主菜单回应。

## 网络说明

本 Bot 涉及三段网络连接：

| 连接 | 目标 | 方式 |
|------|------|------|
| Bot → Telegram API | `api.telegram.org` | 通过 `PROXY_URL` 代理 |
| Bot → SD WebUI | `10.126.126.1:7860` | 直连（内网） |
| Bot → DeepSeek API | `api.deepseek.com` | 直连（公网，无需代理） |

如果 SD WebUI 和 Bot 不在同一台机器上，确认网络可达且防火墙放行 7860 端口。

## 更新

```bash
cd /opt/sd_telegram_bot
git pull
# 如果依赖有变化
uv sync
# 重启服务
sudo systemctl restart sd-telegram-bot
```
