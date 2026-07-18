import asyncio
import html
import io
import logging
import random
import time
import uuid
from dataclasses import dataclass

from config import HIRES_FIX_PARAMS, COMFY_WORKFLOWS, LOG_FULL_PROMPT, DEFAULT_PROMPT_PREFIX
from config import COMFY_VIDEO_ASPECTS, COMFY_VIDEO_RESOLUTIONS, COMFY_VIDEO_FRAMES_PRESETS
from services import sd_api, comfy_api, credits
from services.network import is_network_error, retry_on_network_error
from services.translator import translate
from services.face_prompt import extract_face_prompt
from ui.keyboards import generation_menu, comfy_generation_menu

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

    async def _translate_prompt(self, task: GenerationTask,
                               updater: ThrottledProgressUpdater) -> str:
        """翻译提示词（含翻译开关判断和 img2img 跳过逻辑）。"""
        backend = task.settings.get("backend", "sd")
        if backend == "comfyui":
            translate_enabled = task.settings.get("comfy_translate", False)
        else:
            translate_enabled = task.settings.get("translate", True)

        if (backend == "comfyui"
                and task.settings.get("_uploaded_image")
                and not task.prompt):
            return task.prompt
        if translate_enabled:
            await updater.set_stage("正在翻译提示词...")
            return await translate(task.prompt)
        return task.prompt

    async def _generate(self, task: GenerationTask, translated: str,
                       updater: ThrottledProgressUpdater) -> tuple:
        """执行生成（SD/ComfyUI 两条路径）。返回 (raw_data, actual_seed, wf_config, optimized_prompt)。"""
        settings = task.settings
        backend = settings.get("backend", "sd")

        if backend == "sd":
            if settings["model"]:
                await updater.set_stage("正在切换模型...")
                try:
                    await sd_api.set_model(settings["model"])
                except Exception:
                    pass

            await updater.set_stage("正在生成：0%")
            payload = _build_payload(settings, translated)

            last_progress_task = None

            def on_progress(ratio: float, _eta):
                nonlocal last_progress_task
                if last_progress_task and not last_progress_task.done():
                    last_progress_task.cancel()
                last_progress_task = asyncio.create_task(
                    updater.update_progress(ratio))

            image_data, actual_seed = await sd_api.txt2img(
                payload, progress_callback=on_progress)

            if last_progress_task and not last_progress_task.done():
                last_progress_task.cancel()
            return image_data, actual_seed, {}, None

        # ComfyUI 路径
        await updater.set_stage("正在生成（ComfyUI）...")
        wf_key = settings.get("comfy_workflow", "")
        wf_config = COMFY_WORKFLOWS.get(wf_key, {})
        seed = int(settings.get("comfy_seed", -1))
        if seed == -1:
            seed = random.randint(0, 1125899906842624)
        uploaded_image = settings.get("_uploaded_image")
        uploaded_images = settings.get("_uploaded_images")

        face_prompt = None
        manual_face = settings.get("comfy_face_prompt", "")
        if wf_config.get("face_detailer_prompt_node"):
            if manual_face:
                face_prompt = manual_face
            else:
                await updater.set_stage("正在提取脸部提示词...")
                face_prompt = await extract_face_prompt(task.prompt)

        comfy_output, actual_seed, optimized_prompt = await comfy_api.generate(
            translated, settings, seed,
            uploaded_image=uploaded_image,
            uploaded_images=uploaded_images,
            face_prompt=face_prompt,
        )
        return comfy_output, actual_seed, wf_config, optimized_prompt

    def _cache_gen_context(self, task: GenerationTask, translated: str,
                           actual_seed: int) -> str:
        """缓存生成上下文，返回 context_id。"""
        context_id = uuid.uuid4().hex[:8]
        if "_gen_context" not in self._app.bot_data:
            self._app.bot_data["_gen_context"] = {}
        _gen = self._app.bot_data["_gen_context"]
        _gen[context_id] = {
            "prompt": task.prompt,
            "translated": translated,
            "seed": actual_seed,
        }
        while len(_gen) > 50:
            _gen.pop(next(iter(_gen)))
        return context_id

    async def _send_result(self, task: GenerationTask, raw_data,
                           info: str, reply_markup,
                           wf_config: dict,
                           updater: ThrottledProgressUpdater) -> None:
        """发送图片/视频结果（含重试、fallback、网络错误退款）。内部完整保留原发送失败退款逻辑。"""
        is_video = wf_config.get("output_type") == "video"

        if is_video:
            _filename = raw_data.filename
            data = raw_data.data
            try:
                await retry_on_network_error(
                    lambda: self._app.bot.send_video(
                        chat_id=task.chat_id,
                        video=io.BytesIO(data),
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
                            document=io.BytesIO(data),
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

    async def _process_task(self, task: GenerationTask):
        settings = task.settings
        backend = settings.get("backend", "sd")
        start_time = time.monotonic()
        updater = ThrottledProgressUpdater(
            self._app, task.chat_id, task.status_message_id
        )

        # 翻译 + 生成（此阶段失败退款）
        try:
            translated = await self._translate_prompt(task, updater)
            raw_data, actual_seed, wf_config, optimized_prompt = await self._generate(
                task, translated, updater)
        except Exception:
            if task.credit_charged:
                await credits.refund_one(task.user_id)
            raise

        # 优化提示词可用时，替代 translated 用于显示和缓存
        display_prompt = optimized_prompt or translated

        # 缓存生成上下文
        context_id = self._cache_gen_context(task, display_prompt, actual_seed)

        # 构建结果信息和菜单
        await updater.set_stage("正在发送...")
        elapsed = time.monotonic() - start_time

        if backend == "sd":
            info = _build_sd_info(settings, translated, actual_seed, elapsed)
            reply_markup = generation_menu(context_id)
        else:
            info = _build_comfy_info(task, settings, display_prompt, actual_seed, elapsed)
            reply_markup = comfy_generation_menu(context_id, settings=settings)
            if wf_config.get("output_type") != "video":
                raw_data = raw_data.data  # 图片：提取 bytes；视频：保留 ComfyOutput 供 _send_result 取 .filename

        if task.credit_charged:
            remaining = await credits.get_remaining(task.user_id)
            info += f"\n<b>剩余额度:</b> {remaining}"

        # 发送结果（内部完全保留原发送阶段退款逻辑）
        await self._send_result(task, raw_data, info, reply_markup, wf_config, updater)

        # 清理状态消息
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


CAPTION_LIMIT = 1024
CAPTION_MARGIN = 32


def _escape_and_truncate(text: str, max_chars: int) -> str:
    """HTML 转义并截断，保证返回值长度 <= max_chars 且不切断实体。

    截断在转义后进行，因此 max_chars 按最终发送长度计算，
    不受 escape 膨胀（& → &amp; 等）影响。
    """
    if max_chars < 1:
        return ""
    escaped = html.escape(text)
    if len(escaped) <= max_chars:
        return escaped
    seg = escaped[:max_chars - 1]
    # 末尾若切在实体中间（& 之后没有 ; 闭合），回退到实体起点
    if seg.rfind("&") > seg.rfind(";"):
        seg = seg[:seg.rfind("&")]
    return seg + "…"


def _build_sd_info(settings: dict, translated: str, seed: int, elapsed: float) -> str:
    prompt_text = _escape_and_truncate(f"{DEFAULT_PROMPT_PREFIX} {translated}", 700)
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
        base_len = len("\n".join(info_parts))
        label = "<b>Prompt:</b> "
        budget = CAPTION_LIMIT - base_len - CAPTION_MARGIN - len(label)
        actual = _escape_and_truncate(translated, budget)
        info_parts.insert(0, f"{label}{actual}")
    return "\n".join(info_parts)
