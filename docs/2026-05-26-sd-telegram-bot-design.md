# SD Telegram Bot 设计方案

## 项目概述

基于 `python-telegram-bot` 构建的 Telegram Bot，通过内网连接 SD WebUI Forge（`10.126.126.1:7860`）的 A1111 兼容 API，实现在 Telegram 中输入提示词远程生成 Stable Diffusion 图片。

**使用场景**：单用户，通过 Telegram 客户端随时随地使用 SD 绘图。

---

## 功能清单

| 功能 | 说明 |
|------|------|
| 文本提示词生成 | 发送中文/英文提示词，Bot 调用 SD txt2img 生成图片 |
| 中译英开关 | 可选，调用 DeepSeek API 将中文提示词翻译为英文 |
| 负面提示词 | 内置默认值（可修改 `config.py`），生成时自动拼接 |
| 图片尺寸 | 预制尺寸选项，通过内联按钮选择 |
| 切换模型 | 显示当前 SD 可用模型列表，选择切换 |
| 高清修复 | 开关按钮，启用时使用预置的 Hires Fix 参数 |
| 种子控制 | 默认随机，可手动设置 |
| 参数持久化 | 内存字典存储，单次会话有效 |

---

## 架构

```
用户 Telegram 客户端
       │
       ▼
  Telegram Bot API (云端)
       │
       ▼
  bot.py (python-telegram-bot，轮询模式)
       │
       ├──▶ DeepSeek API (中译英，可选)
       │
       └──▶ SD WebUI Forge API (10.126.126.1:7860)
            ├── /sdapi/v1/txt2img
            ├── /sdapi/v1/sd-models
            └── /sdapi/v1/options
```

---

## 项目结构

```
sd_telegram_bot/
├── bot.py                 # Bot 入口：初始化 Application，注册 handlers，启动轮询
├── config.py              # 所有可配置常量集中管理
├── handlers/
│   ├── __init__.py
│   ├── commands.py        # /start /help /cancel
│   ├── settings.py        # 设置菜单回调：参数面板、尺寸选择、模型选择等
│   └── generation.py      # 接收文本消息 → 可选翻译 → 调用 SD → 返回图片
├── services/
│   ├── __init__.py
│   ├── sd_api.py          # SD WebUI API 封装
│   └── translator.py      # DeepSeek 翻译
└── requirements.txt
```

---

## 模块职责

### `config.py` — 配置中心

```python
# Telegram
TELEGRAM_TOKEN = "your_bot_token"

# SD WebUI API
SD_API_BASE = "http://10.126.126.1:7860"

# DeepSeek（翻译用）
DEEPSEEK_API_KEY = "your_api_key"
DEEPSEEK_BASE_URL = "https://api.deepseek.com"

# 默认生成参数
DEFAULT_NEGATIVE_PROMPT = "lowres, bad anatomy, ..."  # 可随时修改
DEFAULT_STEPS = 30
DEFAULT_CFG_SCALE = 7

# 预置尺寸
SIZE_PRESETS = {
    "512×768 (竖版)":  (512, 768),
    "768×512 (横版)":  (768, 512),
    "512×512 (方形)":  (512, 512),
    "896×1152 (XL竖)": (896, 1152),
    "1152×896 (XL横)": (1152, 896),
}

# 高清修复预置参数
HIRES_FIX_PARAMS = {
    "upscaler": "R-ESRGAN 4x+",
    "upscale": 2.0,
    "denoising_strength": 0.45,
    "steps": 15,
}
```

### `services/sd_api.py` — SD API 封装

- `get_models()` → 获取可用模型列表
- `set_model(model_name)` → 切换当前模型
- `txt2img(params: dict)` → 发送 txt2img 请求，返回图片 bytes
- 使用 `httpx.AsyncClient`，超时设 120 秒（SD 生成可能较慢）

### `services/translator.py` — DeepSeek 翻译

- `translate(text: str)` → 翻译结果
- 使用 OpenAI 兼容接口调用 DeepSeek
- 翻译失败时返回原文（降级处理）

### `handlers/commands.py` — 命令处理

- `/start` → 发送欢迎信息 + 主菜单键盘
- `/help` → 使用说明

### `handlers/settings.py` — 设置面板

用 `ConversationHandler` + 内联按钮实现多级菜单：

**主菜单**：
```
[生成图片] [参数设置]
```

**参数设置子菜单**：
```
尺寸: 512×768 (竖版)  [修改]
模型: miaomiaoHarem     [修改]
高清修复: 关             [切换]
种子: 随机              [修改]
中译英: 关              [切换]
负面提示词: (默认)       [修改]
```

- 每个「修改」按钮弹出选项列表
- 「切换」按钮直接 toggle 状态
- 所有参数存在 `user_settings: dict` 中（内存）

### `handlers/generation.py` — 图片生成

处理流程：
1. 用户发送文本消息（不是命令）
2. 检查中译英开关 → 若开启，调用 DeepSeek 翻译
3. 拼接参数（正面提示词 + 负面提示词 + 尺寸 + 高清修复等）
4. 回复「正在生成...」消息
5. 调用 `sd_api.txt2img()`
6. 编辑消息为「正在上传...」
7. 发送图片 + 生成参数信息
8. 删除「生成中」状态消息
9. 错误处理：超时 / API 不可用 → 回复错误信息

---

## 交互流程示例

```
User: /start
Bot:  欢迎！请使用下方菜单操作。
      [生成图片] [参数设置]

User: 点击 [参数设置]
Bot:  当前参数:
      尺寸: 512×768  [选择尺寸]
      模型: miaomiaoHarem_v195  [切换模型]
      高清修复: 关  [开启]
      种子: 随机  [设置种子]
      中译英: 关  [开启]

User: 点击 [选择尺寸]
Bot:  选择预设尺寸:
      [512×768 (竖版)] [768×512 (横版)]
      [512×512 (方形)] [896×1152 (XL竖)]
      [1152×896 (XL横)]

User: 点击 [896×1152 (XL竖)]
Bot:  ✓ 尺寸已设为 896×1152
      (自动返回设置菜单，显示更新后的参数)

User: 发文本 "一只可爱的猫"
Bot:  正在生成... (翻译为 "A cute cat" → 调用 SD)
      → 返回图片 + 参数信息
```

---

## 依赖

```
python-telegram-bot[job-queue]>=21.0
httpx>=0.27.0
openai>=1.0.0          # DeepSeek 兼容 OpenAI SDK
```

---

## 配置项总览

| 配置 | 位置 | 说明 |
|------|------|------|
| Bot Token | `config.py` | 从 @BotFather 获取 |
| SD API 地址 | `config.py` | `http://10.126.126.1:7860` |
| DeepSeek API Key | `config.py` 或 `.env` | 翻译用 |
| 默认负面提示词 | `config.py` | 可随时修改，无需改代码 |
| 预置尺寸列表 | `config.py` | 可增删尺寸选项 |
| 高清修复参数 | `config.py` | upscaler、倍数、降噪强度等 |
| 默认 Steps / CFG | `config.py` | 生成参数默认值 |

---

## 错误处理策略

| 场景 | 处理方式 |
|------|---------|
| SD API 不可达 | 回复"SD 服务不可用，请检查服务是否启动" |
| SD 生成超时 (>120s) | 回复"生成超时，请尝试降低参数或检查 SD" |
| DeepSeek 翻译失败 | 降级：使用原文发送，不阻塞生成 |
| Telegram 消息过长 | 不适用，返回图片为主 |
| 用户并发请求 | 单用户场景，消息自然排队处理 |

---

## 验证方式

1. 启动 Bot，发送 `/start`，确认菜单正常显示
2. 通过菜单修改各项参数，确认回调正常
3. 发送中文提示词（翻译关），确认直接用原文调用 SD
4. 开启翻译，发送中文提示词，确认翻译后调用 SD
5. 切换模型，确认 SD 端模型切换成功
6. 开启高清修复，确认生成图片尺寸翻倍
7. 修改种子为固定值，两次生成相同图片验证种子生效
8. 模拟 SD 离线，确认错误消息友好
