# SD Telegram Bot

在 Telegram 中通过文本提示词远程调用 Stable Diffusion WebUI 生成图片的 Bot。

## 功能

- **文本生成图片**：发送中文或英文提示词，Bot 调用 SD txt2img API 生成图片
- **中译英**：可选，调用 DeepSeek API 将中文提示词翻译为英文
- **参数面板**：通过内联按钮调整尺寸、模型、采样器、CLIP Skip、高清修复、面部修复、平铺模式、种子、Steps、CFG 等
- **模型切换**：直接读取 SD 可用模型列表，一键切换
- **高清修复**：内置 R-ESRGAN 4x+ 超分参数，开关控制
- **生成队列**：全局串行队列，多个任务自动排队，显示等待位置
- **进度反馈**：生成过程中显示进度百分比
- **设置持久化**：用户参数自动保存到 JSON 文件，Bot 重启后恢复
- **种子控制**：支持随机种子或手动指定

## 前置条件

- Python 3.12+
- 运行中的 [Stable Diffusion WebUI](https://github.com/AUTOMATIC1111/stable-diffusion-webui) (A1111) 或 Forge，需启动 `--api` 参数
- Telegram Bot Token（从 [@BotFather](https://t.me/BotFather) 获取）
- DeepSeek API Key（中译英功能需要）
- （中国大陆）能访问 Telegram API 的代理

## 快速开始

```bash
# 1. 克隆项目
git clone <repo-url> && cd sd_telegram_bot

# 2. 安装依赖
uv sync

# 3. 配置环境变量
cp .env.example .env
# 编辑 .env，填入 TELEGRAM_TOKEN、PROXY_URL、DEEPSEEK_API_KEY

# 4. 启动
uv run python bot.py
```

## 配置

所有敏感信息通过 `.env` 文件管理：

| 变量 | 必填 | 说明 |
|------|------|------|
| `TELEGRAM_TOKEN` | 是 | Bot Token |
| `PROXY_URL` | 推荐 | 代理地址，如 `socks5://127.0.0.1:10808` |
| `DEEPSEEK_API_KEY` | 翻译需要 | DeepSeek API Key |
| `SD_API_BASE` | 否 | SD WebUI 地址，默认 `http://10.126.126.1:7860` |
| `DEEPSEEK_BASE_URL` | 否 | DeepSeek API 地址，默认 `https://api.deepseek.com` |
| `LOG_LEVEL` | 否 | 日志级别，默认 `INFO` |
| `LOG_FULL_PROMPT` | 否 | 日志中记录完整提示词，默认 `false` |

生成参数（尺寸预设、Steps、CFG、高清修复参数等）在 `config.py` 中修改。

## 使用

1. 在 Telegram 中找到你的 Bot，发送 `/start`
2. 点击「参数设置」调整生成参数
3. 直接发送提示词文本即可生成图片

```
/start  — 显示主菜单
/help   — 同 /start
/cancel — 取消种子输入
```

连续发送多条提示词时，后续任务会自动排队，状态消息会显示前方等待数。

## 项目结构

```
sd_telegram_bot/
├── bot.py               # 入口：初始化 Application、队列、注册 handlers
├── config.py            # 配置中心（常量 + .env 加载）
├── handlers/
│   ├── settings.py      # 设置菜单：内联键盘，所有参数切换
│   └── generation.py    # 文本消息处理：创建任务 → 提交队列
├── services/
│   ├── sd_api.py        # SD WebUI API 封装（txt2img、进度轮询、模型/采样器）
│   ├── queue.py          # 生成队列 + 节流进度更新器
│   ├── translator.py    # DeepSeek 翻译（OpenAI 兼容接口）
│   ├── storage.py       # JSON 文件持久化
│   └── logger.py        # 日志配置
└── docs/
    └── deployment.md    # 部署指南
```

## 进度说明

进度百分比来自 SD WebUI 的全局 `/sdapi/v1/progress` 端点。由于 Bot 使用串行队列独占生成，进度显示通常是准确的。如果通过 WebUI 或其他程序同时向同一 SD 后端提交任务，Bot 显示的进度可能不准确。

## 部署

生产环境部署详见 [docs/deployment.md](docs/deployment.md)。
