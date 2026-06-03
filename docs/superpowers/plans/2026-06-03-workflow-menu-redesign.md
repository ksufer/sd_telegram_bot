# 工作流导向主菜单重设计 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `/start` 主菜单从参数设置导向改造为工作流导向，帮助新用户发现和上手各项功能。

**Architecture:** 新增 `handlers/workflow_menu.py` 负责主菜单、工作流说明页、帮助面板。现有 handler 文件最小改动：`settings.py` 移除 `/start`/`/help` handler，`comfy_settings.py` 返回按钮改为指向 `main_menu`，`bot.py` 注册新 handler。`config.py` 新增 `WORKFLOW_REGISTRY` 配置驱动工作流列表。**不碰 generation.py、queue.py、comfy_api.py。**

**Tech Stack:** python-telegram-bot v22.7, InlineKeyboardMarkup, CallbackQueryHandler

---

### Task 1: 新增 WORKFLOW_REGISTRY 到 config.py

**Files:**
- Modify: `config.py`（在 `COMFY_DEFAULT_WORKFLOW` 之后插入）

- [ ] **Step 1: 在 config.py 中添加 WORKFLOW_REGISTRY**

在 `config.py` 第 24 行 `COMFY_DEFAULT_WORKFLOW = "z-image-turbo"` 之后插入：

```python
# ---- 工作流注册表（主菜单驱动） ----
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

- [ ] **Step 2: 验证导入**

```bash
cd /home/ksufer/My_data/Superman/my_projects/sd_telegram_bot && uv run python -c "from config import WORKFLOW_REGISTRY; print(f'{len(WORKFLOW_REGISTRY)} workflows loaded')"
```
Expected: `4 workflows loaded`

- [ ] **Step 3: Commit**

```bash
git add config.py
git commit -m "feat: add WORKFLOW_REGISTRY for workflow-driven main menu"
```

---

### Task 2: 创建 handlers/workflow_menu.py

**Files:**
- Create: `handlers/workflow_menu.py`

- [ ] **Step 1: 创建文件骨架和所有回调处理函数**

```python
"""工作流导向主菜单 — /start、工作流说明页、帮助面板。"""

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest
from telegram.ext import CallbackQueryHandler, CommandHandler

from config import (
    WORKFLOW_REGISTRY,
    COMFY_WORKFLOWS,
    COMFY_DEFAULT_WORKFLOW,
    COMFY_VIDEO_ORIENTATIONS,
    COMFY_VIDEO_FRAMES_PRESETS,
)
from handlers import auth_callback, _user_auth_filter
from handlers.settings import (
    _ensure_settings,
    _save_settings,
    close_menu,
)
from services import credits as credits_service

logger = logging.getLogger(__name__)


# ═══ 工具函数 ═══

async def _safe_answer(query, text: str | None = None, show_alert: bool = False):
    """安全响应回调，代理不稳定时忽略网络错误。"""
    try:
        await query.answer(text, show_alert=show_alert)
    except Exception:
        pass


async def _reply_menu(query, text: str, markup):
    """用内联菜单编辑消息，失败时发送新消息。"""
    try:
        await query.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    except BadRequest:
        await _safe_answer(query)
        await query.message.reply_text(text, reply_markup=markup, parse_mode="HTML")


def _find_workflow(key: str) -> dict | None:
    """在 WORKFLOW_REGISTRY 中按 key 查找工作流。"""
    for wf in WORKFLOW_REGISTRY:
        if wf["key"] == key:
            return wf
    return None


def _get_user_id(update) -> int:
    return update.effective_user.id


# ═══ 自动后端切换 ═══

async def _switch_to_workflow(update, context, workflow_entry: dict):
    """选择工作流时自动切换后端和 ComfyUI workflow。"""
    user_id = _get_user_id(update)
    settings = _ensure_settings(context, user_id)
    comfy_key = workflow_entry["comfy_workflow"]

    settings["backend"] = workflow_entry["backend"]
    settings["comfy_workflow"] = comfy_key

    wf_config = COMFY_WORKFLOWS.get(comfy_key, {})
    if wf_config.get("model_selectable", True):
        default_model = wf_config.get("default_model", "")
        if default_model:
            settings["comfy_model"] = default_model

    _save_settings(context, user_id)


# ═══ 主菜单 ═══

def _build_main_menu() -> tuple[str, InlineKeyboardMarkup]:
    """构建工作流导向主菜单。"""
    text = (
        "<b>🎨 SD 绘图助手</b>\n\n"
        "选择你要使用的功能："
    )

    keyboard = []
    # 工作流按钮 — 一行两个
    row = []
    for wf in WORKFLOW_REGISTRY:
        btn = InlineKeyboardButton(
            f"{wf['emoji']} {wf['label']}",
            callback_data=f"workflow:{wf['key']}",
        )
        row.append(btn)
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    keyboard.append([
        InlineKeyboardButton("⚙️ 参数设置", callback_data="comfy_settings"),
        InlineKeyboardButton("📖 帮助", callback_data="help_menu"),
    ])
    keyboard.append([
        InlineKeyboardButton("关闭", callback_data="close_menu"),
    ])

    return text, InlineKeyboardMarkup(keyboard)


async def show_main_menu(update, context):
    """显示工作流导向主菜单（/start 和 /help 入口）。"""
    text, markup = _build_main_menu()
    msg = update.effective_message
    if msg is None:
        return
    await msg.reply_text(text, reply_markup=markup, parse_mode="HTML")


async def main_menu_callback(update, context):
    """回调返回主菜单。"""
    query = update.callback_query
    await _safe_answer(query)
    text, markup = _build_main_menu()
    await _reply_menu(query, text, markup)


# ═══ 工作流说明页 ═══

def _build_workflow_detail(workflow_entry: dict, settings: dict) -> tuple[str, InlineKeyboardMarkup]:
    """构建单个工作流的说明页。"""
    wf_key = workflow_entry["comfy_workflow"]
    wf_config = COMFY_WORKFLOWS.get(wf_key, {})

    text = f"<b>{workflow_entry['emoji']} {workflow_entry['label']}</b>\n\n"
    text += f"{workflow_entry['description']}\n\n"
    text += f"📋 <b>使用方法：</b>\n{workflow_entry['how_to']}\n\n"

    # 当前设置摘要
    parts = []
    if wf_config.get("model_selectable", True):
        model = settings.get("comfy_model", wf_config.get("default_model", "?"))
        parts.append(f"模型={model}")
    if not wf_config.get("is_img2img", False):
        w = settings.get("comfy_width", 768)
        h = settings.get("comfy_height", 1280)
        parts.append(f"尺寸={w}×{h}")
    if wf_config.get("output_type") == "video":
        orient = settings.get("comfy_video_orientation", "portrait")
        orient_label = COMFY_VIDEO_ORIENTATIONS.get(orient, {}).get("label", orient)
        frames_key = str(settings.get("comfy_video_frames", 81))
        frames_label = COMFY_VIDEO_FRAMES_PRESETS.get(frames_key, {}).get("label", frames_key)
        parts.append(f"方向={orient_label}")
        parts.append(f"长度={frames_label}")
    seed = settings.get("comfy_seed", -1)
    parts.append(f"种子={'随机' if seed == -1 else str(seed)}")

    text += f"<b>当前设置：</b>{' | '.join(parts)}"

    keyboard = [
        [
            InlineKeyboardButton(
                "⚡ 开始使用",
                callback_data=f"workflow_start:{workflow_entry['key']}",
            ),
            InlineKeyboardButton(
                "⚙️ 调整参数",
                callback_data="comfy_settings",
            ),
        ],
        [InlineKeyboardButton("🔙 返回", callback_data="main_menu")],
    ]

    return text, InlineKeyboardMarkup(keyboard)


async def show_workflow_detail(update, context):
    """显示工作流说明页。"""
    query = update.callback_query
    await _safe_answer(query)

    # 从 callback data 提取 workflow key：workflow:z-image-turbo
    wf_key = query.data.split(":", 1)[1]
    workflow_entry = _find_workflow(wf_key)
    if workflow_entry is None:
        await _safe_answer(query, "工作流不存在", show_alert=True)
        return

    user_id = _get_user_id(update)
    settings = _ensure_settings(context, user_id)
    text, markup = _build_workflow_detail(workflow_entry, settings)
    await _reply_menu(query, text, markup)


async def workflow_start(update, context):
    """「⚡ 开始使用」— 切换后端 + 引导用户。"""
    query = update.callback_query
    await _safe_answer(query)

    wf_key = query.data.split(":", 1)[1]
    workflow_entry = _find_workflow(wf_key)
    if workflow_entry is None:
        await _safe_answer(query, "工作流不存在", show_alert=True)
        return

    await _switch_to_workflow(update, context, workflow_entry)

    if workflow_entry["input_type"] == "text":
        hint = "✅ 已就绪！现在发送文字描述即可生成图片。"
    else:
        hint = "✅ 已就绪！现在发送一张图片即可开始。"

    await _safe_answer(query, hint, show_alert=True)

    # 删除菜单消息，让用户直接操作
    try:
        await query.message.delete()
    except Exception:
        pass


# ═══ 帮助面板 ═══

def _build_help_menu() -> tuple[str, InlineKeyboardMarkup]:
    text = "<b>📖 帮助</b>"
    keyboard = [
        [
            InlineKeyboardButton("📋 使用指南", callback_data="help:guide"),
            InlineKeyboardButton("💰 额度查询", callback_data="help:credit"),
        ],
        [
            InlineKeyboardButton("🔑 命令列表", callback_data="help:commands"),
            InlineKeyboardButton("💡 使用技巧", callback_data="help:tips"),
        ],
        [InlineKeyboardButton("🔙 返回", callback_data="main_menu")],
    ]
    return text, InlineKeyboardMarkup(keyboard)


async def show_help_menu(update, context):
    query = update.callback_query
    await _safe_answer(query)
    text, markup = _build_help_menu()
    await _reply_menu(query, text, markup)


async def help_guide(update, context):
    """使用指南 — 列出所有工作流概要，点击跳转详情。"""
    query = update.callback_query
    await _safe_answer(query)

    text = "<b>📋 使用指南</b>\n\n选择你要了解的功能："
    keyboard = []
    for wf in WORKFLOW_REGISTRY:
        keyboard.append([
            InlineKeyboardButton(
                f"{wf['emoji']} {wf['label']} — {wf['description']}",
                callback_data=f"workflow:{wf['key']}",
            ),
        ])
    keyboard.append([InlineKeyboardButton("🔙 返回", callback_data="help_menu")])
    await _reply_menu(query, text, InlineKeyboardMarkup(keyboard))


async def help_credit(update, context):
    query = update.callback_query
    await _safe_answer(query)

    user_id = _get_user_id(update)
    remaining = await credits_service.get_remaining(user_id)
    total_quota = (await credits_service.get_stats(user_id))["total_quota"]

    text = (
        f"<b>💰 额度查询</b>\n\n"
        f"已用：{total_quota - remaining} / {total_quota}\n"
        f"剩余：<b>{remaining}</b>\n\n"
        f"每次生成消耗 1 额度，用完后联系管理员充值。"
    )
    keyboard = [[InlineKeyboardButton("🔙 返回", callback_data="help_menu")]]
    await _reply_menu(query, text, InlineKeyboardMarkup(keyboard))


async def help_commands(update, context):
    query = update.callback_query
    await _safe_answer(query)

    text = (
        "<b>🔑 命令列表</b>\n\n"
        "/mode — 切换后端（SD WebUI / ComfyUI）\n"
        "/cancel — 取消当前等待输入\n"
        "/credit — 查看额度\n"
        "/start — 重新打开主菜单\n"
        "/help — 打开本帮助"
    )
    keyboard = [[InlineKeyboardButton("🔙 返回", callback_data="help_menu")]]
    await _reply_menu(query, text, InlineKeyboardMarkup(keyboard))


async def help_tips(update, context):
    query = update.callback_query
    await _safe_answer(query)

    text = (
        "<b>💡 使用技巧</b>\n\n"
        "• 在 ComfyUI 设置中固定种子，可以复现之前的生成结果\n"
        "• 为工作流设置自定义 Prompt，固定输出风格\n"
        "• 回复生成结果可以复用该次的种子\n"
        "• 图片编辑模式支持多轮修改，每次回复继续改"
    )
    keyboard = [[InlineKeyboardButton("🔙 返回", callback_data="help_menu")]]
    await _reply_menu(query, text, InlineKeyboardMarkup(keyboard))


# ═══ Handler 注册 ═══

def get_handlers() -> list:
    return [
        CommandHandler("start", show_main_menu, filters=_user_auth_filter()),
        CommandHandler("help", show_main_menu, filters=_user_auth_filter()),
        CallbackQueryHandler(auth_callback(main_menu_callback), pattern=r"^main_menu$"),
        CallbackQueryHandler(auth_callback(show_workflow_detail), pattern=r"^workflow:[a-z][a-z0-9-]*$"),
        CallbackQueryHandler(auth_callback(workflow_start), pattern=r"^workflow_start:[a-z][a-z0-9-]*$"),
        CallbackQueryHandler(auth_callback(show_help_menu), pattern=r"^help_menu$"),
        CallbackQueryHandler(auth_callback(help_guide), pattern=r"^help:guide$"),
        CallbackQueryHandler(auth_callback(help_credit), pattern=r"^help:credit$"),
        CallbackQueryHandler(auth_callback(help_commands), pattern=r"^help:commands$"),
        CallbackQueryHandler(auth_callback(help_tips), pattern=r"^help:tips$"),
    ]
```

- [ ] **Step 2: 验证文件无语法错误**

```bash
cd /home/ksufer/My_data/Superman/my_projects/sd_telegram_bot && uv run python -c "import handlers.workflow_menu; print('OK')"
```
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add handlers/workflow_menu.py
git commit -m "feat: add workflow-driven main menu, detail pages, and help panel"
```

---

### Task 3: 修改 handlers/settings.py — 移除 /start /help handler

**Files:**
- Modify: `handlers/settings.py`（第 516-517 行）

- [ ] **Step 1: 从 get_handlers() 中移除 CommandHandler**

在 `handlers/settings.py` 第 514-540 行的 `get_handlers()` 函数中，删除第 516-517 行的两个 CommandHandler：

```python
# 删除这两行：
# CommandHandler("start", show_main_menu, filters=_user_auth_filter()),
# CommandHandler("help", show_main_menu, filters=_user_auth_filter()),
```

修改后的 `get_handlers()`：

```python
def get_handlers() -> list:
    return [
        CallbackQueryHandler(auth_callback(show_settings), pattern="^settings_menu$|^settings_back$"),
        CallbackQueryHandler(auth_callback(show_size_menu), pattern="^set_size$"),
        CallbackQueryHandler(auth_callback(pick_size), pattern="^pick_size_"),
        CallbackQueryHandler(auth_callback(show_model_menu), pattern="^set_model$"),
        CallbackQueryHandler(auth_callback(pick_model), pattern="^pick_model_"),
        CallbackQueryHandler(auth_callback(show_sampler_menu), pattern="^set_sampler$"),
        CallbackQueryHandler(auth_callback(pick_sampler), pattern="^pick_sampler_"),
        CallbackQueryHandler(auth_callback(toggle_hires), pattern="^toggle_hires$"),
        CallbackQueryHandler(auth_callback(toggle_translate), pattern="^toggle_translate$"),
        CallbackQueryHandler(auth_callback(toggle_restore_faces), pattern="^toggle_restore_faces$"),
        CallbackQueryHandler(auth_callback(toggle_tiling), pattern="^toggle_tiling$"),
        CallbackQueryHandler(auth_callback(show_steps_menu), pattern="^set_steps$"),
        CallbackQueryHandler(auth_callback(pick_steps), pattern="^pick_steps_"),
        CallbackQueryHandler(auth_callback(show_cfg_menu), pattern="^set_cfg$"),
        CallbackQueryHandler(auth_callback(pick_cfg), pattern="^pick_cfg_"),
        CallbackQueryHandler(auth_callback(show_clip_skip_menu), pattern="^set_clip_skip$"),
        CallbackQueryHandler(auth_callback(pick_clip_skip), pattern="^pick_clip_skip_"),
        CallbackQueryHandler(auth_callback(start_seed_input), pattern="^set_seed$"),
        CallbackQueryHandler(auth_callback(close_menu), pattern="^close_menu$"),
        CallbackQueryHandler(auth_callback(reuse_prompt), pattern="^reuse_prompt_"),
        CallbackQueryHandler(auth_callback(reuse_seed), pattern="^reuse_seed_"),
        CallbackQueryHandler(auth_callback(random_seed), pattern="^random_seed$"),
    ]
```

- [ ] **Step 2: 验证导入**

```bash
cd /home/ksufer/My_data/Superman/my_projects/sd_telegram_bot && uv run python -c "from handlers.settings import get_handlers; hs = get_handlers(); print(f'{len(hs)} handlers from settings')"
```
Expected: `18 handlers from settings`（从 20 减少到 18）

- [ ] **Step 3: Commit**

```bash
git add handlers/settings.py
git commit -m "refactor: move /start and /help handlers to workflow_menu"
```

---

### Task 4: 修改 handlers/comfy_settings.py — 返回按钮指向 main_menu

**Files:**
- Modify: `handlers/comfy_settings.py`（第 79, 94, 94 行等子菜单的"返回"按钮）

- [ ] **Step 1: 修改 _comfy_settings_menu 关闭按钮为返回主菜单**

在 `_comfy_settings_menu()` 函数末尾（约第 79 行），将"关闭菜单"按钮改为"返回主菜单"：

```python
# 原代码（第 79 行）：
# keyboard.append([InlineKeyboardButton("关闭菜单", callback_data="close_menu")])

# 改为：
keyboard.append([InlineKeyboardButton("🔙 返回主菜单", callback_data="main_menu")])
```

- [ ] **Step 2: 修改 _comfy_workflow_menu 返回按钮**

在 `_comfy_workflow_menu()` 函数末尾（约第 94 行），将"返回"按钮改为指向 `main_menu`：

```python
# 原代码：
# keyboard.append([InlineKeyboardButton("返回", callback_data="comfy_settings")])

# 改为：
keyboard.append([InlineKeyboardButton("🔙 返回主菜单", callback_data="main_menu")])
```

- [ ] **Step 3: 修改所有子菜单的返回按钮**

将以下函数中的 `"返回"` 按钮 callback 从 `"comfy_settings"` 改为 `"main_menu"`：

**`_comfy_model_menu()`** — 末尾的返回按钮：
```python
# 原代码：
# keyboard.append([InlineKeyboardButton("返回", callback_data="comfy_settings")])

# 改为：
keyboard.append([InlineKeyboardButton("🔙 返回主菜单", callback_data="main_menu")])
```

**`_comfy_size_menu()`** — 末尾的返回按钮（同上改动）

**`_comfy_video_orientation_menu()`** — 末尾的返回按钮（同上改动）

**`_comfy_video_length_menu()`** — 末尾的返回按钮（同上改动）

**注意**：`start_comfy_seed_input`、`start_comfy_prompt_input` 处理完成后调用 `show_comfy_settings` 重显示设置菜单，这个逻辑不变。

- [ ] **Step 4: 验证导入**

```bash
cd /home/ksufer/My_data/Superman/my_projects/sd_telegram_bot && uv run python -c "from handlers.comfy_settings import get_handlers; hs = get_handlers(); print(f'{len(hs)} handlers from comfy_settings')"
```
Expected: `17 handlers from comfy_settings`（数量不变）

- [ ] **Step 5: Commit**

```bash
git add handlers/comfy_settings.py
git commit -m "refactor: comfy_settings back buttons now point to main_menu"
```

---

### Task 5: 修改 bot.py — 注册 workflow_menu handler

**Files:**
- Modify: `bot.py`（第 12, 60-63 行）

- [ ] **Step 1: 添加 import**

在 `bot.py` 第 15 行后添加：

```python
from handlers import workflow_menu as workflow_menu_handler
```

- [ ] **Step 2: 注册 workflow_menu handler**

在 `bot.py` 第 60 行 `app.add_handlers(settings_handler.get_handlers())` **之前**插入 workflow_menu handler（确保 `/start` 优先级正确）：

```python
app.add_handlers(workflow_menu_handler.get_handlers())
app.add_handlers(settings_handler.get_handlers())
app.add_handlers(generation_handler.get_handlers())
app.add_handlers(credits_handler.get_handlers())
app.add_handlers(comfy_settings_handler.get_handlers())
```

完整顺序：
```python
app.add_handlers(workflow_menu_handler.get_handlers())   # 新增：主菜单
app.add_handlers(settings_handler.get_handlers())
app.add_handlers(generation_handler.get_handlers())
app.add_handlers(credits_handler.get_handlers())
app.add_handlers(comfy_settings_handler.get_handlers())
```

- [ ] **Step 3: 验证 bot 启动无异常**

```bash
cd /home/ksufer/My_data/Superman/my_projects/sd_telegram_bot && timeout 5 uv run python -c "
from bot import main
import asyncio
# 只验证导入和构建，不实际运行 poll
from telegram.ext import ApplicationBuilder
from config import TELEGRAM_TOKEN, PROXY_URL
" 2>&1 || true
```
Expected: 无 import 错误

- [ ] **Step 4: Commit**

```bash
git add bot.py
git commit -m "feat: register workflow_menu handlers in bot"
```

---

### 验证

#### 手动端到端测试

1. 启动 Bot：`uv run python bot.py`
2. 私聊发送 `/start` → 确认显示 4 个工作流按钮 + 参数设置 + 帮助 + 关闭
3. 点击「🖼 文生图」→ 确认显示说明页（使用方法 + 当前设置）
4. 点击「⚡ 开始使用」→ 确认弹出 "已就绪！发送文字描述" 提示，菜单消息被删除
5. 发送文字描述 → 确认正常生成图片（走 z-image-turbo）
6. 发送 `/start` → 回到主菜单
7. 点击「🔄 图生图」→「⚡ 开始使用」→ 发送图片 → 确认正常图生图
8. 点击「✏️ 图片编辑」→ 发送图片 → 回复结果图继续编辑 → 确认多轮编辑正常
9. 点击「📖 帮助」→「📋 使用指南」→ 确认列出所有工作流
10. 点击「💰 额度查询」→ 确认显示剩余额度
11. 点击「🔑 命令列表」→ 确认显示命令
12. 点击「💡 使用技巧」→ 确认显示技巧
13. 点击「⚙️ 参数设置」→ 确认进入 ComfyUI 设置菜单
14. 在设置菜单中选择其他子菜单 → 点击「🔙 返回主菜单」→ 确认回到主菜单
15. 群组中 @bot 发送文字 → 确认正常生成（行为不变）
