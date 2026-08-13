"""Ollama 图片反推提示词 — 主菜单「🔍 反推提示词」。

入口：主菜单按钮 → 等待用户发图（_waiting_input="rev_prompt"）→ 扣 1 额度 →
入队（backend="ollama"）→ worker 串行执行（先卸载 ComfyUI 模型防显存冲突，
再调用本地 Ollama 视觉模型反推）。输出 SD 标签词 + Krea 2 句子版两种格式供复制。
"""

import copy
import io
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler

from handlers import auth_callback
from handlers.common import get_user_id, reply_menu, safe_answer
from handlers.generation import (
    _check_and_charge_credit,
    _clear_firstlast_state,
    _create_status_message,
    _download_tg_photo,
    _enqueue_and_notify,
)
from services import credits
from services.queue import GenerationTask

logger = logging.getLogger(__name__)


# ═══ 菜单入口 ═══

async def rev_prompt_menu(update, context):
    """主菜单按钮：进入反推等待状态（与其他等待状态互斥）。"""
    query = update.callback_query
    await safe_answer(query)

    if context.user_data is None:
        await safe_answer(query, "会话状态不可用，请重新发送 /start。", show_alert=True)
        return

    # 清除 firstlast / pipeline 等互斥状态
    _clear_firstlast_state(context.user_data)
    context.user_data.pop("_pipe_collect", None)
    context.user_data.pop("_pipe_edit_step", None)
    context.user_data.pop("_pipe_ready", None)
    context.user_data["_waiting_input"] = "rev_prompt"

    text = (
        "<b>🔍 反推提示词</b>\n\n"
        "请发送一张图片，我会反推出两种提示词（各可点按复制）：\n"
        "• <b>SD 标签词</b>：逗号分隔的标签形式\n"
        "• <b>Krea 2 句子版</b>：连贯句子的详细描述\n\n"
        "消耗 1 额度，发送 /cancel 可取消。"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("关闭", callback_data="close_menu")],
    ])
    await reply_menu(query, text, keyboard)


# ═══ 发图处理（由 generation.handle_photo 分流调用）═══

async def handle_rev_photo(update, context):
    """等待反推期间收到图片：下载 → 扣额度 → 入队。"""
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    user_id = user.id if user else (message.sender_chat.id if message.sender_chat else 0)

    # 下载图片（失败不扣费）
    try:
        image_bytes = await _download_tg_photo(message.photo[-1])
    except Exception as e:
        logger.warning("反推图片下载失败: %s", e)
        await message.reply_text(f"图片下载失败: {e}")
        return

    # 额度检查 + 扣减
    ok, credit_charged, err = await _check_and_charge_credit(user_id)
    if not ok:
        await message.reply_text(err)
        return

    status_id = await _create_status_message(message, "正在反推提示词...")

    # 反推任务走独立处理路径，settings 只需 backend + 图片数据
    task_settings = copy.deepcopy(context.user_data.get("settings", {})) if context.user_data else {}
    task_settings["backend"] = "ollama"
    task_settings["_rev_image"] = image_bytes.getvalue()

    task = GenerationTask(
        user_id=user_id,
        chat_id=chat.id,
        prompt="",
        settings=task_settings,
        status_message_id=status_id,
        original_message_id=message.message_id,
        reply_to_message_id=message.message_id if chat.type in ("group", "supergroup") else None,
        credit_charged=credit_charged,
    )

    try:
        queue = context.bot_data["queue"]
        await _enqueue_and_notify(task, queue, context, chat.id, status_id)
    except Exception:
        logger.error("用户 %s 反推入队失败", user_id, exc_info=True)
        if credit_charged:
            await credits.refund_one(user_id)
        await message.reply_text("任务提交失败，请稍后重试。")
        return

    # 入队成功后清除等待状态
    if context.user_data is not None:
        context.user_data["_waiting_input"] = None


# ═══ 文字处理（由 generation.handle_text 分发调用）═══

async def handle_rev_text(update, context):
    """等待反推期间收到文字 → 提示发图（避免误入生成流程）。"""
    await update.message.reply_text(
        "🔍 反推提示词等待图片：请发送一张图片，或 /cancel 取消。"
    )


# ═══ Handler 注册 ═══

def get_handlers() -> list:
    return [
        CallbackQueryHandler(auth_callback(rev_prompt_menu), pattern=r"^rev_prompt$"),
    ]
