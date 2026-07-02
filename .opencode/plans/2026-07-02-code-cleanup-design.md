# 重构计划：消除代码重复、拆分大函数、整理跨模块依赖

## 背景

项目当前共 6 个 handler 模块 + 9 个 service 模块，代码总量约 3500 行。经过全面审查，发现以下 6 项中等至严重问题需要重构。目标是不改变任何现有行为，仅做结构性优化。

---

## 第 1 项：handlers/generation.py 自动编辑流程代码重复

### 现状（问题）

`handlers/generation.py` 中 `handle_text()`（第 124-269 行）和 `handle_photo()`（第 591-636 + 698-712 + 724-756 + 757-808 行）各自实现了高度相似的多轮编辑流程。

**重复模式核心段（两处共约 160 行几乎相同）：**

```
额度检查 → 创建状态消息 → 下载 Telegram 图片 → 上传 ComfyUI → 创建任务 → 入队 → 队列状态提示
```

各分支的位置映射：

| 流程阶段 | handle_text 编辑分支 | handle_text 正常分支 | handle_photo 编辑分支 |
|----------|---------------------|---------------------|----------------------|
| 额度检查 | 156-169 行 | 299-315 行 | 698-712 行 |
| 创建状态消息 | 173-180 行 | 317-325 行 | 714-722 行 |
| 下载+上传 | 182-212 行 | N/A（纯文本） | 724-755 行 |
| 构建任务 | 214-234 行 | 327-342 行 | 757-787 行 |
| 入队 | 235-244 行 | 344-352 行 | 789-797 行 |
| 队列状态 | 246-267 行 | 358-382 行 | 799-808 行 |

`handle_text` 编辑分支和 `handle_photo` 编辑分支的**唯一**差异是图片来源：
- handle_text: `message.reply_to_message.photo[-1]`（回复消息的图片）
- handle_photo: `message.photo[-1]`（本条消息的图片）

入队 + 队列状态提示的 try/except 模式在**同一个文件**中重复出现 3 次（第 236-267、344-382、789-808 行），几乎逐行相同。

### 修复方案

在 `handlers/generation.py` 中新增以下模块级辅助函数。

**关键设计原则：辅助函数只做单一职责，不做退款。退款统一在 `handle_text()` / `handle_photo()` 主流程中处理。**

**a) `_check_and_charge_credit(user_id)` → `tuple[bool, bool, str]`**

```python
async def _check_and_charge_credit(user_id) -> tuple[bool, bool, str]:
    """额度检查+扣减。返回 (ok, credit_charged, error_msg)。
    ok=True 表示可以继续，credit_charged 表示本次已实际扣费（需被调用方用于退款判断）。"""
```

合并三处相同的额度检查代码（第 156-169、299-315、698-712 行）。返回三值元组，让调用方明确知道是否已扣费。

**b) `_create_status_message(message, text="准备中...")` → `int | None`**

```python
async def _create_status_message(message, text="准备中...") -> int | None:
    """创建状态消息，失败返回 None。"""
```

消除第 173-180、317-325、714-722 行中重复的 `retry_on_network_error` + 状态消息创建模式。

**c) `_download_tg_photo(photo)` → `io.BytesIO`**

```python
async def _download_tg_photo(photo) -> io.BytesIO:
    """下载 Telegram 图片到内存。失败抛异常。"""
```

**d) `_upload_to_comfy(image_bytes, status_fn=None)` → `str`**

```python
async def _upload_to_comfy(image_bytes, status_fn=None) -> str:
    """上传图片到 ComfyUI，返回 upload_name。失败抛异常（不退款）。"""
```

**只负责上传，不负责退款**。调用方统一处理退款：

```python
try:
    uploaded_name = await _upload_to_comfy(image_bytes, status_fn)
except Exception:
    if credit_charged:
        await credits.refund_one(user_id)
    await message.reply_text(f"上传图片失败: {e}")
    return
```

**e) `_enqueue_and_notify(task, queue, context, chat_id, status_id)` → `int | None`**

```python
async def _enqueue_and_notify(task, queue, context, chat_id, status_id) -> int | None:
    """入队 + 更新队列状态提示。失败抛异常（不退款）。返回 ahead 计数。"""
```

同样只做入队和状态更新，不退款。调用方统一退款：

```python
try:
    ahead = await _enqueue_and_notify(task, queue, context, chat.id, status_id)
except Exception:
    if credit_charged:
        await credits.refund_one(user_id)
    await message.reply_text("任务提交失败，请稍后重试。")
    return
```

**重构后的 `handle_text` 编辑分支简化为：**

```python
# 额度检查
ok, credit_charged, err = await _check_and_charge_credit(user_id)
if not ok:
    await message.reply_text(err)
    return

status_id = await _create_status_message(message, "正在上传图片...")

# 下载+上传（退款在外层）
try:
    photo = message.reply_to_message.photo[-1]
    image_bytes = await _download_tg_photo(photo)
    uploaded_name = await _upload_to_comfy(image_bytes)
except Exception as e:
    if credit_charged:
        await credits.refund_one(user_id)
    await message.reply_text(f"上传图片失败: {e}")
    return

# 构建任务（auto_edit 特殊逻辑保留在此处）
task_settings = copy.deepcopy(settings)
if auto_edit:
    task_settings["backend"] = "comfyui"
    task_settings["comfy_workflow"] = "qwen-image-edit"
    ...
task_settings["_uploaded_image"] = uploaded_name
task = GenerationTask(..., credit_charged=credit_charged)

# 入队（退款在外层）
try:
    ahead = await _enqueue_and_notify(task, queue, context, chat.id, status_id)
except Exception:
    if credit_charged:
        await credits.refund_one(user_id)
    await message.reply_text("任务提交失败，请稍后重试。")
    return
```

**重构后 `handle_photo` 编辑分支同理。**

### 不变性约束
- 所有异常处理路径不变（额度退还时机、错误提示文字）
- 退款逻辑完全保留在原 `handle_text()` / `handle_photo()` 主流程中，不藏入辅助函数
- auto_edit 模式下临时强制切换 qwen-image-edit 的逻辑不变
- 队列状态消息编辑逻辑不变
- `reply_to_message_id` 在群聊中的处理逻辑不变

### 文件影响范围
- 仅修改 `handlers/generation.py`
- 新增 5 个模块级 `_` 前缀辅助函数
- `handle_text()` 从 ~380 行缩至 ~120 行
- `handle_photo()` 从 ~200 行缩至 ~80 行

---

## 第 2 项：键盘构建逻辑重复（comfy_settings.py vs queue.py）

### 现状（问题）

生成后菜单键盘（生成图片/视频后附带的 inline keyboard）在两个地方以几乎相同的逻辑构建：

**位置 A：** `handlers/comfy_settings.py:656-709` — `_update_gen_keyboard(query, settings)`
此函数用于快速刷新生成后菜单键盘。三种分支：
- `lora_node` 存在（zit-pussy）：第 661-678 行
- `lora_enable_node` 存在（krea2）：第 679-696 行
- 默认：第 697-705 行

**位置 B：** `services/queue.py:459-527` — `_comfy_generation_menu(context_id, settings)`
此函数在生成完成后首次创建生成后菜单。三种分支：
- `lora_node` 存在（zit-pussy）：第 464-494 行
- `lora_enable_node` 存在（krea2）：第 496-516 行
- 默认：第 518-527 行

此外，`services/queue.py` 第 15 行存在反向依赖 `from handlers.settings import _generation_menu`，是唯一一处 service 层依赖 handler 层的地方，结构不干净。

**两者差异：**
- A 不含种子复用按钮（仅在 B 中首次创建时显示）
- A 不接收 `context_id`（不需要 seed 按钮的 callback_data）
- A 最后调用 `query.message.edit_reply_markup(markup)` 而非 `return`
- A 中 krea2 分支的 `_build_toggle_row` 被内联展开（而非调用 B 中已有逻辑），导致二者代码略有不同
- B 中依赖 `_generation_menu`（来自 handlers/settings），进一步导致交叉依赖

### 修复方案

**新建 `ui/keyboards.py` 模块**，集中管理所有菜单键盘构建函数：

```
ui/
└── keyboards.py          # 新建，包含：
    ├── generation_menu(context_id)          # SD 生成后菜单（原 handlers/settings.py)
    ├── comfy_generation_menu(context_id,    # ComfyUI 生成后菜单
    │      settings, include_seed_buttons)   #   （原 queue.py + comfy_settings.py）
    └── build_toggle_row(settings, wf_config)# 切换行辅助（原 comfy_settings.py）
```

`comfy_generation_menu()` 签名：

```python
def comfy_generation_menu(
    context_id: str = "",
    settings: dict | None = None,
    include_seed_buttons: bool = True,
) -> InlineKeyboardMarkup:
```

参数说明：
- `context_id` — 种子按钮的上下文 ID，`include_seed_buttons=False` 时可为空
- `include_seed_buttons=False` — 不创建种子复用行和随机种子行（供 `_update_gen_keyboard` 刷新场景使用）
- `include_seed_buttons=True`（默认）— 保持向后兼容现有 `_process_task` 中的调用

**`generation_menu()` 也移入**，消除 `queue.py → handlers/settings` 的反向依赖。

`ui/keyboards.py` 是**无副作用的键盘构建模块**：
- 不访问数据库，不调用 Bot API，不读写用户状态
- 不依赖任何 handler/ 或 services/ 模块
- 仅依赖 `telegram`（InlineKeyboardButton/Markup）和 `config`（常量），根据入参返回 `InlineKeyboardMarkup`

**依赖关系变为：**

```
ui/keyboards.py           （无副作用键盘构建模块）
    ↑         ↑
    │         │
handlers/    services/
comfy_       queue.py
settings.py
```

两边都导入 `from ui.keyboards import comfy_generation_menu`。

`comfy_settings.py` 中 `_update_gen_keyboard` 改为：

```python
from ui.keyboards import comfy_generation_menu

async def _update_gen_keyboard(query, settings):
    markup = comfy_generation_menu(context_id="", settings=settings, include_seed_buttons=False)
    try:
        await query.message.edit_reply_markup(markup)
    except Exception:
        pass
```

`comfy_settings.py` 中原有的 `_build_toggle_row()`（第 641-653 行）删除，因为新模块中已包含。

`queue.py` 中原来的 `_comfy_generation_menu` 函数删除，改为导入。

### 不变性约束
- 所有三种分支的键盘布局不变
- 按钮 callback_data 值不变
- `include_seed_buttons` 默认 `True`，所有现有调用点无需修改
- SD 生成后菜单（`generation_menu`）同样的按钮和行为
- krea2 分支的脸部开关逻辑统一切换

### 文件影响范围
- **新建** `ui/` 目录 + `ui/keyboards.py`（约 80 行）
- 修改 `services/queue.py`：删除 `_comfy_generation_menu`，改为从 `ui.keyboards` 导入；删除 `from handlers.settings import _generation_menu`
- 修改 `handlers/comfy_settings.py`：删除 `_update_gen_keyboard` 和 `_build_toggle_row`，改为从 `ui.keyboards` 导入
- 修改 `handlers/settings.py`：`_generation_menu` 移入 `ui.keyboards`，此处改为导入

---

## 第 3 项：三个 handler 中工具函数重复定义

### 现状（问题）

以下 3 个函数在以下位置各自独立定义，共 9 处：

| 函数 | workflow_menu.py | settings.py | comfy_settings.py |
|------|-----------------|-------------|-------------------|
| `_safe_answer()` | 29-34 行 | 229-234 行 | 193-197 行 |
| `_reply_menu()` | 37-43 行 | 221-226 行 | 200-204 行 |
| `_get_user_id()` | 54 行 | 247-248 行 | 207-208 行 |

**`_reply_menu()` 的 bug（comfy_settings.py 第 200-204 行）：**
```python
# comfy_settings.py 的错误版本：
async def _reply_menu(query, text: str, markup):
    try:
        await query.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    except Exception:              # ← 应只捕获 BadRequest
        await query.message.reply_text(text, reply_markup=markup, parse_mode="HTML")
        # ← 缺少 _safe_answer(query) 关闭 loading 状态

# workflow_menu.py / settings.py 的正确版本：
async def _reply_menu(query, text: str, markup):
    try:
        await query.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    except BadRequest:             # ← 仅捕获消息未修改的异常
        await _safe_answer(query)  # ← 关闭回调 loading 状态
        await query.message.reply_text(text, reply_markup=markup, parse_mode="HTML")
```

差异影响：comfy_settings.py 版本会因为 catch-all Exception 导致网络错误被静默吞掉（不报错但也不重试），且回调永远停留 loading 图标。

### 修复方案

**新建 `handlers/common.py`**（不放 `handlers/__init__.py`，避免循环导入和隐式导入问题）。

函数改为公开（去掉下划线前缀，既然作为公共模块导出就不再假装私有）：

```python
# handlers/common.py

from telegram.error import BadRequest

def get_user_id(update) -> int:
    return update.effective_user.id

async def safe_answer(query, text: str | None = None, show_alert: bool = False):
    try:
        await query.answer(text, show_alert=show_alert)
    except Exception:
        pass

async def reply_menu(query, text: str, markup):
    try:
        await query.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    except BadRequest:
        await safe_answer(query)
        await query.message.reply_text(text, reply_markup=markup, parse_mode="HTML")
```

三个 handler 文件中：
1. 删除各自的 `_safe_answer`、`_reply_menu`、`_get_user_id` 定义
2. 在文件顶部新增导入：
   ```python
   from handlers.common import safe_answer, reply_menu, get_user_id
   ```
3. 将各自内部调用 `_safe_answer` → `safe_answer`、`_reply_menu` → `reply_menu`、`_get_user_id` → `get_user_id`（仅改名，行为不变）

### 不变性约束
- `safe_answer` 和 `get_user_id`：所有版本一致，无行为变化
- `reply_menu`：统一为 `workflow_menu.py`/`settings.py` 的正确版本（修复了 comfy_settings 的 bug）
- comfy_settings.py 中所有调用 `_reply_menu` 的地方行为变为正确的 BadRequest 处理（这是修复，非破坏性变更）

### 文件影响范围
- **新建** `handlers/common.py`（约 20 行）
- 修改 `handlers/workflow_menu.py`：删除第 29-54 行中 3 个函数 + 所有调用点改名
- 修改 `handlers/settings.py`：删除第 221-248 行中 3 个函数 + 所有调用点改名
- 修改 `handlers/comfy_settings.py`：删除第 193-208 行中 3 个函数 + 所有调用点改名

---

## 第 4 项：queue.py `_process_task()` 职责过重

### 现状（问题）

`services/queue.py:146-341` — `_process_task()` 方法约 200 行，是项目中最大的单个函数。所有逻辑平铺，没有子函数提取。

其内部流程：
1. **翻译**（第 158-170 行）：判断后端→判断翻译开关→调用 `translate()`
2. **SD 模型切换**（第 173-178 行）：仅 SD 模式
3. **生成**（第 181-221 行）：SD/ComfyUI 两条大分支，Comfy 分支内含脸部提示词提取（第 208-215 行）
4. **生成上下文缓存**（第 228-239 行）
5. **结果信息构建**（第 245-257 行）
6. **发送结果**（第 261-329 行）：图片/视频分支，含 3 次重试、**内部退款**（网络错误）、fallback
7. **删除状态消息**（第 332-339 行）

### 退款边界分析（重要）

当前原代码有两层退款：
- **外层**（第 223-226 行）：`try...except` 包裹翻译+生成阶段，失败退款
- **内层**（`_send_result` 对应区域，第 296-328 行）：**仅网络错误**时退款（非网络错误 re-raise）

拆分时必须保留这一逻辑：**`_send_result` 内部针对网络错误的退款逻辑完整保留，外层不对发送阶段重复退款。**

### 修复方案

将 `_process_task` 的方法体拆分为以下 `GenerationQueue` 的私有方法：

**a) `_translate_prompt(task, updater)` → `str`**
从第 158-170 行提取。处理翻译开关判断和实际翻译调用。

**b) `_switch_sd_model(settings, updater)` → `None`**
从第 173-178 行提取。仅 SD 模式切模型，失败静默继续。

**c) `_generate(task, translated, updater)` → `tuple[bytes | ComfyOutput, int, dict]`**
从第 181-221 行提取。返回 `(raw_data, actual_seed, wf_config)`。包含脸部提示词提取。

**d) `_send_result(task, raw_data, info, reply_markup, wf_config, updater)` → `None`**
从第 241-329 行完整提取。**内部完整保留**：图片/视频分支、重试、视频 fallback、**网络错误退款**、非网络错误 re-raise。外层 `_process_task` 不在此阶段新增退款。

**e) `_cache_gen_context(task, translated, seed)` → `str`**
从第 228-239 行提取。返回 `context_id`。

注意：**不新增 `_build_result_info`**。现有的 `_build_sd_info` 和 `_build_comfy_info` 已经是清晰的模块级函数，保留在 `_process_task` 主流程中直接调用。

重构后 `_process_task()` 变为约 40 行：

```python
async def _process_task(self, task: GenerationTask):
    settings = task.settings
    backend = settings.get("backend", "sd")
    start_time = time.monotonic()
    updater = ThrottledProgressUpdater(self._app, task.chat_id, task.status_message_id)

    # 翻译 + 模型切换 + 生成（此阶段失败退款）
    try:
        translated = await self._translate_prompt(task, updater)
        if backend == "sd":
            await self._switch_sd_model(settings, updater)
        raw_data, actual_seed, wf_config = await self._generate(task, translated, updater)
    except Exception:
        if task.credit_charged:
            await credits.refund_one(task.user_id)
        raise

    # 缓存生成上下文
    context_id = self._cache_gen_context(task, translated, actual_seed)

    # 结果信息 + 键盘
    elapsed = time.monotonic() - start_time
    if backend == "sd":
        info = _build_sd_info(settings, translated, actual_seed, elapsed)
        reply_markup = generation_menu(context_id)
    else:
        info = _build_comfy_info(task, settings, translated, actual_seed, elapsed)
        reply_markup = comfy_generation_menu(context_id, settings=settings)

    if task.credit_charged:
        remaining = await credits.get_remaining(task.user_id)
        info += f"\n<b>剩余额度:</b> {remaining}"

    # 发送结果（内部处理网络错误退款，不在此处再退款）
    await self._send_result(task, raw_data, info, reply_markup, wf_config, updater)

    # 清理状态消息
    if task.status_message_id is not None:
        try:
            await self._app.bot.delete_message(
                chat_id=task.chat_id, message_id=task.status_message_id
            )
        except Exception:
            logger.debug("删除状态消息失败", exc_info=True)

    logger.info("用户 %s 生成完成 | 耗时 %.1fs", task.user_id, elapsed)
```

### 不变性约束
- 翻译失败静默降级逻辑不变
- SD 模型切换失败静默继续的逻辑不变
- 脸部提示词提取失败静默降级逻辑不变
- **生成阶段失败退款**保留在外层 try/except
- **`_send_result` 内部网络错误退款逻辑完整保留**，外层不新增发送阶段退款（避免重复退）
- 视频发送 fallback 到 send_document 的逻辑不变
- `supports_streaming=True` 暂时保留（不影响行为），标记 TODO 后续评估移除

### 文件影响范围
- 仅修改 `services/queue.py`
- 在 `GenerationQueue` 类中新增 5 个私有方法
- `_process_task` 方法体从 ~200 行缩至 ~40 行
- 模块级函数 `_build_sd_info` / `_build_comfy_info` 不变
- `_comfy_generation_menu` 在第 2 项中移至 `ui/keyboards.py`，此处改为从 `ui.keyboards` 导入

---

## 第 5 项：comfy_api.py `_build_payload()` 过长

### 现状（问题）

`services/comfy_api.py` — `_build_payload()` 函数约 150 行，在一个函数内通过 `if wf_config.get(...)` 层层嵌套注入工作流参数。涉及的操作：

1. prompt 节点注入（`prompt_node` / `prompt_node_2`）
2. seed 注入（`seed_node` / 多个 seed 节点）
3. 模型节点注入（`model_selectable`）
4. 图片尺寸注入（`width_node` / `height_node`）
5. 视频尺寸注入（`compute_video_dimensions` + 帧数）
6. 上传图片路径注入（`load_image_nodes`，单张/多张/视频首帧）
7. LoRA 变体配置（`lora_node`）
8. 级联开关 reroute（Upscale/PussyDetailer/FaceDetailer，3 个独立的 switch+reroute 操作）
9. 脸部提示词注入（`face_detailer_prompt_node`）
10. SD Upscale 正/负面提示词（NSFW 关键词注入到 upscale_prompt_node）

### 修复方案

拆分为以下模块级子函数，**统一返回 `None`（原地修改 payload，不返回新对象）**：

| 新函数 | 签名 | 职责 |
|--------|------|------|
| `_apply_prompt_and_seed` | `(payload, wf_config, prompt, seed) → None` | 注入 prompt + seed 节点 |
| `_apply_model` | `(payload, wf_config, settings) → None` | `model_node` 注入 |
| `_apply_dimensions` | `(payload, wf_config, settings) → None` | 图片/视频尺寸+帧数注入 |
| `_apply_images` | `(payload, wf_config, uploaded_image, uploaded_images) → None` | `load_image_nodes` 注入 |
| `_apply_lora` | `(payload, wf_config, settings) → None` | `lora_node` + 变体配置 |
| `_apply_switches` | `(payload, wf_config, settings) → None` | 三级开关 reroute |
| `_apply_face_prompt` | `(payload, wf_config, face_prompt) → None` | 脸部提示词注入 |
| `_apply_upscale_prompts` | `(payload, wf_config, prompt) → None` | SD Upscale 正/负面提示词 w/ NSFW 注入 |

**调用顺序必须与原始 `_build_payload` 中的执行顺序严格一致**。重构时逐行对照原函数，确保图片注入→LoRA→开关→upscale prompt→face prompt 的先后顺序不改变。

重构后的 `_build_payload` 函数主体（约 30 行）：
```python
def _build_payload(settings, prompt, seed, uploaded_image=None, uploaded_images=None, face_prompt=None):
    wf_key = settings.get("comfy_workflow", COMFY_DEFAULT_WORKFLOW)
    wf_config = COMFY_WORKFLOWS.get(wf_key, COMFY_WORKFLOWS[COMFY_DEFAULT_WORKFLOW])
    payload = _load_workflow(wf_key)

    _apply_prompt_and_seed(payload, wf_config, prompt, seed)
    _apply_model(payload, wf_config, settings)
    _apply_dimensions(payload, wf_config, settings)
    _apply_images(payload, wf_config, uploaded_image, uploaded_images)
    _apply_switches(payload, wf_config, settings)
    _apply_lora(payload, wf_config, settings)
    _apply_face_prompt(payload, wf_config, face_prompt)
    _apply_upscale_prompts(payload, wf_config, prompt)

    return payload
```

### 本地快照验证（推荐不提交）

重构此文件时建议临时写一段验证脚本，对 3-5 个不同 workflow 构造相同参数，分别调用旧 `_build_payload` 和新 `_build_payload`，断言输出 JSON 完全一致：

```python
# 临时验证脚本（不提交）
for wf_key in ["z-image-turbo", "zit-pussy", "qwen-image-edit"]:
    settings = {"comfy_workflow": wf_key, ...}
    old_result = old_build_payload(settings, "test prompt", 42)
    new_result = new_build_payload(settings, "test prompt", 42)
    assert json.dumps(old_result, sort_keys=True) == json.dumps(new_result, sort_keys=True)
```

### 不变性约束
- 所有节点 ID 引用不变（`prompt_node` / `width_node` / `load_image_nodes` 等字段名和取值逻辑不变）
- 各步骤**执行顺序严格不变**
- 级联开关 reroute 逻辑（直连上游链→跳过节选→直连下游链）不变
- NSFW 关键词检测和注入逻辑不变
- `_load_workflow` 返回的 deepcopy 行为不变
- 未配置可选节点时静默跳过行为不变

### 文件影响范围
- 仅修改 `services/comfy_api.py`
- 新增约 8 个模块级 `_apply_*` 函数（各 10-30 行）
- `_build_payload` 从 ~150 行缩至 ~30 行

---

## 第 6 项：comfy_settings.py `_comfy_settings_menu()` 拆分

### 现状（问题）

`handlers/comfy_settings.py:20-139` — `_comfy_settings_menu()` 约 120 行。问题在于：

1. 使用 `keyboard.insert(-2)` 和 `keyboard.insert(1)` 动态插入，插入顺序改变会破坏最终布局
2. 文本拼接和键盘构建混在一起，无法单独理解某一行的来源
3. 视频三段设置（比例/画质/长度）各有独立的 `insert(1)` / `insert(2)` 操作

### 执行前准备：录制当前布局快照

在执行重构前，先在每种 workflow 组合下记录当前菜单的按钮行顺序（用于重构后逐行比对）：

| Workflow 场景 | 需记录 |
|--------------|--------|
| 普通图片 workflow（z-image-turbo） | 按钮行顺序 |
| 视频 workflow（image-to-video） | 按钮行顺序 |
| zit-pussy（带 lora_node + 三级开关） | 按钮行顺序 |
| krea2（带 lora_enable_node + facedetailer） | 按钮行顺序 |
| 带 FaceDetailer 的 workflow | 按钮行顺序 |
| 带 Upscale 的 workflow | 按钮行顺序 |

每种场景记录按钮 callback_data 数组，重构后逐项比对。

### 修复方案

在不改变最终输出顺序的前提下：

**a) 信息文本构建和键盘构建分离**

将文字行和键盘行分别收集到独立列表：

```python
def _comfy_settings_menu(settings: dict) -> tuple[str, InlineKeyboardMarkup]:
    wf_key = settings.get("comfy_workflow", COMFY_DEFAULT_WORKFLOW)
    wf_config = COMFY_WORKFLOWS.get(wf_key, COMFY_WORKFLOWS[COMFY_DEFAULT_WORKFLOW])
    is_video = wf_config.get("output_type") == "video"
    model_selectable = wf_config.get("model_selectable", True)

    # 信息文本
    info_lines = [f"<b>🎨 ComfyUI 设置</b>", f"Workflow: {wf_config['label']}"]
    if model_selectable:
        info_lines.append(f"模型: <code>{settings.get('comfy_model', wf_config.get('default_model', '?'))}</code>")
    info_lines.append(f"种子: {'随机' if settings.get('comfy_seed', -1) == -1 else str(settings['comfy_seed'])}")
    translate = settings.get("comfy_translate", False)
    info_lines.append(f"翻译: {'ON' if translate else 'OFF'}")
    
    comfy_prompt = settings.get("comfy_prompt", "")
    prompt_preview = comfy_prompt[:30] + "..." if comfy_prompt else "（使用默认）"
    info_lines.append(f"Prompt: {prompt_preview}")

    # 键盘行（按最终顺序收集）
    translate = settings.get("comfy_translate", False)
    keyboard = [
        [InlineKeyboardButton("切换 Workflow", callback_data="comfy_workflow")],
    ]
    if model_selectable:
        keyboard.append([InlineKeyboardButton("切换模型", callback_data="comfy_model")])
    keyboard.append([
        InlineKeyboardButton("种子输入", callback_data="comfy_seed"),
        InlineKeyboardButton(f"翻译 · {'ON' if translate else 'OFF'}", callback_data="comfy_translate"),
    ])

    # 条件行 — 不再用 insert
    _add_dimension_rows(keyboard, wf_config, settings, info_lines)     # 尺寸或视频参数
    _add_lora_rows(keyboard, wf_config, settings, info_lines)         # LoRA 变体
    _add_switch_rows(keyboard, wf_config, settings, info_lines)       # 三级开关
    _add_krea2_rows(keyboard, wf_config, settings, info_lines)        # krea2 LoRA
    _add_face_prompt_rows(keyboard, wf_config, settings, info_lines)  # 脸部提示词

    # 底部固定行
    keyboard.append([InlineKeyboardButton("自定义 Prompt", callback_data="comfy_prompt")])
    if comfy_prompt:
        keyboard.append([InlineKeyboardButton("🗑 清除 Prompt", callback_data="clear_comfy_prompt")])
    keyboard.append([InlineKeyboardButton("🔙 返回主菜单", callback_data="main_menu")])

    return "\n".join(info_lines), InlineKeyboardMarkup(keyboard)
```

辅助函数（`_add_dimension_rows` 等）仅在条件满足时才 append 到 keyboard 和 info_lines。

**b) 不改变最终菜单布局**

由于各辅助函数只 `keyboard.append()` 不 `insert()`，最终顺序完全由 `_comfy_settings_menu` 中 append 调用的顺序决定，一眼可知。

### 不变性约束
- 最终键盘按钮顺序与重构前完全一致
- 所有 callback_data 值不变
- 所有文字内容不变（包含 emoji 前缀 `🔍`/`🅿️`/`👤` 和 ON/OFF 标签）
- 条件显示的按钮仅在相同条件下出现

### 文件影响范围
- 仅修改 `handlers/comfy_settings.py`
- 新增 5 个 `_add_*` 模块级辅助函数（各约 10-20 行）
- `_comfy_settings_menu` 从 120 行缩至约 50 行

---

## 执行顺序和依赖关系

重构顺序按依赖排列，推荐分阶段进行。第 2 项放在最后，因为它涉及跨模块依赖重组（新建 `ui/keyboards.py`），等 queue 和 comfy_settings 各自稳定后再统一键盘模块。

```
阶段 1: 第 5 项（build_payload 拆分）
        独立性最高，无任何依赖，最适合先做
        可以与阶段 2 并行

阶段 2: 第 3 项（handler 公共函数提取 → handlers/common.py）
        无强依赖，后续所有 handler 相关项可受益于统一工具函数
        可以与阶段 1 并行

阶段 3: 第 6 项（comfy_settings 菜单拆分）
        依赖阶段 2（使用 common.py 中的统一工具函数）
        不依赖第 4 项

阶段 4: 第 1 项（generation 去重）
        与阶段 2 无强依赖（如需统一 get_user_id 可在阶段 2 后执行）
        涉及扣费和入队，独立做、独立测

阶段 5: 第 4 项（_process_task 拆分）
        依赖阶段 2（common.py 工具函数可选）
        重点测异常退款和发送 fallback

阶段 6: 第 2 项（新建 ui/keyboards.py，键盘逻辑合并）
        依赖阶段 3 和阶段 5（comfy_settings 和 queue 各自稳定后统一）
        顺带消除 queue.py → handlers/settings 的反向依赖
```

每项应独立 commit，便于回滚。如果出问题，最多回滚单次重构。

---

## 每 Commit 最低验证项

代码重构最怕"错在第一个 commit，最后才发现"。建议每阶段完成后立即验收：

| 阶段 | 最低验证 |
|------|---------|
| 阶段 1 第 5 项 | 跑 `_build_payload` 快照对比脚本（3-5 个 workflow），确认新旧输出一致 |
| 阶段 2 第 3 项 | 点击主菜单/workflow/settings/comfy_settings 所有**一级按钮**，确认回调正常无异常 |
| 阶段 3 第 6 项 | 对比重构前后 ComfyUI 设置菜单 callback_data **行顺序**（对齐录制快照） |
| 阶段 4 第 1 项 | 验证三条生成路径：文生图、回复图片编辑、本图加描述 |
| 阶段 5 第 4 项 | 验证三场景：正常生成成功、生成阶段失败退款、发送 fallback 退款 |
| 阶段 6 第 2 项 | 生成后菜单首次显示 + 点击开关按钮刷新键盘 |

其中阶段 1 和阶段 4/5 建议加临时日志观察退款逻辑（确认后可删除或改为 debug 级别）：

```python
logger.debug("credit_check user=%s ok=%s charged=%s", user_id, ok, credit_charged)
logger.debug("refund user=%s reason=%s", user_id, reason)
```

---

## 验证方式

项目没有自动化测试，重构后需手动验证清单：

| # | 验证项 | 操作 |
|---|--------|------|
| 1 | 主菜单正常 | 发送 `/start`，确认 9 个工作流按钮正确显示 |
| 2 | 工作流切换 | 点击任意工作流 → 说明页 → 开始使用，确认后端和 workflow 正确切换 |
| 3 | ComfyUI 设置菜单 | 进入设置页，确认所有开关/菜单/按钮布局正确 |
| 4 | SD 设置菜单 | 切换到 SD 后端，进入设置，确认布局正确 |
| 5 | 文生图生成 | 发送提示词，等待结果，确认图片+键盘正确 |
| 6 | 图生视频 | 切换到 图生视频，发送图片，确认视频输出正确 |
| 7 | 首尾帧视频 | 发送两张首尾帧图片 + 描述，确认流程完整 |
| 8 | 多轮编辑 | 回复 bot 图片 + 文字，确认自动切换到 qwen-image-edit |
| 9 | 生成后菜单刷新 | 在生成结果上点击开关按钮（🔍/🅿️/👤），确认键盘刷新不刷消息 |
| 10 | LoRA 变体切换 | 在 zit-pussy 生成后菜单切换 LoRA 变体，确认高亮正确更新 |
| 11 | 额度系统 | `/credit` 查看额度，消耗 1 次确认 -1，管理员 `/credit add` 测试 |
| 12 | 翻译 | 切换翻译 ON，发送中文提示词，确认翻译生效 |
| 13 | 日志 | `tail -f logs/bot.log`，确认无新增异常日志 |
| 14 | 异常处理 | 故意断开 ComfyUI，发送生成请求，确认错误提示和额度退还 |
| 15 | 入队失败退款 | 临时让队列入队抛异常，确认额度退还（不会被多退或少退） |
| 16 | 图片上传失败退款 | 临时断开 ComfyUI 上传接口，确认额度退还 |
| 17 | 发送失败退款 | 临时模拟 Telegram 发送失败，确认行为和旧版一致（网络错误退、非网络错误抛） |
| 18 | 管理员与普通用户额度差异 | 管理员生成不扣费，普通用户正常扣费 |
| 19 | 回调 loading 状态 | 在 ComfyUI 设置中快速点击按钮（如翻译开关、LoRA 切换），确认 loading 图标正确消失，不会一直转圈 |

---

## 附：轻度问题（单独 commit，不混入结构重构）

以下改动极小且独立，应单独 commit，不与上述任何重构项混合。尤其 C 项涉及提示词权重变化，**不纳入本轮重构**。

| # | 问题 | 文件:行号 | 修改 | commit 策略 |
|---|------|-----------|------|------------|
| A | 拼写 `blum effect` | `config.py` DEFAULT_PROMPT_PREFIX | `blum` → `bloom` | 单独 commit |
| B | `main.py` 占位文件 | `main.py` | 删除整个文件（按 AGENTS.md 指示忽略） | 单独 commit |
| - | ~~C `DEFAULT_NEGATIVE_PROMPT` 重复词~~ | ~~`config.py`~~ | ~~不纳入本次重构~~ — 可能改变权重，需独立评估后单独修改 |
| D | `supports_streaming=True` 无效 | `queue.py:274` | 移除参数（BytesIO 不支持 streaming） | 单独 commit，需验证视频发送不受影响 |
