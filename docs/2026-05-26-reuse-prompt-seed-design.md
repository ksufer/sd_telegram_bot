# 生成图片菜单增强：重用提示词与种子

## 需求

生成图片后的菜单增加三个按钮：
1. **用本图提示词** — 发送提示词原文，用户可复制→修改→发送
2. **用本图种子** — 将种子设为本次生成的种子值
3. **🎲（随机）** — 一键将种子置为 -1

## 核心挑战

Telegram `callback_data` 限制 64 字节，提示词最长可达 1000+ 字符，无法直接嵌入。

## 方案：内存缓存

在 `app.bot_data["_gen_context"]` 中维护生成上下文缓存，key 为短 ID（UUID 前 8 位）。

```
生成完成 → 存缓存 {id: {prompt, seed, user_id}}
          → 图片菜单 button callback_data="reuse_prompt_<id>"
用户点击 → handler 通过 id 查缓存 → 执行对应操作
```

## 菜单布局

图片下方的菜单从 `_main_menu()` 改为 `_generation_menu(context_id)`：

```
[ 参数设置  ] [ 关闭菜单 ]
[ 用本图提示词 ] [ 用本图种子 ] [ 🎲 ]
```

## 按钮行为

| 按钮 | callback_data | 行为 |
|------|--------------|------|
| 用本图提示词 | `reuse_prompt_<id>` | 从缓存取 prompt → 回复纯文本消息（提示词原文） |
| 用本图种子 | `reuse_seed_<id>` | 从缓存取 seed → 更新 `settings["seed"]` → 提示"种子已设为 xxx" |
| 🎲 | `random_seed` | `settings["seed"] = -1` → `_save_settings()` → 提示"种子已设为随机" |

## 涉及文件

| 文件 | 变更 |
|------|------|
| `services/queue.py` | `_process_task` 中：生成 context_id → 存缓存 → 改用 `_generation_menu()` |
| `handlers/settings.py` | 新增 `_generation_menu()` 菜单构建、`reuse_prompt/reuse_seed/random_seed` 回调、注册 handler |

## 数据流

```
_generation_menu(context_id) 返回键盘:
  [参数设置]/[关闭菜单]  → 现有 handler，不变
  [用本图提示词]         → CallbackQueryHandler(pattern="^reuse_prompt_")
  [用本图种子]            → CallbackQueryHandler(pattern="^reuse_seed_")
  [🎲]                   → CallbackQueryHandler(pattern="^random_seed$")

reuse_prompt handler:
  context_id = data.replace("reuse_prompt_", "")
  ctx = app.bot_data["_gen_context"].get(context_id)
  → message.reply_text(ctx["prompt"])

reuse_seed handler:
  context_id = data.replace("reuse_seed_", "")
  ctx = app.bot_data["_gen_context"].get(context_id)
  → settings["seed"] = ctx["seed"]
  → _save_settings()
  → 提示"种子已设为 xxx"

random_seed handler:
  → settings["seed"] = -1
  → _save_settings()
  → 提示"种子已设为随机"
```
