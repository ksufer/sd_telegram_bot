"""Pipeline 动态编排 — 把多个工作流串联执行，上一步的输出图自动作为下一步的输入。

入口：主菜单「⛓ Pipeline」按钮。
- 步骤列表持久化在 settings["pipeline_steps"]，元素为 {"key", "prompt"}：
  prompt 是该步的预设提示词（空 = 运行时收集）。旧格式（纯 key 字符串）读取时自动迁移。
- 每步提示词独立：编辑步的提示词与文生图步必然不同。
- 双图编辑步（如 qwen-2pic-edit / f2k-2pic-edit 换装）：pipeline 产出图注入第 1 个
  角色（主图），运行时向用户收集的参考图注入第 2 个角色。
- 运行：pipe:run 构建收集计划 user_data["_pipe_collect"]，按步骤顺序依次收集
  缺失的 prompt / 首步起始图 / 双图步参考图，集齐后首步任务入队。
- 连跑：每步是独立任务（独立扣 1 额度、独立状态消息），上一步发送成功后由
  services/queue.py 的 _maybe_chain_pipeline 回注输出图并组下一步任务。
- 步骤合法性（v1）：仅图片输出的 ComfyUI 工作流；首步为文生图或单图图生图；
  后续步为单图或双角色图生图；视频工作流不可作为步骤。
"""

import copy
import html
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler

from config import WORKFLOW_REGISTRY, COMFY_WORKFLOWS
from handlers import auth_callback
from handlers.common import safe_answer, reply_menu, get_user_id, refresh_workflows
from handlers.settings import _ensure_settings, _save_settings
from handlers.generation import (
    _extract_prompt,
    _check_and_charge_credit,
    _create_status_message,
    _download_tg_photo,
    _upload_to_comfy,
    _enqueue_and_notify,
)
from services import credits
from services.queue import GenerationTask

logger = logging.getLogger(__name__)


# ═══ 步骤合法性 ═══

def _is_dual_step(key: str) -> bool:
    """双角色图生图（如 qwen-2pic-edit：image1=主图，image2=参考图）。"""
    cfg = COMFY_WORKFLOWS.get(key)
    if not cfg or cfg.get("output_type") == "video":
        return False
    nodes = cfg.get("load_image_nodes")
    return bool(cfg.get("is_img2img")) and bool(nodes) and len(nodes) == 2


def _is_first_step_eligible(key: str) -> bool:
    """首步：图片输出的文生图，或单图图生图。"""
    cfg = COMFY_WORKFLOWS.get(key)
    if not cfg or cfg.get("output_type") == "video":
        return False
    return not cfg.get("is_img2img") or bool(cfg.get("load_image_node"))


def _is_follow_step_eligible(key: str) -> bool:
    """后续步：单图或双角色图生图（接收上一步输出图）。"""
    cfg = COMFY_WORKFLOWS.get(key)
    if not cfg or cfg.get("output_type") == "video":
        return False
    if not cfg.get("is_img2img"):
        return False
    return bool(cfg.get("load_image_node")) or _is_dual_step(key)


def _registry_entry(key: str) -> dict | None:
    return next((w for w in WORKFLOW_REGISTRY if w["key"] == key), None)


def _step_label(key: str) -> str:
    wf = _registry_entry(key)
    return f"{wf['emoji']} {wf['label']}" if wf else f"⚠️ {key}（已失效）"


def _get_steps(settings: dict) -> list:
    """读取步骤列表（归一化为 {"key", "prompt"} dict；返回拷贝，不写共享对象）。"""
    return [
        {"key": s, "prompt": ""} if isinstance(s, str) else dict(s)
        for s in (settings.get("pipeline_steps") or [])
    ]


def _validate_run(steps: list) -> str | None:
    """运行前校验，返回错误文案；None 表示可运行。"""
    if len(steps) < 2:
        return "至少需要 2 个步骤才能运行，请先添加步骤。"
    if not _is_first_step_eligible(steps[0]["key"]):
        return f"首步「{steps[0]['key']}」已不可用（仅支持图片输出的文生图/单图图生图），请调整编排。"
    for s in steps[1:]:
        if not _is_follow_step_eligible(s["key"]):
            return f"步骤「{s['key']}」不是可用的图生图工作流（单图/双图），请调整编排。"
    return None


def pipe_waiting_image(user_data: dict | None) -> bool:
    """当前是否处于 Pipeline 收集流程的图片等待项（供 handle_text/photo 分流）。"""
    collect = (user_data or {}).get("_pipe_collect")
    if not collect:
        return False
    items = collect.get("items") or []
    pos = collect.get("pos", 0)
    return pos < len(items) and items[pos][0] in ("image", "ref")


# ═══ 编排菜单 ═══

def _pipeline_menu(settings: dict) -> tuple[str, InlineKeyboardMarkup]:
    steps = _get_steps(settings)
    lines = [
        "<b>⛓ Pipeline 编排</b>\n",
        "把多个工作流串联执行：上一步的输出图自动作为下一步的输入。"
        "每步提示词独立（可预设，缺省运行时询问）。\n",
    ]
    if steps:
        lines.append(f"<b>当前步骤（{len(steps)} 步）：</b>")
        for i, s in enumerate(steps):
            role = "起始" if i == 0 else "接收上一步输出"
            dual = "·需参考图" if i > 0 and _is_dual_step(s["key"]) else ""
            lines.append(f"{i + 1}. {html.escape(_step_label(s['key']))}（{role}{dual}）")
            pmt = s.get("prompt") or ""
            preview = html.escape(pmt if len(pmt) <= 30 else pmt[:30] + "…")
            lines.append(f"    💬 {preview if pmt else '<i>运行时输入</i>'}")
    else:
        lines.append("尚未添加步骤。点击「➕ 添加步骤」开始编排（至少 2 步才能运行）。")

    keyboard = []
    for i in range(len(steps)):
        keyboard.append([
            InlineKeyboardButton(f"⬆️{i + 1}", callback_data=f"pipe:up:{i}"),
            InlineKeyboardButton(f"⬇️{i + 1}", callback_data=f"pipe:down:{i}"),
            InlineKeyboardButton(f"✏️{i + 1}", callback_data=f"pipe:pp:{i}"),
            InlineKeyboardButton(f"❌{i + 1}", callback_data=f"pipe:del:{i}"),
        ])
    keyboard.append([InlineKeyboardButton("➕ 添加步骤", callback_data="pipe:add")])
    if len(steps) >= 2:
        keyboard.append([InlineKeyboardButton("▶️ 运行", callback_data="pipe:run")])
    keyboard.append([
        InlineKeyboardButton("🗑 清空", callback_data="pipe:clear"),
        InlineKeyboardButton("🔙 返回主菜单", callback_data="main_menu"),
    ])
    return "\n".join(lines), InlineKeyboardMarkup(keyboard)


def _add_step_menu(settings: dict) -> tuple[str, InlineKeyboardMarkup]:
    steps = _get_steps(settings)
    position = len(steps)
    eligible = _is_first_step_eligible if position == 0 else _is_follow_step_eligible
    candidates = [wf for wf in WORKFLOW_REGISTRY if eligible(wf["key"])]

    if position == 0:
        hint = "首步支持文生图或单图图生图工作流："
    else:
        hint = "后续步支持单图/双图图生图工作流（接收上一步的输出图）："
    text = f"<b>➕ 添加第 {position + 1} 步</b>\n\n{hint}"

    keyboard = [
        [InlineKeyboardButton(f"{wf['emoji']} {wf['label']}",
                              callback_data=f"pipe:add:{wf['key']}")]
        for wf in candidates
    ]
    if not candidates:
        text += "\n\n（当前没有可用的工作流）"
    keyboard.append([InlineKeyboardButton("🔙 返回编排", callback_data="pipe:menu")])
    return text, InlineKeyboardMarkup(keyboard)


# ═══ 菜单回调 ═══

async def pipe_menu(update, context):
    """编排菜单入口（顺便清理已失效的步骤并迁移旧格式）。"""
    query = update.callback_query
    await safe_answer(query)
    refresh_workflows()
    user_id = get_user_id(update)
    settings = _ensure_settings(context, user_id)
    steps = [s for s in _get_steps(settings) if s["key"] in COMFY_WORKFLOWS]
    if steps != (settings.get("pipeline_steps") or []):
        settings["pipeline_steps"] = steps
        _save_settings(context, user_id)
    text, markup = _pipeline_menu(settings)
    await reply_menu(query, text, markup)


async def pipe_add_menu(update, context):
    query = update.callback_query
    await safe_answer(query)
    refresh_workflows()
    settings = _ensure_settings(context, get_user_id(update))
    text, markup = _add_step_menu(settings)
    await reply_menu(query, text, markup)


async def pipe_add_step(update, context):
    query = update.callback_query
    key = query.data.split(":", 2)[2]
    user_id = get_user_id(update)
    settings = _ensure_settings(context, user_id)
    steps = _get_steps(settings)
    eligible = _is_first_step_eligible if not steps else _is_follow_step_eligible
    if not eligible(key):
        await safe_answer(query, "该工作流不能添加在此位置", show_alert=True)
        return
    settings["pipeline_steps"] = steps + [{"key": key, "prompt": ""}]
    _save_settings(context, user_id)
    await safe_answer(query, f"已添加第 {len(steps) + 1} 步")
    text, markup = _pipeline_menu(settings)
    await reply_menu(query, text, markup)


async def pipe_move(update, context):
    query = update.callback_query
    _, direction, idx_str = query.data.split(":")
    idx = int(idx_str)
    user_id = get_user_id(update)
    settings = _ensure_settings(context, user_id)
    steps = _get_steps(settings)
    new_idx = idx - 1 if direction == "up" else idx + 1
    if 0 <= idx < len(steps) and 0 <= new_idx < len(steps):
        steps[idx], steps[new_idx] = steps[new_idx], steps[idx]
        settings["pipeline_steps"] = steps
        _save_settings(context, user_id)
    await safe_answer(query)
    text, markup = _pipeline_menu(settings)
    await reply_menu(query, text, markup)


async def pipe_del(update, context):
    query = update.callback_query
    idx = int(query.data.split(":", 2)[2])
    user_id = get_user_id(update)
    settings = _ensure_settings(context, user_id)
    steps = _get_steps(settings)
    if 0 <= idx < len(steps):
        removed = steps.pop(idx)
        settings["pipeline_steps"] = steps
        _save_settings(context, user_id)
        await safe_answer(query, f"已删除 {_step_label(removed['key'])}")
    else:
        await safe_answer(query)
    text, markup = _pipeline_menu(settings)
    await reply_menu(query, text, markup)


async def pipe_clear(update, context):
    query = update.callback_query
    user_id = get_user_id(update)
    settings = _ensure_settings(context, user_id)
    settings["pipeline_steps"] = []
    _save_settings(context, user_id)
    await safe_answer(query, "已清空")
    text, markup = _pipeline_menu(settings)
    await reply_menu(query, text, markup)


async def pipe_prompt_edit(update, context):
    """「✏️」— 设置某步的预设提示词（由 handle_text 分发输入）。"""
    query = update.callback_query
    idx = int(query.data.split(":", 2)[2])
    user_id = get_user_id(update)
    settings = _ensure_settings(context, user_id)
    steps = _get_steps(settings)
    if not (0 <= idx < len(steps)):
        await safe_answer(query, "步骤不存在", show_alert=True)
        return
    context.user_data["_waiting_input"] = "pipe_step_prompt"
    context.user_data["_pipe_edit_step"] = idx
    await safe_answer(query)
    cur = steps[idx].get("prompt") or "<i>（无预设，运行时将询问）</i>"
    await reply_menu(
        query,
        f"<b>✏️ 第 {idx + 1} 步「{html.escape(_step_label(steps[idx]['key']))}」预设提示词</b>\n\n"
        f"当前：{html.escape(cur, quote=False) if steps[idx].get('prompt') else cur}\n\n"
        "请发送新的预设提示词（发送 <code>-</code> 清除预设，/cancel 取消）：",
        None,
    )


def _build_collect(steps: list, seed_images: dict | None = None) -> dict:
    """构建运行收集计划：缺预设 prompt 的步骤、首步起始图、双图步参考图。

    seed_images（会话缓存的上次图片 {step_idx: 文件名}）已覆盖的项不再收集。
    """
    items = []
    prompts = {}
    images = dict(seed_images or {})
    for i, s in enumerate(steps):
        if s.get("prompt"):
            prompts[i] = s["prompt"]
        else:
            items.append(("prompt", i))
        cfg = COMFY_WORKFLOWS.get(s["key"], {})
        if i == 0 and cfg.get("is_img2img"):
            if 0 not in images:
                items.append(("image", 0))
        elif i > 0 and _is_dual_step(s["key"]):
            if i not in images:
                items.append(("ref", i))
    return {"items": items, "pos": 0, "prompts": prompts, "images": images}


def _pipe_confirm_menu(settings: dict, collect: dict) -> tuple[str, InlineKeyboardMarkup]:
    """运行确认页：所有输入齐备（预设/上次沿用）时展示，一键重跑。"""
    steps = _get_steps(settings)
    lines = ["<b>⛓ 运行确认</b>\n"]
    for i, s in enumerate(steps):
        lines.append(f"{i + 1}. {html.escape(_step_label(s['key']))}")
        pmt = collect["prompts"].get(i) or s.get("prompt") or ""
        if pmt:
            lines.append(f"    💬 {html.escape(pmt if len(pmt) <= 40 else pmt[:40] + '…')}")
        if i in collect["images"]:
            kind = "起始图" if i == 0 else "参考图"
            lines.append(f"    🖼 {kind}：沿用上次")
    lines.append("\n改提示词：返回编排用 ✏️；换图片：点「🖼 重选图片」。")
    keyboard = [[InlineKeyboardButton("▶️ 开始", callback_data="pipe:go")]]
    if collect["images"]:
        keyboard.append([InlineKeyboardButton("🖼 重选图片", callback_data="pipe:reimg")])
    keyboard.append([InlineKeyboardButton("🔙 返回编排", callback_data="pipe:menu")])
    return "\n".join(lines), InlineKeyboardMarkup(keyboard)


async def _start_run(update, context):
    """▶️ 运行 / 🖼 重选图片 共用入口：输入齐备走确认页，否则进入收集流程。"""
    query = update.callback_query
    user_id = get_user_id(update)
    settings = _ensure_settings(context, user_id)
    steps = _get_steps(settings)
    err = _validate_run(steps)
    if err:
        await safe_answer(query, err, show_alert=True)
        return

    collect = _build_collect(steps, context.user_data.get("_pipe_last_images"))
    context.user_data.pop("_pipe_edit_step", None)
    if not collect["items"]:
        context.user_data["_pipe_ready"] = collect
        await safe_answer(query)
        text, markup = _pipe_confirm_menu(settings, collect)
        await reply_menu(query, text, markup)
        return

    context.user_data["_pipe_collect"] = collect
    await safe_answer(query)
    # 删除菜单消息，让收集对话保持清晰
    try:
        await query.message.delete()
    except Exception:
        pass
    await _advance_pipe_collect(update, context)


async def pipe_run(update, context):
    """「▶️ 运行」。"""
    await _start_run(update, context)


async def pipe_reimg(update, context):
    """「🖼 重选图片」— 清掉会话缓存的图片，重新走收集流程。"""
    context.user_data.pop("_pipe_ready", None)
    context.user_data.pop("_pipe_last_images", None)
    await _start_run(update, context)


async def pipe_go(update, context):
    """确认页「▶️ 开始」— 直接组首步任务入队。"""
    query = update.callback_query
    collect = context.user_data.pop("_pipe_ready", None)
    if not collect:
        await safe_answer(query, "状态已过期，请重新运行", show_alert=True)
        return
    user_id = get_user_id(update)
    settings = _ensure_settings(context, user_id)
    steps = _get_steps(settings)
    err = _validate_run(steps)
    if err:
        await safe_answer(query, err, show_alert=True)
        return
    await safe_answer(query)
    try:
        await query.message.delete()
    except Exception:
        pass
    await _finish_pipeline_start(query.message, context, user_id, settings, steps, collect)


# ═══ 收集流程（由 handle_text/handle_photo 顶部分发调用） ═══

async def _finish_pipeline_start(message, context, user_id: int, settings: dict,
                                 steps: list, collect: dict):
    """收集齐备（或确认页直达）：固化输入 → 扣费 → 组首步任务入队。

    固化便于重复执行：本次输入的 prompt 写入步骤预设；图片文件名缓存到
    user_data["_pipe_last_images"]（会话级），下次运行可一键沿用。
    """
    changed = settings.get("pipeline_steps") != steps
    for i, pmt in collect["prompts"].items():
        if 0 <= i < len(steps) and steps[i].get("prompt") != pmt:
            steps[i] = {**steps[i], "prompt": pmt}
            changed = True
    if changed:
        settings["pipeline_steps"] = steps
        _save_settings(context, user_id)
    if collect["images"]:
        context.user_data["_pipe_last_images"] = dict(collect["images"])

    ok, credit_charged, charge_err = await _check_and_charge_credit(user_id)
    if not ok:
        await message.reply_text(charge_err)
        return
    status_id = await _create_status_message(
        message, f"⛓ Pipeline 步骤 1/{len(steps)} 准备中...")
    await _enqueue_first_step(message, context, user_id, settings, steps,
                              collect, credit_charged, status_id)


async def _advance_pipe_collect(update, context):
    """推进收集：提示当前项；全部集齐则组首步任务入队。"""
    message = update.effective_message
    user_id = get_user_id(update)
    settings = _ensure_settings(context, user_id)
    steps = _get_steps(settings)
    collect = context.user_data["_pipe_collect"]
    items = collect["items"]

    if collect["pos"] >= len(items):
        context.user_data.pop("_pipe_collect", None)
        context.user_data["_waiting_input"] = None
        err = _validate_run(steps)
        if err:
            await message.reply_text(f"Pipeline 无法运行：{err}")
            return
        await _finish_pipeline_start(message, context, user_id, settings, steps, collect)
        return

    kind, step_i = items[collect["pos"]]
    label = _step_label(steps[step_i]["key"]) if step_i < len(steps) else f"第 {step_i + 1} 步"
    if kind == "prompt":
        context.user_data["_waiting_input"] = "pipe_collect"
        await message.reply_text(
            f"⛓ 请输入第 {step_i + 1} 步「{label}」的提示词（/cancel 取消）：")
    elif kind == "image":
        context.user_data["_waiting_input"] = None
        await message.reply_text(
            f"⛓ 首步「{label}」需要一张起始图片，请发送（/cancel 取消）：")
    else:  # ref
        context.user_data["_waiting_input"] = None
        await message.reply_text(
            f"⛓ 第 {step_i + 1} 步「{label}」需要一张参考图（如衣物/脸部参考），"
            "请发送（/cancel 取消）：")


async def handle_pipe_collect_text(update, context):
    """收到收集流程中的提示词文本。"""
    message = update.effective_message
    collect = context.user_data.get("_pipe_collect") if context.user_data else None
    if not collect or collect["pos"] >= len(collect["items"]):
        context.user_data["_waiting_input"] = None
        return
    prompt, for_me = _extract_prompt(message, context.bot.username)
    if not for_me:
        return
    kind, step_i = collect["items"][collect["pos"]]
    if kind != "prompt":
        return
    if not prompt:
        await message.reply_text(
            f"请输入第 {step_i + 1} 步的提示词文字，或发送 /cancel 取消。")
        return
    context.user_data["_waiting_input"] = None
    collect["prompts"][step_i] = prompt
    collect["pos"] += 1
    await _advance_pipe_collect(update, context)


async def handle_pipe_step_prompt_input(update, context):
    """收到某步预设提示词的编辑输入。"""
    message = update.effective_message
    prompt, for_me = _extract_prompt(message, context.bot.username)
    if not for_me:
        return
    idx = context.user_data.get("_pipe_edit_step")
    context.user_data["_waiting_input"] = None
    context.user_data.pop("_pipe_edit_step", None)

    user_id = get_user_id(update)
    settings = _ensure_settings(context, user_id)
    steps = _get_steps(settings)
    if idx is None or not (0 <= idx < len(steps)):
        await message.reply_text("步骤已变化，请重新操作。")
        return
    if not prompt:
        await message.reply_text("预设提示词未修改。")
    else:
        steps[idx] = {**steps[idx], "prompt": "" if prompt == "-" else prompt}
        settings["pipeline_steps"] = steps
        _save_settings(context, user_id)
        await message.reply_text(
            "✅ 已清除预设，运行时将询问。" if prompt == "-" else "✅ 预设提示词已保存。")
    text, markup = _pipeline_menu(settings)
    await message.reply_text(text, reply_markup=markup, parse_mode="HTML")


async def handle_pipe_image_input(update, context):
    """收到收集流程中的图片（首步起始图或双图步参考图）：上传 ComfyUI 后推进。"""
    message = update.effective_message
    collect = context.user_data.get("_pipe_collect") if context.user_data else None
    if not collect or collect["pos"] >= len(collect["items"]):
        return
    kind, step_i = collect["items"][collect["pos"]]
    if kind not in ("image", "ref"):
        return
    try:
        image_bytes = await _download_tg_photo(message.photo[-1])
        uploaded_name = await _upload_to_comfy(image_bytes.read())
    except Exception as e:
        # 尚未扣费，不推进 pos，允许用户重发
        logger.error("Pipeline 图片上传失败: %s", e)
        await message.reply_text(f"上传图片失败: {e}，请重发图片或 /cancel 取消。")
        return
    collect["images"][step_i] = uploaded_name
    collect["pos"] += 1
    await _advance_pipe_collect(update, context)


async def _enqueue_first_step(message, context, user_id: int, settings: dict,
                              steps: list, collect: dict,
                              credit_charged: bool, status_id: int | None):
    """组首步任务并入队（后续步由 queue 的 _maybe_chain_pipeline 自动接力）。

    模型策略：使用每步工作流自己的 default_model（缺失时弹出 comfy_model，
    让 resolve_model 走家族最新/列表回退），而不是用户全局 comfy_model。
    """
    first_key = steps[0]["key"]
    cfg0 = COMFY_WORKFLOWS.get(first_key, {})
    prompt = collect["prompts"].get(0, "")
    start_image = collect["images"].get(0)
    ref_images = {i: name for i, name in collect["images"].items() if i != 0}

    task_settings = copy.deepcopy(settings)
    task_settings["backend"] = "comfyui"
    task_settings["comfy_workflow"] = first_key
    default_model = cfg0.get("default_model")
    if default_model:
        task_settings["comfy_model"] = default_model
    else:
        task_settings.pop("comfy_model", None)
    task_settings.pop("_uploaded_images", None)
    if start_image:
        task_settings["_uploaded_image"] = start_image

    chat = message.chat
    task = GenerationTask(
        user_id=user_id,
        chat_id=chat.id,
        prompt=prompt,
        settings=task_settings,
        status_message_id=status_id,
        original_message_id=message.message_id,
        reply_to_message_id=message.message_id if chat.type in ("group", "supergroup") else None,
        credit_charged=credit_charged,
        pipeline={
            "steps": [s["key"] for s in steps],
            "idx": 0,
            "prompts": dict(collect["prompts"]),
            "ref_images": ref_images,
        },
    )
    try:
        queue = context.bot_data["queue"]
        await _enqueue_and_notify(task, queue, context, chat.id, status_id)
    except Exception:
        logger.error("用户 %s Pipeline 首步入队失败", user_id, exc_info=True)
        if credit_charged:
            await credits.refund_one(user_id)
        await message.reply_text("任务提交失败，请稍后重试。")


# ═══ Handler 注册 ═══

def get_handlers() -> list:
    return [
        CallbackQueryHandler(auth_callback(pipe_menu), pattern=r"^pipe:menu$"),
        CallbackQueryHandler(auth_callback(pipe_add_menu), pattern=r"^pipe:add$"),
        CallbackQueryHandler(auth_callback(pipe_add_step), pattern=r"^pipe:add:[a-z0-9-]+$"),
        CallbackQueryHandler(auth_callback(pipe_move), pattern=r"^pipe:(?:up|down):\d+$"),
        CallbackQueryHandler(auth_callback(pipe_prompt_edit), pattern=r"^pipe:pp:\d+$"),
        CallbackQueryHandler(auth_callback(pipe_del), pattern=r"^pipe:del:\d+$"),
        CallbackQueryHandler(auth_callback(pipe_clear), pattern=r"^pipe:clear$"),
        CallbackQueryHandler(auth_callback(pipe_run), pattern=r"^pipe:run$"),
        CallbackQueryHandler(auth_callback(pipe_go), pattern=r"^pipe:go$"),
        CallbackQueryHandler(auth_callback(pipe_reimg), pattern=r"^pipe:reimg$"),
    ]
