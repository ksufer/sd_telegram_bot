import asyncio
import html
import io
import logging
import random
import time
import uuid
from dataclasses import dataclass

from telegram import InlineKeyboardMarkup, InlineKeyboardButton

from config import HIRES_FIX_PARAMS, COMFY_WORKFLOWS, LOG_FULL_PROMPT, DEFAULT_PROMPT_PREFIX
from config import COMFY_VIDEO_ASPECTS, COMFY_VIDEO_RESOLUTIONS, COMFY_VIDEO_FRAMES_PRESETS
from config import COMFY_LORA_VARIANTS
from handlers.settings import _generation_menu
from services import sd_api, comfy_api, credits
from services.network import is_network_error, retry_on_network_error
from services.translator import translate

logger = logging.getLogger(__name__)


@dataclass
class GenerationTask:
    user_id: int
    chat_id: int
    prompt: str
    settings: dict
    status_message_id: int | None = None
    original_message_id: int | None = None
    reply_to_message_id: int | None = None
    credit_charged: bool = False


class ThrottledProgressUpdater:

    def __init__(self, app, chat_id: int, status_msg_id: int | None):
        self._app = app
        self._chat_id = chat_id
        self._msg_id = status_msg_id
        self._last_update_time = 0.0
        self._last_reported_pct = -1

    async def set_stage(self, text: str):
        await self._update(text)

    async def update_progress(self, ratio: float):
        now = time.monotonic()
        pct = int(ratio * 100)
        if (now - self._last_update_time >= 3
                and abs(pct - self._last_reported_pct) >= 5):
            self._last_update_time = now
            self._last_reported_pct = pct
            await self._update(f"正在生成：{pct}%")

    async def _update(self, text: str):
        if self._msg_id is None:
            return
        try:
            await retry_on_network_error(
                lambda: self._app.bot.edit_message_text(
                    text, chat_id=self._chat_id, message_id=self._msg_id
                ),
                max_retries=2,
            )
        except Exception as e:
            if "Message is not modified" not in str(e):
                logger.debug("状态消息更新失败: %s", e)


class GenerationQueue:
    def __init__(self, app):
        self._app = app
        self._queue: asyncio.Queue[GenerationTask] = asyncio.Queue()
        self._current_task: GenerationTask | None = None
        self._processing = False
        self._worker_task: asyncio.Task | None = None

    @property
    def pending_count(self) -> int:
        return self._queue.qsize()

    @property
    def is_processing(self) -> bool:
        return self._current_task is not None

    async def enqueue(self, task: GenerationTask) -> int:
        ahead = self._queue.qsize() + (1 if self._current_task is not None else 0)
        await self._queue.put(task)

        prompt_preview = task.prompt[:50] if LOG_FULL_PROMPT else f"({len(task.prompt)} chars)"
        logger.info(
            "用户 %s 提交生成任务 | prompt=%s | 前方 %s 个任务",
            task.user_id, prompt_preview, ahead,
        )

        if not self._processing or (self._worker_task and self._worker_task.done()):
            self._processing = True
            try:
                self._worker_task = asyncio.create_task(self._worker())
            except RuntimeError:
                self._processing = False
                raise
        return ahead

    async def stop_worker(self):
        if self._worker_task and not self._worker_task.done():
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        logger.info("Worker 已停止")

    async def _worker(self):
        logger.info("Worker 启动")
        try:
            while not self._queue.empty():
                task = await self._queue.get()
                self._current_task = task
                try:
                    await self._process_task(task)
                except Exception as e:
                    error_text = str(e)
                    if "ConnectError" in error_text or "connect" in error_text.lower():
                        backend_label = "ComfyUI" if task.settings.get("backend") == "comfyui" else "SD"
                        hint = f"{backend_label} 服务不可用，请检查后端是否运行。"
                    elif "timeout" in error_text.lower() or "Timeout" in error_text:
                        if task.settings.get("backend") == "comfyui":
                            hint = "ComfyUI 生成超时，请稍后重试。"
                        else:
                            hint = "生成超时，请尝试降低 Steps 或关闭高清修复。"
                    else:
                        hint = f"生成失败: {error_text[:200]}"
                    logger.error("Worker 处理任务异常: %s", e, exc_info=True)
                    await self._update_status(task, hint)
                finally:
                    self._current_task = None
                    self._queue.task_done()
        finally:
            self._processing = False
            self._worker_task = None
        logger.info("Worker 空闲退出")

    async def _process_task(self, task: GenerationTask):
        settings = task.settings
        start_time = time.monotonic()
        updater = ThrottledProgressUpdater(
            self._app, task.chat_id, task.status_message_id
        )

        # 1-3. 翻译 + 模型 + 生成（失败时返还额度）
        try:
            backend = settings.get("backend", "sd")

            # 1. 翻译（SD 和 ComfyUI 各自独立开关；img2img 无文字 prompt 跳过）
            if backend == "comfyui":
                translate_enabled = settings.get("comfy_translate", False)
            else:
                translate_enabled = settings.get("translate", True)

            if backend == "comfyui" and settings.get("_uploaded_image") and not task.prompt:
                # 图生图模式且无文字 prompt，跳过翻译
                translated = task.prompt
            elif translate_enabled:
                await updater.set_stage("正在翻译提示词...")
                translated = await translate(task.prompt)
            else:
                translated = task.prompt

            # 2. 切换模型（仅在 SD 模式下）
            if backend == "sd" and settings["model"]:
                await updater.set_stage("正在切换模型...")
                try:
                    await sd_api.set_model(settings["model"])
                except Exception:
                    pass

            # 3. 构建 payload 并生成（带进度轮询）
            if backend == "sd":
                await updater.set_stage("正在生成：0%")
                payload = _build_payload(settings, translated)

                last_progress_task = None

                def on_progress(ratio: float, _eta):
                    nonlocal last_progress_task
                    if last_progress_task and not last_progress_task.done():
                        last_progress_task.cancel()
                    last_progress_task = asyncio.create_task(updater.update_progress(ratio))

                image_data, actual_seed = await sd_api.txt2img(payload, progress_callback=on_progress)

                if last_progress_task and not last_progress_task.done():
                    last_progress_task.cancel()
            else:
                # ComfyUI 路径
                await updater.set_stage("正在生成（ComfyUI）...")
                wf_key = settings.get("comfy_workflow", "")
                wf_config = COMFY_WORKFLOWS.get(wf_key, {})
                seed = int(settings.get("comfy_seed", -1))
                if seed == -1:
                    seed = random.randint(0, 2**63 - 1)
                uploaded_image = settings.get("_uploaded_image")
                uploaded_images = settings.get("_uploaded_images")
                comfy_output, actual_seed = await comfy_api.generate(
                    translated, settings, seed,
                    uploaded_image=uploaded_image,
                    uploaded_images=uploaded_images,
                )

        except Exception:
            if task.credit_charged:
                await credits.refund_one(task.user_id)
            raise

        context_id = uuid.uuid4().hex[:8]
        if "_gen_context" not in self._app.bot_data:
            self._app.bot_data["_gen_context"] = {}
        _gen = self._app.bot_data["_gen_context"]
        _gen[context_id] = {
            "prompt": task.prompt,
            "translated": translated,
            "seed": actual_seed,
        }
        # 只保留最近 50 条，按 Python 3.7+ dict 插入顺序淘汰最旧
        while len(_gen) > 50:
            _gen.pop(next(iter(_gen)))

        # 4. 发送结果（带重试，网络失败时退款并通知用户）
        await updater.set_stage("正在发送...")
        elapsed = time.monotonic() - start_time

        if backend == "sd":
            info = _build_sd_info(settings, translated, actual_seed, elapsed)
            reply_markup = _generation_menu(context_id)
            raw_data = image_data
        else:
            info = _build_comfy_info(task, settings, translated, actual_seed, elapsed)
            reply_markup = _comfy_generation_menu(context_id, settings=settings)
            raw_data = comfy_output.data

        # 非管理员显示剩余额度
        if task.credit_charged:
            remaining = await credits.get_remaining(task.user_id)
            info += f"\n<b>剩余额度:</b> {remaining}"

        is_video = backend == "comfyui" and wf_config.get("output_type") == "video"

        if is_video:
            # 视频工作流：send_video，失败则 fallback 到 send_document
            _filename = comfy_output.filename
            try:
                await retry_on_network_error(
                    lambda: self._app.bot.send_video(
                        chat_id=task.chat_id,
                        video=io.BytesIO(raw_data),
                        filename=_filename,
                        caption=info,
                        parse_mode="HTML",
                        reply_to_message_id=task.reply_to_message_id or task.original_message_id,
                        reply_markup=reply_markup,
                        supports_streaming=True,
                    ),
                    on_retry=lambda attempt, max_retries: updater.set_stage(
                        f"视频发送失败，正在重试 ({attempt}/{max_retries})..."
                    ),
                )
            except Exception as e:
                logger.exception("send_video 失败，fallback 到 send_document")
                _fallback_info = info + "\n（视频无法直接播放，已改为文件发送）"
                try:
                    await retry_on_network_error(
                        lambda: self._app.bot.send_document(
                            chat_id=task.chat_id,
                            document=io.BytesIO(raw_data),
                            filename=_filename,
                            caption=_fallback_info,
                            parse_mode="HTML",
                            reply_to_message_id=task.reply_to_message_id or task.original_message_id,
                            reply_markup=reply_markup,
                        ),
                    )
                except Exception as e2:
                    if is_network_error(e2):
                        logger.error("视频文件发送失败（网络错误）: %s", e2)
                        if task.credit_charged:
                            await credits.refund_one(task.user_id)
                        await self._update_status(
                            task, "网络不稳定，视频发送失败，已退还额度。请稍后重试。"
                        )
                        return
                    raise
        else:
            try:
                await retry_on_network_error(
                    lambda: self._app.bot.send_photo(
                        chat_id=task.chat_id,
                        photo=io.BytesIO(raw_data),
                        caption=info,
                        parse_mode="HTML",
                        reply_to_message_id=task.reply_to_message_id or task.original_message_id,
                        reply_markup=reply_markup,
                    ),
                    on_retry=lambda attempt, max_retries: updater.set_stage(
                        f"图片发送失败，正在重试 ({attempt}/{max_retries})..."
                    ),
                )
            except Exception as e:
                if is_network_error(e):
                    logger.error("图片发送失败（网络错误，已重试3次）: %s", e)
                    if task.credit_charged:
                        await credits.refund_one(task.user_id)
                    await self._update_status(
                        task, "网络不稳定，图片发送失败，已退还额度。请稍后重试。"
                    )
                    return
                raise

        # 5. 删除状态消息
        if task.status_message_id is not None:
            try:
                await self._app.bot.delete_message(
                    chat_id=task.chat_id,
                    message_id=task.status_message_id,
                )
            except Exception:
                logger.debug("删除状态消息失败", exc_info=True)

        logger.info("用户 %s 生成完成 | 耗时 %.1fs", task.user_id, elapsed)

    async def _update_status(self, task: GenerationTask, text: str):
        if task.status_message_id is None:
            return
        try:
            await retry_on_network_error(
                lambda: self._app.bot.edit_message_text(
                    text, chat_id=task.chat_id, message_id=task.status_message_id
                ),
                max_retries=2,
            )
        except Exception as e:
            if "Message is not modified" not in str(e):
                logger.debug("状态消息更新失败: %s", e)


def _build_payload(settings: dict, prompt: str) -> dict:
    # 如果提示词已包含默认前缀关键词，不再重复添加
    quality_keywords = ["masterpiece", "best quality", "amazing quality"]
    has_prefix = any(prompt.lower().startswith(kw) for kw in quality_keywords)
    full_prompt = prompt if has_prefix else f"{DEFAULT_PROMPT_PREFIX} {prompt}"

    payload = {
        "prompt": full_prompt,
        "negative_prompt": settings["negative_prompt"],
        "width": settings["width"],
        "height": settings["height"],
        "steps": settings["steps"],
        "cfg_scale": settings["cfg_scale"],
        "sampler_name": settings["sampler"],
        "seed": settings["seed"],
        "restore_faces": settings["restore_faces"],
        "tiling": settings["tiling"],
        "batch_size": 1,
        "n_iter": 1,
    }

    if settings.get("clip_skip", 1) > 1:
        payload["override_settings"] = {
            "CLIP_stop_at_last_layers": settings["clip_skip"]
        }

    if settings["hires_fix"]:
        payload["enable_hr"] = True
        payload["hr_upscaler"] = HIRES_FIX_PARAMS["upscaler"]
        payload["hr_scale"] = HIRES_FIX_PARAMS["upscale"]
        payload["denoising_strength"] = HIRES_FIX_PARAMS["denoising_strength"]
        payload["hr_second_pass_steps"] = HIRES_FIX_PARAMS["steps"]
        payload["hr_additional_modules"] = []  # 修复 Forge bug: None 导致 TypeError

    return payload


def _truncate_for_caption(text: str, max_chars: int = 700) -> str:
    """截断过长文本以适配 Telegram caption 1024 字符限制"""
    if len(text) <= max_chars:
        return text
    return text[:max_chars - 1] + "…"


def _build_sd_info(settings: dict, translated: str, seed: int, elapsed: float) -> str:
    prompt_text = _truncate_for_caption(html.escape(f"{DEFAULT_PROMPT_PREFIX} {translated}"))
    return (
        f"<b>Prompt:</b> {prompt_text}\n"
        f"<b>Size:</b> {settings['width']}x{settings['height']}\n"
        f"<b>Steps:</b> {settings['steps']} | <b>CFG:</b> {settings['cfg_scale']}\n"
        f"<b>Sampler:</b> {html.escape(settings['sampler'])}\n"
        f"<b>Hires Fix:</b> {'开' if settings['hires_fix'] else '关'}\n"
        f"<b>Seed:</b> {seed}\n"
        f"<b>模型:</b> {html.escape(settings['model'] or '默认')}\n"
        f"<b>耗时:</b> {elapsed:.1f}s"
    )


def _build_comfy_info(task, settings: dict, translated: str, seed: int, elapsed: float) -> str:
    wf_config = COMFY_WORKFLOWS.get(settings.get("comfy_workflow", ""), {})
    is_video = wf_config.get("output_type") == "video"
    model_selectable = wf_config.get("model_selectable", True)

    if is_video:
        # 视频工作流：显示比例/画质/长度
        aspect = settings.get("comfy_video_aspect", "9:16")
        aspect_cfg = COMFY_VIDEO_ASPECTS.get(aspect, COMFY_VIDEO_ASPECTS["9:16"])
        resolution = settings.get("comfy_video_resolution", "480p")
        resolution_cfg = COMFY_VIDEO_RESOLUTIONS.get(resolution, COMFY_VIDEO_RESOLUTIONS["480p"])
        frames_key = str(settings.get("comfy_video_frames", 81))
        frames_cfg = COMFY_VIDEO_FRAMES_PRESETS.get(frames_key, COMFY_VIDEO_FRAMES_PRESETS["81"])
        info_parts = [
            f"<b>视频比例:</b> {aspect_cfg['label']}",
            f"<b>视频画质:</b> {resolution_cfg['label']}",
            f"<b>视频长度:</b> {frames_cfg['label']}",
            f"<b>Seed:</b> {seed}",
            f"<b>耗时:</b> {elapsed:.1f}s",
        ]
    else:
        model = html.escape(settings.get("comfy_model", "?"))
        if wf_config.get("is_img2img") and not wf_config.get("width_node"):
            size = "跟随输入图片"
        else:
            size = f"{settings.get('comfy_width', '?')}×{settings.get('comfy_height', '?')}"
        info_parts = [
            f"<b>模型:</b> {model}",
            f"<b>尺寸:</b> {size}",
            f"<b>Seed:</b> {seed}",
            f"<b>耗时:</b> {elapsed:.1f}s",
        ]

    if translated and translated.strip():
        actual = html.escape(translated)
        if translated == task.prompt:
            info_parts.insert(0, f"<b>Prompt:</b> {_truncate_for_caption(actual)}")
        else:
            info_parts.insert(0, f"<b>实际 Prompt:</b> {_truncate_for_caption(actual)}")
            info_parts.insert(0, f"<b>原始 Prompt:</b> {_truncate_for_caption(html.escape(task.prompt), 350)}")
    return "\n".join(info_parts)


def _comfy_generation_menu(context_id: str, settings: dict | None = None) -> InlineKeyboardMarkup:
    # zit-pussy 等有 lora_node 的 workflow：显示 LoRA 变体按钮替代 Seed
    if settings:
        wf_key = settings.get("comfy_workflow", "")
        wf_config = COMFY_WORKFLOWS.get(wf_key, {})
        if wf_config.get("lora_node"):
            current = settings.get("comfy_lora_variant", "normal")
            lora_buttons = []
            for key, variant in COMFY_LORA_VARIANTS.items():
                prefix = "✓ " if key == current else ""
                lora_buttons.append(InlineKeyboardButton(
                    f"{prefix}{variant['label']}",
                    callback_data=f"comfy_lora_var:{key}"
                ))
            rows = [lora_buttons]
            # Upscale 开关
            if wf_config.get("upscale_switch_node"):
                upscale_on = settings.get("comfy_upscale_enabled", True)
                upscale_label = "SD Upscale · ON" if upscale_on else "SD Upscale · OFF"
                rows.append([InlineKeyboardButton(upscale_label, callback_data="comfy_upscale_toggle_gen")])
            rows.append([
                InlineKeyboardButton("⚙️ ComfyUI 设置", callback_data="comfy_settings"),
                InlineKeyboardButton("关闭菜单", callback_data="close_menu"),
            ])
            return InlineKeyboardMarkup(rows)
    # 默认菜单（无 lora_node 的 workflow）
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔁 复用本次 Seed", callback_data=f"comfy_reuse_seed_{context_id}"),
            InlineKeyboardButton("🎲 随机 Seed", callback_data="comfy_random_seed"),
        ],
        [
            InlineKeyboardButton("⚙️ ComfyUI 设置", callback_data="comfy_settings"),
            InlineKeyboardButton("关闭菜单", callback_data="close_menu"),
        ],
    ])
