import copy
import logging
import re

from telegram import MessageEntity, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import MessageHandler, CommandHandler, CallbackQueryHandler, filters

from config import ADMIN_USER_ID, DEFAULT_USER_SETTINGS
from services.network import retry_on_network_error
from services.queue import GenerationTask
from services import credits, comfy_api
from handlers.settings import _ensure_settings, _save_settings, _settings_menu
from handlers import is_authorized, _user_auth_filter

logger = logging.getLogger(__name__)


def _extract_prompt(message, bot_username: str) -> tuple[str | None, bool]:
    """群聊中提取提示词。返回 (prompt, is_for_me)。
    prompt 为 None 表示无需处理（非 @本 bot 或无提示词）。
    """
    if message.chat.type not in ("group", "supergroup", "channel"):
        return message.text.strip(), True

    # 检查是否 @了本 bot
    entities = message.parse_entities(types=[MessageEntity.MENTION])
    mentioned_bot = any(
        text.lower() == f"@{bot_username.lower()}"
        for text in entities.values()
    )
    if not mentioned_bot:
        return None, False

    # 用正则去掉 @bot_username（避免 UTF-16 offset 问题）
    pattern = re.compile(rf"@{re.escape(bot_username)}", re.IGNORECASE)
    prompt = pattern.sub("", message.text, count=1).strip()

    if not prompt:
        return None, True  # 只 @了 bot 没给提示词

    return prompt, True


async def handle_text(update, context):
    message = update.effective_message
    if message is None:
        return
    chat = update.effective_chat

    logger.info("DEBUG handle_text: chat_type=%s text=%.80s",
                chat.type, message.text if message.text else None)

    # 权限检查
    user = update.effective_user
    if not is_authorized(user.id if user else 0, chat.id, chat.type):
        return

    # 群聊 @bot 检测 + 提示词提取
    prompt, is_for_me = _extract_prompt(message, context.bot.username)
    if prompt is None:
        if is_for_me:
            await message.reply_text("请在 @Bot 后输入提示词。")
        return

    # 种子输入处理（channel 消息无 user_data，跳过）
    if context.user_data is not None and context.user_data.get("_waiting_seed"):
        await _handle_seed_input(update, context)
        return

    user = update.effective_user
    user_id = user.id if user else (message.sender_chat.id if message.sender_chat else 0)
    if context.user_data is not None:
        settings = _ensure_settings(context, user_id)
    else:
        settings = copy.deepcopy(DEFAULT_USER_SETTINGS)

    logger.info("DEBUG settings: user_id=%s translate=%s model=%s",
                user_id, settings.get("translate"), settings.get("model"))

    # 额度检查 + 扣减（管理员跳过）
    is_admin = ADMIN_USER_ID is not None and user_id == ADMIN_USER_ID
    credit_charged = False
    if not is_admin:
        remaining = await credits.get_remaining(user_id)
        if remaining <= 0:
            stats = await credits.get_stats(user_id)
            used = stats["used"]
            total = stats["total_quota"]
            await message.reply_text(
                f"额度已用完（已用 {used}/{total}），请联系管理员增加额度。"
            )
            return
        if not await credits.use_one(user_id):
            await message.reply_text("额度扣减失败，请稍后重试。")
            return
        credit_charged = True

    try:
        status_msg = await retry_on_network_error(
            lambda: message.reply_text("准备中..."),
            max_retries=2,
        )
        status_id = status_msg.message_id
    except Exception:
        logger.warning("创建状态消息失败，任务将继续执行")
        status_id = None

    reply_to = message.message_id if chat.type in ("group", "supergroup") else None

    task = GenerationTask(
        user_id=user_id,
        chat_id=chat.id,
        prompt=prompt,
        settings=copy.deepcopy(settings),
        status_message_id=status_id,
        original_message_id=message.message_id,
        reply_to_message_id=reply_to,
        credit_charged=credit_charged,
    )

    queue = context.bot_data["queue"]
    try:
        ahead = await queue.enqueue(task)
    except Exception:
        logger.error("用户 %s 入队失败", user_id, exc_info=True)
        if credit_charged:
            await credits.refund_one(user_id)
        await message.reply_text("任务提交失败，请稍后重试。")
        return

    if ahead == 0:
        try:
            if status_id is not None:
                await retry_on_network_error(
                    lambda: context.bot.edit_message_text(
                        "正在准备生成...",
                        chat_id=chat.id,
                        message_id=status_id,
                    ),
                    max_retries=2,
                )
        except Exception:
            pass
    else:
        try:
            if status_id is not None:
                await retry_on_network_error(
                    lambda: context.bot.edit_message_text(
                        f"已加入队列，前方还有 {ahead} 个任务",
                        chat_id=chat.id,
                        message_id=status_id,
                    ),
                    max_retries=2,
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
    if not is_authorized(
        update.effective_user.id,
        update.effective_chat.id,
        update.effective_chat.type,
    ):
        return

    if context.user_data.get("_waiting_seed"):
        context.user_data["_waiting_seed"] = False
        user_id = update.effective_user.id
        settings = _ensure_settings(context, user_id)
        await update.message.reply_text("已取消。")
        txt, markup = _settings_menu(settings)
        await update.message.reply_text(txt, reply_markup=markup, parse_mode="HTML")
    else:
        await update.message.reply_text("当前没有需要取消的操作。")


async def handle_mode(update, context):
    """发送后端选择菜单。"""
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if message is None or user is None or chat is None:
        return
    user_id = user.id if user else (message.sender_chat.id if message.sender_chat else 0)
    if not is_authorized(user_id, chat.id, chat.type):
        return

    settings = _ensure_settings(context, user_id)
    current = settings.get("backend", "sd")
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🖼️ SD WebUI", callback_data="mode:sd"),
            InlineKeyboardButton("🎨 ComfyUI", callback_data="mode:comfyui"),
        ]
    ])
    await message.reply_text(
        f"当前后端: {'SD WebUI' if current == 'sd' else 'ComfyUI'}\n请选择后端：",
        reply_markup=keyboard,
    )


async def handle_mode_callback(update, context):
    """处理后端切换。"""
    query = update.callback_query
    await query.answer()

    user = query.from_user
    chat = query.message.chat if query.message else None
    if user is None or chat is None:
        return
    if not is_authorized(user.id, chat.id, chat.type):
        await query.edit_message_text("⛔ 无使用权限")
        return

    backend = query.data.split(":", 1)[1]  # "sd" or "comfyui"

    if backend == "comfyui":
        try:
            comfy_api.validate_workflow()
        except Exception as e:
            await query.edit_message_text(
                f"ComfyUI 工作流不可用：{e}\n请联系管理员。"
            )
            return

    settings = _ensure_settings(context, user.id)
    settings["backend"] = backend
    _save_settings(context, user.id)

    label = "SD WebUI" if backend == "sd" else "ComfyUI"
    await query.edit_message_text(f"已切换为 {label} 模式。现在直接发送提示词即可生成图片。")


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
