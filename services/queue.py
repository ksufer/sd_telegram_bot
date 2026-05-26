import asyncio
import html
import io
import logging
import time
import uuid
from dataclasses import dataclass

from config import HIRES_FIX_PARAMS, LOG_FULL_PROMPT, DEFAULT_PROMPT_PREFIX
from handlers.settings import _generation_menu
from services import sd_api
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
            await self._app.bot.edit_message_text(
                text, chat_id=self._chat_id, message_id=self._msg_id
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
                        hint = "SD 服务不可用，请检查后端是否运行。"
                    elif "timeout" in error_text.lower() or "Timeout" in error_text:
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

        # 1. 翻译
        if settings["translate"]:
            await updater.set_stage("正在翻译提示词...")
            translated = await translate(task.prompt)
        else:
            translated = task.prompt

        # 2. 切换模型（在 worker 内串行执行）
        if settings["model"]:
            await updater.set_stage("正在切换模型...")
            try:
                await sd_api.set_model(settings["model"])
            except Exception:
                pass

        # 3. 构建 payload 并生成（带进度轮询）
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

        context_id = uuid.uuid4().hex[:8]
        if "_gen_context" not in self._app.bot_data:
            self._app.bot_data["_gen_context"] = {}
        self._app.bot_data["_gen_context"][context_id] = {
            "prompt": task.prompt,
            "translated": translated,
            "seed": actual_seed,
        }

        # 4. 发送图片
        await updater.set_stage("正在发送图片...")
        elapsed = time.monotonic() - start_time

        info = (
            f"<b>Prompt:</b> {html.escape(f'{DEFAULT_PROMPT_PREFIX} {translated}'[:200])}\n"
            f"<b>Size:</b> {settings['width']}x{settings['height']}\n"
            f"<b>Steps:</b> {settings['steps']} | <b>CFG:</b> {settings['cfg_scale']}\n"
            f"<b>Sampler:</b> {html.escape(settings['sampler'])}\n"
            f"<b>Hires Fix:</b> {'开' if settings['hires_fix'] else '关'}\n"
            f"<b>Seed:</b> {actual_seed}\n"
            f"<b>模型:</b> {html.escape(settings['model'] or '默认')}\n"
            f"<b>耗时:</b> {elapsed:.1f}s"
        )

        await self._app.bot.send_photo(
            chat_id=task.chat_id,
            photo=io.BytesIO(image_data),
            caption=info,
            parse_mode="HTML",
            reply_to_message_id=task.original_message_id,
            reply_markup=_generation_menu(context_id),
        )

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
            await self._app.bot.edit_message_text(
                text, chat_id=task.chat_id, message_id=task.status_message_id
            )
        except Exception as e:
            if "Message is not modified" not in str(e):
                logger.debug("状态消息更新失败: %s", e)


def _build_payload(settings: dict, prompt: str) -> dict:
    payload = {
        "prompt": f"{DEFAULT_PROMPT_PREFIX} {prompt}",
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

    return payload
