# SD Telegram Bot

在 Telegram 中通过文本提示词或图片远程调用 ComfyUI / Stable Diffusion WebUI 生成图片和视频的 Bot。

## 功能

### 工作流系统

Bot 提供 6 种工作流，从主菜单一键切换：

| 工作流 | 输入 | 后端 | 说明 |
|--------|------|------|------|
| 文生图 | 文字描述 | ComfyUI (Z-Image-Turbo) | 输入文字，AI 生成图片 |
| 图生图 | 图片 + 可选文字 | ComfyUI (Image-to-Real) | 基于上传图片风格生成新图 |
| 图片编辑 | 图片 + 编辑指令 | ComfyUI (Qwen Image Edit) | 上传图片后多轮编辑修改 |
| 图生视频 | 图片 + 可选文字 | ComfyUI (Wan2.2) | 上传图片生成短视频 |
| 文生图（SDXL） | 文字描述 | ComfyUI (SDXL) | SDXL 模型高质量大图 |
| 首尾帧生视频 | 首帧 + 尾帧 + 可选文字 | ComfyUI (Wan2.2) | 两张图片生成过渡视频 |

### 双后端支持

- **ComfyUI**：主力后端，支持文生图、图生图、视频生成、多轮编辑
- **SD WebUI** (Forge)：传统 txt2img 后端，支持高清修复、面部修复等
- 通过 `/mode` 命令或工作流选择自动切换后端

### 其他功能

- **中译英**：ComfyUI 模式下中文提示词自动翻译为英文（可开关）
- **生成队列**：全局串行队列，多任务自动排队，显示等待位置
- **进度反馈**：生成过程中显示进度百分比
- **设置持久化**：用户参数保存到 JSON 文件，Bot 重启后恢复
- **额度系统**：每用户 100 额度，管理员可通过 `/credit` 命令管理
- **访问控制**：支持用户白名单、群组白名单、管理员
- **群聊支持**：群聊中 @Bot 触发，channel 关联讨论组兼容
- **自定义 Prompt**：可为工作流设置固定 Prompt 前缀，统一输出风格
- **种子控制**：支持随机种子或手动指定，回复生成结果可复用种子

## 前置条件

- Python 3.12+
- 运行中的 [ComfyUI](https://github.com/comfyanonymous/ComfyUI)（必需，需启动 `--enable-cors-header`）或 [Stable Diffusion WebUI Forge](https://github.com/lllyasviel/stable-diffusion-webui-forge)（可选，需 `--api`）
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
# 编辑 .env，填入 TELEGRAM_TOKEN、PROXY_URL、DEEPSEEK_API_KEY 等

# 4. 启动
uv run python bot.py
```

## 配置

所有敏感信息通过 `.env` 文件管理：

| 变量 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `TELEGRAM_TOKEN` | 是 | — | Bot Token |
| `PROXY_URL` | 推荐 | — | 代理地址，如 `socks5://127.0.0.1:10808` |
| `DEEPSEEK_API_KEY` | 翻译需要 | — | DeepSeek API Key |
| `COMFY_API_BASE` | 否 | `http://10.126.126.4:8188` | ComfyUI 服务地址 |
| `SD_API_BASE` | 否 | `http://10.126.126.1:7860` | SD WebUI 服务地址 |
| `DEEPSEEK_BASE_URL` | 否 | `https://api.deepseek.com` | DeepSeek API 地址 |
| `LOG_LEVEL` | 否 | `INFO` | 日志级别 |
| `LOG_FULL_PROMPT` | 否 | `false` | 日志中记录完整提示词 |
| `COMFY_WORKFLOW_PATH` | 否 | `data/zit-api.json` | ComfyUI 文生图工作流 JSON |
| `COMFY_IMG2IMG_WORKFLOW_PATH` | 否 | `data/templates-image_to_real.json` | 图生图工作流 JSON |
| `COMFY_QWEN_EDIT_WORKFLOW_PATH` | 否 | `data/Qwen Image Edit Rapid v1.0 (api).json` | 图片编辑工作流 JSON |
| `COMFY_DEFAULT_MODEL` | 否 | `moodyPornMix_zitV9.safetensors` | 文生图默认模型 |

工作流 JSON 文件、尺寸预设、采样器列表、额度默认值等更多参数在 `config.py` 中修改。

### 访问控制

在 `config.py` 中配置：

| 变量 | 说明 |
|------|------|
| `ALLOWED_USER_IDS` | 用户白名单，空列表 = 不限制 |
| `ALLOWED_CHAT_IDS` | 群组白名单（仅 group/supergroup 生效），空列表 = 不限制 |
| `ADMIN_USER_ID` | 管理员用户 ID，跳过额度限制 |

## 使用

### 基本流程

1. 在 Telegram 中找到 Bot，发送 `/start`
2. 从主菜单选择工作流（如「文生图」），进入说明页
3. 点击「开始使用」或「调整参数」设置好后直接发送内容
4. 文生图：直接发送文字描述
5. 图生图/视频：发送图片（可附带文字描述）

### 工作流详情

**文生图** — 发送英文描述词即可。例如：`a cat sitting on a sofa, masterpiece, best quality`。如需中译英，在 ComfyUI 设置中开启翻译。

**图生图** — 发送图片，Bot 基于图片风格生成新图。例如：发送真人照片 + 描述 `anime style, portrait`。

**图片编辑** — 支持多轮编辑。第一轮发送图片，之后回复生成结果 + 新指令继续修改。例如：回复图片 + `change hair color to blue`。想换底图直接发新图片。

**图生视频** — 发送图片生成短视频。可在设置中调整视频方向（竖版/横版）和长度（3s/5s/7s/10s）。

**文生图（SDXL）** — 与文生图类似，但使用 SDXL 模型，提示词会自动添加画质前缀，可在设置中切换模型和尺寸。

**首尾帧生视频** — 三步交互：
1. 发送首帧图片
2. 发送尾帧图片（可附带文字描述）
3. 如尾帧未附带描述，再发送文字说明
发送 `/cancel` 可随时取消。

### 图片编辑多轮模式

选择「图片编辑」工作流后，有两种进入方式：
- 直接发送图片 — 进入全新的编辑流程
- 回复 Bot 之前的生成结果 + 文字指令 — 在之前结果上继续修改

系统会自动检测你的意图，回复之前的生成结果时无需重新上传底图。

### 群聊使用

群聊中需要 @Bot 来触发：
- `@your_bot 一只猫坐在沙发上` — 生成图片
- `@your_bot` + 发送图片 — 图生图
- `/mode`、`/credit` 等命令无需 @Bot

### 命令列表

| 命令 | 说明 |
|------|------|
| `/start` | 打开工作流主菜单 |
| `/help` | 同 `/start` |
| `/mode` | 切换后端（SD WebUI / ComfyUI） |
| `/credit` | 查看剩余额度 |
| `/cancel` | 取消当前等待输入（种子、Prompt、首尾帧等） |

管理员命令：

| 命令 | 说明 |
|------|------|
| `/credit check <user_id>` | 查看指定用户额度 |
| `/credit add <user_id> <数量>` | 给用户增加额度（可回复用户消息 + `/credit add 20`） |
| `/credit set <user_id> <总数>` | 设置用户总配额 |

### 额度系统

- 每个用户默认 100 额度
- 每次生成消耗 1 额度（视频生成也按 1 次计）
- 管理员不受额度限制
- 生成失败自动退还额度
- 通过帮助面板「额度查询」或 `/credit` 查看余额

## 项目结构

```
sd_telegram_bot/
├── bot.py                    # 入口：初始化 Application、队列、注册 handlers
├── config.py                 # 配置中心（常量、工作流注册表、.env 加载）
├── main.py                   # 备用入口
├── pyproject.toml            # 项目元数据和依赖
├── Dockerfile                # Docker 镜像
├── docker-compose.yml        # Docker Compose 配置
├── handlers/
│   ├── __init__.py           # 权限判断、auth filter、回调装饰器
│   ├── workflow_menu.py      # 工作流导向主菜单、帮助面板
│   ├── generation.py         # 文本/图片消息处理、任务创建、多轮编辑、firstlast-video
│   ├── settings.py           # SD WebUI 设置菜单
│   ├── comfy_settings.py     # ComfyUI 设置菜单（模型、尺寸、视频参数等）
│   └── credits.py            # /credit 命令处理
├── services/
│   ├── comfy_api.py          # ComfyUI API 封装（工作流提交、进度轮询、图片上传）
│   ├── sd_api.py             # SD WebUI API 封装（txt2img、进度轮询、模型/采样器）
│   ├── queue.py              # 生成队列 + 节流进度更新器
│   ├── translator.py         # DeepSeek 翻译（OpenAI 兼容接口）
│   ├── storage.py            # JSON 文件持久化（用户设置、额度数据）
│   ├── credits.py            # 额度管理逻辑
│   ├── network.py            # 网络重试工具
│   └── logger.py             # 日志配置（文件轮转 + 控制台输出）
├── data/
│   ├── zit-api.json          # 文生图工作流 JSON
│   ├── templates-image_to_real.json  # 图生图工作流 JSON
│   ├── Qwen Image Edit Rapid v1.0 (api).json  # 图片编辑工作流 JSON
│   ├── image_to_video.json   # 图生视频工作流 JSON
│   ├── sdxl.json             # SDXL 工作流 JSON
│   ├── video_wan2_2_14B_flf2v.json  # 首尾帧生视频工作流 JSON
│   ├── user_settings/        # 用户设置 JSON（自动创建）
│   ├── credits/              # 额度数据（自动创建）
│   └── tags/                 # 标签缓存
├── logs/                     # 日志文件（自动创建，5MB 轮转，保留 3 个备份）
└── docs/
    └── deployment.md         # 部署指南（systemd、Docker 等）
```

## 架构

```
用户 Telegram → Telegram Bot API → bot.py (轮询)
                                      ├── DeepSeek API (中译英，可选)
                                      ├── ComfyUI API (10.126.126.4:8188)
                                      └── SD WebUI Forge API (10.126.126.1:7860)
```

三个核心层：

| 层 | 文件 | 职责 |
|------|------|------|
| 入口 | `bot.py` | 初始化 `Application`，注册 handlers，`run_polling()` |
| 消息路由 | `handlers/*.py` | 命令和文本/图片消息分发，内联键盘回调 |
| 外部服务 | `services/*.py` | ComfyUI API、SD API、DeepSeek 翻译的 HTTP 封装 |

### 关键设计

- **SDK**：`python-telegram-bot` v22.7，通过 `python-telegram-bot[job-queue,socks]` 安装以支持代理
- **代理**：通过 `PROXY_URL`（socks5/http）连接 Telegram API，`bot.py` 中用 `HTTPXRequest(proxy=...)` 实现
- **工作流注册**：`config.py` 中 `WORKFLOW_REGISTRY` 驱动主菜单，每个工作流关联后端类型和 ComfyUI workflow 配置
- **设置存储**：`data/user_settings/` 下 JSON 文件持久化，Bot 重启不丢失
- **额度存储**：`data/credits/` 下 JSON 文件持久化
- **ComfyUI 调用超时**：1500 秒（`config.py:24`），视频生成可能很慢
- **SD API 调用超时**：180 秒（`services/sd_api.py:33`）
- **翻译降级**：DeepSeek 调用失败时静默返回原文，不阻断生成流程
- **多步交互**：通过 `context.user_data` 标记实现（种子输入、Prompt 输入、firstlast-video 首尾帧收集）
- **自动编辑检测**：回复 Bot 图片消息 + 文字时自动切换为 Qwen Image Edit 模式
- **Handler 注册顺序**：`bot.py:61-65`，先注册的优先匹配

### 数据流（文生图）

```
用户发文本 → handle_text() → 权限检查 → 等待输入检查
  → 可选翻译 → 额度检查 → 创建 GenerationTask → 入队
  → Worker 取出 → ComfyUI API → 轮询进度 → 返回结果
  → 回复图片/视频给用户
```

### 数据流（首尾帧生视频）

```
步骤1: 用户发首帧 → 上传 ComfyUI → 缓存 _firstlast_start_frame
步骤2: 用户发尾帧（可选文字）→ 上传 ComfyUI → 缓存 _firstlast_end_frame
  → 无文字: 提示输入描述
  → 有文字: 额度检查 → 创建任务 → 入队 → 生成 → 回复视频
/cancel: 清除所有 firstlast 状态
```

## 进度说明

**SD WebUI 模式**：进度百分比来自 SD WebUI 的全局 `/sdapi/v1/progress` 端点。Bot 使用串行队列独占生成，正常情况下进度显示准确。如果通过 WebUI 或其他程序同时提交任务，显示的进度可能不准确。

**ComfyUI 模式**：进度来自 ComfyUI 的 `/history/{prompt_id}` 端点，反映当前任务的执行状态（queue / running / 百分比），不受其他客户端影响。

## 部署

### Docker（推荐）

```bash
# 准备 .env 文件
cp .env.example .env
vim .env  # 配置 TELEGRAM_TOKEN、PROXY_URL 等

# 启动
docker compose up -d --build

# 查看日志
docker logs sd-telegram-bot --since 1m

# 停止
docker compose down
```

容器使用 `network_mode: host` 以直接访问宿主机上的 ComfyUI 和代理服务。

### 手动部署

详见 [docs/deployment.md](docs/deployment.md)，涵盖 systemd 服务配置、网络说明、数据目录等。

## 推送代码到服务器

```bash
# 同步源码（排除不需要的文件）
rsync -avz \
  --exclude '.git' --exclude '__pycache__' --exclude '.venv/' \
  --exclude '.env' --exclude 'data/' --exclude 'logs/' \
  --exclude '.codegraph/' --exclude '.claude/' --exclude 'docs/' \
  --exclude '*.pyc' \
  ./ homelab:/home/ksufer/homelab/stacks/sd-telegram-bot/

# 重建并启动
ssh homelab "cd /home/ksufer/homelab/stacks/sd-telegram-bot && docker compose up -d --build"
```
