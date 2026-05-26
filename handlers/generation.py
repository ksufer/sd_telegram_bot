import io
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import MessageHandler, filters
from config import HIRES_FIX_PARAMS
from services import sd_api
from services.translator import translate
from handlers.settings import _default_settings


async def handle_prompt(update, context):
    """处理用户发来的提示词文本 —— 这是核心生成流程。"""
    message = update.message
    prompt = message.text.strip()

    if len(prompt) > 1000:
        await message.reply_text("提示词过长，请控制在 1000 字以内。")
        return

    settings = context.user_data.setdefault("settings", _default_settings())

    # 发送状态消息
    status_msg = await message.reply_text("正在生成...")

    try:
        # 中译英
        if settings["translate"]:
            translated = await translate(prompt)
        else:
            translated = prompt

        # 如果设置了特定模型，先切换
        if settings["model"]:
            try:
                await sd_api.set_model(settings["model"])
            except Exception:
                pass  # 模型切换失败不阻塞生成

        # 构建 txt2img 参数
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

        # 高清修复
        if settings["hires_fix"]:
            payload["enable_hr"] = True
            payload["hr_upscaler"] = HIRES_FIX_PARAMS["upscaler"]
            payload["hr_scale"] = HIRES_FIX_PARAMS["upscale"]
            payload["denoising_strength"] = HIRES_FIX_PARAMS["denoising_strength"]
            payload["hr_second_pass_steps"] = HIRES_FIX_PARAMS["steps"]

        await status_msg.edit_text("正在生成（SD 处理中）...")

        # 调用 SD API
        image_data = await sd_api.txt2img(payload)

        await status_msg.edit_text("正在上传...")

        # 构建生成信息
        info = (
            f"<b>Prompt:</b> {translated[:200]}\n"
            f"<b>Negative:</b> {settings['negative_prompt'][:100]}\n"
            f"<b>Size:</b> {settings['width']}×{settings['height']}\n"
            f"<b>Steps:</b> {settings['steps']} | <b>CFG:</b> {settings['cfg_scale']}\n"
            f"<b>Hires Fix:</b> {'开' if settings['hires_fix'] else '关'}\n"
            f"<b>Seed:</b> {settings['seed'] if settings['seed'] != -1 else '随机'}\n"
            f"<b>模型:</b> {settings['model'] or '默认'}"
        )

        # 发送图片
        await message.reply_photo(
            photo=io.BytesIO(image_data),
            caption=info,
            parse_mode="HTML",
        )

        # 删除状态消息
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
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_prompt),
    ]
