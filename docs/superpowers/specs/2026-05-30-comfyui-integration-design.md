# ComfyUI 集成设计文档 v2.1

**日期**: 2026-05-30
**目标**: 在现有 SD Telegram Bot 中增加 ComfyUI 后端支持，与现有 SD WebUI Forge 后端并存

## 上下文

用户已有基于 SD WebUI Forge 的出图 Bot（10.126.126.1:7860），现在需要接入第二台机器上的 ComfyUI（10.126.126.4:8188），使用固定的 z-image-turbo workflow。两个后端并存，用户通过 `/mode` 命令切换。

## 设计决策

- **集成到现有 Bot**：复用鉴权、额度、翻译、存储、部署，一套代码维护
- **极简交互**：ComfyUI 模式下只需输入 prompt，workflow 参数用预设值
- **固定 workflow**：启动时加载 `data/zit-api.json` 到内存缓存，每次 deepcopy 后替换 prompt 和 seed
- **翻译复用现有策略**：与 SD 共用同一套翻译开关（`settings["translate"]`），不因切到 ComfyUI 就强制翻译
- **额度扣退统一在队列层处理**：comfy_api 遇错直接抛异常，不返回 None，由 `_process_task` 统一 catch 退额度
- **seed 自动生成**：`settings["seed"] == -1` 时随机生成，范围 `0 ~ 2^63-1`，适配 ComfyUI KSampler 的 64 位种子

## 架构概览

```
用户发 prompt → handle_text()
  → 检查 settings["backend"] (默认 "sd")
  → 翻译 (复用 translator.py，尊重 translate 开关)
  → 分叉:
      sd 模式:      sd_api.txt2img(payload, progress_callback)
      comfyui 模式: comfy_api.generate(prompt, seed)
  → 返回 (image_bytes, seed) → send_photo()
```

---

## 1. config.py — 新增配置常量

```python
# ComfyUI
COMFY_API_BASE = os.getenv("COMFY_API_BASE", "http://10.126.126.4:8188")
COMFY_WORKFLOW_PATH = os.getenv("COMFY_WORKFLOW_PATH", "data/zit-api.json")
COMFY_POLL_INTERVAL = 2       # 轮询间隔（秒）
COMFY_TIMEOUT = 300           # 生成超时（秒），含模型加载时间

# Workflow 节点 ID 和字段路径（基于 data/zit-api.json）
COMFY_PROMPT_NODE_ID = "83:27"     # CLIPTextEncode
COMFY_PROMPT_INPUT_KEY = "text"
COMFY_SEED_NODE_ID = "83:3"        # KSampler
COMFY_SEED_INPUT_KEY = "seed"

# 默认设置新增 backend 字段（直接写在 DEFAULT_USER_SETTINGS 定义内）
# "backend": "sd",
```

节点 ID 从实际 workflow 确认：

| 节点 ID | 类型 | 用途 |
|---------|------|------|
| `83:27` | CLIPTextEncode | 正向提示词，字段 `inputs.text` |
| `83:3` | KSampler | 采样器，字段 `inputs.seed`（steps/cfg/sampler 用预设） |
| `83:33` | ConditioningZeroOut | 负向条件（零化，无需设置文本） |
| `60` | SaveImage | 输出节点，图片出现在 `outputs["60"].images` |

---

## 2. 新增 services/comfy_api.py

### 对外接口

```python
async def generate(prompt: str, seed: int) -> tuple[bytes, int]:
    """提交 prompt 到 ComfyUI，轮询等待完成，返回 (image_bytes, seed)。

    失败时抛出异常，不返回 None。
    """

def validate_workflow() -> None:
    """校验 workflow 文件存在且节点结构正确。失败时抛 ComfyWorkflowError。"""
```

队列层只调用这两个函数，不感知内部细节。

### 完整实现

```python
import asyncio
import copy
import json
import logging
import time
from pathlib import Path

import httpx

from config import (
    COMFY_API_BASE, COMFY_WORKFLOW_PATH,
    COMFY_PROMPT_NODE_ID, COMFY_PROMPT_INPUT_KEY,
    COMFY_SEED_NODE_ID, COMFY_SEED_INPUT_KEY,
    COMFY_POLL_INTERVAL, COMFY_TIMEOUT,
)

logger = logging.getLogger(__name__)

_workflow_cache: dict | None = None


# ── 自定义异常 ───────────────────────────────────────────

class ComfyApiError(Exception):
    """ComfyUI API 错误（提交失败、无 prompt_id、生成报错）。"""

class ComfyWorkflowError(Exception):
    """Workflow 文件缺失、无法解析或节点结构不正确。"""

class ComfyTimeoutError(Exception):
    """ComfyUI 生成超时。"""


# ── 内部工具 ─────────────────────────────────────────────

def _set_node_input(workflow: dict, node_id: str, input_key: str, value):
    """安全设置 workflow 节点输入字段，KeyError 时给出明确信息。"""
    try:
        workflow[node_id]["inputs"][input_key] = value
    except KeyError as e:
        raise ComfyWorkflowError(
            f"Workflow 节点或字段不存在: node_id={node_id}, input_key={input_key}"
        ) from e


def _load_workflow() -> dict:
    """启动时加载一次到缓存，后续 deepcopy 使用。"""
    global _workflow_cache
    if _workflow_cache is not None:
        return copy.deepcopy(_workflow_cache)
    path = Path(COMFY_WORKFLOW_PATH)
    if not path.exists():
        raise ComfyWorkflowError(f"Workflow 文件不存在: {path}")
    with open(path, "r", encoding="utf-8") as f:
        _workflow_cache = json.load(f)
    return copy.deepcopy(_workflow_cache)


def _build_payload(workflow: dict, prompt: str, seed: int) -> dict:
    """替换 workflow 中的 prompt 文本和 seed 值。"""
    _set_node_input(workflow, COMFY_PROMPT_NODE_ID, COMFY_PROMPT_INPUT_KEY, prompt)
    _set_node_input(workflow, COMFY_SEED_NODE_ID, COMFY_SEED_INPUT_KEY, seed)
    return workflow


def validate_workflow() -> None:
    """校验 workflow 文件存在且关键节点结构正确。"""
    workflow = _load_workflow()
    _set_node_input(workflow, COMFY_PROMPT_NODE_ID, COMFY_PROMPT_INPUT_KEY, "test")
    _set_node_input(workflow, COMFY_SEED_NODE_ID, COMFY_SEED_INPUT_KEY, 1)


# ── API 调用 ──────────────────────────────────────────────

async def _submit_prompt(client: httpx.AsyncClient, workflow: dict) -> str:
    """POST /prompt，返回 prompt_id。"""
    resp = await client.post("/prompt", json={"prompt": workflow})
    resp.raise_for_status()
    data = resp.json()
    prompt_id = data.get("prompt_id")
    if not prompt_id:
        raise ComfyApiError(f"ComfyUI 未返回 prompt_id: {data}")
    return prompt_id


async def _poll_result(client: httpx.AsyncClient, prompt_id: str) -> bytes:
    """轮询 /history/{prompt_id} 直到完成，返回图片 bytes。"""
    deadline = time.monotonic() + COMFY_TIMEOUT
    while time.monotonic() < deadline:
        resp = await client.get(f"/history/{prompt_id}")
        resp.raise_for_status()
        history = resp.json()

        item = history.get(prompt_id)
        if not item:
            await asyncio.sleep(COMFY_POLL_INTERVAL)
            continue

        # 检查执行是否出错
        status = item.get("status", {})
        if status.get("status_str") == "error":
            raise ComfyApiError(f"ComfyUI 生成失败: {status}")

        # 遍历所有 output node，找第一张图片
        outputs = item.get("outputs", {})
        for _node_id, node_output in outputs.items():
            images = node_output.get("images")
            if images and len(images) > 0:
                img_info = images[0]
                return await _download_image(
                    client,
                    filename=img_info["filename"],
                    subfolder=img_info.get("subfolder", ""),
                    image_type=img_info.get("type", "output"),
                )

        # 有 history 但没有 outputs — 可能还在生成
        await asyncio.sleep(COMFY_POLL_INTERVAL)

    raise ComfyTimeoutError(f"ComfyUI 生成超时 ({COMFY_TIMEOUT}s)")


async def _download_image(
    client: httpx.AsyncClient,
    filename: str,
    subfolder: str = "",
    image_type: str = "output",
) -> bytes:
    """GET /view 下载图片。"""
    resp = await client.get(
        "/view",
        params={"filename": filename, "subfolder": subfolder, "type": image_type},
    )
    resp.raise_for_status()
    return resp.content


# ── 对外入口 ──────────────────────────────────────────────

async def generate(prompt: str, seed: int) -> tuple[bytes, int]:
    workflow = _load_workflow()
    payload = _build_payload(workflow, prompt, seed)
    timeout = httpx.Timeout(connect=10, read=COMFY_TIMEOUT, write=30, pool=10)
    async with httpx.AsyncClient(base_url=COMFY_API_BASE, timeout=timeout) as client:
        prompt_id = await _submit_prompt(client, payload)
        image_bytes = await _poll_result(client, prompt_id)
    return image_bytes, seed
```

### 关键设计点

- **deepcopy 保护并发**：`_load_workflow()` 每次返回深拷贝，多用户排队不会互相串参数
- **内存缓存**：启动后只读一次文件，后续从缓存 deepcopy
- **`_set_node_input()`**：带明确错误信息的节点校验，KeyError 时指出具体节点 ID 和字段名，方便排查
- **`validate_workflow()`**：供 `/mode` 切换时调用，在切换阶段就发现问题，不等生成时才报错
- **httpx.AsyncClient 统一 base_url + timeout**：代码更清晰，减少 URL 拼接
- **ComfyUI 错误状态解析**：`_poll_result` 检查 `status.status_str == "error"`，不误杀正在执行的任务
- **错误只抛异常**：不返回 None，队列层统一 catch 处理

---

## 3. 修改 services/queue.py

### `_process_task()` 分支

在现有第 3 步（构建 payload 并生成）处按 backend 分支：

```python
# 原有 SD 路径
if settings.get("backend", "sd") == "sd":
    payload = _build_payload(settings, translated)
    image_data, actual_seed = await sd_api.txt2img(payload, progress_callback=on_progress)
else:
    # ComfyUI 路径
    import random
    seed = int(settings.get("seed", -1))
    if seed == -1:
        seed = random.randint(0, 2**63 - 1)
    image_data, actual_seed = await comfy_api.generate(translated, seed)
```

注意：seed 从 settings 取出时加 `int()` 转换（防御字符串值），范围用 `2**63 - 1` 适配 ComfyUI KSampler 的 64 位种子。

### caption 分支

ComfyUI 模式 caption：区分原始 prompt 和实际 prompt（翻译模式下二者不同），字段更少：

```python
if settings.get("backend", "sd") == "sd":
    info = f"<b>Prompt:</b> ...\n<b>Size:</b> ...\n..."  # 不变
else:
    info_parts = [
        f"<b>原始 Prompt:</b> {html.escape(task.prompt)}",
        f"<b>实际 Prompt:</b> {html.escape(translated)}",
        f"<b>Seed:</b> {actual_seed}",
        f"<b>后端:</b> ComfyUI",
        f"<b>耗时:</b> {elapsed:.1f}s",
    ]
    # 如果开了翻译且翻译结果与原文不同，同时显示两者
    # 如果未翻译或相同，跳过"实际 Prompt"
    if translated == task.prompt:
        info_parts = [
            f"<b>Prompt:</b> {html.escape(translated)}",
            f"<b>Seed:</b> {actual_seed}",
            f"<b>后端:</b> ComfyUI",
            f"<b>耗时:</b> {elapsed:.1f}s",
        ]
    info = "\n".join(info_parts)
```

### 额度扣退逻辑（不变）

现有结构已经满足。ComfyUI 异常会被队列层统一 catch 并退额度：

```python
try:
    # 生成（SD 或 ComfyUI）
except Exception:
    if task.credit_charged:
        await credits.refund_one(task.user_id)
    raise
```

### 进度回调

ComfyUI 目前不做进度展示（极简模式，无进度回调）。`on_progress` 只用于 SD 路径。

### 导入新增

```python
from services import sd_api, comfy_api, credits
```

---

## 4. 修改 handlers/generation.py — 新增 /mode 命令

### handle_mode

```python
async def handle_mode(update, context):
    """发送后端选择菜单。"""
    if not is_authorized(...):
        return
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🖼️ SD WebUI", callback_data="mode:sd"),
            InlineKeyboardButton("🎨 ComfyUI", callback_data="mode:comfyui"),
        ]
    ])
    user_id = update.effective_user.id
    settings = _ensure_settings(context, user_id)
    current = settings.get("backend", "sd")
    await update.message.reply_text(
        f"当前后端: {'SD WebUI' if current == 'sd' else 'ComfyUI'}\n请选择后端：",
        reply_markup=keyboard,
    )
```

### handle_mode_callback

```python
async def handle_mode_callback(update, context):
    """处理后端切换。"""
    query = update.callback_query
    await query.answer()

    # Callback 也需要鉴权（群聊中可能被非授权用户点击）
    if not is_authorized(query.from_user.id, query.message.chat.id, query.message.chat.type):
        await query.edit_message_text("⛔ 无使用权限")
        return

    backend = query.data.split(":", 1)[1]  # "sd" or "comfyui"

    # ComfyUI 模式下校验 workflow 结构和节点
    if backend == "comfyui":
        try:
            from services import comfy_api
            comfy_api.validate_workflow()
        except Exception as e:
            await query.edit_message_text(
                f"ComfyUI 工作流不可用：{e}\n请联系管理员。"
            )
            return

    user_id = query.from_user.id
    settings = _ensure_settings(context, user_id)
    settings["backend"] = backend
    _save_settings(context, user_id)

    label = "SD WebUI" if backend == "sd" else "ComfyUI"
    await query.edit_message_text(f"已切换为 {label} 模式。现在直接发送提示词即可生成图片。")
```

注意：`query.from_user` 是触发 callback 的用户（不是 `update.effective_user`），鉴权时需要用 `query.message.chat` 获取 chat 信息。

### handler 注册

```python
def get_handlers() -> list:
    return [
        CommandHandler("cancel", handle_cancel, filters=_user_auth_filter()),
        CommandHandler("mode", handle_mode, filters=_user_auth_filter()),
        CallbackQueryHandler(handle_mode_callback, pattern=r"^mode:"),
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & _user_auth_filter(),
            handle_text,
        ),
    ]
```

### callback 冲突防护

- `/mode` 的 callback data 使用 `mode:sd` / `mode:comfyui` 前缀
- `CallbackQueryHandler` 限制 `pattern=r"^mode:"`，不会与现有 settings 按钮冲突
- callback 内部二次鉴权，防止群聊中非授权用户点击内联按钮

---

## 5. backend 设置持久化

现有 `storage.py` 的 `load()` 逻辑已天然支持：

```python
# storage.load() 内部:
merged = copy.deepcopy(defaults)   # defaults 含 backend: "sd"
merged.update(data)                # 文件中的值覆盖默认值
```

- 旧用户文件中没有 `backend` 字段 → 自动补 `"sd"`
- 用户切换后 `save()` 保存完整 dict → `backend` 持久化
- Bot 重启不丢失

无需改 `storage.py`。

---

## 6. 不改的文件

- `bot.py` — handler 注册方式不变
- `handlers/settings.py` — ComfyUI 模式不使用参数设置菜单
- `services/translator.py` — 翻译复用，尊重 `settings["translate"]` 开关
- `services/credits.py` — 额度系统完全复用
- `services/storage.py` — 设置持久化无需改动
- `services/network.py` — 现有 `network.py` 只封装 Telegram/httpx 重试逻辑，不涉及 SD API；ComfyUI 在 `comfy_api.py` 内自行处理 httpx 请求

---

## 7. 错误处理

| 层级 | 场景 | 处理 |
|------|------|------|
| `/mode` 切换 | `validate_workflow()` 失败（文件缺失/节点不存在/JSON 损坏） | 拒绝切换，提示具体错误 |
| `/mode` callback | 非授权用户点击内联按钮 | 二次鉴权，显示无权限提示 |
| 提交 `POST /prompt` | 非 200 | httpx 自动 `raise_for_status()` → `ComfyApiError` |
| 提交 `POST /prompt` | 返回无 `prompt_id` | 抛出 `ComfyApiError` |
| 轮询 `/history` | `status.status_str == "error"` | 抛出 `ComfyApiError`（不误杀正在执行的任务） |
| 轮询 `/history` | 超时（300s）无图片 | 抛出 `ComfyTimeoutError` |
| 下载 `/view` | 非 200 | httpx 自动 `raise_for_status()` |
| `_build_payload` | 节点 ID 或字段不存在 | `_set_node_input` 抛出 `ComfyWorkflowError`，明确指出 node_id 和 input_key |
| 队列层 `_process_task` | 任意异常 | catch → 退额度 → 发错误提示 |

所有异常在队列层统一处理，不返回 None。

---

## 8. 验证方式

1. 发送 `/mode` → 看到当前后端和两个按钮 → 选择 ComfyUI → 确认消息 "已切换为 ComfyUI 模式"
2. 发送任意中文 prompt（`settings["translate"]=True`）→ 翻译成英文 → 生成图片 → 收到图片 + 简化 caption
3. 关闭翻译后发 prompt → 原文直接写入 workflow → 生成图片
4. 切换回 `/mode` → SD WebUI → 发 prompt → 验证 SD 功能正常
5. 删除 `data/zit-api.json` → `/mode` 切 ComfyUI → 收到 "workflow 未配置" 提示
6. 额度检查：生成成功扣额度，关停 ComfyUI 后生成失败退额度
