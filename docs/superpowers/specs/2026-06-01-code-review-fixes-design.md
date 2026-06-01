# 代码审查修复设计

日期: 2026-06-01 | 来源: `/code-review` 12+1 条发现
审查人: 用户（Ksufer）| 状态: 已批准

## 概述

修复 13 条代码审查发现，按问题域分为 4 组。改动总量约 70 行，分布在 6 个文件中。

---

## Group A：数据修复

### #2 LoRA 路径反斜杠 → Linux 兼容

**文件**: `data/Qwen Image Edit Rapid v1.0 (api).json`  
**改动**: 节点 103 的 `lora` 字段: `qwen_edit\\next-scene_lora-v2-3000.safetensors` → `qwen_edit/next-scene_lora-v2-3000.safetensors`

**验证**: `python3 -c "import json; d=json.load(open('data/Qwen Image Edit Rapid v1.0 (api).json')); print(d['103']['inputs']['lora_1']['lora'])"` 输出 `qwen_edit/next-scene_lora-v2-3000.safetensors`

---

## Group B：img2img 路由加固

**文件**: `handlers/generation.py`

### #1 handle_text() 增加 img2img 守卫

**位置**: seed 输入处理之后、额度检查之前（~82 行）

**原因**: 用户在 img2img 工作流下发文字 → 不触发图片上传 → LoadImage 用不存在的默认图 → ComfyUI 报错。守卫前置可避免走到额度/任务逻辑。

```python
# 在 _extract_prompt 和 seed 输入处理之后，额度检查之前插入：
if settings.get("backend") == "comfyui":
    wf_config = COMFY_WORKFLOWS.get(
        settings.get("comfy_workflow", COMFY_DEFAULT_WORKFLOW), {}
    )
    if wf_config.get("is_img2img"):
        await message.reply_text("当前工作流是图生图模式，请直接发送图片。")
        return
```

### #13 handle_photo() 增加群聊 @bot 检测

**位置**: `handle_photo()` 权限检查之后、backend 检查之前（~323 行后）

**关键**: 图片消息的 @bot 在 `caption_entities` 中，不是 `entities`。用 `parse_caption_entities()`。

```python
if chat.type in ("group", "supergroup"):
    bot_username = context.bot.username
    if not bot_username:
        return
    entities = message.parse_caption_entities(types=[MessageEntity.MENTION])
    mentioned = any(
        text.lower() == f"@{bot_username.lower()}"
        for text in entities.values()
    )
    if not mentioned:
        return
```

复用 `_extract_prompt()`（generation.py:28-34）的 MENTION 检测模式，保持一致。

### #10 handle_photo() 硬编码 fallback 改用常量

**位置**: `handle_photo()` 第 334 行  
**旧**: `wf_key = settings.get("comfy_workflow", "z-image-turbo")`  
**新**: `wf_key = settings.get("comfy_workflow", COMFY_DEFAULT_WORKFLOW)`  

确保 `COMFY_DEFAULT_WORKFLOW` 已在模块顶部从 config import。

### #12 is_img2img 配置约定 → 校验阶段强制

**文件**: `services/comfy_api.py` `validate_workflow()` + `handlers/comfy_settings.py` 第 49 行

**原因修正**: 原有分析 `not None == True` 导致误判已存在行为变更。实际 `wf_config.get("is_img2img")` vs `wf_config.get("is_img2img", False)` 在缺失键时行为相同。真正的问题是：缺失 `is_img2img` 应该被视为配置错误，而不是静默回退。

**方案**: 在 `validate_workflow()` 中强制要求 `is_img2img` 字段存在：

```python
# validate_workflow() 中追加：
if "is_img2img" not in wf:
    raise ComfyWorkflowError(
        f"Workflow '{wf_key}': 缺少 'is_img2img' 字段（必须显式指定 True/False）"
    )
```

同时在 `comfy_settings.py` 第 49 行改为显式默认值作为防御性编程：
```python
if not wf_config.get("is_img2img", False):
```
行为不变，但表达更清晰。

---

## Group C：配置/校验增强

**文件**: `services/comfy_api.py` + `config.py`

### #5 validate_workflow() 增加 class_type 检查（含缺失保护）

**位置**: `validate_workflow()` 末尾

**注意**: 节点 ID 在 ComfyUI API 格式中为字符串（如 `"118"`），配置中也统一用字符串。

```python
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
```

### #6 get_models() 静默失败加日志

**位置**: `get_models()` ~121 行

```python
if model_key not in required:
    logger.warning(
        "Workflow '%s': model_key '%s' 不在 %s 的 required 字段中，模型列表为空",
        settings.get("comfy_workflow", "?"), model_key, loader_class
    )
    return []
models = required[model_key][0]
return models if isinstance(models, list) else []
```

### #11 qwen-image-edit 路径支持环境变量

**文件**: `config.py`

**旧**: `"path": "data/Qwen Image Edit Rapid v1.0 (api).json",`  
**新**: `"path": os.getenv("COMFY_QWEN_EDIT_WORKFLOW_PATH", "data/Qwen Image Edit Rapid v1.0 (api).json"),`

与其他两个工作流保持一致。

---

## Group D：UI / 显示 / 其他修复

**涉及文件**: `handlers/comfy_settings.py` + `services/queue.py` + `services/comfy_api.py`

### #3 Image Saver Simple 兼容性（日志增强，带防御）

**位置**: `_poll_result()` 超时处（comfy_api.py ~180 行）

```python
logger.warning(
    "ComfyUI 生成超时，输出节点: %s",
    [nid for nid, node in workflow.items()
     if node.get("class_type") in ("SaveImage", "Image Saver Simple")]
)
```

注意 `node.get("class_type")` 带防御，避免缺字段节点导致超时处理中抛新异常。

### #4 自定义 Prompt 支持清除

**文件**: `handlers/comfy_settings.py`

1. `_comfy_settings_menu()`: 当 `comfy_prompt` 非空时追加「🗑 清除 Prompt」按钮（callback: `clear_comfy_prompt`）
2. 新增回调处理:
```python
elif data == "clear_comfy_prompt":
    await query.answer("已清除 Prompt")
    settings["comfy_prompt"] = ""
    await _comfy_settings_menu(update, context)
```

### #7 _gen_context 内存泄漏 → 用插入顺序淘汰

**文件**: `services/queue.py` ~211-217 行

用 Python 3.7+ dict 插入顺序，pop 第一个 key（最旧）:

```python
_gen = self._app.bot_data["_gen_context"]
_gen[context_id] = {...}
while len(_gen) > 50:
    _gen.pop(next(iter(_gen)))
```

比 `sorted()` 更准且更快（O(1) vs O(n log n)）。

### #8 img2img 图注尺寸 → 语义化判断

**文件**: `services/queue.py` `_build_comfy_info()` ~340 行

```python
wf_config = COMFY_WORKFLOWS.get(settings.get("comfy_workflow", ""), {})
if wf_config.get("is_img2img") and not wf_config.get("width_node"):
    size = "跟随输入图片"
else:
    size = f"{settings.get('comfy_width', '?')}×{settings.get('comfy_height', '?')}"
```

`is_img2img` + 无 `width_node` 双重判断，语义更明确。纯文生图工作流即使临时缺 `width_node` 也不会误显示。

### #9 空 Prompt 标签

**文件**: `services/queue.py` `_build_comfy_info()`

```python
if translated and translated.strip():
    lines.append(f"<b>Prompt:</b> {html.escape(translated)}")
```

防止纯空白字符串也输出空标签。

---

## 改动文件汇总

| 文件 | 修复项 | 估计行数 |
|------|--------|---------|
| `data/Qwen Image Edit Rapid v1.0 (api).json` | #2 | 1 字符 |
| `handlers/generation.py` | #1, #13, #10 | ~25 行 |
| `handlers/comfy_settings.py` | #4, #12 | ~15 行 |
| `services/comfy_api.py` | #5, #6, #3, #12（校验部分） | ~25 行 |
| `services/queue.py` | #7, #8, #9 | ~15 行 |
| `config.py` | #11 | 1 行 |

总计约 80 行，6 个文件。

---

## 测试验证

| # | 测试场景 | 预期结果 |
|---|---------|---------|
| 1 | `validate_workflow()` | 三个工作流均通过校验 |
| 2 | 故意把 `model_loader_class` 改错后运行 `validate_workflow()` | 明确报错指出 class_type 不匹配 |
| 3 | 群聊发图，不加 @bot | 不触发生成、不扣额度 |
| 4 | 群聊发图，caption 写 `@YourBot 一只猫` | 正常触发生成 |
| 5 | 群聊发文字 `@YourBot hello`（img2img 工作流） | 提示"请发送图片" |
| 6 | 私聊发文字（img2img 工作流） | 提示"请发送图片" |
| 7 | 设置自定义 Prompt → 点清除 | Prompt 恢复为空，实时输入生效 |
| 8 | 模拟插入 60 条 _gen_context | 只保留 50 条，最早插入的被淘汰 |
| 9 | 图生图生成完毕查看图注 | 尺寸显示"跟随输入图片"，无空 Prompt 标签 |
| 10 | 文生图生成完毕查看图注 | 尺寸正常显示数值，Prompt 正常显示 |
