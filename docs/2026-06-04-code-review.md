# 代码审查报告

**日期**：2026-06-04  
**审查范围**：`git diff HEAD`（未提交的工作区变更）  
**涉及文件**：5 个文件，约 400 行变更  
**审查方法**：9 角度并行扫描 → 2 轮交叉验证 → 补充扫描 → 去重排序  

---

## 变更概述

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `config.py` | 新增 | 两个新工作流：`sdxl`（SDXL 文生图）、`firstlast-video`（首尾帧生视频） |
| `handlers/generation.py` | 重构+新增 | `_clean_caption()` 提取；firstlast-video 多步交互；auto_edit 自动检测；handle_photo 重构 |
| `handlers/workflow_menu.py` | 新增 | 切换工作流时清除 firstlast 状态 |
| `services/comfy_api.py` | 重构+新增 | `ComfyOutput` 数据类；视频/gif 输出支持；多节点注入；`prompt_prefix`；`model_selectable` |
| `services/queue.py` | 新增 | `_truncate_for_caption()`；视频输出发送；`uploaded_images` 支持 |

---

## 🔴 正确性 Bug（会导致数据丢失、错误输出或崩溃）

### B1. firstlast-video 帧在任务创建前从 user_data 删除，多个提前返回路径会丢失帧引用

- **文件**：[handlers/generation.py](handlers/generation.py)
- **位置**：第 83–85 行
- **严重度**：🔴 高

**问题描述**：

`handle_text` 在检测到 firstlast-video 的两个帧都已上传、用户发送了文字描述后，**立即**从 `user_data` 中删除帧引用（第 83–85 行），然后继续执行到额度检查和任务入队。如果后续任一环节失败导致提前返回，帧引用永久丢失。

**关键代码**：

```python
# handlers/generation.py:76-86
if start_frame and end_frame:
    prompt_text = (message.text or "").strip()
    if not prompt_text:
        await message.reply_text("请输入视频描述文字，或发送 /cancel 取消。")
        return
    _firstlast_frames = {"start": start_frame, "end": end_frame}
    _firstlast_prompt = prompt_text
    # 清除状态（任务创建前清除，避免失败残留）
    del context.user_data["_firstlast_start_frame"]     # ← 过早删除
    del context.user_data["_firstlast_end_frame"]       # ← 过早删除
    # 继续执行，不 return —— 让后续额度检查+任务创建流程处理
```

**会丢失帧的提前返回路径**：

| 行号 | 触发条件 | 后果 |
|------|----------|------|
| 93–99 | `_waiting_input` 处于激活状态（如 comfy_seed） | 帧已删除但 handler 提前 return |
| 120–249 | `auto_edit` 触发（用户回复了 bot 图片） | 创建 qwen-image-edit 任务，帧被丢弃 |
| 288–291 | 非管理员用户额度用完 | 帧已删除，用户需重新上传 |
| 332 | `queue.enqueue()` 失败 | 帧已删除，任务未创建 |

**修复建议**：将 `del` 操作移动到额度检查通过之后、任务创建之前，或使用 `try/finally` 确保仅在成功路径清除。

---

### B2. 仅存首帧时 auto_edit 触发后 user_data 残留 _firstlast_start_frame，后续配错尾帧

- **文件**：[handlers/generation.py](handlers/generation.py)
- **位置**：第 76 行（条件 `if start_frame and end_frame` 未命中）
- **严重度**：🔴 高

**问题描述**：

用户上传首帧后（`_firstlast_start_frame` 已存），尚未发尾帧。此时若用户**回复 bot 图片消息发送文字**（触发 auto_edit），`handle_text` line 76 的条件 `if start_frame and end_frame` 为 False → 不进入 firstlast 删除块 → auto_edit 路径 (line 120) 创建 qwen-image-edit 任务并 return (line 249) → `_firstlast_start_frame` 残留在 `user_data`。

后续用户在 firstlast-video 模式下发送**新图片**时，`handle_photo` line 572 检测到旧的首帧残留 → 将新图片当作尾帧配对，产生一个使用了错误首帧的视频。

**复现步骤**：

1. 切换到 firstlast-video 工作流
2. 发送首帧图片（存入 `_firstlast_start_frame`）
3. **回复**某个 bot 之前生成的图片，发送文字描述 → 触发 auto_edit，创建 qwen-image-edit 任务
4. 再次在 firstlast-video 模式下发送一张新图片 → bot 误将其当作尾帧，与步骤 2 的首帧配对生成视频

**修复建议**：在 `handle_text` 中，当 `auto_edit` 触发时，主动清除 `_firstlast_start_frame` 和 `_firstlast_end_frame`。

---

### B3. handle_photo 中 auto_edit 绕过 firstlast 清理逻辑

- **文件**：[handlers/generation.py](handlers/generation.py)
- **位置**：第 566–616 行
- **严重度**：🔴 高

**问题描述**：

与 B2 镜像问题，但发生在 `handle_photo` 路径。line 566 的条件是 `if wf_key == "firstlast-video" and not auto_edit`，当 `auto_edit` 为 True 时直接跳过整个 firstlast 块 → line 617 的 `else` 分支仅设置局部变量 `_firstlast_frames = None`，不动 `user_data` → 残留的首帧永不清除。

**修复建议**：在 auto_edit 路径中增加 firstlast 状态清理。

---

### B4. auto_edit 外层 try/except 吞异常后静默回退

- **文件**：[handlers/generation.py](handlers/generation.py)
- **位置**：第 120–251 行（外层 `try/except Exception`）
- **严重度**：🟡 中

**问题描述**：

auto_edit / 多轮编辑块被包裹在 `try/except Exception: logger.error(...)` 中。如果异常发生在额度扣减（line 146–149）之后、任务入队（line 218）之前，异常被捕获但**额度未退还**，且执行回退到 `_extract_prompt` (line 254) → 可能触发第二次额度扣减。

**关键代码**：

```python
try:
    if (...):  # line 121, multi-edit 条件
        ...
        credit_charged = True  # line 149, 额度已扣
        ...
        ahead = await queue.enqueue(task)  # line 218
        ...
        return  # line 249
except Exception:  # line 250
    logger.error("多轮编辑检测异常", exc_info=True)  # ← 只记日志，不退额度
    # 执行继续回退到 line 254 _extract_prompt
```

虽然实际触发概率较低（内部的 download/upload/enqueue 都有各自的异常处理），但一旦触发后果严重：用户额度被扣但任务未创建。

**修复建议**：在 `except` 块中增加 `credits.refund_one` 调用，或用更精确的异常类型。

---

### B5. _truncate_for_caption 在 html.escape 之后截断，可能切断 HTML 实体

- **文件**：[services/queue.py](services/queue.py)
- **位置**：第 391、435、439 行
- **严重度**：🟡 中

**问题描述**：

`_truncate_for_caption(text, max_chars)` 在 `html.escape()` 调用**之后**执行。如果已转义的文本中包含 `&amp;`、`&lt;`、`&gt;` 等 HTML 实体，截断可能切在实体中间，产生 `&am…` 这样的非法 HTML，Telegram API 会返回 `BadRequest` 错误。

**受影响的三处调用**：

```python
# queue.py:391 - _build_sd_info
prompt_text = _truncate_for_caption(html.escape(f"{DEFAULT_PROMPT_PREFIX} {translated}"))

# queue.py:435-437 - _build_comfy_info
actual = html.escape(translated)
info_parts.insert(0, f"<b>Prompt:</b> {_truncate_for_caption(actual)}")

# queue.py:439-440 - _build_comfy_info
info_parts.insert(0, f"<b>原始 Prompt:</b> {_truncate_for_caption(html.escape(task.prompt), 350)}")
```

**修复建议**：调换顺序——先截断，后 escape：`html.escape(_truncate_for_caption(text))`。

---

### B6. _build_comfy_info 当 comfy_prompt 覆盖生效时显示错误的 prompt

- **文件**：[services/queue.py](services/queue.py)
- **位置**：第 404–441 行（cross-ref [services/comfy_api.py](services/comfy_api.py) 第 106 行）
- **严重度**：🟡 中

**问题描述**：

`_build_payload` (comfy_api.py:106) 使用 `settings.get("comfy_prompt", "") or prompt` —— 如果用户配置了 `comfy_prompt`（自定义固定 prompt），它会**完全替换**传入的 prompt。但 `_build_comfy_info` (queue.py:435-439) 始终显示 `translated`（翻译后的用户输入）作为 "Prompt" 或 "实际 Prompt"。用户在 caption 中看到的是自己的输入，但模型实际收到的是 `comfy_prompt`，两者可能完全不同。

**修复建议**：在 info caption 中增加 comfy_prompt 覆盖的提示，例如 `"<b>Prompt (覆盖):</b> {comfy_prompt}"`。

---

### B7. 硬编码的配置回退值使用 dict 直接索引，`.get()` 回退机制无效

- **文件**：[services/comfy_api.py](services/comfy_api.py) 第 125、131 行；[services/queue.py](services/queue.py) 第 412、414 行
- **严重度**：🟢 低（仅在配置被人为破坏时触发）

**问题描述**：

Python 函数参数的**立即求值**特性导致 `.get()` 的回退值在调用前就被计算：

```python
# Python 会先求值 COMFY_VIDEO_ORIENTATIONS["portrait"]，
# 然后才调用 .get()。如果 "portrait" 键不存在，直接 KeyError
cfg = COMFY_VIDEO_ORIENTATIONS.get(orient, COMFY_VIDEO_ORIENTATIONS["portrait"])
```

四处受影响位置：

| 文件 | 行号 | 代码 |
|------|------|------|
| comfy_api.py | 125 | `COMFY_VIDEO_ORIENTATIONS.get(orient, COMFY_VIDEO_ORIENTATIONS["portrait"])` |
| comfy_api.py | 131 | `COMFY_VIDEO_FRAMES_PRESETS.get(frames_key, COMFY_VIDEO_FRAMES_PRESETS["81"])` |
| queue.py | 412 | `COMFY_VIDEO_ORIENTATIONS.get(orient, COMFY_VIDEO_ORIENTATIONS["portrait"])` |
| queue.py | 414 | `COMFY_VIDEO_FRAMES_PRESETS.get(frames_key, COMFY_VIDEO_FRAMES_PRESETS["81"])` |

**修复建议**：使用字面量或提前提取默认值：

```python
_default_orient = COMFY_VIDEO_ORIENTATIONS.get("portrait", {"width": 480, "height": 848, "label": "竖版"})
cfg = COMFY_VIDEO_ORIENTATIONS.get(orient, _default_orient)
```

---

## 🟡 状态管理 / UX Bug

### B8. 误导性错误提示："请先发送首帧图片"（首帧已上传）

- **文件**：[handlers/generation.py](handlers/generation.py)
- **位置**：第 264 行
- **严重度**：🟡 中

**问题描述**：

用户在 firstlast-video 模式下已上传首帧（`_firstlast_start_frame` 有值），尚未发尾帧，发送了文字而非图片。`handle_text` line 76 检测不到尾帧（`end_frame` 为 None），回退到 line 264，输出：

> "当前工作流是首尾帧生视频模式，请先发送首帧图片。"

但首帧早已上传！`handle_photo` line 587 刚在上一步告诉用户"请发送尾帧图片"，而 `handle_text` 给出矛盾的指引。

**修复建议**：在 img2img 拦截检查中增加 firstlast 单帧状态检测，给出正确指引。

---

### B9. handle_photo 缺少 ahead > 0 的队列位置反馈

- **文件**：[handlers/generation.py](handlers/generation.py)
- **位置**：第 722–731 行
- **严重度**：🟢 低

**问题描述**：

`handle_photo` 入队后仅处理 `ahead == 0` 的情况（显示"正在准备生成..."），缺少 `ahead > 0` 的 else 分支。对比 `handle_text` (lines 334–359) 同时处理了两种情况。当 photo 任务排队时，用户只看到"正在上传图片..."的状态消息卡住，看不到排队位置。

**修复建议**：补充 `else` 分支显示"前方还有 N 个任务"。

---

### B10. _clean_caption 未处理 "channel" 聊天类型

- **文件**：[handlers/generation.py](handlers/generation.py)
- **位置**：第 49 行
- **严重度**：🟢 低

**问题描述**：

`_clean_caption` line 49 的 chat type 检查仅包含 `("group", "supergroup")`，与 `_extract_prompt` line 24 的 `not in ("group", "supergroup", "channel")` 不一致。在 channel-linked 讨论组中 @bot 提及不会被清除。

**修复建议**：`_clean_caption` 中的 chat type 检查与 `_extract_prompt` 保持一致。

---

## 🔵 代码质量 / 维护问题

### Q1. auto_edit 检测逻辑在 handle_text 和 handle_photo 中重复

- **文件**：[handlers/generation.py](handlers/generation.py)
- **位置**：第 108–115 行（handle_text）、第 531–537 行（handle_photo）
- **严重度**：🔵 维护

**问题描述**：两个 handler 中 `is_reply_to_bot` / `auto_edit` 的检测逻辑结构相同（检查 `reply_to_message`、`from_user`、`from_user.id == bot.id`），仅末尾差异（handle_text 检查 `.photo`，handle_photo 检查 `.caption`）。约 130 行编排代码在两处重复。

**修复建议**：提取 `_detect_auto_edit(message, context) -> bool` 函数。

---

### Q2. Telegram 图片下载+上传逻辑重复 4 次

- **文件**：[handlers/generation.py](handlers/generation.py)
- **位置**：第 577–581、591–596、163–168+186、648–676 行
- **严重度**：🔵 维护

**问题描述**：`get_file() → download_to_memory → seek(0) → upload_image()` 模式在首帧上传、尾帧上传、auto_edit 路径、普通 photo 路径中重复出现，代码几乎相同。

**修复建议**：提取 `_download_and_upload_photo(message) -> str` 函数。

---

### Q3. auto_edit 任务设置覆写代码重复

- **文件**：[handlers/generation.py](handlers/generation.py)
- **位置**：第 196–201 行（handle_text）、第 682–687 行（handle_photo）
- **严重度**：🔵 维护

**问题描述**：两处代码完全相同——设置 backend/comfy_workflow/comfy_model 为 qwen-image-edit 的值。

**修复建议**：提取 `_apply_auto_edit_settings(settings, task_settings)` 或合并到 Q1 的建议中。

---

### Q4. comfy_output.kind 字段从未用于媒体分发

- **文件**：[services/queue.py](services/queue.py)
- **位置**：第 247 行
- **严重度**：🔵 设计

**问题描述**：`_detect_output_kind()` 根据文件扩展名正确区分 image/video/gif/file，但 `queue.py` 的分发逻辑（line 247）使用 `wf_config.get("output_type")` 而非 `comfy_output.kind`。后果：

1. `kind` 字段成为死代码
2. GIF 输出（`kind="gif"`）无专用分发路径 → 被 `send_photo` 发送，仅显示静态第一帧
3. 配置与实际输出不一致时行为不可预测

**修复建议**：使用 `comfy_output.kind` 主导分发，`wf_config["output_type"]` 作为 fallback。

---

### Q5. prompt_prefix 字符串拼接依赖配置中的尾部空格

- **文件**：[services/comfy_api.py](services/comfy_api.py)
- **位置**：第 108–110 行
- **严重度**：🔵 设计

**问题描述**：

```python
# comfy_api.py:108-110
prefix = wf.get("prompt_prefix", "")
if prefix:
    final_prompt = prefix + final_prompt  # ← 无分隔符，依赖 prefix 结尾空格
```

SDXL 配置目前以 `"blum effect, "` 结尾（包含逗号+空格），但无代码保障。如果未来工作流的 `prompt_prefix` 缺少尾部空格，输出将变成 `"high quality,a cat"`。

**修复建议**：自动添加分隔符或对 prefix 做规范化处理。

---

## 统计汇总

| 类别 | 数量 | ID |
|------|------|-----|
| 🔴 高严重度 Bug | 3 | B1, B2, B3 |
| 🟡 中严重度 Bug | 4 | B4, B5, B6, B8 |
| 🟢 低严重度 Bug | 3 | B7, B9, B10 |
| 🔵 代码质量 | 5 | Q1–Q5 |
| **合计** | **15** | |

---

## 修复优先级建议

1. **立即修复**（数据丢失风险）：B1、B2、B3 — firstlast-video 状态管理
2. **尽快修复**（可能影响用户体验）：B4（额度丢失）、B5（HTML 截断）、B6（prompt 显示）、B8（误导提示）
3. **计划修复**（低风险）：B7、B9、B10
4. **重构建议**：Q1–Q5
