# Code Review Fixes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix 13 code review findings across 6 files: LoRA path, img2img routing guards, config validation, UI improvements, and display fixes.

**Architecture:** Four independent groups applied in dependency order: (1) data/config fixes, (2) validation hardening, (3) routing guards, (4) UI/display polish. Each group self-contained with its own verify step.

**Tech Stack:** Python 3.12, python-telegram-bot, httpx, ComfyUI API

---

### Task 1: Group A — LoRA path fix + config/env parity

**Files:**
- Modify: `data/Qwen Image Edit Rapid v1.0 (api).json`
- Modify: `config.py:61`

- [ ] **Step 1: Fix LoRA backslash path using text replacement (not json.dump)**

Use a string-level replace to change only the one character, avoiding full-file reformat:

```bash
python3 - <<'PY'
from pathlib import Path

p = Path("data/Qwen Image Edit Rapid v1.0 (api).json")
s = p.read_text(encoding="utf-8")
old = r"qwen_edit\\next-scene_lora-v2-3000.safetensors"
new = r"qwen_edit/next-scene_lora-v2-3000.safetensors"

if old not in s:
    raise SystemExit(f"ERROR: old path not found in {p}")

p.write_text(s.replace(old, new, 1), encoding="utf-8")
print(f"Updated: {old} -> {new}")
PY
```

Note: In the JSON file text, the backslash is stored as `\\`. The Python raw string `r"qwen_edit\\next-scene..."` matches literally `qwen_edit\next-scene...` in the file. Count the characters: in the JSON file you see `"qwen_edit\\next-scene..."`. If the raw string doesn't match, read the file first to confirm the exact text, then adjust.

- [ ] **Step 2: Verify the fix**

```bash
python3 -c "import json; d=json.load(open('data/Qwen Image Edit Rapid v1.0 (api).json')); print(repr(d['103']['inputs']['lora_1']['lora']))"
```

Expected: `'qwen_edit/next-scene_lora-v2-3000.safetensors'` (with forward slash `/`)

- [ ] **Step 3: Add env var override for qwen-image-edit path**

In `config.py`, change line 61:
```python
# old
"path": "data/Qwen Image Edit Rapid v1.0 (api).json",
# new
"path": os.getenv("COMFY_QWEN_EDIT_WORKFLOW_PATH", "data/Qwen Image Edit Rapid v1.0 (api).json"),
```

- [ ] **Step 4: Verify config import still works**

```bash
uv run python -c "from config import COMFY_WORKFLOWS; wf = COMFY_WORKFLOWS['qwen-image-edit']; print(f'path={wf[\"path\"]}')"
```

Expected: `path=data/Qwen Image Edit Rapid v1.0 (api).json`

- [ ] **Step 5: Commit**

```bash
git add "data/Qwen Image Edit Rapid v1.0 (api).json" config.py
git commit -m "fix: LoRA path backslash + env var parity for qwen-image-edit workflow

- Fix LoRA path from Windows backslash to forward slash for Linux compat
- Add COMFY_QWEN_EDIT_WORKFLOW_PATH env var support (parity with other workflows)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Group C — validate_workflow hardening + get_models logging

**Files:**
- Modify: `services/comfy_api.py:93-122` (validate_workflow + get_models)
- Modify: `services/comfy_api.py:149-181` (_poll_result timeout log)

- [ ] **Step 1: Add is_img2img mandatory check + class_type check to validate_workflow()**

Replace `validate_workflow()` (lines 93-106) with:

```python
def validate_workflow() -> None:
    """校验所有配置的 workflow 文件存在且关键节点正确。"""
    for wf_key, wf in COMFY_WORKFLOWS.items():
        workflow = _load_workflow(wf_key)

        # 强制要求 is_img2img 字段
        if "is_img2img" not in wf:
            raise ComfyWorkflowError(
                f"Workflow '{wf_key}': 缺少 'is_img2img' 字段（必须显式指定 True/False）"
            )

        _set_node_input(workflow, wf["prompt_node"], wf["prompt_key"], "test")
        _set_node_input(workflow, wf["seed_node"], wf["seed_key"], 1)
        _set_node_input(workflow, wf["model_node"], wf["model_key"],
                        wf.get("default_model", ""))
        if "width_node" in wf:
            _set_node_input(workflow, wf["width_node"], wf["width_key"], 768)
            _set_node_input(workflow, wf["height_node"], wf["height_key"], 1280)
        if "load_image_node" in wf:
            _set_node_input(workflow, wf["load_image_node"], wf["load_image_key"], "test.png")

        # 校验 model_loader_class 与实际节点 class_type 一致
        model_node = wf.get("model_node")
        expected_class = wf.get("model_loader_class")
        if model_node is not None and expected_class:
            node = workflow.get(str(model_node))
            if not node:
                raise ComfyWorkflowError(
                    f"Workflow '{wf_key}': model_node '{model_node}' 不存在"
                )
            actual_class = node.get("class_type")
            if actual_class != expected_class:
                raise ComfyWorkflowError(
                    f"Workflow '{wf_key}': model_loader_class "
                    f"'{expected_class}' 与节点 {model_node} "
                    f"class_type '{actual_class}' 不匹配"
                )

    logger.info("所有 ComfyUI workflow 校验通过")
```

- [ ] **Step 2: Add warning log to get_models() for missing model_key**

In `get_models()` (lines 109-122), replace lines 120-122:

```python
# old (lines 120-122)
    required = node_info.get("input", {}).get("required", {})
    models = required.get(model_key, [[]])[0]
    return models if isinstance(models, list) else []

# new
    required = node_info.get("input", {}).get("required", {})
    if model_key not in required:
        logger.warning(
            "Workflow '%s': model_key '%s' 不在 %s 的 required 字段中，模型列表为空",
            settings.get("comfy_workflow", "?"), model_key, loader_class
        )
        return []
    models = required[model_key][0]
    return models if isinstance(models, list) else []
```

- [ ] **Step 3: Add diagnostic log in _poll_result() on timeout**

First, check `_workflow_cache` structure:
```bash
grep -n "_workflow_cache" services/comfy_api.py
```

If `_workflow_cache` is keyed by `wf_key` (as `_workflow_cache: dict[str, dict]` at line 20 implies), use the full diagnostic log. Otherwise use the fallback.

**Primary approach** (if `_workflow_cache` is keyed by `wf_key`):

In `generate()`, change the `_poll_result` call (~line 207):
```python
# old
image_bytes = await _poll_result(client, prompt_id)
# new
image_bytes = await _poll_result(client, prompt_id, wf_key)
```

Update `_poll_result` signature and timeout line:
```python
# old signature
async def _poll_result(client: httpx.AsyncClient, prompt_id: str) -> bytes:
# new signature
async def _poll_result(client: httpx.AsyncClient, prompt_id: str, wf_key: str = None) -> bytes:
```

Replace the timeout raise (line 181):
```python
# old
    raise ComfyTimeoutError(f"ComfyUI 生成超时 ({COMFY_TIMEOUT}s)")

# new
    logger.warning(
        "ComfyUI 生成超时 (%ss), workflow=%s, 输出节点: %s",
        COMFY_TIMEOUT,
        wf_key or "?",
        [nid for nid, node in _workflow_cache.get(wf_key, {}).items()
         if isinstance(node, dict) and node.get("class_type") in ("SaveImage", "Image Saver Simple")]
    )
    raise ComfyTimeoutError(f"ComfyUI 生成超时 ({COMFY_TIMEOUT}s)")
```

**Fallback** (if `_workflow_cache` structure doesn't allow lookup by `wf_key`):

```python
# simpler: just record wf_key, don't try to enumerate nodes
    logger.warning(
        "ComfyUI 生成超时 (%ss), workflow=%s",
        COMFY_TIMEOUT,
        wf_key or "?",
    )
    raise ComfyTimeoutError(f"ComfyUI 生成超时 ({COMFY_TIMEOUT}s)")
```

- [ ] **Step 4: Verify validation passes**

```bash
uv run python -c "from services.comfy_api import validate_workflow; validate_workflow()"
```

Expected: `所有 ComfyUI workflow 校验通过` (in logs)

- [ ] **Step 5: Test misconfiguration actually triggers an error**

Use monkey-patch to verify the check works:

```bash
uv run python - <<'PY'
import config
originals = {}

# Monkey-patch both copies of COMFY_WORKFLOWS
# (config.py and comfy_api.py each have their own module-level reference
#  because comfy_api does `from config import COMFY_WORKFLOWS`)
import services.comfy_api as api

# Verify we're patching the same dict object
assert config.COMFY_WORKFLOWS is api.COMFY_WORKFLOWS, \
    "config.COMFY_WORKFLOWS and api.COMFY_WORKFLOWS are different objects"

old = config.COMFY_WORKFLOWS["qwen-image-edit"]["model_loader_class"]
config.COMFY_WORKFLOWS["qwen-image-edit"]["model_loader_class"] = "__WRONG_CLASS__"

try:
    api.validate_workflow()
    print("ERROR: validate_workflow should have raised ComfyWorkflowError")
    raise SystemExit(1)
except api.ComfyWorkflowError as e:
    print(f"OK - caught expected error: {e}")
finally:
    config.COMFY_WORKFLOWS["qwen-image-edit"]["model_loader_class"] = old
PY
```

Expected output: `OK - caught expected error: ... class_type ... 不匹配`

- [ ] **Step 6: Commit**

```bash
git add services/comfy_api.py
git commit -m "fix: harden validate_workflow + get_models warning + poll timeout diagnostics

- Require is_img2img field in all workflow configs
- Validate model_loader_class matches actual node class_type
- Add warning log when model_key missing from /object_info response
- Add diagnostic log on generation timeout (workflow + output node types)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Group B — img2img routing guards

**Files:**
- Modify: `handlers/generation.py`
- Modify: `handlers/comfy_settings.py:49`

- [ ] **Step 1: Add COMFY_DEFAULT_WORKFLOW to generation.py imports**

In `generation.py` line 9:
```python
# old
from config import ADMIN_USER_ID, DEFAULT_USER_SETTINGS, COMFY_WORKFLOWS
# new
from config import ADMIN_USER_ID, DEFAULT_USER_SETTINGS, COMFY_WORKFLOWS, COMFY_DEFAULT_WORKFLOW
```

- [ ] **Step 2: Confirm MessageEntity is already imported**

Check the existing import on line 6:
```bash
grep -n "MessageEntity" handlers/generation.py
```

Expected: line 6 already has `from telegram import MessageEntity, ...`. If not, add it.

- [ ] **Step 3: Add img2img guard in handle_text()**

**Critical positioning rule**: Must be placed AFTER `_extract_prompt()` has already returned a valid prompt (so group-chat non-@bot messages are already filtered out), AFTER `_ensure_settings()`, and BEFORE the credit check.

Read `handle_text()` and find the code block that looks like:
```python
    if context.user_data is not None:
        settings = _ensure_settings(context, user_id)
    else:
        settings = copy.deepcopy(DEFAULT_USER_SETTINGS)

    # ... logger.debug ...

    # 额度检查
    is_admin = ADMIN_USER_ID is not None and user_id == ADMIN_USER_ID
```

Insert the guard between `_ensure_settings()` and the credit check:

```python
    if context.user_data is not None:
        settings = _ensure_settings(context, user_id)
    else:
        settings = copy.deepcopy(DEFAULT_USER_SETTINGS)

    # 图生图工作流拦截纯文字消息
    if settings.get("backend") == "comfyui":
        wf_config = COMFY_WORKFLOWS.get(
            settings.get("comfy_workflow", COMFY_DEFAULT_WORKFLOW), {}
        )
        if wf_config.get("is_img2img"):
            await message.reply_text("当前工作流是图生图模式，请直接发送图片。")
            return

    # 额度检查 + 扣减（管理员跳过）
    is_admin = ADMIN_USER_ID is not None and user_id == ADMIN_USER_ID
```

- [ ] **Step 4: Add @bot mention check in handle_photo()**

In `handle_photo()`, after the `is_authorized()` check (~line 323) and before the backend check (~line 332), insert:

```python
    # 群聊中需要 @bot 才触发（与 handle_text 行为一致）
    if chat.type in ("group", "supergroup"):
        bot_username = context.bot.username
        if not bot_username:
            return
        # 图片消息的 @bot 在 caption_entities 中
        entities = message.parse_caption_entities(types=[MessageEntity.MENTION])
        mentioned = any(
            text.lower() == f"@{bot_username.lower()}"
            for text in entities.values()
        )
        if not mentioned:
            return
```

- [ ] **Step 5: Fix hardcoded fallback in handle_photo()**

In `handle_photo()` line 334:
```python
# old
wf_key = settings.get("comfy_workflow", "z-image-turbo")
# new
wf_key = settings.get("comfy_workflow", COMFY_DEFAULT_WORKFLOW)
```

- [ ] **Step 6: Make is_img2img default explicit in comfy_settings.py**

In `comfy_settings.py` line 49:
```python
# old
if not wf_config.get("is_img2img"):
# new
if not wf_config.get("is_img2img", False):
```

- [ ] **Step 7: Verify handler registration**

```bash
uv run python -c "from handlers.generation import get_handlers; print(f'{len(get_handlers())} handlers registered')"
```

- [ ] **Step 8: Commit**

```bash
git add handlers/generation.py handlers/comfy_settings.py
git commit -m "fix: img2img routing guards — text-block, @bot check, fallback constant

- Block text messages when img2img workflow is active (#1)
- Add @bot mention check via caption_entities for photos in group chats (#13)
- Replace hardcoded 'z-image-turbo' with COMFY_DEFAULT_WORKFLOW constant (#10)
- Explicit False default for is_img2img check (#12)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Group D — UI, display, and memory fixes

**Files:**
- Modify: `handlers/comfy_settings.py` (clear prompt button + handler)
- Modify: `services/queue.py` (gen_context LRU, build_comfy_info display)

- [ ] **Step 1: Add "Clear Prompt" button to comfy settings menu**

In `_comfy_settings_menu()` in `comfy_settings.py`, find the line where `keyboard.append([...关闭菜单...])` is done and insert before it:

```python
    if comfy_prompt:
        keyboard.insert(-1, [InlineKeyboardButton("🗑 清除 Prompt", callback_data="clear_comfy_prompt")])
```

The variable `comfy_prompt` is already defined at line 26 in this function (`comfy_prompt = settings.get("comfy_prompt", "")`).

- [ ] **Step 2: Add clear_comfy_prompt callback handler**

Add this new async function, placing it near the other callback handlers (before `get_handlers()` at ~line 325):

```python
async def clear_comfy_prompt(update, context):
    """清除自定义 Prompt，恢复使用实时输入。"""
    query = update.callback_query
    user_id = _get_user_id(update)
    settings = _ensure_settings(context, user_id)
    settings["comfy_prompt"] = ""
    _save_settings(context, user_id)

    await _safe_answer(query, "已清除 Prompt")
    text, markup = _comfy_settings_menu(settings)
    await _reply_menu(query, text, markup)
```

- [ ] **Step 3: Register clear_comfy_prompt callback — watch handler ordering**

In `get_handlers()`, add BEFORE the closing `]` but BEFORE any catch-all pattern handlers. Looking at the existing handler list (lines 328-341), all handlers use specific patterns like `^comfy_settings$`, `^comfy_model:`, etc. No catch-all pattern exists, so adding to the end of the list is safe.

Add this line before `]`:
```python
        CallbackQueryHandler(auth_callback(clear_comfy_prompt), pattern=r"^clear_comfy_prompt$"),
```

- [ ] **Step 4: Fix _gen_context memory leak with LRU eviction**

In `services/queue.py`, find the lines ~211-217:
```python
        context_id = uuid.uuid4().hex[:8]
        if "_gen_context" not in self._app.bot_data:
            self._app.bot_data["_gen_context"] = {}
        self._app.bot_data["_gen_context"][context_id] = {
            "prompt": task.prompt,
            "translated": translated,
            "seed": actual_seed,
        }
```

Replace with:
```python
        context_id = uuid.uuid4().hex[:8]
        if "_gen_context" not in self._app.bot_data:
            self._app.bot_data["_gen_context"] = {}
        _gen = self._app.bot_data["_gen_context"]
        _gen[context_id] = {
            "prompt": task.prompt,
            "translated": translated,
            "seed": actual_seed,
        }
        # 只保留最近 50 条，按 Python 3.7+ 插入顺序淘汰最旧
        while len(_gen) > 50:
            _gen.pop(next(iter(_gen)))
```

- [ ] **Step 5: Fix size display + empty prompt in _build_comfy_info() — partial replacement only**

Read `_build_comfy_info()` (queue.py ~337-352) and make TWO targeted changes (do NOT replace the whole function):

**Change A — size calculation** (line 340):
```python
# old
size = f"{settings.get('comfy_width', '?')}×{settings.get('comfy_height', '?')}"
# new
wf_config = COMFY_WORKFLOWS.get(settings.get("comfy_workflow", ""), {})
if wf_config.get("is_img2img") and not wf_config.get("width_node"):
    size = "跟随输入图片"
else:
    size = f"{settings.get('comfy_width', '?')}×{settings.get('comfy_height', '?')}"
```

**Change B — prompt lines** (lines 347-351 — the `if translated == task.prompt:` block):
```python
# old
if translated == task.prompt:
    info_parts.insert(0, f"<b>Prompt:</b> {actual}")
else:
    info_parts.insert(0, f"<b>实际 Prompt:</b> {actual}")
    info_parts.insert(0, f"<b>原始 Prompt:</b> {html.escape(task.prompt)}")

# new
if translated and translated.strip():
    actual = html.escape(translated)
    if translated == task.prompt:
        info_parts.insert(0, f"<b>Prompt:</b> {actual}")
    else:
        info_parts.insert(0, f"<b>实际 Prompt:</b> {actual}")
        info_parts.insert(0, f"<b>原始 Prompt:</b> {html.escape(task.prompt)}")
```

All other fields in the function (model, seed, elapsed) remain unchanged.

- [ ] **Step 6: Check if COMFY_WORKFLOWS import is needed in queue.py**

```bash
grep -n "from config import\|COMFY_WORKFLOWS" services/queue.py
```

If `COMFY_WORKFLOWS` is not imported, add it. Check existing import line and modify:
```python
# look for existing config import, e.g.:
# from config import ...
# add COMFY_WORKFLOWS if not already present
```

- [ ] **Step 7: Verify all imports and basic logic**

```bash
uv run python -c "
from handlers.comfy_settings import get_handlers
from services.queue import _build_comfy_info
print('All imports OK')
"
```

- [ ] **Step 8: Commit**

```bash
git add handlers/comfy_settings.py services/queue.py
git commit -m "fix: clear prompt button, gen_context LRU eviction, img2img display polish

- Add clear prompt button in comfy settings menu (#4)
- Limit _gen_context to 50 entries using dict insertion-order LRU (#7)
- Show '跟随输入图片' for img2img workflows without size controls (#8)
- Suppress empty prompt label in img2img info caption (#9)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: End-to-end verification

- [ ] **Step 1: Run workflow validation**

```bash
uv run python -c "from services.comfy_api import validate_workflow; validate_workflow()"
```

Expected: `所有 ComfyUI workflow 校验通过` in logs.

- [ ] **Step 2: Verify all 3 workflows have is_img2img field**

```bash
uv run python -c "
from config import COMFY_WORKFLOWS
for key, wf in COMFY_WORKFLOWS.items():
    assert 'is_img2img' in wf, f'{key}: missing is_img2img'
    print(f'{key}: is_img2img={wf[\"is_img2img\"]}, loader={wf[\"model_loader_class\"]}')
print('OK')
"
```

- [ ] **Step 3: Deploy to homelab**

```bash
# Sync code (excludes data/)
rsync -avz \
  --exclude '.git' --exclude '__pycache__' --exclude '.venv/' \
  --exclude '.env' --exclude 'logs/' \
  --exclude '.codegraph/' --exclude '.claude/' --exclude 'docs/' \
  --exclude '*.pyc' \
  ./ homelab:/home/ksufer/homelab/stacks/sd-telegram-bot/

# Sync data files explicitly (data/ is excluded from above)
rsync -avz \
  "data/Qwen Image Edit Rapid v1.0 (api).json" \
  "data/templates-image_to_real.json" \
  homelab:/home/ksufer/homelab/stacks/sd-telegram-bot/data/

# Rebuild and restart
ssh homelab "cd /home/ksufer/homelab/stacks/sd-telegram-bot && docker compose up -d --build"

# Check startup logs
ssh homelab "docker logs sd-telegram-bot --since 30s"
```

- [ ] **Step 4: Manual Telegram smoke tests**

Perform these tests manually in Telegram:

| # | Scenario | Expected |
|---|----------|----------|
| 1 | 群聊发图（不加 @bot）| 不触发生成，不扣额度 |
| 2 | 群聊发图，caption 写 `@YourBot 一只猫` | 正常触发生成 |
| 3 | img2img 工作流下发文字 | 提示「请直接发送图片」|
| 4 | 私聊发文字（img2img 工作流）| 同上 |
| 5 | 设置自定义 Prompt → 出现「清除」按钮 → 点击 | Prompt 清空，恢复实时输入 |
| 6 | 图生图生成完毕查看图注 | 尺寸「跟随输入图片」，无空 Prompt 标签 |
| 7 | 文生图生成完毕查看图注 | 尺寸正常数值，Prompt 正常显示 |

- [ ] **Step 5: Commit deployment tweaks if any**

```bash
git add -A
git commit -m "chore: deployment verification

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```
