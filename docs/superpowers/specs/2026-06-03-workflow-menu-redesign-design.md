# 工作流导向主菜单重设计

**日期**: 2026-06-03
**目标**: 将以参数设置为中心的交互模式，改造为以工作流为中心的主菜单，帮助新用户快速发现和上手各项功能

## 上下文

Bot 目前已有 4 个 ComfyUI 工作流（文生图、图生图、图片编辑、图生视频）和 SD WebUI 传统文生图，但 `/start` 仅展示"参数设置"和"关闭菜单"两个按钮，新用户无从得知能力全貌，也无法知道如何使用。

用户反馈核心痛点为：
1. **功能发现** — 不知道 Bot 能做什么（有图生图、图生视频、多轮编辑等）
2. **操作引导** — 知道功能但不知道如何触发（要发图片？回复？@bot？）

## 设计决策

- **工作流是第一公民**：主菜单按"用户想做什么"组织，而非按后端/技术分类
- **配置驱动**：工作流在 `config.py` 的 `WORKFLOW_REGISTRY` 中声明，增删工作流不改 handler 代码
- **SD WebUI 隐藏但保留**：代码不动，主菜单不显示，日后若需用可恢复
- **自动后端切换**：选择工作流时自动切到对应后端，对用户透明
- **现有设置菜单不动**：高级用户仍可通过 `⚙️ 参数设置` 进入现有详细设置

## 架构概览

```
/start → 主菜单（工作流列表 + 设置 + 帮助）
           │
           ├── 🖼 文生图 → 说明页 → [⚡开始][⚙️参数][🔙返回]
           ├── 🔄 图生图 → 说明页 → [⚡开始][⚙️参数][🔙返回]
           ├── ✏️ 图片编辑 → 说明页 → [⚡开始][⚙️参数][🔙返回]
           ├── 🎬 图生视频 → 说明页 → [⚡开始][⚙️参数][🔙返回]
           ├── ⚙️ 参数设置 → 现有 ComfyUI 设置菜单
           └── 📖 帮助 → 指南/额度/命令/技巧
```

新增 handler 文件 `handlers/workflow_menu.py`，负责主菜单、工作流说明页、帮助面板的所有逻辑。现有 handler 文件仅做最小改动（`/start` `/help` 指向新主菜单）。

---

## 1. config.py — 新增 WORKFLOW_REGISTRY

```python
WORKFLOW_REGISTRY = [
    {
        "key": "z-image-turbo",
        "emoji": "🖼",
        "label": "文生图",
        "description": "输入文字描述，AI 生成图片",
        "how_to": (
            "直接发送描述词即可\n"
            "例如：a cat sitting on a sofa, masterpiece, best quality\n\n"
            "可选：在 ComfyUI 设置中自定义 Prompt，固定描述风格"
        ),
        "backend": "comfyui",
        "comfy_workflow": "z-image-turbo",
        "input_type": "text",
    },
    {
        "key": "image-to-real",
        "emoji": "🔄",
        "label": "图生图",
        "description": "上传图片，AI 基于图片风格生成新图",
        "how_to": (
            "1. 发送一张图片（可附带文字描述）\n"
            "2. Bot 将基于该图片生成新图片\n"
            "例如：发一张真人照片 + 描述 'anime style, portrait'"
        ),
        "backend": "comfyui",
        "comfy_workflow": "image-to-real",
        "input_type": "photo",
    },
    {
        "key": "qwen-image-edit",
        "emoji": "✏️",
        "label": "图片编辑",
        "description": "上传图片后持续修改，支持多轮编辑",
        "how_to": (
            "第一轮：发送一张图片 → AI 编辑后返回结果\n"
            "第二轮：回复结果图 + 新指令 → 继续修改\n"
            "例如：回复图片 + 'change hair color to blue'\n\n"
            "想换底图？直接发新图片即可重新开始"
        ),
        "backend": "comfyui",
        "comfy_workflow": "qwen-image-edit",
        "input_type": "photo",
    },
    {
        "key": "image-to-video",
        "emoji": "🎬",
        "label": "图生视频",
        "description": "上传图片，AI 生成短视频",
        "how_to": (
            "发送一张图片（可附带描述词）\n"
            "例如：发一张风景照 → 生成动态视频\n\n"
            "可在 ComfyUI 设置中调整视频方向和长度"
        ),
        "backend": "comfyui",
        "comfy_workflow": "image-to-video",
        "input_type": "photo",
    },
]
```

### 字段说明

| 字段 | 用途 |
|------|------|
| `key` | 唯一标识，用于 callback data（`workflow:z-image-turbo`） |
| `emoji` | 按钮和标题的前缀图标 |
| `label` | 显示名称（按钮文字和页面标题） |
| `description` | 一行简介，主菜单可做副标题 |
| `how_to` | 使用说明正文，展示在工作流详情页 |
| `backend` | 自动切换的后端：`"comfyui"` |
| `comfy_workflow` | 对应 `COMFY_WORKFLOWS` 的 key |
| `input_type` | `"text"` 或 `"photo"`，决定引导文字 |

---

## 2. handlers/workflow_menu.py — 新文件

### 2.1 主菜单

**触发**：`/start`、`/help`、callback `main_menu`

**显示内容**：
```
🎨 SD 绘图助手

选择你要使用的功能：

[🖼 文生图]  [🔄 图生图]
[✏️ 图片编辑] [🎬 图生视频]

[⚙️ 参数设置] [📖 帮助] [关闭]
```

**实现要点**：
- `show_main_menu(update, context)` 读取 `WORKFLOW_REGISTRY`，动态生成按钮网格（一行两个）
- 每个工作流按钮的 callback data 为 `workflow:{key}`
- 底部固定按钮：`⚙️ 参数设置`(callback `comfy_settings`)、`📖 帮助`(callback `help_menu`)、`关闭`(callback `close_menu`)
- 复用现有 `close_menu` 回调删除消息

### 2.2 工作流说明页

**触发**：callback `workflow:{key}`

**显示内容**（以文生图为例）：
```
🖼 文生图

输入文字描述，AI 生成图片

📋 使用方法：
直接发送描述词即可
例如：a cat sitting on a sofa, masterpiece, best quality

可选：在 ComfyUI 设置中自定义 Prompt，固定描述风格

当前设置：模型=moodyPornMix_zitV9 | 尺寸=768x1280 | 种子=随机

[⚡ 开始使用] [⚙️ 调整参数] [🔙 返回]
```

**实现要点**：
- `show_workflow_detail(update, context, workflow_key)` 从 `WORKFLOW_REGISTRY` 和 `COMFY_WORKFLOWS` 查找配置
- 读取用户当前设置（模型、尺寸、种子），显示为"当前设置"行
- `[⚡ 开始使用]` → callback `workflow_start:{key}`：切换后端到 `comfyui`，切换 `comfy_workflow`，根据 `input_type` 显示引导（"请发送文字描述" / "请发送图片"），然后删除菜单消息
- `[⚙️ 调整参数]` → callback `comfy_settings`：进入现有 ComfyUI 设置。额外逻辑：如果用户当前 workflow 不是此页面的 workflow，先切换再显示设置
- `[🔙 返回]` → callback `main_menu`

### 2.3 帮助面板

**触发**：callback `help_menu`、`/help`（当主菜单已打开时，`/help` 刷新到帮助面板）
> **注意**：`/help` 命令统一走 `show_main_menu`，与 `/start` 一致。帮助面板仅通过主菜单按钮进入。

**显示内容**：
```
📖 帮助

[📋 使用指南] [💰 额度查询]
[🔑 命令列表] [💡 使用技巧]

[🔙 返回]
```

**子项**：

**📋 使用指南**（callback `help:guide`）— 循环 `WORKFLOW_REGISTRY`，列出每个工作流的 emoji + label + description，点击跳转到对应说明页

**💰 额度查询**（callback `help:credit`）— 调用 `credits.get_remaining()` 显示当前剩余额度，附带说明"每次生成消耗 1 额度，用完后联系管理员充值"

**🔑 命令列表**（callback `help:commands`）：
```
/mode — 切换后端（SD WebUI / ComfyUI）
/cancel — 取消当前等待输入
/credit — 查看额度
/start — 重新打开主菜单
/help — 打开本帮助
```

**💡 使用技巧**（callback `help:tips`）— 静态文案：
```
• 在 ComfyUI 设置中固定种子，可以复现之前的生成结果
• 为工作流设置自定义 Prompt，固定输出风格
• 回复生成结果可以复用该次的种子
• 图片编辑模式支持多轮修改，每次回复继续改
```

### 2.4 自动后端切换

```python
async def _switch_to_workflow(update, context, workflow_entry):
    """选择工作流时自动切换后端和 ComfyUI workflow"""
    settings = _ensure_settings(context, user_id)
    target_backend = workflow_entry["backend"]
    comfy_key = workflow_entry["comfy_workflow"]

    settings["backend"] = target_backend
    settings["comfy_workflow"] = comfy_key

    # 如果是 model_selectable 的工作流，重置为默认模型
    wf_config = COMFY_WORKFLOWS.get(comfy_key, {})
    if wf_config.get("model_selectable", False):
        settings["comfy_model"] = wf_config.get("default_model", "")

    _save_settings(user_id, settings)
```

---

## 3. 现有文件改动

### 3.1 handlers/settings.py

- `show_main_menu()`（第 239-244 行）：改为调用 `workflow_menu.py` 的新 `show_main_menu`
- 移除 `show_main_menu` 上的 `/start` 和 `/help` handler 注册（第 516-517 行），移到 `workflow_menu.py`

### 3.2 handlers/comfy_settings.py

- 无需改动结构
- 当从工作流说明页的「⚙️ 调整参数」进入时，确保显示的是该工作流对应的设置（检查并切换 `comfy_workflow`）
- 所有设置菜单的「返回」按钮统一回到 `main_menu`（当前部分返回 `comfy_settings` 自身）

### 3.3 handlers/generation.py

- 无需改动核心流程
- SD WebUI 相关 handler（`mode:sd` callback、SD 生成逻辑）保留不动，只是不在主菜单暴露

### 3.4 bot.py

- 移除 `show_main_menu` 上的 `/start` / `/help` handler
- 新增 `workflow_menu.py` 的 handler 注册：
  - `/start` → `show_main_menu`
  - `/help` → `show_main_menu`
  - 所有 `workflow:*` / `workflow_start:*` / `help_menu` / `help:*` / `main_menu` callback patterns

---

## 4. 扩展方式

未来添加新工作流的步骤（只需两步）：

1. 在 `config.py` 的 `WORKFLOW_REGISTRY` 中追加一个 dict
2. 确保 `COMFY_WORKFLOWS` 中有对应的 workflow 配置

主菜单按钮、说明页、使用指南自动出现，无需改动任何 handler 代码。

---

## 5. 验证

### 功能验证

| 场景 | 预期行为 |
|------|---------|
| 新用户 `/start` | 看到工作流主菜单，4 个工作流按钮 + 设置 + 帮助 |
| 点击「🖼 文生图」 | 看到说明页，显示使用方法 + 当前设置 |
| 点击「⚡ 开始使用」 | 后端自动切 ComfyUI + 对应 workflow，提示用户输入 |
| 用户发文字 | 正常生成图片（走 z-image-turbo） |
| 点击「🔄 图生图」→「⚡ 开始」 | 后端切 ComfyUI + image-to-real，提示用户发图 |
| 用户发图片 | 正常图生图 |
| 点击「📖 帮助」→「📋 使用指南」 | 列出所有工作流概要 |
| 点击「🔑 命令列表」 | 显示所有命令及说明 |
| 点击「💰 额度查询」 | 显示当前剩余额度 |
| 点击「⚙️ 参数设置」 | 进入现有 ComfyUI 设置菜单 |
| 「🔙 返回」按钮 | 回到主菜单 |
| 旧用户（已有设置的） | 正常使用，无破坏性变化 |
| 群组中 @bot | 正常响应，行为不变 |

### 手动测试流程

1. `uv run python bot.py` 启动 Bot
2. 私聊发送 `/start` → 确认主菜单显示
3. 依次点击 4 个工作流 → 确认说明页内容正确
4. 选择「文生图」→「开始使用」→ 发送文字 → 确认生成成功
5. 选择「图生图」→「开始使用」→ 发送图片 → 确认生成成功
6. 选择「图片编辑」→ 发送图片 → 回复结果图再编辑 → 确认多轮编辑正常
7. 打开帮助面板 → 确认所有子项正常显示
8. 群组中 @bot 发 `/start@botusername` → 确认功能正常
