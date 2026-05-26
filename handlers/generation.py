import copy
import logging

from telegram.ext import MessageHandler, CommandHandler, filters

from services.queue import GenerationTask
from handlers.settings import _ensure_settings, _save_settings, _settings_menu

logger = logging.getLogger(__name__)


async def handle_text(update, context):
    message = update.message
    text = message.text.strip()

    # 种子输入处理
    if context.user_data.get("_waiting_seed"):
        await _handle_seed_input(update, context)
        return

    if len(text) > 1000:
        await message.reply_text("提示词过长，请控制在 1000 字以内。")
        return

    user_id = update.effective_user.id
    settings = _ensure_settings(context, user_id)

    try:
        status_msg = await message.reply_text("准备中...")
        status_id = status_msg.message_id
    except Exception:
        logger.warning("创建状态消息失败，任务将继续执行")
        status_id = None

    task = GenerationTask(
        user_id=user_id,
        chat_id=update.effective_chat.id,
        prompt=text,
        settings=copy.deepcopy(settings),
        status_message_id=status_id,
        original_message_id=message.message_id,
    )

    queue = context.bot_data["queue"]
    ahead = await queue.enqueue(task)

    if ahead == 0:
        try:
            if status_id is not None:
                await context.bot.edit_message_text(
                    "正在准备生成...",
                    chat_id=update.effective_chat.id,
                    message_id=status_id,
                )
        except Exception:
            pass
    else:
        try:
            if status_id is not None:
                await context.bot.edit_message_text(
                    f"已加入队列，前方还有 {ahead} 个任务",
                    chat_id=update.effective_chat.id,
                    message_id=status_id,
                )
        except Exception:
            pass


async def _handle_seed_input(update, context):
    user_id = update.effective_user.id
    settings = _ensure_settings(context, user_id)
    try:
        seed = int(update.message.text.strip())
        settings["seed"] = seed
    except ValueError:
        await update.message.reply_text("请输入有效的数字。发送 /cancel 取消。")
        return

    context.user_data["_waiting_seed"] = False
    _save_settings(context, user_id)
    label = "随机" if seed == -1 else str(seed)
    await update.message.reply_text(f"种子已设为: {label}")
    txt, markup = _settings_menu(settings)
    await update.message.reply_text(txt, reply_markup=markup, parse_mode="HTML")


async def handle_cancel(update, context):
    if context.user_data.get("_waiting_seed"):
        context.user_data["_waiting_seed"] = False
        user_id = update.effective_user.id
        settings = _ensure_settings(context, user_id)
        await update.message.reply_text("已取消。")
        txt, markup = _settings_menu(settings)
        await update.message.reply_text(txt, reply_markup=markup, parse_mode="HTML")
    else:
        await update.message.reply_text("当前没有需要取消的操作。")


def get_handlers() -> list:
    return [
        CommandHandler("cancel", handle_cancel),
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text),
    ]
