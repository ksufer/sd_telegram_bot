import copy
import io
import logging
import re

from telegram import MessageEntity, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import MessageHandler, CommandHandler, CallbackQueryHandler, filters

from config import ADMIN_USER_ID, DEFAULT_USER_SETTINGS, COMFY_WORKFLOWS, COMFY_DEFAULT_WORKFLOW
from services.network import retry_on_network_error
from services.queue import GenerationTask
from services import credits, comfy_api
from handlers.settings import _ensure_settings, _save_settings, _settings_menu
from handlers import is_authorized, _user_auth_filter
from handlers.comfy_settings import _comfy_settings_menu as _comfy_settings_menu_shim

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


def _clean_caption(message, context) -> str:
    """提取并清理图片 caption（群聊中去除 @bot 提及）。"""
    caption = (message.caption or "").strip()
    if caption and message.chat.type in ("group", "supergroup"):
        bot_username = context.bot.username
        if bot_username:
            entities = message.parse_caption_entities(types=[MessageEntity.MENTION])
            for text in entities.values():
                if text.lower() == f"@{bot_username.lower()}":
                    caption = caption.replace(text, "", 1).strip()
                    break
    return caption


def _clear_firstlast_state(user_data: dict | None) -> None:
    """清除 firstlast-video 多步交互状态。"""
    if not user_data:
        return
    user_data.pop("_firstlast_start_frame", None)
    user_data.pop("_firstlast_end_frame", None)


async def handle_text(update, context):
    message = update.effective_message
    if message is None:
        return
    chat = update.effective_chat

    user = update.effective_user
    if not is_authorized(user.id if user else 0, chat.id, chat.type):
        return

    # 多图工作流等待文字描述（优先级高于其他 waiting_input）
    _firstlast_frames = None
    _firstlast_prompt = None
    if context.user_data is not None:
        start_frame = context.user_data.get("_firstlast_start_frame")
        end_frame = context.user_data.get("_firstlast_end_frame")
        if start_frame and end_frame:
            prompt_text = (message.text or "").strip()
            if not prompt_text:
                await message.reply_text("请输入编辑描述文字，或发送 /cancel 取消。")
                return
            # 从当前 workflow 配置读取角色名（如 firstlast: start/end, qwen-2pic: image1/image2）
            wf_key = context.user_data.get("settings", {}).get("comfy_workflow", "")
            wf_config = COMFY_WORKFLOWS.get(wf_key, {})
            roles = list(wf_config.get("load_image_nodes", {}).keys()) or ["start", "end"]
            _firstlast_frames = {roles[0]: start_frame, roles[1]: end_frame}
            _firstlast_prompt = prompt_text
            # 继续执行，不 return —— 让后续额度检查+任务创建流程处理
            # 注意：不在此处清除 user_data 状态，保留到 enqueue 成功后再清理（B1 修复）

    # 等待输入处理（种子等）— 必须在 _extract_prompt 之前，避免被拦截
    if context.user_data is not None:
        waiting = context.user_data.get("_waiting_input")
        if waiting == "comfy_prompt":
            await _handle_comfy_prompt_input(update, context)
            return
        elif waiting == "comfy_seed":
            await _handle_comfy_seed_input(update, context)
            return
        elif waiting == "sd_seed" or context.user_data.get("_waiting_seed"):
            await _handle_seed_input(update, context)
            return

    user = update.effective_user
    user_id = user.id if user else (message.sender_chat.id if message.sender_chat else 0)
    if context.user_data is not None:
        settings = _ensure_settings(context, user_id)
    else:
        settings = copy.deepcopy(DEFAULT_USER_SETTINGS)

    # 自动检测：回复机器人图片消息 + 文字 → 临时使用 qwen-image-edit
    is_reply_to_bot_image = (
        message.reply_to_message
        and message.reply_to_message.from_user
        and message.reply_to_message.from_user.id == context.bot.id
        and message.reply_to_message.photo
    )
    auto_edit = is_reply_to_bot_image and (message.text or "").strip()

    # auto_edit 触发时清除 firstlast 状态（B2 修复：用户意图已切换）
    if auto_edit and context.user_data:
        _clear_firstlast_state(context.user_data)

    # 多轮编辑检测：回复 bot 图片结果 + 文字指令（在 _extract_prompt 之前，无需 @bot）
    wf_key = settings.get("comfy_workflow", COMFY_DEFAULT_WORKFLOW)
    wf_config = COMFY_WORKFLOWS.get(wf_key, {})
    try:
        if (
            settings.get("backend") == "comfyui"
            and (wf_key == "qwen-image-edit" or auto_edit)
            and (wf_config.get("is_img2img") if wf_key == "qwen-image-edit" else True)
            and message.reply_to_message
            and message.reply_to_message.from_user
            and message.reply_to_message.from_user.id == context.bot.id
            and message.reply_to_message.photo
        ):
            prompt_text = (message.text or "").strip()
            if not prompt_text:
                await message.reply_text("请在回复中附上修改指令，例如「把头发变红」。")
                return

            # 额度检查
            is_admin = ADMIN_USER_ID is not None and user_id == ADMIN_USER_ID
            credit_charged = False
            if not is_admin:
                remaining = await credits.get_remaining(user_id)
                if remaining <= 0:
                    stats = await credits.get_stats(user_id)
                    await message.reply_text(
                        f"额度已用完（已用 {stats['used']}/{stats['total_quota']}），请联系管理员增加额度。"
                    )
                    return
                if not await credits.use_one(user_id):
                    await message.reply_text("额度扣减失败，请稍后重试。")
                    return
                credit_charged = True

            # 状态消息
            try:
                status_msg = await retry_on_network_error(
                    lambda: message.reply_text("正在上传图片..."),
                    max_retries=2,
                )
                status_id = status_msg.message_id
            except Exception:
                logger.warning("创建状态消息失败")
                status_id = None

            # 下载被回复消息中的图片
            try:
                replied_photo = message.reply_to_message.photo[-1]
                photo_file = await replied_photo.get_file()
                image_bytes = io.BytesIO()
                await photo_file.download_to_memory(image_bytes)
                image_bytes.seek(0)
            except Exception as e:
                logger.error("下载回复图片失败: %s", e)
                if credit_charged:
                    await credits.refund_one(user_id)
                await message.reply_text("下载图片失败，请稍后重试。")
                return

            # 上传到 ComfyUI
            try:
                if status_id is not None:
                    await retry_on_network_error(
                        lambda: context.bot.edit_message_text(
                            "正在上传图片到 ComfyUI...",
                            chat_id=chat.id, message_id=status_id,
                        ),
                        max_retries=2,
                    )
                uploaded_name = await comfy_api.upload_image(image_bytes.read())
            except Exception as e:
                logger.error("上传图片到 ComfyUI 失败: %s", e)
                if credit_charged:
                    await credits.refund_one(user_id)
                await message.reply_text(f"上传图片失败: {e}")
                return

            # 入队
            task_settings = copy.deepcopy(settings)
            if auto_edit:
                task_settings["backend"] = "comfyui"
                task_settings["comfy_workflow"] = "qwen-image-edit"
                qwen_wf = COMFY_WORKFLOWS.get("qwen-image-edit", {})
                if qwen_wf.get("default_model"):
                    task_settings["comfy_model"] = qwen_wf["default_model"]
            task_settings["_uploaded_image"] = uploaded_name

            reply_to = message.message_id if chat.type in ("group", "supergroup") else None
            task = GenerationTask(
                user_id=user_id,
                chat_id=chat.id,
                prompt=prompt_text,
                settings=task_settings,
                status_message_id=status_id,
                original_message_id=message.message_id,
                reply_to_message_id=reply_to,
                credit_charged=credit_charged,
            )

            queue = context.bot_data["queue"]
            try:
                ahead = await queue.enqueue(task)
            except Exception:
                logger.error("用户 %s 入队失败（多轮编辑）", user_id, exc_info=True)
                if credit_charged:
                    await credits.refund_one(user_id)
                await message.reply_text("任务提交失败，请稍后重试。")
                return

            # 队列状态提示
            if ahead == 0 and status_id is not None:
                try:
                    await retry_on_network_error(
                        lambda: context.bot.edit_message_text(
                            "正在准备生成...", chat_id=chat.id, message_id=status_id,
                        ),
                        max_retries=2,
                    )
                except Exception:
                    pass
            elif ahead > 0 and status_id is not None:
                try:
                    await retry_on_network_error(
                        lambda: context.bot.edit_message_text(
                            f"已加入队列，前方还有 {ahead} 个任务",
                            chat_id=chat.id, message_id=status_id,
                        ),
                        max_retries=2,
                    )
                except Exception:
                    pass

            return
    except Exception:
        logger.error("多轮编辑检测异常", exc_info=True)

    # 群聊 @bot 检测 + 提示词提取（多轮编辑未触发时才走到这里）
    prompt, is_for_me = _extract_prompt(message, context.bot.username)
    if prompt is None:
        if is_for_me:
            await message.reply_text("请在 @Bot 后输入提示词。")
        return

    # 图生图工作流拦截纯文字消息（多轮编辑未触发时）
    # firstlast-video: 已收到首尾帧时正常创建任务，无帧时提示发首帧
    if settings.get("backend") == "comfyui":
        if wf_config.get("is_img2img") and not _firstlast_frames:
            if wf_key == "firstlast-video":
                await message.reply_text("当前工作流是首尾帧生视频模式，请先发送首帧图片。")
            elif wf_key == "qwen-image-edit":
                await message.reply_text(
                    "当前工作流是图生图模式，请直接发送图片，"
                    "或回复之前的生成结果并输入文字来继续修改。"
                )
            elif wf_config.get("output_type") == "video":
                await message.reply_text(
                    "当前工作流是图生视频模式，请发送图片，并可在图片说明中填写提示词。"
                )
            else:
                await message.reply_text("当前工作流是图生图模式，请直接发送图片。")
            return

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

    task_settings = copy.deepcopy(settings)
    if _firstlast_frames:
        task_settings["_uploaded_images"] = _firstlast_frames

    task = GenerationTask(
        user_id=user_id,
        chat_id=chat.id,
        prompt=_firstlast_prompt if _firstlast_prompt else prompt,
        settings=task_settings,
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

    # enqueue 成功后清理 firstlast 状态（B1 修复：只在成功路径清除）
    if _firstlast_frames:
        _clear_firstlast_state(context.user_data)

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


async def _handle_comfy_prompt_input(update, context):
    text = update.message.text.strip()
    if not text:
        await update.message.reply_text("Prompt 不能为空，请重新输入。发送 /cancel 取消。")
        return

    user_id = update.effective_user.id
    settings = _ensure_settings(context, user_id)
    settings["comfy_prompt"] = text
    context.user_data["_waiting_input"] = None
    _save_settings(context, user_id)

    await update.message.reply_text(f"Prompt 已设置: {text[:80]}{'...' if len(text) > 80 else ''}")
    txt, markup = _comfy_settings_menu_shim(settings)
    await update.message.reply_text(txt, reply_markup=markup, parse_mode="HTML")


async def _handle_comfy_seed_input(update, context):
    user_id = update.effective_user.id
    settings = _ensure_settings(context, user_id)
    try:
        seed = int(update.message.text.strip())
        settings["comfy_seed"] = seed
    except ValueError:
        await update.message.reply_text("请输入有效的数字。发送 /cancel 取消。")
        return

    context.user_data["_waiting_input"] = None
    _save_settings(context, user_id)
    label = "随机" if seed == -1 else str(seed)
    await update.message.reply_text(f"ComfyUI 种子已设为: {label}")
    txt, markup = _comfy_settings_menu_shim(settings)
    await update.message.reply_text(txt, reply_markup=markup, parse_mode="HTML")


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

    waiting = context.user_data.get("_waiting_input") if context.user_data else None
    waiting_seed = context.user_data.get("_waiting_seed") if context.user_data else None
    has_firstlast = (
        context.user_data.get("_firstlast_start_frame")
        or context.user_data.get("_firstlast_end_frame")
    ) if context.user_data else False

    if waiting or waiting_seed or has_firstlast:
        if context.user_data:
            context.user_data["_waiting_input"] = None
            context.user_data["_waiting_seed"] = False
            _clear_firstlast_state(context.user_data)
        user_id = update.effective_user.id
        settings = _ensure_settings(context, user_id)
        await update.message.reply_text("已取消。")
        if settings.get("backend") == "comfyui":
            txt, markup = _comfy_settings_menu_shim(settings)
        else:
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
    if backend == "comfyui":
        await query.edit_message_text(
            f"已切换为 {label} 模式。\n直接发送提示词即可生成，或进入设置调整参数：",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("⚙️ ComfyUI 设置", callback_data="comfy_settings"),
            ]]),
        )
    else:
        await query.edit_message_text(f"已切换为 {label} 模式。现在直接发送提示词即可生成图片。")


async def handle_photo(update, context):
    """ComfyUI 图生图模式：上传图片到 ComfyUI 并生成。"""
    message = update.effective_message
    if message is None or message.photo is None:
        return
    chat = update.effective_chat

    user = update.effective_user
    user_id = user.id if user else (message.sender_chat.id if message.sender_chat else 0)
    if not is_authorized(user_id, chat.id, chat.type):
        return

    # 加载设置
    if context.user_data is not None:
        settings = _ensure_settings(context, user_id)
    else:
        settings = copy.deepcopy(DEFAULT_USER_SETTINGS)

    # 自动检测：回复机器人消息 + 图片带文字 → 临时使用 qwen-image-edit
    is_reply_to_bot = (
        message.reply_to_message
        and message.reply_to_message.from_user
        and message.reply_to_message.from_user.id == context.bot.id
    )
    auto_edit = is_reply_to_bot and message.caption and message.caption.strip()

    # auto_edit 触发时清除 firstlast 状态（B3 修复：用户意图已切换）
    if auto_edit and context.user_data:
        _clear_firstlast_state(context.user_data)

    # 群聊中需要 @bot 才触发（回复机器人消息时除外）
    if chat.type in ("group", "supergroup") and not auto_edit:
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

    # 确认是 ComfyUI 模式且当前 workflow 是图生图（自动编辑时绕过）
    if not auto_edit:
        if settings.get("backend", "sd") != "comfyui":
            return  # SD 模式不处理图片
    if auto_edit:
        wf_key = "qwen-image-edit"
        wf_config = COMFY_WORKFLOWS.get("qwen-image-edit", {})
    else:
        wf_key = settings.get("comfy_workflow", COMFY_DEFAULT_WORKFLOW)
        wf_config = COMFY_WORKFLOWS.get(wf_key, {})
        if not wf_config.get("is_img2img"):
            return  # 文生图 workflow 不处理图片

    # ── 多图工作流交互（额度检查之前分流）──
    if wf_config.get("load_image_nodes") and not auto_edit:
        user_data = context.user_data
        if user_data is None:
            await message.reply_text("会话状态不可用，请重新发送 /start。")
            return

        # 从配置读取角色名（如 firstlast: start/end, qwen-2pic: image1/image2）
        roles = list(wf_config["load_image_nodes"].keys())

        has_start = "_firstlast_start_frame" in user_data

        if not has_start:
            # 步骤1: 收到第一张图片 → 仅上传缓存，不扣额度，不创建任务
            try:
                photo_file = await message.photo[-1].get_file()
                image_bytes = io.BytesIO()
                await photo_file.download_to_memory(image_bytes)
                image_bytes.seek(0)
                uploaded_name = await comfy_api.upload_image(image_bytes.read())
            except Exception as e:
                logger.error("第一张图片上传失败: %s", e)
                await message.reply_text(f"上传第一张图片失败: {e}")
                return
            user_data["_firstlast_start_frame"] = uploaded_name
            await message.reply_text("✅ 已收到第一张图片，请发送第二张图片（可附带文字描述）。")
            return

        # 步骤2: 收到第二张图片
        try:
            photo_file = await message.photo[-1].get_file()
            image_bytes = io.BytesIO()
            await photo_file.download_to_memory(image_bytes)
            image_bytes.seek(0)
            uploaded_name = await comfy_api.upload_image(image_bytes.read())
        except Exception as e:
            logger.error("第二张图片上传失败: %s", e)
            await message.reply_text(f"上传第二张图片失败: {e}")
            return
        user_data["_firstlast_end_frame"] = uploaded_name

        # 提取 caption（复用 _clean_caption）
        caption = _clean_caption(message, context)

        if caption:
            # 有 caption → 清除状态，继续走到额度检查 + 任务创建
            start_frame = user_data.get("_firstlast_start_frame")
            end_frame = user_data.get("_firstlast_end_frame")
            _clear_firstlast_state(user_data)
            # 设置局部变量，后续任务创建代码会用到
            _firstlast_frames = {roles[0]: start_frame, roles[1]: end_frame}
            _firstlast_prompt = caption
        else:
            # 无 caption → 提示输入文字，不扣额度
            await message.reply_text("✅ 已收到第二张图片，请发送编辑描述文字。")
            return
    else:
        _firstlast_frames = None
        _firstlast_prompt = None

    # 额度检查
    is_admin = ADMIN_USER_ID is not None and user_id == ADMIN_USER_ID
    credit_charged = False
    if not is_admin:
        remaining = await credits.get_remaining(user_id)
        if remaining <= 0:
            stats = await credits.get_stats(user_id)
            await message.reply_text(
                f"额度已用完（已用 {stats['used']}/{stats['total_quota']}），请联系管理员增加额度。"
            )
            return
        if not await credits.use_one(user_id):
            await message.reply_text("额度扣减失败，请稍后重试。")
            return
        credit_charged = True

    try:
        status_msg = await retry_on_network_error(
            lambda: message.reply_text("正在上传图片..."),
            max_retries=2,
        )
        status_id = status_msg.message_id
    except Exception:
        logger.warning("创建状态消息失败")
        status_id = None

    # 下载 Telegram 图片（firstlast-video 已在分流阶段下载上传，跳过）
    if _firstlast_frames is None:
        try:
            photo_file = await message.photo[-1].get_file()
            image_bytes = io.BytesIO()
            await photo_file.download_to_memory(image_bytes)
            image_bytes.seek(0)
        except Exception as e:
            logger.error("下载图片失败: %s", e)
            if credit_charged:
                await credits.refund_one(user_id)
            await message.reply_text("下载图片失败，请稍后重试。")
            return

        # 上传到 ComfyUI
        try:
            if status_id is not None:
                await retry_on_network_error(
                    lambda: context.bot.edit_message_text(
                        "正在上传图片到 ComfyUI...", chat_id=chat.id, message_id=status_id,
                    ),
                    max_retries=2,
                )
            uploaded_name = await comfy_api.upload_image(image_bytes.read())
        except Exception as e:
            logger.error("上传图片到 ComfyUI 失败: %s", e)
            if credit_charged:
                await credits.refund_one(user_id)
            await message.reply_text(f"上传图片失败: {e}")
            return
    else:
        uploaded_name = None

    # 创建任务并入队
    task_settings = copy.deepcopy(settings)
    if auto_edit:
        task_settings["backend"] = "comfyui"
        task_settings["comfy_workflow"] = "qwen-image-edit"
        qwen_wf = COMFY_WORKFLOWS.get("qwen-image-edit", {})
        if qwen_wf.get("default_model"):
            task_settings["comfy_model"] = qwen_wf["default_model"]
    if _firstlast_frames:
        task_settings["_uploaded_images"] = _firstlast_frames
    else:
        task_settings["_uploaded_image"] = uploaded_name

    # 提取 caption 作为 prompt（仅 use_caption_as_prompt=True 的工作流）
    if _firstlast_frames:
        prompt_text = _firstlast_prompt
    elif wf_config.get("use_caption_as_prompt"):
        prompt_text = _clean_caption(message, context)
    else:
        prompt_text = ""  # 其他 img2img 保持原行为

    task = GenerationTask(
        user_id=user_id,
        chat_id=chat.id,
        prompt=prompt_text,
        settings=task_settings,
        status_message_id=status_id,
        original_message_id=message.message_id,
        reply_to_message_id=message.message_id if chat.type in ("group", "supergroup") else None,
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

    if ahead == 0 and status_id is not None:
        try:
            await retry_on_network_error(
                lambda: context.bot.edit_message_text(
                    "正在准备生成...", chat_id=chat.id, message_id=status_id,
                ),
                max_retries=2,
            )
        except Exception:
            pass


def get_handlers() -> list:
    return [
        CommandHandler("cancel", handle_cancel, filters=_user_auth_filter()),
        CommandHandler("mode", handle_mode, filters=_user_auth_filter()),
        CallbackQueryHandler(handle_mode_callback, pattern=r"^mode:"),
        MessageHandler(
            filters.PHOTO & _user_auth_filter(),
            handle_photo,
        ),
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & _user_auth_filter(),
            handle_text,
        ),
    ]
