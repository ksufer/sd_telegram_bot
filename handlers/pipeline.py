"""Pipeline 动态编排 — 把多个工作流串联执行，上一步的输出图自动作为下一步的输入。

入口：主菜单「⛓ Pipeline」按钮。
- 步骤列表持久化在 settings["pipeline_steps"]（工作流 key 有序列表）。
- 运行：收集一次 Prompt（首步为图生图时再收集一张起始图）→ 首步任务入队。
- 连跑：每步是独立任务（独立扣 1 额度、独立状态消息），上一步发送成功后由
  services/queue.py 的 _maybe_chain_pipeline 回注输出图并组下一步任务。
- 步骤合法性（v1）：仅图片输出的 ComfyUI 工作流；后续步必须是单图图生图
  （is_img2img + load_image_node），视频/双图工作流不可作为步骤。
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

def _is_first_step_eligible(key: str) -> bool:
    """首步：图片输出的文生图，或单图图生图。"""
    cfg = COMFY_WORKFLOWS.get(key)
    if not cfg or cfg.get("output_type") == "video":
        return False
    return not cfg.get("is_img2img") or bool(cfg.get("load_image_node"))


def _is_follow_step_eligible(key: str) -> bool:
    """后续步：仅单图图生图（输出图只能回注到一个 LoadImage 节点）。"""
    cfg = COMFY_WORKFLOWS.get(key)
    if not cfg or cfg.get("output_type") == "video":
        return False
    return bool(cfg.get("is_img2img")) and bool(cfg.get("load_image_node"))


def _registry_entry(key: str) -> dict | None:
    return next((w for w in WORKFLOW_REGISTRY if w["key"] == key), None)


def _step_label(key: str) -> str:
    wf = _registry_entry(key)
    return f"{wf['emoji']} {wf['label']}" if wf else f"⚠️ {key}（已失效）"


def _get_steps(settings: dict) -> list:
    """读取步骤列表（返回新 list，避免原地修改共享的默认对象）。"""
    return list(settings.get("pipeline_steps") or [])


def _validate_run(steps: list) -> str | None:
    """运行前校验，返回错误文案；None 表示可运行。"""
    if len(steps) < 2:
        return "至少需要 2 个步骤才能运行，请先添加步骤。"
    if not _is_first_step_eligible(steps[0]):
        return f"首步「{steps[0]}」已不可用（仅支持图片输出的文生图/单图图生图），请调整编排。"
    for key in steps[1:]:
        if not _is_follow_step_eligible(key):
            return f"步骤「{key}」不是可用的单图图生图工作流，请调整编排。"
    return None


# ═══ 编排菜单 ═══

def _pipeline_menu(settings: dict) -> tuple[str, InlineKeyboardMarkup]:
    steps = _get_steps(settings)
    lines = [
        "<b>⛓ Pipeline 编排</b>\n",
        "把多个工作流串联执行：上一步的输出图自动作为下一步的输入，全程共用同一个 Prompt。\n",
    ]
    if steps:
        lines.append(f"<b>当前步骤（{len(steps)} 步）：</b>")
        for i, key in enumerate(steps):
            role = "起始" if i == 0 else "接收上一步输出"
            lines.append(f"{i + 1}. {html.escape(_step_label(key))}（{role}）")
    else:
        lines.append("尚未添加步骤。点击「➕ 添加步骤」开始编排（至少 2 步才能运行）。")

    keyboard = []
    for i in range(len(steps)):
        keyboard.append([
            InlineKeyboardButton(f"⬆️{i + 1}", callback_data=f"pipe:up:{i}"),
            InlineKeyboardButton(f"⬇️{i + 1}", callback_data=f"pipe:down:{i}"),
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
        hint = "后续步仅支持单图图生图工作流（接收上一步的输出图）："
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
    """编排菜单入口（会顺便清理已失效的步骤）。"""
    query = update.callback_query
    await safe_answer(query)
    refresh_workflows()
    user_id = get_user_id(update)
    settings = _ensure_settings(context, user_id)
    steps = _get_steps(settings)
    pruned = [k for k in steps if k in COMFY_WORKFLOWS]
    if len(pruned) != len(steps):
        settings["pipeline_steps"] = pruned
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
    settings["pipeline_steps"] = steps + [key]
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
        await safe_answer(query, f"已删除 {_step_label(removed)}")
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


async def pipe_run(update, context):
    """「▶️ 运行」— 校验后进入 Prompt 收集（由 handle_text 顶部分发）。"""
    query = update.callback_query
    user_id = get_user_id(update)
    settings = _ensure_settings(context, user_id)
    err = _validate_run(_get_steps(settings))
    if err:
        await safe_answer(query, err, show_alert=True)
        return
    context.user_data["_waiting_input"] = "pipe_prompt"
    context.user_data.pop("_pipe_prompt", None)
    context.user_data.pop("_pipe_wait_image", None)
    await safe_answer(query)
    await reply_menu(
        query,
        "<b>⛓ Pipeline 运行</b>\n\n请发送本次 Pipeline 共用的 Prompt（/cancel 取消）：",
        None,
    )


# ═══ 输入收集（由 handlers/generation.py 顶部分发调用） ═══

async def handle_pipe_prompt_input(update, context):
    """收到 Pipeline 共用 Prompt：首步图生图时继续等图，否则直接入队首步。"""
    message = update.effective_message
    prompt, for_me = _extract_prompt(message, context.bot.username)
    if not for_me:
        return
    if not prompt:
        await message.reply_text("请输入 Pipeline 共用的 Prompt 文字，或发送 /cancel 取消。")
        return
    context.user_data["_waiting_input"] = None

    user_id = get_user_id(update)
    settings = _ensure_settings(context, user_id)
    steps = _get_steps(settings)
    err = _validate_run(steps)
    if err:
        await message.reply_text(f"Pipeline 无法运行：{err}")
        return

    cfg0 = COMFY_WORKFLOWS.get(steps[0], {})
    if cfg0.get("is_img2img"):
        context.user_data["_pipe_prompt"] = prompt
        context.user_data["_pipe_wait_image"] = True
        await message.reply_text(
            f"✅ Prompt 已记录。首步「{_step_label(steps[0])}」需要一张输入图片，"
            "请发送图片（/cancel 取消）。"
        )
        return

    ok, credit_charged, charge_err = await _check_and_charge_credit(user_id)
    if not ok:
        await message.reply_text(charge_err)
        return
    status_id = await _create_status_message(
        message, f"⛓ Pipeline 步骤 1/{len(steps)} 准备中...")
    await _enqueue_first_step(message, context, user_id, settings, steps, prompt,
                              uploaded_name=None,
                              credit_charged=credit_charged, status_id=status_id)


async def handle_pipe_image_input(update, context):
    """收到首步（图生图）的起始图片：上传 ComfyUI 后入队首步。"""
    message = update.effective_message
    context.user_data.pop("_pipe_wait_image", None)
    prompt = context.user_data.pop("_pipe_prompt", "") or ""

    user_id = get_user_id(update)
    settings = _ensure_settings(context, user_id)
    steps = _get_steps(settings)
    err = _validate_run(steps)
    if err:
        await message.reply_text(f"Pipeline 无法运行：{err}")
        return
    if not COMFY_WORKFLOWS.get(steps[0], {}).get("is_img2img"):
        await message.reply_text("首步已变为文生图工作流，请重新运行 Pipeline。")
        return

    ok, credit_charged, charge_err = await _check_and_charge_credit(user_id)
    if not ok:
        await message.reply_text(charge_err)
        return
    status_id = await _create_status_message(message, "正在上传图片...")
    try:
        image_bytes = await _download_tg_photo(message.photo[-1])
        uploaded_name = await _upload_to_comfy(image_bytes.read())
    except Exception as e:
        logger.error("Pipeline 首图上传失败: %s", e)
        if credit_charged:
            await credits.refund_one(user_id)
        await message.reply_text(f"上传图片失败: {e}")
        return
    await _enqueue_first_step(message, context, user_id, settings, steps, prompt,
                              uploaded_name=uploaded_name,
                              credit_charged=credit_charged, status_id=status_id)


async def _enqueue_first_step(message, context, user_id: int, settings: dict,
                              steps: list, prompt: str, uploaded_name: str | None,
                              credit_charged: bool, status_id: int | None):
    """组首步任务并入队（后续步由 queue 的 _maybe_chain_pipeline 自动接力）。

    模型策略：使用每步工作流自己的 default_model（缺失时弹出 comfy_model，
    让 resolve_model 走家族最新/列表回退），而不是用户全局 comfy_model。
    """
    cfg0 = COMFY_WORKFLOWS.get(steps[0], {})
    task_settings = copy.deepcopy(settings)
    task_settings["backend"] = "comfyui"
    task_settings["comfy_workflow"] = steps[0]
    default_model = cfg0.get("default_model")
    if default_model:
        task_settings["comfy_model"] = default_model
    else:
        task_settings.pop("comfy_model", None)
    task_settings.pop("_uploaded_images", None)
    if uploaded_name:
        task_settings["_uploaded_image"] = uploaded_name

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
        pipeline={"steps": list(steps), "idx": 0},
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
        CallbackQueryHandler(auth_callback(pipe_del), pattern=r"^pipe:del:\d+$"),
        CallbackQueryHandler(auth_callback(pipe_clear), pattern=r"^pipe:clear$"),
        CallbackQueryHandler(auth_callback(pipe_run), pattern=r"^pipe:run$"),
    ]
