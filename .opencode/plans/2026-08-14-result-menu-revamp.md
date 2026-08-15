# 生成结果菜单改造 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 精简生成结果菜单（去掉种子按钮）、把 prompt_log 改为「💾 记录」按钮按需落盘、在图片结果菜单加入「🔍 反推提示词」「🎰 灵感抽卡」入口，并让反推支持用户附带文字额外要求。

**Architecture:** 复用现有 `_gen_context` 缓存（bot_data，LRU 50）携带记录所需的全部元数据，记录时从 Telegram 消息重新下载图片字节（避免内存常驻大图）；菜单构建集中在 `ui/keyboards.py`，回调分属现有 handler 模块；反推额外要求经 `GenerationTask.settings["_rev_extra"]` 传递到 `ollama_api.reverse_prompt()`。

**Tech Stack:** python-telegram-bot 22.x、asyncio、无测试框架（AGENTS.md 明令不运行测试命令）。

## Global Constraints

- 运行验证只用 `uv run python -m py_compile <file>` 和 `uv run python -c "import ..."`；**不要**试图运行 pytest/lint/typecheck（项目无此配置）。
- **不要自动 git commit**（除非用户明确要求）。
- 注释一律中文，风格对齐现有代码；回调命名沿用 `snake_case` + `pattern` 注册约定。
- 用户确认的行为决策：
  - 结果菜单「🔍 反推提示词」= 进入等待状态（与主菜单入口行为一致，由用户自行发图）。
  - 「💾 记录」按钮图片与视频菜单**都加**；视频记录无缩略图（`image_bytes=None`，仅 txt+json）。
  - 「🔍 反推提示词」「🎰 灵感抽卡」按钮**只加图片菜单**。

---

### Task 1: 结果菜单键盘改造（ui/keyboards.py）

**Files:**
- Modify: `ui/keyboards.py`（重写两个菜单函数，新增 `_gen_action_row` 辅助）

**Interfaces:**
- Consumes: `config.COMFY_WORKFLOWS`、`config.COMFY_LORA_VARIANTS`、`config.COMFY_PROMPT_OPTIMIZE_MODES`
- Produces:
  - `_gen_action_row(context_id: str, is_video: bool) -> list[InlineKeyboardButton]`
  - `generation_menu(context_id: str) -> InlineKeyboardMarkup`（签名不变）
  - `comfy_generation_menu(context_id: str = "", settings: dict | None = None) -> InlineKeyboardMarkup`（**移除 `include_seed_buttons` 参数**）

- [ ] **Step 1: 新增 `_gen_action_row` 并重写 `generation_menu`**

`ui/keyboards.py` 中替换 `generation_menu` 函数为：

```python
def _gen_action_row(context_id: str, is_video: bool) -> list[InlineKeyboardButton]:
    """生成后菜单动作按钮行：反推/抽卡（仅图片）+ 记录（图片视频均有）。"""
    if not context_id:
        return []
    buttons = []
    if not is_video:
        buttons.append(InlineKeyboardButton("🔍 反推提示词", callback_data="rev_prompt"))
        buttons.append(InlineKeyboardButton("🎰 灵感抽卡", callback_data="gacha:menu"))
    buttons.append(InlineKeyboardButton("💾 记录", callback_data=f"log_gen_{context_id}"))
    return buttons


def generation_menu(context_id: str) -> InlineKeyboardMarkup:
    """SD WebUI 生成后菜单。"""
    return InlineKeyboardMarkup([
        _gen_action_row(context_id, is_video=False),
        [
            InlineKeyboardButton("用本图提示词",
                                 callback_data=f"reuse_prompt_{context_id}"),
            InlineKeyboardButton("参数设置", callback_data="settings_menu"),
        ],
        [
            InlineKeyboardButton("关闭菜单", callback_data="close_menu"),
        ],
    ])
```

> **行为机制说明（供执行者/验收者参考，非 bug）：** 结果菜单挂在图片/视频消息上。点「🔍 反推提示词」（`rev_prompt` 回调）或「🎰 灵感抽卡」（`gacha:menu` 回调）后，两个回调都走 `reply_menu()`（`handlers/common.py:29`）→ `edit_text` 对媒体消息抛 `BadRequest` → 命中 fallback 分支改为 `reply_text` 引用原图新发一条消息。验收项 2「原图消息不被破坏」正是该机制的预期行为。

- [ ] **Step 2: 重写 `comfy_generation_menu`（去掉 `include_seed_buttons` 与全部种子行，接入动作行）**

替换 `comfy_generation_menu` 函数整体为：

```python
def comfy_generation_menu(context_id: str = "",
                          settings: dict | None = None) -> InlineKeyboardMarkup:
    """ComfyUI 生成后菜单。

    Args:
        context_id: 「💾 记录」按钮上下文 ID（空字符串则不显示动作按钮行）。
        settings: 用户设置（用于判断 zit-pussy/krea2/默认三种分支与视频输出）。
    """
    if settings:
        wf_key = settings.get("comfy_workflow", "")
        wf_config = COMFY_WORKFLOWS.get(wf_key, {})
        is_video = wf_config.get("output_type") == "video"

        if wf_config.get("lora_node"):
            # zit-pussy: LoRA 变体 + 三级开关
            current = settings.get("comfy_lora_variant", "normal")
            lora_buttons = []
            for key, variant in COMFY_LORA_VARIANTS.items():
                prefix = "✓ " if key == current else ""
                lora_buttons.append(InlineKeyboardButton(
                    f"{prefix}{variant['label']}",
                    callback_data=f"comfy_lora_var:{key}",
                ))
            rows = [lora_buttons]
            toggle_row = build_toggle_row(settings, wf_config)
            if toggle_row:
                rows.append(toggle_row)
            action_row = _gen_action_row(context_id, is_video)
            if action_row:
                rows.append(action_row)
            rows.append([
                InlineKeyboardButton("⚙️ ComfyUI 设置", callback_data="comfy_settings"),
                InlineKeyboardButton("关闭菜单", callback_data="close_menu"),
            ])
            return InlineKeyboardMarkup(rows)

        if wf_config.get("lora_enable_node"):
            # krea2: 放大 + 优化 + 脸部精修 + LoRA 开关
            rows = []
            toggle_row = []
            if wf_config.get("upscale_switch_node"):
                upscale_on = settings.get("comfy_upscale_enabled", True)
                toggle_row.append(InlineKeyboardButton(
                    "🔍" if upscale_on else "🔍✖", callback_data="comfy_upscale_toggle_gen"))
            if wf_config.get("prompt_optimize_node"):
                mode = settings.get("comfy_prompt_optimize", "nsfw")
                if isinstance(mode, bool):
                    mode = "nsfw" if mode else "off"
                mode_cfg = COMFY_PROMPT_OPTIMIZE_MODES.get(
                    mode, COMFY_PROMPT_OPTIMIZE_MODES["nsfw"])
                toggle_row.append(InlineKeyboardButton(
                    mode_cfg["icon"], callback_data="comfy_prompt_optimize_cycle_gen"))
            if wf_config.get("facedetailer_switch_node"):
                facedetailer_on = settings.get("comfy_facedetailer_enabled", True)
                toggle_row.append(InlineKeyboardButton(
                    "👤" if facedetailer_on else "👤✖", callback_data="comfy_facedetailer_toggle_gen"))
            lora_on = settings.get("comfy_krea2_lora_enabled", False)
            toggle_row.append(InlineKeyboardButton(
                "🧬" if lora_on else "🧬✖", callback_data="comfy_krea2_lora_toggle_gen"))
            if toggle_row:
                rows.append(toggle_row)
            action_row = _gen_action_row(context_id, is_video)
            if action_row:
                rows.append(action_row)
            rows.append([
                InlineKeyboardButton("⚙️ ComfyUI 设置", callback_data="comfy_settings"),
                InlineKeyboardButton("关闭菜单", callback_data="close_menu"),
            ])
            return InlineKeyboardMarkup(rows)

    # 默认菜单（无 lora_node 的 workflow / settings is None）
    is_video = bool(settings and COMFY_WORKFLOWS.get(
        settings.get("comfy_workflow", ""), {}).get("output_type") == "video")
    rows = []
    action_row = _gen_action_row(context_id, is_video)
    if action_row:
        rows.append(action_row)
    if settings:
        wf_key = settings.get("comfy_workflow", "")
        wf_config = COMFY_WORKFLOWS.get(wf_key, {})
        toggle = build_toggle_row(settings, wf_config)
        if toggle:
            rows.append(toggle)
    rows.append([
        InlineKeyboardButton("⚙️ ComfyUI 设置", callback_data="comfy_settings"),
        InlineKeyboardButton("关闭菜单", callback_data="close_menu"),
    ])
    return InlineKeyboardMarkup(rows)
```

- [ ] **Step 3: 语法验证**

Run: `uv run python -m py_compile ui/keyboards.py`
Expected: 退出码 0，无输出。

---

### Task 2: 队列上下文扩展 + 移除自动日志（services/queue.py）

**Files:**
- Modify: `services/queue.py:19`（import）、`services/queue.py:277-291`（`_cache_gen_context`）、`services/queue.py:448-510`（`_do_process_task`）、`services/queue.py:548-587`（`_process_reverse`）

**Interfaces:**
- Consumes: `ui.keyboards.generation_menu(context_id)` / `comfy_generation_menu(context_id, settings=settings)`（Task 1 新签名）
- Produces: `_cache_gen_context(self, task, translated, actual_seed, model, wf_key, label, elapsed, is_video) -> str`；gen ctx 新字段 `model/wf_key/label/elapsed/is_video/user_id/logged`

- [ ] **Step 1: 移除 `prompt_log` 导入**

`services/queue.py:19` 改为：

```python
from services import sd_api, comfy_api, credits, ollama_api
```

- [ ] **Step 2: 扩展 `_cache_gen_context`**

替换 `services/queue.py:277-291` 为：

```python
    def _cache_gen_context(self, task: GenerationTask, translated: str,
                           actual_seed: int, model: str, wf_key: str,
                           label: str, elapsed: float, is_video: bool) -> str:
        """缓存生成上下文（供「💾 记录」按钮落盘提示词日志），返回 context_id。"""
        context_id = uuid.uuid4().hex[:8]
        if "_gen_context" not in self._app.bot_data:
            self._app.bot_data["_gen_context"] = {}
        _gen = self._app.bot_data["_gen_context"]
        _gen[context_id] = {
            "prompt": task.prompt,
            "translated": translated,
            "seed": actual_seed,
            "model": model,
            "wf_key": wf_key,
            "label": label,
            "elapsed": elapsed,
            "is_video": is_video,
            "user_id": task.user_id,
            "logged": False,
        }
        while len(_gen) > 50:
            _gen.pop(next(iter(_gen)))
        return context_id
```

- [ ] **Step 3: `_do_process_task` 中计算元数据、缓存、删除自动日志**

替换 `services/queue.py` 中从 `# 优化提示词可用时` 到 `reply_markup = comfy_generation_menu(...)` 段（原 454-472 行）为：

```python
            # 优化提示词可用时，替代 translated 用于显示和缓存
            display_prompt = optimized_prompt or translated

            # 构建结果信息和菜单
            await updater.set_stage("正在发送...")
            elapsed = time.monotonic() - start_time

            if backend == "sd":
                model = settings.get("model") or ""
                wf_key = "sd-webui"
                label = "SD WebUI"
                is_video = False
            else:
                model = settings.get("comfy_model") or ""
                wf_key = settings.get("comfy_workflow", "")
                label = wf_config.get("label", "") if wf_config else ""
                is_video = wf_config.get("output_type") == "video" if wf_config else False

            # 缓存生成上下文（供「💾 记录」按钮使用）
            context_id = self._cache_gen_context(
                task, display_prompt, actual_seed,
                model, wf_key, label, elapsed, is_video)

            if backend == "sd":
                info = _build_sd_info(settings, translated, actual_seed, elapsed)
                reply_markup = generation_menu(context_id)
            else:
                info = _build_comfy_info(task, settings, display_prompt, actual_seed,
                                         elapsed, wf_config)
                reply_markup = comfy_generation_menu(context_id, settings=settings)
                if wf_config.get("output_type") != "video":
                    raw_data = raw_data.data  # 图片：提取 bytes；视频：保留 ComfyOutput 供 _send_result 取 .filename
```

再删除原 495-510 行的整段自动日志（`# 提示词日志：完整提示词 + 缩略图按日落盘...` 的 `if sent: prompt_log.log_generation(...)` 块）。删除后，原 `# Pipeline 自动连跑` 的 `if sent:` 块保持不变。

- [ ] **Step 4: `_process_reverse` 传递额外要求**

`services/queue.py` 中 `_process_reverse` 内，`sd_tags, krea2_prompt = await ollama_api.reverse_prompt(image_bytes)` 一行（原 571 行）替换为：

```python
            extra = (task.settings.get("_rev_extra") or "").strip()
            sd_tags, krea2_prompt = await ollama_api.reverse_prompt(image_bytes, extra)
```

并把结果文本标题行（替换原 581-582 行，注意保留 583 行的「SD 标签词」小标题）替换为：

```python
            title = "🔍 反推提示词（已应用额外要求）" if extra else "🔍 反推提示词"
            info = (
                f"<b>{title}</b>\n\n"
                "<b>SD 标签词（点按复制）：</b>\n"
```

- [ ] **Step 5: 语法与导入验证**

Run:
```bash
uv run python -m py_compile services/queue.py
uv run python -c "import services.queue"
```
Expected: 退出码 0。

---

### Task 3: 「💾 记录」回调（handlers/generation.py）

**Files:**
- Modify: `handlers/generation.py:14、:16`（两处 import）、新增 `log_gen` 函数（放在 `handle_cancel` 之后）、`get_handlers()` 注册（`handlers/generation.py:1039-1056`）

**Interfaces:**
- Consumes: `services.prompt_log.log_generation(**kwargs)`（`services/prompt_log.py:64` 签名不变）、本模块 `_download_tg_photo`、`handlers.auth_callback`
- Produces: callback `log_gen`，pattern `^log_gen_`

- [ ] **Step 1: 补充 import**

`handlers/generation.py:14` 改为：

```python
from services import credits, comfy_api, prompt_log
```

`handlers/generation.py:16` 同时改为（Task 3 Step 3 注册要用到 `auth_callback`，漏改会导致 Bot 启动 NameError）：

```python
from handlers import is_authorized, _user_auth_filter, auth_callback
```

- [ ] **Step 2: 新增 `log_gen` 回调**

在 `handle_cancel` 函数之后插入：

```python
async def log_gen(update, context):
    """「💾 记录」按钮：把本次生成记录到提示词日志（仅按需，不自动落盘）。"""
    query = update.callback_query
    context_id = query.data.replace("log_gen_", "")
    gen_ctx = context.bot_data.get("_gen_context", {}).get(context_id)
    if gen_ctx is None:
        await safe_answer(query, "记录信息已过期，无法记录。", show_alert=True)
        return
    if gen_ctx.get("logged"):
        await safe_answer(query, "本次生成已记录过。", show_alert=True)
        return

    msg = query.message
    image_bytes = None
    # 防御：消息过旧等场景 msg 可能为 None，按无缩略图处理（image_bytes=None 直接落盘 txt+json）
    if msg and not gen_ctx.get("is_video"):
        if msg.photo:
            try:
                image_bytes = (await _download_tg_photo(msg.photo[-1])).getvalue()
            except Exception:
                logger.warning("记录缩略图下载失败（照片）", exc_info=True)
        elif msg.document:
            try:
                doc_file = await msg.document.get_file()
                buf = io.BytesIO()
                await doc_file.download_to_memory(buf)
                image_bytes = buf.getvalue()
            except Exception:
                logger.warning("记录缩略图下载失败（文件）", exc_info=True)

    prompt_log.log_generation(
        prompt=gen_ctx["prompt"],
        final_prompt=gen_ctx["translated"],
        seed=gen_ctx["seed"],
        model=gen_ctx["model"],
        wf_key=gen_ctx["wf_key"],
        label=gen_ctx["label"],
        source="bot",
        user_id=gen_ctx["user_id"],
        elapsed=gen_ctx["elapsed"],
        image_bytes=image_bytes,
    )
    gen_ctx["logged"] = True
    await safe_answer(query, "✅ 已记录到提示词日志。", show_alert=True)

    # 按钮文案标记为已记录（telegram 对象不可变，必须重建键盘，不能直接改 btn.text）
    try:
        markup = msg.reply_markup
        if markup:
            new_rows = [
                [
                    InlineKeyboardButton("✅ 已记录", callback_data=btn.callback_data)
                    if btn.callback_data == f"log_gen_{context_id}" else btn
                    for btn in row
                ]
                for row in markup.inline_keyboard
            ]
            await msg.edit_reply_markup(InlineKeyboardMarkup(new_rows))
    except Exception:
        pass
```

（`InlineKeyboardButton` / `InlineKeyboardMarkup` 已在 `handlers/generation.py:7` 导入，无需新增。）

- [ ] **Step 3: 注册 handler**

`get_handlers()` 的返回列表中加入一行（放在 `handle_cancel` 的 CommandHandler 之后）：

```python
        CallbackQueryHandler(auth_callback(log_gen), pattern=r"^log_gen_"),
```

- [ ] **Step 4: 验证**

Run:
```bash
uv run python -m py_compile handlers/generation.py
uv run python -c "import handlers.generation"
```
Expected: 退出码 0。

---

### Task 4: 清理种子回调（handlers/comfy_settings.py + handlers/settings.py）

**Files:**
- Modify: `handlers/comfy_settings.py:494-520`（删除两个函数）、`handlers/comfy_settings.py:764-787`（`_extract_seed_context_id` → `_extract_log_context_id` + `_update_gen_keyboard`）、`handlers/comfy_settings.py:865-866`（注册行）
- Modify: `handlers/settings.py:446-466`（删除两个函数）、`handlers/settings.py:503-504`（注册行）

**Interfaces:**
- Consumes: `ui.keyboards.comfy_generation_menu(context_id, settings=settings)`（Task 1 新签名）
- Produces: `_extract_log_context_id(query) -> str | None`

- [ ] **Step 1: comfy_settings.py 删除 `reuse_comfy_seed` / `random_comfy_seed` 两个函数**

删除 `handlers/comfy_settings.py:494-520` 的 `reuse_comfy_seed` 与 `random_comfy_seed` 函数体（连同 docstring）。

- [ ] **Step 2: comfy_settings.py 重写 `_extract_seed_context_id` 与 `_update_gen_keyboard`**

替换 `handlers/comfy_settings.py:764-787` 为：

```python
def _extract_log_context_id(query) -> str | None:
    """从当前消息键盘解析 log_gen_ 按钮携带的 context_id。"""
    markup = query.message.reply_markup
    if markup:
        for row in markup.inline_keyboard:
            for btn in row:
                data = btn.callback_data or ""
                if data.startswith("log_gen_"):
                    return data.replace("log_gen_", "")
    return None


async def _update_gen_keyboard(query, settings):
    """刷新生成后菜单键盘（各 fast handler 公用）。"""
    context_id = _extract_log_context_id(query)
    markup = comfy_generation_menu(context_id or "", settings=settings)
    try:
        await query.message.edit_reply_markup(markup)
    except Exception:
        pass
```

> **已知取舍（非 bug）：** 本次改动**之前**生成的结果消息键盘里没有 `log_gen_` 按钮，`_extract_log_context_id` 返回 None，点 fast toggle 刷新后动作行不再出现（其余功能正常）。仅影响升级前遗留的旧结果消息。

- [ ] **Step 3: comfy_settings.py 删除两条注册行**

删除 `handlers/comfy_settings.py:865-866`：

```python
        CallbackQueryHandler(auth_callback(reuse_comfy_seed), pattern=r"^comfy_reuse_seed_"),
        CallbackQueryHandler(auth_callback(random_comfy_seed), pattern=r"^comfy_random_seed$"),
```

- [ ] **Step 4: settings.py 删除 `reuse_seed` / `random_seed` 函数与注册行**

删除 `handlers/settings.py:446-466` 两个函数；删除 `handlers/settings.py:503-504`：

```python
        CallbackQueryHandler(auth_callback(reuse_seed), pattern="^reuse_seed_"),
        CallbackQueryHandler(auth_callback(random_seed), pattern="^random_seed$"),
```

保留 `reuse_prompt`（「用本图提示词」按钮仍用）。

- [ ] **Step 5: 验证**

Run:
```bash
uv run python -m py_compile handlers/comfy_settings.py handlers/settings.py
uv run python -c "import handlers.comfy_settings, handlers.settings"
```
Expected: 退出码 0。

---

### Task 5: 反推提示词支持额外要求（handlers/rev_prompt.py + services/ollama_api.py + config.py + generation.py）

**Files:**
- Modify: `handlers/rev_prompt.py`（入口文案、`handle_rev_photo`、`handle_rev_text`、import）
- Modify: `services/ollama_api.py:102-128`（`reverse_prompt` 加 `extra` 参数）
- Modify: `config.py:880`（`REV_PROMPT_SYSTEM` 插入一句）
- Modify: `handlers/generation.py:552-559`（`handle_cancel` 清理 `_rev_extra`）

**Interfaces:**
- Consumes: `handlers.generation._clean_caption`（rev_prompt.py 新增 import）
- Produces: `ollama_api.reverse_prompt(image_bytes: bytes, extra: str = "") -> tuple[str, str]`

- [ ] **Step 1: rev_prompt.py 入口文案 + 清残留状态**

`rev_prompt_menu` 中在设置 `_waiting_input` 前加一行清理：

```python
    context.user_data.pop("_rev_extra", None)
    context.user_data["_waiting_input"] = "rev_prompt"
```

并把 `text` 改为：

```python
    text = (
        "<b>🔍 反推提示词</b>\n\n"
        "请发送一张图片，我会反推出两种提示词（各可点按复制）：\n"
        "• <b>SD 标签词</b>：逗号分隔的标签形式\n"
        "• <b>Krea 2 句子版</b>：连贯句子的详细描述\n\n"
        "💡 可附带额外要求：发图时带上文字（如「写实风格」「去掉眼镜」），"
        "或先发送要求文字再发图。\n"
        "消耗 1 额度，发送 /cancel 可取消。"
    )
```

- [ ] **Step 2: rev_prompt.py `handle_rev_photo` 收集 caption 并透传**

在 import 段（`handlers/rev_prompt.py:17-23`）的 `handlers.generation` import 中追加 `_clean_caption`：

```python
from handlers.generation import (
    _check_and_charge_credit,
    _clean_caption,
    _clear_firstlast_state,
    _create_status_message,
    _download_tg_photo,
    _enqueue_and_notify,
)
```

在 `handle_rev_photo` 中，`status_id = await _create_status_message(...)` 之前插入：

```python
    # 额外要求：优先取本次发图的 caption，否则取之前单独发送的文字
    extra = _clean_caption(message, context).strip()
    if not extra and context.user_data is not None:
        extra = (context.user_data.get("_rev_extra") or "").strip()
```

并把 task settings 处：

```python
    task_settings["backend"] = "ollama"
    task_settings["_rev_image"] = image_bytes.getvalue()
```

改为：

```python
    task_settings["backend"] = "ollama"
    task_settings["_rev_image"] = image_bytes.getvalue()
    task_settings["_rev_extra"] = extra
```

成功清除等待状态处（原 113-114 行）：

```python
    if context.user_data is not None:
        context.user_data["_waiting_input"] = None
```

改为：

```python
    if context.user_data is not None:
        context.user_data["_waiting_input"] = None
        context.user_data.pop("_rev_extra", None)
```

- [ ] **Step 3: rev_prompt.py `handle_rev_text` 存储要求文字**

替换 `handle_rev_text` 整体为：

```python
async def handle_rev_text(update, context):
    """等待反推期间收到文字 → 存为额外要求，继续等待图片。"""
    text = update.message.text.strip()
    if text:
        if context.user_data is not None:
            context.user_data["_rev_extra"] = text
        await update.message.reply_text(
            f"✅ 已记录额外要求：{text[:200]}{'...' if len(text) > 200 else ''}\n"
            "请发送图片开始反推（发图时再附文字可覆盖本要求），或 /cancel 取消。"
        )
    else:
        await update.message.reply_text(
            "🔍 反推提示词等待图片：请发送一张图片（可附带文字要求），或 /cancel 取消。"
        )
```

- [ ] **Step 4: ollama_api.reverse_prompt 支持 extra**

替换 `services/ollama_api.py:102-108` 为：

```python
async def reverse_prompt(image_bytes: bytes, extra: str = "") -> tuple[str, str]:
    """反推图片提示词。返回 (sd_tags, krea2_prompt)。失败抛 OllamaError。

    extra: 用户额外要求（如「写实风格」「去掉眼镜」），非空时附加到请求中。
    """
    image_b64 = _prepare_image(image_bytes)
    user_text = "请反推这张图片。"
    if extra and extra.strip():
        user_text += f"\n\n额外要求：{extra.strip()}"
    messages = [
        {"role": "system", "content": REV_PROMPT_SYSTEM},
        {"role": "user", "content": user_text, "images": [image_b64]},
    ]
```

其余（timeout 块、解析失败修复重试、`_unload_model`）保持不变。

- [ ] **Step 5: config.py 更新 REV_PROMPT_SYSTEM**

`config.py:880` 该行末尾（"……不要遗漏会影响复现效果的关键细节。"）之后插入新段落：

```python
如果用户的请求中附带「额外要求」（如风格转换、写实化、增删元素、调整人物特征），额外要求优先于上述严格复现原则，请在两种提示词中体现这些要求；没有额外要求时才严格按图反推。
```

（即在原第 1-11 条检查点列表之前、第 880 行句子后追加。）

- [ ] **Step 6: handle_cancel 清理 `_rev_extra`**

`handlers/generation.py` `handle_cancel` 内 `if waiting or waiting_seed or has_firstlast or has_pipe:` 分支的 user_data 清理块中，`context.user_data.pop("_pipe_ready", None)` 之后加：

```python
            context.user_data.pop("_rev_extra", None)
```

- [ ] **Step 7: 验证**

Run:
```bash
uv run python -m py_compile handlers/rev_prompt.py services/ollama_api.py config.py handlers/generation.py
uv run python -c "import handlers.rev_prompt, services.ollama_api"
```
Expected: 退出码 0。

---

### Task 6: 文案与文档同步（workflow_menu.py + AGENTS.md）

**Files:**
- Modify: `handlers/workflow_menu.py:299`（help_commands 反推行）、`handlers/workflow_menu.py:313`（help_tips 种子行）
- Modify: `AGENTS.md`（pipeline 种子行 + prompt_log 模块行）

- [ ] **Step 1: workflow_menu.py 帮助文案**

`handlers/workflow_menu.py:299` 改为：

```python
        "🔍 反推提示词：主菜单或生成结果菜单按钮，上传图片反推出 SD 标签词 + Krea 2 句子版两种提示词（发图时可附文字额外要求）"
```

`handlers/workflow_menu.py:313` 改为：

```python
        "• 生成结果菜单提供「💾 记录」「🔍 反推」「🎰 抽卡」快捷按钮"
```

- [ ] **Step 2: AGENTS.md pipeline 种子行（种子按钮已删除，原文过时）**

`AGENTS.md:77` 「seed 每步独立（各自的 `_gen_context` / reuse 按钮照常）。」改为：

```
- seed 每步独立（各自的 `_gen_context` 缓存）。
```

- [ ] **Step 3: AGENTS.md prompt_log 行**

`AGENTS.md` 中 `services/prompt_log.py` 表格行改为：

```
| `services/prompt_log.py` | 提示词日志（`data/prompt_log/<日期>/` 下每记录三件套：缩略图 jpg + 完整提示词 txt + 元数据 json；Admin Web 生成成功自动记录，Bot 端改为结果菜单「💾 记录」按钮按需落盘，失败仅记日志；收藏/删除供 Admin API 用） |
```

- [ ] **Step 4: 验证**

Run:
```bash
uv run python -m py_compile handlers/workflow_menu.py
```
Expected: 退出码 0。

---

## 手工验收清单（全部任务完成后）

启动 Bot（`uv run python bot.py`），依次验证：

1. 文生图结果菜单：无「🔁 复用本次 Seed」「🎲 随机 Seed」；有「🔍 反推提示词」「🎰 灵感抽卡」「💾 记录」。
2. 点「🎰 灵感抽卡」→ 出现抽卡消息（原图消息不被破坏）。
3. 点「🔍 反推提示词」→ 进入等待状态提示；发图（带 caption「写实风格」）→ 反推结果标题显示「已应用额外要求」，内容偏写实。
4. 反推时先发文字要求再发图 → 要求生效；`/cancel` 后再进反推不残留旧要求。
5. 点「💾 记录」→ 按钮变「✅ 已记录」，`data/prompt_log/<今天>/` 出现 jpg+txt+json；再点一次提示已记录。
6. 视频工作流结果菜单：无种子按钮，有「💾 记录」（无 反推/抽卡）；点记录后日志只有 txt+json。
7. zit-pussy / krea2 工作流的 fast 开关（🔍 🎨 👤 🅿️ 等）点击后键盘刷新正常，动作行仍在。
8. SD WebUI 模式（`/mode` 切换）生成结果菜单：无「用本图种子」「🎲」，有动作行三按钮 +「用本图提示词」。
