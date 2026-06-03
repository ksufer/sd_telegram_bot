# Qwen Image Edit 多轮对话编辑 — 设计文档

**日期**: 2026-06-03
**状态**: 设计完成，待实现

## Context

当前 `qwen-image-edit` 工作流已支持图片 + caption 编辑（2026-06-03 前一个改动）。但每次编辑都是独立的：用户发一张图片得到结果后，如果想继续修改，需要重新发送图片。

本功能实现多轮对话式编辑：用户回复 bot 的生成结果，输入文字指令，即可在上一轮结果基础上继续修改。

## 用户交互流程

```
第一轮（与现在一致）：
  用户发送图片 + caption "红发"
    → bot 返回编辑结果图片 A

多轮编辑（新增）：
  用户回复结果 A，输入文字 "蓝衣服"
    → bot 以图片 A 为底图，"蓝衣服" 为 prompt 编辑
    → 返回结果图片 B

  用户回复结果 B，输入文字 "微笑"
    → bot 以图片 B 为底图，"微笑" 为 prompt 编辑
    → 返回结果图片 C

换图重新开始：
  用户回复结果 C，附带新图片 + caption "金发"
    → bot 以新图片为底图（handle_photo 路由）
    → 返回结果图片 D

中断链条：
  用户直接发送新图片（非回复）
    → 等同于第一轮，替换底图
```

## 核心规则

- 仅在 `qwen-image-edit` 工作流下生效
- 回复 + 文字 → 文字作为修改 prompt，被回复消息中的图片作为底图
- 回复 + 新图片 → 走 `handle_photo`，替换底图（天然路由分离）
- 回复 + 无文字 → 提示用户输入修改指令
- 每轮 prompt 独立，不累积历史
- 非回复的普通图片消息 → 行为与现在完全一致

## 边界情况

| 场景 | 路由 | 行为 |
|------|------|------|
| 回复 bot 图片 + 文字 | `handle_text` — 多轮编辑 | 下载回复图片为底图，文字为 prompt |
| 回复 bot 图片 + 新图片 | `handle_photo` | 替换底图，新第一轮 |
| 回复 bot 图片 + 无文字 | `handle_text` — 多轮编辑 | 提示「请输入修改指令」 |
| 回复 bot 普通文字消息 | `handle_text` — img2img 拦截 | 提示发送图片或回复图片结果 |
| 回复别人的消息 | `handle_text` — img2img 拦截 | `from_user.id != bot.id` |
| 回复 bot 图片，非 qwen 工作流 | `handle_text` — img2img 拦截 | 限于 qwen-image-edit |
| 普通发图片（非回复） | `handle_photo` | 与现在完全一致 |

## 实现架构

### 修改文件

仅 **1 个文件**：`handlers/generation.py`

### 改动位置

在 `handle_text` 函数中，**img2img 拦截逻辑之前**（约第 90 行），插入回复检测代码。

### handle_text 新流程

```
1. 权限检查、群聊 @bot 检测（不变）
2. _waiting_input / _waiting_seed 状态处理（不变）
3. ★ 新增：检测「回复 bot 图片结果」→ 多轮编辑处理
4. img2img 拦截（保留，文案微调）
5. 额度检查、prompt 提取、入队（不变）
```

### 新增代码逻辑

```python
# 步骤 3：多轮编辑检测
if (settings.get("backend") == "comfyui"
    and wf_key == "qwen-image-edit"
    and wf_config.get("is_img2img")
    and message.reply_to_message
    and message.reply_to_message.from_user.id == context.bot.id
    and message.reply_to_message.photo):

    # 下载被回复消息中的图片
    replied_photo = message.reply_to_message.photo[-1]
    photo_file = await replied_photo.get_file()
    image_bytes = io.BytesIO()
    await photo_file.download_to_memory(image_bytes)
    image_bytes.seek(0)

    # 上传到 ComfyUI
    uploaded_name = await comfy_api.upload_image(image_bytes.read())

    # 获取修改 prompt
    prompt_text = message.text.strip()
    if not prompt_text:
        await message.reply_text("请在回复中附上修改指令，例如「把头发变红」。")
        return

    # 创建任务并入队（与 handle_photo 后半段一致）
    task_settings = copy.deepcopy(settings)
    task_settings["_uploaded_image"] = uploaded_name
    task = GenerationTask(
        prompt=prompt_text,
        settings=task_settings,
        user_id=user_id,
        chat_id=chat_id,
        message_id=message.message_id,
    )
    await queue.enqueue(task)
    return
```

### img2img 拦截文案微调

```python
# 步骤 4：拦截其他纯文字
if wf_config.get("is_img2img"):
    await message.reply_text(
        "当前工作流是图生图模式，请直接发送图片，"
        "或回复之前的生成结果并输入文字来继续修改。"
    )
    return
```

### 不需要改动的文件

| 文件 | 原因 |
|------|------|
| `handle_photo` | 回复 + 图片由 `filters.PHOTO` 优先路由，不影响 |
| `services/queue.py` | prompt 翻译逻辑已在上一个改动中修复（非空 prompt 不跳过翻译） |
| `services/comfy_api.py` | 现有 API 完全满足需求 |
| `config.py` | 硬编码 `wf_key == "qwen-image-edit"` 判断，无需新字段 |

## 与基础流程的隔离

两条消息路由在 python-telegram-bot 层面天然分离：

- `filters.PHOTO` → `handle_photo`：所有带图片的消息（包括回复 + 图片）
- `filters.TEXT & ~filters.COMMAND` → `handle_text`：纯文字消息（包括回复 + 文字）

因此：
- 回复 bot 图片 + 发新图片 → 始终进 `handle_photo`，不受影响
- 回复 bot 图片 + 发文字 → 进 `handle_text`，被新逻辑捕获
- 普通 @bot + 图片 → 进 `handle_photo`，不受影响

## 验证

1. 切换到 qwen-image-edit 工作流
2. 发送图片 + caption → 确认正常生成
3. 回复结果图片 + 文字指令 → 确认以结果图为底图重新生成
4. 回复结果图片（无文字）→ 确认收到「请输入修改指令」提示
5. 回复结果图片 + 新图片 → 确认替换底图
6. 直接发送文字（非回复）→ 确认收到 img2img 拦截提示
7. 切换到其他工作流 → 确认回复检测不触发
