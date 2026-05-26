# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

SD Telegram Bot — 在 Telegram 中通过文本提示词远程调用 Stable Diffusion WebUI Forge 生成图片。单用户场景，轮询模式连接 Telegram API。

## 常用命令

```bash
# 运行 Bot
uv run python bot.py

# 添加依赖
uv add <package>

# 检查 SD API 连通性
curl -s --connect-timeout 5 http://10.126.126.1:7860/sdapi/v1/sd-models
```

无测试、无 lint 配置。

## 架构

```
用户 Telegram → Telegram Bot API → bot.py (轮询)
                                      ├── DeepSeek API (中译英，可选)
                                      └── SD WebUI Forge API (10.126.126.1:7860)
```

三个核心层：

| 层 | 文件 | 职责 |
|------|------|------|
| 入口 | `bot.py` | 初始化 `Application`，注册 handlers，`run_polling()` |
| 消息路由 | `handlers/*.py` | 命令和文本消息分发，内联键盘回调 |
| 外部服务 | `services/*.py` | SD API 和 DeepSeek 翻译的 HTTP 封装 |

### 关键设计

- **SDK `python-telegram-bot`** v22.7，通过 `python-telegram-bot[job-queue,socks]` 安装以支持代理。
- **代理**：中国网络环境需通过 `PROXY_URL`（socks5/http）连接 Telegram API，`bot.py` 中用 `HTTPXRequest(proxy=...)` 实现。
- **设置存储**：用户参数（尺寸、模型、翻译开关等）存在 `context.user_data["settings"]` 内存字典中，Bot 重启即丢失。
- **SD API 调用超时** 180 秒（`services/sd_api.py:33`），SD 生成大图可能较慢。
- **翻译降级**：DeepSeek 调用失败时静默返回原文，不阻断生成流程（`services/translator.py:28`）。
- **种子输入**：`handlers/generation.py` 通过 `context.user_data["_waiting_seed"]` 标记实现多步交互（点击「种子」→ 等待用户输入数字 → `/cancel` 取消）。
- **Handler 注册**：`get_handlers()` 返回 handler 列表，注册顺序决定优先级（`bot.py:29-30`）。

### 数据流（图片生成）

```
用户发文本 → handle_text() → 检查 _waiting_seed 标记
  → 可选翻译 (translate()) → 可选切换模型 (set_model())
  → txt2img(payload) → 返回图片 bytes → reply_photo()
```

### 配置

所有敏感信息通过 `.env` 加载（`python-dotenv`），常量集中在 `config.py`。`.env.example` 包含完整模板。`config.py` 中 `DEFAULT_USER_SETTINGS` 定义所有参数默认值，`SIZE_PRESETS` / `HIRES_FIX_PARAMS` 可按需增删。
