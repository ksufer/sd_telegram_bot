import copy
import io
import logging
import re
from typing import Callable

from telegram import MessageEntity, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.error import BadRequest
from telegram.ext import MessageHandler, CommandHandler, CallbackQueryHandler, filters

from config import ADMIN_USER_ID, DEFAULT_USER_SETTINGS, COMFY_WORKFLOWS, COMFY_DEFAULT_WORKFLOW
from services.network import retry_on_network_error
from services.queue import GenerationTask
from services import credits, comfy_api
from handlers.settings import _ensure_settings, _save_settings, _settings_menu
from handlers import is_authorized, _user_auth_filter
from handlers.common import refresh_workflows, safe_answer
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
    """清除 firstlast-video 多步交互状态（含单图等待文字与文件提示词）。"""
    if not user_data:
        return
    user_data.pop("_firstlast_start_frame", None)
    user_data.pop("_firstlast_end_frame", None)
    user_data.pop("_firstlast_caption", None)
    user_data.pop("_file_prompt", None)


def _decode_text_file(raw: bytes) -> str:
    """解码用户上传的提示词文件（UTF-8 带/不带 BOM → GBK 容错）。"""
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


# ═══ 流程辅助函数（抽取重复的额度检查/上传/入队逻辑） ═══

async def _check_and_charge_credit(user_id: int) -> tuple[bool, bool, str]:
    """额度检查+扣减。返回 (ok, credit_charged, error_msg)。
    ok=False 时 error_msg 为错误提示；ok=True 时 credit_charged 表示已扣费。"""
    is_admin = ADMIN_USER_ID is not None and user_id == ADMIN_USER_ID
    if is_admin:
        return True, False, ""
    remaining = await credits.get_remaining(user_id)
    if remaining <= 0:
        stats = await credits.get_stats(user_id)
        return False, False, (
            f"额度已用完（已用 {stats['used']}/{stats['total_quota']}），请联系管理员增加额度。"
        )
    if not await credits.use_one(user_id):
        return False, False, "额度扣减失败，请稍后重试。"
    return True, True, ""


async def _create_status_message(message, text: str = "准备中...") -> int | None:
    """创建状态消息，返回 message_id。失败返回 None。"""
    try:
        status_msg = await retry_on_network_error(
            lambda: message.reply_text(text), max_retries=2,
        )
        return status_msg.message_id
    except Exception:
        logger.warning("创建状态消息失败")
        return None


async def _download_tg_photo(photo) -> io.BytesIO:
    """下载 Telegram 图片到内存，失败抛异常。"""
    photo_file = await photo.get_file()
    image_bytes = io.BytesIO()
    await photo_file.download_to_memory(image_bytes)
    image_bytes.seek(0)
    return image_bytes


async def _upload_to_comfy(image_bytes: bytes,
                           status_fn: Callable | None = None) -> str:
    """上传图片到 ComfyUI。失败抛异常（不做退款）。"""
    if status_fn:
        await status_fn()
    return await comfy_api.upload_image(image_bytes)


async def _enqueue_and_notify(task: GenerationTask, queue, context,
                              chat_id: int, status_id: int | None) -> int:
    """入队 + 更新队列状态。失败抛异常（不做退款）。返回 ahead 计数。"""
    ahead = await queue.enqueue(task)
    if ahead == 0 and status_id is not None:
        try:
            await retry_on_network_error(
                lambda: context.bot.edit_message_text(
                    "正在准备生成...", chat_id=chat_id, message_id=status_id,
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
                    chat_id=chat_id, message_id=status_id,
                ),
                max_retries=2,
            )
        except Exception:
            pass
    return ahead


async def handle_text(update, context):
    message = update.effective_message
    if message is None:
        return
    chat = update.effective_chat

    user = update.effective_user
    if not is_authorized(user.id if user else 0, chat.id, chat.type):
        return

    refresh_workflows()

    # 多图工作流等待文字描述（优先级高于其他 waiting_input）
    _firstlast_frames = None
    _firstlast_prompt = None
    if context.user_data is not None:
        start_frame = context.user_data.get("_firstlast_start_frame")
        end_frame = context.user_data.get("_firstlast_end_frame")
        if start_frame and end_frame:
            # 复用 _extract_prompt（群聊中去除 @bot 提及；非 @bot 消息静默忽略）
            prompt_text, prompt_for_me = _extract_prompt(message, context.bot.username)
            if not prompt_text:
                if prompt_for_me:
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
        elif waiting == "comfy_face_prompt":
            await _handle_comfy_face_prompt_input(update, context)
            return
        elif waiting == "comfy_seed":
            await _handle_comfy_seed_input(update, context)
            return
        elif waiting == "comfy_krea2_lora_strength":
            await _handle_krea2_lora_strength_input(update, context)
            return
        elif waiting == "pipe_collect":
            # 延迟 import 避免循环（pipeline 依赖本模块的辅助函数）
            from handlers.pipeline import handle_pipe_collect_text
            await handle_pipe_collect_text(update, context)
            return
        elif waiting == "pipe_step_prompt":
            from handlers.pipeline import handle_pipe_step_prompt_input
            await handle_pipe_step_prompt_input(update, context)
            return
        elif waiting == "sd_seed" or context.user_data.get("_waiting_seed"):
            await _handle_seed_input(update, context)
            return

    # Pipeline 收集流程等待图片期间：文字不进入普通生成流程（避免误扣费）
    if (context.user_data is not None
            and context.user_data.get("_pipe_collect")
            and _firstlast_frames is None):
        from handlers.pipeline import pipe_waiting_image
        if pipe_waiting_image(context.user_data):
            _, for_me = _extract_prompt(message, context.bot.username)
            if for_me:
                await message.reply_text(
                    "⛓ Pipeline 等待图片：请发送图片，或 /cancel 取消。")
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
    # 触发条件：当前就是 qwen-image-edit 工作流；或回复 bot 图片（auto_edit，自动切换工作流，
    # 与 handle_photo 的 auto_edit 行为一致，不要求当前后端为 comfyui）
    wf_key = settings.get("comfy_workflow", COMFY_DEFAULT_WORKFLOW)
    wf_config = COMFY_WORKFLOWS.get(wf_key, {})
    multi_edit_flow = (
        settings.get("backend") == "comfyui"
        and wf_key == "qwen-image-edit"
        and wf_config.get("is_img2img")
    )
    try:
        if (
            (multi_edit_flow or auto_edit)
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
            ok, credit_charged, err = await _check_and_charge_credit(user_id)
            if not ok:
                await message.reply_text(err)
                return

            status_id = await _create_status_message(message, "正在上传图片...")

            # 下载+上传（退款在外层统一处理）
            try:
                replied_photo = message.reply_to_message.photo[-1]
                image_bytes = await _download_tg_photo(replied_photo)
                if status_id is not None:

                    async def _status_fn():
                        await retry_on_network_error(
                            lambda: context.bot.edit_message_text(
                                "正在上传图片到 ComfyUI...",
                                chat_id=chat.id, message_id=status_id,
                            ),
                            max_retries=2,
                        )

                    uploaded_name = await _upload_to_comfy(image_bytes.read(), _status_fn)
                else:
                    uploaded_name = await _upload_to_comfy(image_bytes.read())
            except Exception as e:
                logger.error("上传图片失败: %s", e)
                if credit_charged:
                    await credits.refund_one(user_id)
                await message.reply_text(f"上传图片失败: {e}")
                return

            # 构建任务（auto_edit 特殊逻辑保留在此处）
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

            try:
                queue = context.bot_data["queue"]
                await _enqueue_and_notify(task, queue, context, chat.id, status_id)
            except Exception:
                logger.error("用户 %s 入队失败（多轮编辑）", user_id, exc_info=True)
                if credit_charged:
                    await credits.refund_one(user_id)
                await message.reply_text("任务提交失败，请稍后重试。")
                return

            return
    except Exception:
        logger.error("多轮编辑检测异常", exc_info=True)
        return

    # 群聊 @bot 检测 + 提示词提取（多轮编辑未触发时才走到这里）
    prompt, is_for_me = _extract_prompt(message, context.bot.username)
    if prompt is None:
        if is_for_me:
            await message.reply_text("请在 @Bot 后输入提示词。")
        return
    if not prompt:
        # 私聊空白文本（群聊空提示已在 _extract_prompt 内归一为 None）
        await message.reply_text("提示词不能为空，请重新输入。")
        return

    # 单图视频工作流：图片已缓存 → 本次文字作为提示词生成
    _pending_single_image = None
    if (settings.get("backend") == "comfyui" and context.user_data is not None
            and wf_config.get("output_type") == "video"
            and wf_config.get("load_image_node") and not wf_config.get("load_image_nodes")
            and context.user_data.get("_firstlast_start_frame")
            and _firstlast_frames is None):
        _pending_single_image = context.user_data["_firstlast_start_frame"]

    # 图生图工作流拦截纯文字消息（多轮编辑未触发时）
    # firstlast-video: 已收到首尾帧时正常创建任务，无帧时提示发首帧
    if settings.get("backend") == "comfyui":
        if wf_config.get("is_img2img") and not _firstlast_frames and not _pending_single_image:
            # 多图工作流已收到第一张图 → 提示发第二张（而非笼统的"发首帧/发图片"）
            if (wf_config.get("load_image_nodes") and context.user_data
                    and context.user_data.get("_firstlast_start_frame")):
                await message.reply_text("已收到第一张图片，请发送第二张图片（可附带文字描述）。")
                return
            if wf_key in ("firstlast-video", "minimax-h3-flf2v"):
                await message.reply_text("当前工作流是首尾帧生视频模式，请先发送首帧图片。")
            elif wf_key == "qwen-image-edit":
                await message.reply_text(
                    "当前工作流是图生图模式，请直接发送图片，"
                    "或回复之前的生成结果并输入文字来继续修改。"
                )
            elif wf_config.get("output_type") == "video":
                await message.reply_text(
                    "当前工作流是图生视频模式，请先发送图片，"
                    "再发送描述文字作为提示词（支持长文本，或发送 .txt 文件）。"
                )
            else:
                await message.reply_text("当前工作流是图生图模式，请直接发送图片。")
            return

    # 额度检查 + 扣减
    ok, credit_charged, err = await _check_and_charge_credit(user_id)
    if not ok:
        await message.reply_text(err)
        return

    status_id = await _create_status_message(message)

    reply_to = message.message_id if chat.type in ("group", "supergroup") else None

    task_settings = copy.deepcopy(settings)
    if _firstlast_frames:
        task_settings["_uploaded_images"] = _firstlast_frames
    elif _pending_single_image:
        task_settings["_uploaded_image"] = _pending_single_image

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

    try:
        queue = context.bot_data["queue"]
        await _enqueue_and_notify(task, queue, context, chat.id, status_id)
    except Exception:
        logger.error("用户 %s 入队失败", user_id, exc_info=True)
        if credit_charged:
            await credits.refund_one(user_id)
        await message.reply_text("任务提交失败，请稍后重试。")
        return

    # enqueue 成功后清理 firstlast 状态（B1 修复：只在成功路径清除）
    if _firstlast_frames or _pending_single_image:
        _clear_firstlast_state(context.user_data)


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


async def _handle_comfy_face_prompt_input(update, context):
    text = update.message.text.strip()
    if not text:
        await update.message.reply_text("脸部提示词不能为空，请重新输入。发送 /cancel 取消。")
        return

    user_id = update.effective_user.id
    settings = _ensure_settings(context, user_id)
    settings["comfy_face_prompt"] = text
    context.user_data["_waiting_input"] = None
    _save_settings(context, user_id)

    await update.message.reply_text(f"脸部提示词已设置: {text[:80]}{'...' if len(text) > 80 else ''}")
    txt, markup = _comfy_settings_menu_shim(settings)
    await update.message.reply_text(txt, reply_markup=markup, parse_mode="HTML")


async def _handle_krea2_lora_strength_input(update, context):
    user_id = update.effective_user.id
    settings = _ensure_settings(context, user_id)
    try:
        strength = int(update.message.text.strip())
        if strength < -15 or strength > 10:
            await update.message.reply_text("LoRA 强度范围为 -15 ~ 10，请重新输入。发送 /cancel 取消。")
            return
        settings["comfy_krea2_lora_strength"] = strength
    except ValueError:
        await update.message.reply_text("请输入有效的整数（-15 ~ 10）。发送 /cancel 取消。")
        return

    context.user_data["_waiting_input"] = None
    _save_settings(context, user_id)
    await update.message.reply_text(f"LoRA 强度已设置为: {strength}")
    txt, markup = _comfy_settings_menu_shim(settings)
    await update.message.reply_text(txt, reply_markup=markup, parse_mode="HTML")


async def _handle_comfy_seed_input(update, context):
    user_id = update.effective_user.id
    settings = _ensure_settings(context, user_id)
    try:
        seed = int(update.message.text.strip())
        if seed < -1 or seed > 2**63 - 1:
            await update.message.reply_text("种子范围为 -1（随机）~ 2^63-1，请重新输入。发送 /cancel 取消。")
            return
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
        if seed < -1 or seed > 2**63 - 1:
            await update.message.reply_text("种子范围为 -1（随机）~ 2^63-1，请重新输入。发送 /cancel 取消。")
            return
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
    has_pipe = (
        context.user_data.get("_pipe_collect")
        or context.user_data.get("_pipe_edit_step") is not None
    ) if context.user_data else False

    if waiting or waiting_seed or has_firstlast or has_pipe:
        if context.user_data:
            context.user_data["_waiting_input"] = None
            context.user_data["_waiting_seed"] = False
            _clear_firstlast_state(context.user_data)
            context.user_data.pop("_pipe_collect", None)
            context.user_data.pop("_pipe_edit_step", None)
            context.user_data.pop("_pipe_ready", None)
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
    await safe_answer(query)

    user = query.from_user
    chat = query.message.chat if query.message else None
    if user is None or chat is None:
        return
    if not is_authorized(user.id, chat.id, chat.type):
        try:
            await query.edit_message_text("⛔ 无使用权限")
        except BadRequest:
            pass
        return

    backend = query.data.split(":", 1)[1]  # "sd" or "comfyui"

    if backend == "comfyui":
        try:
            comfy_api.validate_workflow()
        except Exception as e:
            try:
                await query.edit_message_text(
                    f"ComfyUI 工作流不可用：{e}\n请联系管理员。"
                )
            except BadRequest:
                pass
            return

    settings = _ensure_settings(context, user.id)
    settings["backend"] = backend
    _save_settings(context, user.id)

    # 清除 firstlast 多图状态（切换后端时重置，与 _switch_to_workflow 一致）
    if context.user_data:
        _clear_firstlast_state(context.user_data)

    label = "SD WebUI" if backend == "sd" else "ComfyUI"
    try:
        if backend == "comfyui":
            await query.edit_message_text(
                f"已切换为 {label} 模式。\n直接发送提示词即可生成，或进入设置调整参数：",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("⚙️ ComfyUI 设置", callback_data="comfy_settings"),
                ]]),
            )
        else:
            await query.edit_message_text(f"已切换为 {label} 模式。现在直接发送提示词即可生成图片。")
    except BadRequest:
        pass


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

    refresh_workflows()

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

    # 群聊中需要 @bot 或回复 bot 消息才触发
    if chat.type in ("group", "supergroup") and not is_reply_to_bot:
        bot_username = context.bot.username
        if not bot_username:
            return
        caption = message.caption or ""
        entities = message.parse_caption_entities(types=[MessageEntity.MENTION]) if caption else {}
        mentioned = any(
            text.lower() == f"@{bot_username.lower()}"
            for text in entities.values()
        )
        if not mentioned:
            return

    # Pipeline 收集流程等待图片（首步起始图/双图步参考图），延迟 import 避免循环
    if context.user_data is not None and context.user_data.get("_pipe_collect"):
        from handlers.pipeline import pipe_waiting_image, handle_pipe_image_input
        if pipe_waiting_image(context.user_data):
            await handle_pipe_image_input(update, context)
            return

    # 确认是 ComfyUI 模式且当前 workflow 是图生图（自动编辑时绕过）
    if not auto_edit:
        if settings.get("backend", "sd") != "comfyui":
            await message.reply_text(
                "当前是 SD WebUI 模式，不支持图片处理。\n"
                "请发送 /start 选择图生图工作流（如「动漫转写实」或「图片编辑」）。"
            )
            return
    if auto_edit:
        wf_key = "qwen-image-edit"
        wf_config = COMFY_WORKFLOWS.get("qwen-image-edit", {})
    else:
        wf_key = settings.get("comfy_workflow", COMFY_DEFAULT_WORKFLOW)
        wf_config = COMFY_WORKFLOWS.get(wf_key, {})
        if not wf_config.get("is_img2img"):
            await message.reply_text(
                "当前工作流是文生图模式，不支持图片处理。\n"
                "请发送 /start 选择图生图工作流（如「动漫转写实」或「图片编辑」）。"
            )
            return

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
            # 相册（media group）的 caption 通常挂在第一张图上，暂存供步骤2 取用
            caption = _clean_caption(message, context)
            if caption:
                user_data["_firstlast_caption"] = caption
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

        # 提取 caption（复用 _clean_caption）；无 caption 时取用步骤1 暂存的相册 caption
        caption = _clean_caption(message, context)
        if not caption:
            caption = user_data.get("_firstlast_caption", "")

        if caption:
            # 有 caption → 继续走到额度检查 + 任务创建
            # 注意：不在此处清除 user_data 状态，保留到 enqueue 成功后再清理（与 handle_text 的 B1 修复对齐）
            start_frame = user_data.get("_firstlast_start_frame")
            end_frame = user_data.get("_firstlast_end_frame")
            # 设置局部变量，后续任务创建代码会用到
            _firstlast_frames = {roles[0]: start_frame, roles[1]: end_frame}
            _firstlast_prompt = caption
        else:
            file_prompt = user_data.get("_file_prompt", "")
            if file_prompt:
                start_frame = user_data.get("_firstlast_start_frame")
                end_frame = user_data.get("_firstlast_end_frame")
                _firstlast_frames = {roles[0]: start_frame, roles[1]: end_frame}
                _firstlast_prompt = file_prompt
            else:
                # 无 caption → 提示输入文字，不扣额度
                await message.reply_text("✅ 已收到第二张图片，请发送编辑描述文字。")
                return
    else:
        _firstlast_frames = None
        _firstlast_prompt = None
        # 单图视频工作流：无 caption/文件提示词且无缓存图 → 缓存图片等待文字
        # （Telegram caption 仅 1024 字符，文字可用足 4096）
        if (wf_config.get("output_type") == "video"
                and wf_config.get("load_image_node") and not wf_config.get("load_image_nodes")):
            user_data = context.user_data
            caption = _clean_caption(message, context)
            file_prompt = (user_data or {}).get("_file_prompt", "")
            if (not caption and not file_prompt and user_data is not None
                    and not user_data.get("_firstlast_start_frame")):
                try:
                    photo_file = await message.photo[-1].get_file()
                    image_bytes = io.BytesIO()
                    await photo_file.download_to_memory(image_bytes)
                    image_bytes.seek(0)
                    uploaded_name = await comfy_api.upload_image(image_bytes.read())
                except Exception as e:
                    logger.error("图片上传失败: %s", e)
                    await message.reply_text(f"上传图片失败: {e}")
                    return
                user_data["_firstlast_start_frame"] = uploaded_name
                await message.reply_text(
                    "✅ 已收到图片，请发送描述文字作为提示词（支持长文本，也可发送 .txt 文件），"
                    "发送 /cancel 可取消。"
                )
                return

    # 额度检查
    ok, credit_charged, err = await _check_and_charge_credit(user_id)
    if not ok:
        await message.reply_text(err)
        return

    status_id = await _create_status_message(message, "正在上传图片...")

    # 下载 + 上传（firstlast-video 已在分流阶段上传，跳过）
    if _firstlast_frames is None:
        try:
            photo = message.photo[-1]
            image_bytes = await _download_tg_photo(photo)

            async def _status_fn():
                await retry_on_network_error(
                    lambda: context.bot.edit_message_text(
                        "正在上传图片到 ComfyUI...",
                        chat_id=chat.id, message_id=status_id,
                    ),
                    max_retries=2,
                )

            uploaded_name = await _upload_to_comfy(image_bytes.read(),
                                                   _status_fn if status_id is not None else None)
        except Exception as e:
            logger.error("上传图片失败: %s", e)
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

    if _firstlast_frames:
        prompt_text = _firstlast_prompt
    elif wf_config.get("use_caption_as_prompt"):
        # 文件提示词优先于 caption（caption 仅 1024 字符）
        file_prompt = (context.user_data or {}).get("_file_prompt", "") if context.user_data else ""
        prompt_text = file_prompt or _clean_caption(message, context)
    else:
        prompt_text = ""

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

    try:
        queue = context.bot_data["queue"]
        await _enqueue_and_notify(task, queue, context, chat.id, status_id)
    except Exception:
        logger.error("用户 %s 入队失败", user_id, exc_info=True)
        if credit_charged:
            await credits.refund_one(user_id)
        await message.reply_text("任务提交失败，请稍后重试。")
        return

    # enqueue 成功后清理 firstlast 状态（B1 修复：只在成功路径清除）
    if ((_firstlast_frames or (context.user_data and context.user_data.get("_file_prompt")))
            and context.user_data):
        _clear_firstlast_state(context.user_data)


async def handle_document(update, context):
    """文件提示词：.txt/.md/.json 内容作为生成提示词（突破 Telegram 4096 字符限制）。

    分派：已缓存图片（flf2v 双图 / 单图视频）或文生工作流 → 直接生成；
    其余图生图工作流 → 存为待用提示词，发图后自动使用。
    """
    message = update.effective_message
    if message is None or message.document is None:
        return
    chat = update.effective_chat
    user = update.effective_user
    user_id = user.id if user else (message.sender_chat.id if message.sender_chat else 0)
    if not is_authorized(user_id, chat.id, chat.type):
        return

    refresh_workflows()

    # 群聊中需要 @bot 或回复 bot 消息才触发
    if chat.type in ("group", "supergroup"):
        is_reply_to_bot = (
            message.reply_to_message
            and message.reply_to_message.from_user
            and message.reply_to_message.from_user.id == context.bot.id
        )
        if not is_reply_to_bot:
            caption = message.caption or ""
            entities = (message.parse_caption_entities(types=[MessageEntity.MENTION])
                        if caption else {})
            mentioned = any(
                text.lower() == f"@{context.bot.username.lower()}"
                for text in entities.values()
            )
            if not mentioned:
                return

    doc = message.document
    ext = doc.file_name.rsplit(".", 1)[-1].lower() if doc.file_name else ""
    if ext not in ("txt", "md", "json"):
        await message.reply_text("仅支持 .txt / .md / .json 文本文件作为提示词。")
        return
    if doc.file_size and doc.file_size > 100_000:
        await message.reply_text("文件过大（>100KB）。")
        return

    # 下载 + 解码（UTF-8 带/不带 BOM → GBK 容错）
    try:
        file = await doc.get_file()
        buf = io.BytesIO()
        await file.download_to_memory(buf)
        content = _decode_text_file(buf.getvalue()).strip()
    except Exception as e:
        logger.error("提示词文件下载失败: %s", e)
        await message.reply_text(f"文件下载失败: {e}")
        return
    if not content:
        await message.reply_text("文件内容为空。")
        return

    if context.user_data is not None:
        settings = _ensure_settings(context, user_id)
    else:
        settings = copy.deepcopy(DEFAULT_USER_SETTINGS)
    if settings.get("backend") != "comfyui":
        await message.reply_text("文件提示词仅支持 ComfyUI 模式。")
        return

    wf_config = COMFY_WORKFLOWS.get(settings.get("comfy_workflow", COMFY_DEFAULT_WORKFLOW), {})
    user_data = context.user_data if context.user_data is not None else {}

    # 分派：直接生成 or 存为待用提示词
    start_frame = user_data.get("_firstlast_start_frame")
    end_frame = user_data.get("_firstlast_end_frame")
    uploaded_images = None
    uploaded_image = None
    if start_frame and end_frame and wf_config.get("load_image_nodes"):
        roles = list(wf_config["load_image_nodes"].keys())
        uploaded_images = {roles[0]: start_frame, roles[1]: end_frame}
    elif start_frame and wf_config.get("load_image_node") and not wf_config.get("load_image_nodes"):
        uploaded_image = start_frame
    elif not wf_config.get("is_img2img"):
        pass  # 文生工作流（t2v 等）
    else:
        user_data["_file_prompt"] = content
        await message.reply_text("📄 提示词已保存，请发送图片开始生成。")
        return

    # 直接生成（复用 handle_text 的额度/入队流程）
    ok, credit_charged, err = await _check_and_charge_credit(user_id)
    if not ok:
        await message.reply_text(err)
        return

    status_id = await _create_status_message(message)
    reply_to = message.message_id if chat.type in ("group", "supergroup") else None

    task_settings = copy.deepcopy(settings)
    if uploaded_images:
        task_settings["_uploaded_images"] = uploaded_images
    elif uploaded_image:
        task_settings["_uploaded_image"] = uploaded_image

    task = GenerationTask(
        user_id=user_id,
        chat_id=chat.id,
        prompt=content,
        settings=task_settings,
        status_message_id=status_id,
        original_message_id=message.message_id,
        reply_to_message_id=reply_to,
        credit_charged=credit_charged,
    )

    try:
        queue = context.bot_data["queue"]
        await _enqueue_and_notify(task, queue, context, chat.id, status_id)
    except Exception:
        logger.error("用户 %s 入队失败（文件提示词）", user_id, exc_info=True)
        if credit_charged:
            await credits.refund_one(user_id)
        await message.reply_text("任务提交失败，请稍后重试。")
        return

    # enqueue 成功后清理缓存状态
    if user_data:
        _clear_firstlast_state(user_data)


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
            filters.Document.ALL & _user_auth_filter(),
            handle_document,
        ),
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & _user_auth_filter(),
            handle_text,
        ),
    ]
