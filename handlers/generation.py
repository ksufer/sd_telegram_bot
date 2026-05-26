import io
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import MessageHandler, CommandHandler, filters
from config import HIRES_FIX_PARAMS
from services import sd_api
from services.translator import translate
from handlers.settings import _default_settings, _settings_menu, _ensure_settings, _main_menu


async def handle_text(update, context):
    """处理用户文本消息：种子输入 或 图片生成。"""
    message = update.message
    text = message.text.strip()

    # 如果在等待种子输入
    if context.user_data.get("_waiting_seed"):
        await _handle_seed_input(update, context)
        return

    # 否则走图片生成
    await _handle_generation(update, context)


async def handle_cancel(update, context):
    """取消种子输入。"""
    if context.user_data.get("_waiting_seed"):
        context.user_data["_waiting_seed"] = False
        settings = _ensure_settings(context)
        await update.message.reply_text("已取消。")
        txt, markup = _settings_menu(settings)
        await update.message.reply_text(txt, reply_markup=markup, parse_mode="HTML")
    else:
        await update.message.reply_text("当前没有需要取消的操作。")


async def _handle_seed_input(update, context):
    """处理种子输入。"""
    settings = _ensure_settings(context)
    try:
        seed = int(update.message.text.strip())
        settings["seed"] = seed
    except ValueError:
        await update.message.reply_text("请输入有效的数字。发送 /cancel 取消。")
        return

    context.user_data["_waiting_seed"] = False
    label = "随机" if seed == -1 else str(seed)
    await update.message.reply_text(f"种子已设为: {label}")
    txt, markup = _settings_menu(settings)
    await update.message.reply_text(txt, reply_markup=markup, parse_mode="HTML")


async def _handle_generation(update, context):
    """核心生成流程。"""
    message = update.message
    prompt = message.text.strip()

    if len(prompt) > 1000:
        await message.reply_text("提示词过长，请控制在 1000 字以内。")
        return

    settings = _ensure_settings(context)
    status_msg = await message.reply_text("正在生成...")

    try:
        if settings["translate"]:
            translated = await translate(prompt)
        else:
            translated = prompt

        if settings["model"]:
            try:
                await sd_api.set_model(settings["model"])
            except Exception:
                pass

        payload = {
            "prompt": translated,
            "negative_prompt": settings["negative_prompt"],
            "width": settings["width"],
            "height": settings["height"],
            "steps": settings["steps"],
            "cfg_scale": settings["cfg_scale"],
            "sampler_name": settings["sampler"],
            "seed": settings["seed"],
        }

        if settings["hires_fix"]:
            payload["enable_hr"] = True
            payload["hr_upscaler"] = HIRES_FIX_PARAMS["upscaler"]
            payload["hr_scale"] = HIRES_FIX_PARAMS["upscale"]
            payload["denoising_strength"] = HIRES_FIX_PARAMS["denoising_strength"]
            payload["hr_second_pass_steps"] = HIRES_FIX_PARAMS["steps"]

        await status_msg.edit_text("正在生成（SD 处理中）...")

        image_data = await sd_api.txt2img(payload)

        await status_msg.edit_text("正在上传...")

        info = (
            f"<b>Prompt:</b> {translated[:200]}\n"
            f"<b>Negative:</b> {settings['negative_prompt'][:100]}\n"
            f"<b>Size:</b> {settings['width']}×{settings['height']}\n"
            f"<b>Steps:</b> {settings['steps']} | <b>CFG:</b> {settings['cfg_scale']}\n"
            f"<b>Hires Fix:</b> {'开' if settings['hires_fix'] else '关'}\n"
            f"<b>Seed:</b> {settings['seed'] if settings['seed'] != -1 else '随机'}\n"
            f"<b>模型:</b> {settings['model'] or '默认'}"
        )

        await message.reply_photo(
            photo=io.BytesIO(image_data),
            caption=info,
            parse_mode="HTML",
            reply_markup=_main_menu()[1],
        )

        await status_msg.delete()

    except Exception as e:
        error_text = str(e)
        if "ConnectError" in error_text or "connect" in error_text.lower():
            hint = "SD 服务不可用，请检查 10.126.126.1:7860 是否运行。"
        elif "timeout" in error_text.lower() or "Timeout" in error_text:
            hint = "生成超时，请尝试降低 Steps 或关闭高清修复。"
        else:
            hint = f"生成失败: {error_text[:200]}"
        await status_msg.edit_text(hint)


def get_handlers() -> list:
    return [
        CommandHandler("cancel", handle_cancel),
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text),
    ]
