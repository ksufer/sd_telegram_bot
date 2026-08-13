import asyncio
import copy
import html
import io
import logging
import random
import time
import uuid
from dataclasses import dataclass

import telegram.error
from PIL import Image

from config import HIRES_FIX_PARAMS, LOG_FULL_PROMPT, DEFAULT_PROMPT_PREFIX
from config import COMFY_VIDEO_ASPECTS, COMFY_VIDEO_RESOLUTIONS, COMFY_VIDEO_FRAMES_PRESETS
from config import DEFAULT_VIDEO_FRAMES_KEY
from config import ADMIN_USER_ID, WORKFLOW_REGISTRY, COMFY_WORKFLOWS
from config import COMFY_PROGRESS_HEARTBEAT_INTERVAL
from services import sd_api, comfy_api, credits, ollama_api
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
    # Pipeline 连跑上下文 {"steps": [wf_key...], "idx": int}；None = 普通单步任务
    # （Bot 专有：admin/tasks.py 不创建 pipeline 任务，无需镜像该逻辑）
    pipeline: dict | None = None


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
        # 先同步排空队列（无 await，不会与 worker 竞争）：pending 任务重启后会丢失，
        # 需在取消 worker 前取出，事后统一退款并告知
        pending_tasks = []
        while True:
            try:
                pending_tasks.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        for _ in pending_tasks:
            self._queue.task_done()

        if self._worker_task and not self._worker_task.done():
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass

        # 退款并尽力告知（此时 worker 已停，发消息失败则忽略）
        for pending in pending_tasks:
            if pending.credit_charged:
                try:
                    await credits.refund_one(pending.user_id)
                    pending.credit_charged = False
                except Exception:
                    logger.error("pending 任务退款失败: user=%s",
                                 pending.user_id, exc_info=True)
            try:
                await self._app.bot.send_message(
                    chat_id=pending.chat_id,
                    text="Bot 正在重启，排队中的生成任务已取消并退还额度，请重新提交。",
                )
            except Exception:
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
                    backend = task.settings.get("backend")
                    if "ConnectError" in error_text or "connect" in error_text.lower():
                        if backend == "ollama":
                            hint = "Ollama 服务不可用，请检查后端是否运行。"
                        elif backend == "comfyui":
                            hint = "ComfyUI 服务不可用，请检查后端是否运行。"
                        else:
                            hint = "SD 服务不可用，请检查后端是否运行。"
                    elif "timeout" in error_text.lower() or "Timeout" in error_text:
                        if backend == "ollama":
                            hint = "反推超时，请稍后重试。"
                        elif backend == "comfyui":
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
        # 用户保存的 workflow key 可能已被管理面板删除，解析为实际生效的 key/config
        # （与 comfy_api.generate 内部回退保持一致）
        _, wf_config = comfy_api._get_wf_config(settings)
        seed = int(settings.get("comfy_seed", -1))
        if seed == -1:
            seed = random.randint(0, 1125899906842624)
        uploaded_image = settings.get("_uploaded_image")
        uploaded_images = settings.get("_uploaded_images")

        face_prompt = None
        manual_face = settings.get("comfy_face_prompt", "")
        # 脸部精修已关闭时跳过提取（workflow 无开关节点 = detailer 恒开，仍提取）
        facedetailer_off = ("facedetailer_switch_node" in wf_config
                            and not settings.get("comfy_facedetailer_enabled", True))
        if wf_config.get("face_detailer_prompt_node") and not facedetailer_off:
            if manual_face:
                face_prompt = manual_face
            else:
                await updater.set_stage("正在提取脸部提示词...")
                face_prompt = await extract_face_prompt(task.prompt)

        async def _comfy_progress(elapsed: int):
            """长任务心跳：向用户汇报已用时间（视频任务可达 20+ 分钟）。"""
            await updater.set_stage(
                f"正在生成（ComfyUI）... 已用 {elapsed // 60}分{elapsed % 60:02d}秒"
            )

        comfy_output, actual_seed, optimized_prompt = await comfy_api.generate(
            translated, settings, seed,
            uploaded_image=uploaded_image,
            uploaded_images=uploaded_images,
            face_prompt=face_prompt,
            progress_callback=_comfy_progress,
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
                           updater: ThrottledProgressUpdater) -> bool:
        """发送图片/视频结果（含重试、fallback、失败退款）。

        返回 True 表示发送成功；失败时已退款并尽力通过状态消息告知用户，返回 False。
        """
        try:
            await self._send_media(task, raw_data, info, reply_markup,
                                   wf_config, updater)
            return True
        except Exception as e:
            if _is_reply_not_found(e) and (task.reply_to_message_id
                                           or task.original_message_id):
                # 被回复的原消息已被删除：去掉 reply_to 整体重试一次
                logger.info("reply_to 目标消息不存在，改为不回复重发: %s", e)
                task.reply_to_message_id = None
                task.original_message_id = None
                try:
                    await self._send_media(task, raw_data, info, reply_markup,
                                           wf_config, updater)
                    return True
                except Exception as e2:
                    e = e2
            return await self._handle_send_failure(task, e, wf_config)

    async def _send_media(self, task: GenerationTask, raw_data,
                          info: str, reply_markup,
                          wf_config: dict,
                          updater: ThrottledProgressUpdater) -> None:
        """实际发送图片/视频（视频失败时 fallback 到 send_document）。失败抛异常。"""
        is_video = wf_config.get("output_type") == "video"
        reply_to = task.reply_to_message_id or task.original_message_id

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
                        reply_to_message_id=reply_to,
                        reply_markup=reply_markup,
                        supports_streaming=True,
                    ),
                    on_retry=lambda attempt, max_retries: updater.set_stage(
                        f"视频发送失败，正在重试 ({attempt}/{max_retries})..."
                    ),
                )
            except Exception:
                logger.exception("send_video 失败，fallback 到 send_document")
                # info 已是转义后的 HTML（长度 <= CAPTION_LIMIT），拼后缀前需截断
                suffix = "\n（视频无法直接播放，已改为文件发送）"
                fallback_info = (_truncate_escaped(info, CAPTION_LIMIT - len(suffix))
                                 + suffix)
                await retry_on_network_error(
                    lambda: self._app.bot.send_document(
                        chat_id=task.chat_id,
                        document=io.BytesIO(data),
                        filename=_filename,
                        caption=fallback_info,
                        parse_mode="HTML",
                        reply_to_message_id=reply_to,
                        reply_markup=reply_markup,
                    ),
                )
        else:
            photo_data, fit = _fit_photo(raw_data)
            if fit == "document":
                # JPEG 重编码后仍超限或解码失败：改发文件（bot 上限 50MB）
                suffix = "\n（原图过大，已改为文件发送）"
                doc_info = (_truncate_escaped(info, CAPTION_LIMIT - len(suffix))
                            + suffix)
                await retry_on_network_error(
                    lambda: self._app.bot.send_document(
                        chat_id=task.chat_id,
                        document=io.BytesIO(photo_data),
                        filename="image.png",
                        caption=doc_info,
                        parse_mode="HTML",
                        reply_to_message_id=reply_to,
                        reply_markup=reply_markup,
                    ),
                )
                return
            if fit == "jpeg":
                suffix = "\n（原图过大，已压缩发送）"
                info = (_truncate_escaped(info, CAPTION_LIMIT - len(suffix))
                        + suffix)
            await retry_on_network_error(
                lambda: self._app.bot.send_photo(
                    chat_id=task.chat_id,
                    photo=io.BytesIO(photo_data),
                    caption=info,
                    parse_mode="HTML",
                    reply_to_message_id=reply_to,
                    reply_markup=reply_markup,
                ),
                on_retry=lambda attempt, max_retries: updater.set_stage(
                    f"图片发送失败，正在重试 ({attempt}/{max_retries})..."
                ),
            )

    async def _handle_send_failure(self, task: GenerationTask, e: Exception,
                                   wf_config: dict) -> bool:
        """生成成功但发送失败：统一退款并尽力告知用户。

        无论网络错误还是 BadRequest/Forbidden 等永久错误都退款（退款后 credit_charged
        置 False 防止重复退款）；Forbidden（用户拉黑 Bot）时状态消息同样发不出，仅退款即可。
        """
        media = "视频" if wf_config.get("output_type") == "video" else "图片"
        if is_network_error(e):
            logger.error("%s发送失败（网络错误）: %s", media, e)
            hint = f"网络不稳定，{media}发送失败，已退还额度。请稍后重试。"
        else:
            logger.error("%s发送失败: %s", media, e, exc_info=True)
            hint = f"{media}发送失败，已退还额度。请稍后重试。"
        if task.credit_charged:
            await credits.refund_one(task.user_id)
            task.credit_charged = False
        await self._update_status(task, hint)
        return False

    async def _process_task(self, task: GenerationTask):
        """处理入口：停机/重启导致任务被取消（CancelledError）时退还已扣额度。"""
        try:
            await self._do_process_task(task)
        except asyncio.CancelledError:
            if task.credit_charged:
                try:
                    await credits.refund_one(task.user_id)
                    task.credit_charged = False
                except Exception:
                    logger.error("取消任务退款失败: user=%s",
                                 task.user_id, exc_info=True)
            raise

    async def _do_process_task(self, task: GenerationTask):
        settings = task.settings
        backend = settings.get("backend", "sd")
        start_time = time.monotonic()
        updater = ThrottledProgressUpdater(
            self._app, task.chat_id, task.status_message_id
        )

        # Ollama 图片反推：完全独立的处理路径（不进翻译/生成/媒体发送流程）
        if backend == "ollama":
            await self._process_reverse(task, updater)
            return

        # 翻译 + 生成 + 结果信息构建（此阶段失败退款；发送阶段失败在 _send_result 内退款）
        try:
            translated = await self._translate_prompt(task, updater)
            raw_data, actual_seed, wf_config, optimized_prompt = await self._generate(
                task, translated, updater)

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
                info = _build_comfy_info(task, settings, display_prompt, actual_seed,
                                         elapsed, wf_config)
                reply_markup = comfy_generation_menu(context_id, settings=settings)
                if wf_config.get("output_type") != "video":
                    raw_data = raw_data.data  # 图片：提取 bytes；视频：保留 ComfyOutput 供 _send_result 取 .filename

            if task.credit_charged:
                remaining = await credits.get_remaining(task.user_id)
                info += f"\n<b>剩余额度:</b> {remaining}"
        except Exception:
            if task.credit_charged:
                await credits.refund_one(task.user_id)
                task.credit_charged = False
            raise

        # Pipeline 任务：结果信息加步骤前缀
        if task.pipeline:
            steps = task.pipeline.get("steps") or []
            idx = task.pipeline.get("idx", 0)
            cur_key = steps[idx] if 0 <= idx < len(steps) else ""
            label = next((w["label"] for w in WORKFLOW_REGISTRY if w["key"] == cur_key), cur_key)
            info = f"<b>⛓ Pipeline {idx + 1}/{len(steps)} · {html.escape(label)}</b>\n{info}"

        # 发送结果（失败时已退款并告知用户，保留状态消息作为告知渠道）
        sent = await self._send_result(task, raw_data, info, reply_markup,
                                       wf_config, updater)

        # Pipeline 自动连跑：回注输出图并组下一步任务入队
        if sent:
            try:
                await self._maybe_chain_pipeline(task, raw_data, wf_config)
            except Exception:
                # 连跑失败不影响已交付的当前步结果（CancelledError 不拦截，正常上抛）
                logger.error("Pipeline 连跑异常: user=%s", task.user_id, exc_info=True)

        # 清理状态消息
        if sent and task.status_message_id is not None:
            try:
                await self._app.bot.delete_message(
                    chat_id=task.chat_id,
                    message_id=task.status_message_id,
                )
            except Exception:
                logger.debug("删除状态消息失败", exc_info=True)

        logger.info("用户 %s 生成完成 | 耗时 %.1fs", task.user_id, elapsed)

    async def _process_reverse(self, task: GenerationTask,
                               updater: ThrottledProgressUpdater) -> None:
        """Ollama 图片反推提示词。

        串行队列内执行，与 ComfyUI 生成天然互斥（防共享 GPU 显存 OOM）：
        先卸载 ComfyUI 模型 → 调用 Ollama 视觉模型（27B 需数分钟）→ 发送
        两种格式的 prompt 文本。任何失败统一退款并通过状态消息告知。
        """
        try:
            # 先卸载 ComfyUI 模型释放显存；失败仅降级（Ollama 会 CPU offload 更多层）
            await updater.set_stage("正在清理显存...")
            try:
                await comfy_api.free_memory()
            except Exception:
                logger.warning("ComfyUI free_memory 失败，降级继续", exc_info=True)

            image_bytes = task.settings.get("_rev_image")
            if not image_bytes:
                raise ValueError("反推任务缺少图片数据")

            await updater.set_stage("正在反推提示词（大模型推理中，可能需要几分钟）...")

            # 心跳：长推理期间定期汇报已用时间，避免状态看似卡死
            start = time.monotonic()
            heartbeat_stop = asyncio.Event()

            async def _heartbeat():
                while not heartbeat_stop.is_set():
                    await asyncio.sleep(COMFY_PROGRESS_HEARTBEAT_INTERVAL)
                    elapsed = int(time.monotonic() - start)
                    try:
                        await updater.set_stage(
                            f"正在反推提示词... 已用 {elapsed // 60}分{elapsed % 60:02d}秒"
                        )
                    except Exception:
                        pass

            heartbeat_task = asyncio.create_task(_heartbeat())
            try:
                sd_tags, krea2_prompt = await ollama_api.reverse_prompt(image_bytes)
            finally:
                heartbeat_stop.set()
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except (asyncio.CancelledError, Exception):
                    pass

            # 构建结果文本（SD 标签词 + Krea 2 句子版，各可点按复制）
            info = (
                "<b>🔍 反推提示词</b>\n\n"
                "<b>SD 标签词（点按复制）：</b>\n"
                f"<code>{_escape_and_truncate(sd_tags, 1500)}</code>\n\n"
                "<b>Krea 2 句子版（点按复制）：</b>\n"
                f"<code>{_escape_and_truncate(krea2_prompt, 2200)}</code>"
            )
            if task.credit_charged:
                remaining = await credits.get_remaining(task.user_id)
                info += f"\n<b>剩余额度:</b> {remaining}"

            reply_to = task.reply_to_message_id or task.original_message_id
            try:
                await updater.set_stage("正在发送...")
                await retry_on_network_error(
                    lambda: self._app.bot.send_message(
                        chat_id=task.chat_id,
                        text=info,
                        parse_mode="HTML",
                        reply_to_message_id=reply_to,
                    ),
                    max_retries=2,
                )
            except Exception as e:
                logger.error("反推结果发送失败: %s", e, exc_info=True)
                if task.credit_charged:
                    await credits.refund_one(task.user_id)
                    task.credit_charged = False
                await self._update_status(task, "结果发送失败，已退还额度。")
                return
        except Exception as e:
            logger.error("反推提示词失败: %s", e, exc_info=True)
            if task.credit_charged:
                await credits.refund_one(task.user_id)
                task.credit_charged = False
            await self._update_status(task, f"反推失败: {str(e)[:200]}")
            return

        # 清理状态消息
        if task.status_message_id is not None:
            try:
                await self._app.bot.delete_message(
                    chat_id=task.chat_id,
                    message_id=task.status_message_id,
                )
            except Exception:
                logger.debug("删除状态消息失败", exc_info=True)
        logger.info("用户 %s 反推完成", task.user_id)

    async def _pipe_notify(self, chat_id: int, text: str):
        """Pipeline 中止通知（独立消息，不复用即将删除的状态消息）。"""
        try:
            await retry_on_network_error(
                lambda: self._app.bot.send_message(chat_id=chat_id, text=text),
                max_retries=2,
            )
        except Exception:
            logger.debug("Pipeline 通知发送失败", exc_info=True)

    async def _maybe_chain_pipeline(self, task: GenerationTask, raw_data,
                                    wf_config: dict) -> None:
        """Pipeline 自动连跑：当前步发送成功后，输出图回传 ComfyUI 并组下一步任务。

        每步独立任务、独立扣 1 额度；任何失败仅通知用户并中止链路，
        不影响已交付的步骤结果。admin 端不创建 pipeline 任务，无需镜像。
        """
        pipe = task.pipeline
        if not pipe:
            return
        steps = pipe.get("steps") or []
        idx = pipe.get("idx", 0)
        next_idx = idx + 1
        if next_idx >= len(steps):
            return
        # 以下守卫正常编排不会命中（菜单层已过滤），但配置可热改，命中时通知中止
        abort_reason = None
        if task.settings.get("backend") != "comfyui":
            abort_reason = "后端已切换"
        elif wf_config.get("output_type") == "video" or not isinstance(raw_data, bytes):
            abort_reason = "上一步产出不是图片，无法回注"
        if abort_reason:
            await self._pipe_notify(
                task.chat_id,
                f"⛓ Pipeline 中止：{abort_reason}，"
                f"已在第 {idx + 1}/{len(steps)} 步完成后停止。")
            return

        total = len(steps)
        next_key = steps[next_idx]
        label = next((w["label"] for w in WORKFLOW_REGISTRY if w["key"] == next_key), next_key)

        next_cfg = COMFY_WORKFLOWS.get(next_key)
        # 双角色图生图（如换装：image1=主图，image2=参考图）
        dual_nodes = (next_cfg or {}).get("load_image_nodes")
        is_dual = bool(dual_nodes) and len(dual_nodes) == 2
        if (not next_cfg or not next_cfg.get("is_img2img")
                or (not next_cfg.get("load_image_node") and not is_dual)):
            await self._pipe_notify(
                task.chat_id,
                f"⛓ Pipeline 中止：第 {next_idx + 1}/{total} 步「{label}」"
                "不是可用的图生图工作流，无法接收上一步输出。")
            return

        # 双图步需要运行时收集的参考图（第 2 个角色）
        ref_name = None
        if is_dual:
            ref_name = (pipe.get("ref_images") or {}).get(next_idx)
            if not ref_name:
                await self._pipe_notify(
                    task.chat_id,
                    f"⛓ Pipeline 中止：第 {next_idx + 1}/{total} 步「{label}」"
                    "缺少参考图，无法继续。")
                return

        try:
            uploaded_name = await comfy_api.upload_image(raw_data)
        except Exception as e:
            logger.error("Pipeline 输出回传失败: %s", e, exc_info=True)
            await self._pipe_notify(
                task.chat_id,
                f"⛓ Pipeline 中止：第 {next_idx + 1}/{total} 步图片回传失败（{e}）。")
            return

        # 扣费（管理员免费，与 handlers/generation._check_and_charge_credit 对齐）
        credit_charged = False
        is_admin = ADMIN_USER_ID is not None and task.user_id == ADMIN_USER_ID
        if not is_admin:
            if not await credits.use_one(task.user_id):
                await self._pipe_notify(
                    task.chat_id,
                    f"⛓ Pipeline 中止：额度不足，已在第 {idx + 1}/{total} 步完成后停止。")
                return
            credit_charged = True

        try:
            # 每步使用自己工作流的默认模型解析链，而非用户全局 comfy_model
            next_settings = copy.deepcopy(task.settings)
            next_settings["comfy_workflow"] = next_key
            default_model = next_cfg.get("default_model")
            if default_model:
                next_settings["comfy_model"] = default_model
            else:
                next_settings.pop("comfy_model", None)
            if is_dual:
                # 双图步：产出图 → 第 1 角色（主图），参考图 → 第 2 角色
                roles = list(dual_nodes.keys())
                next_settings["_uploaded_images"] = {
                    roles[0]: uploaded_name, roles[1]: ref_name,
                }
                next_settings.pop("_uploaded_image", None)
            else:
                next_settings["_uploaded_image"] = uploaded_name
                next_settings.pop("_uploaded_images", None)

            # 每步提示词独立（无预设时回退上一步 prompt）
            next_prompt = (pipe.get("prompts") or {}).get(next_idx, task.prompt)

            status_id = None
            try:
                status_msg = await retry_on_network_error(
                    lambda: self._app.bot.send_message(
                        chat_id=task.chat_id,
                        text=f"⛓ Pipeline 步骤 {next_idx + 1}/{total} · {label}\n准备中..."),
                    max_retries=2,
                )
                status_id = status_msg.message_id
            except Exception:
                logger.debug("Pipeline 状态消息创建失败", exc_info=True)

            next_task = GenerationTask(
                user_id=task.user_id,
                chat_id=task.chat_id,
                prompt=next_prompt,
                settings=next_settings,
                status_message_id=status_id,
                credit_charged=credit_charged,
                pipeline={
                    "steps": steps,
                    "idx": next_idx,
                    "prompts": pipe.get("prompts") or {},
                    "ref_images": pipe.get("ref_images") or {},
                },
            )
            try:
                await self.enqueue(next_task)
            except Exception:
                logger.error("Pipeline 下一步入队失败", exc_info=True)
                if credit_charged:
                    await credits.refund_one(task.user_id)
                await self._pipe_notify(
                    task.chat_id,
                    f"⛓ Pipeline 中止：第 {next_idx + 1}/{total} 步提交失败，已退还额度。")
        except asyncio.CancelledError:
            # 停机排空竞态：额度已扣但任务未入队，对称退款后上抛
            if credit_charged:
                try:
                    await credits.refund_one(task.user_id)
                except Exception:
                    logger.error("Pipeline 停机退款失败: user=%s",
                                 task.user_id, exc_info=True)
            raise

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

# Telegram sendPhoto 上限 10MB；但 5-9MB 的 PNG 经代理上传也频繁超时（write_timeout 内传不完），
# 阈值定为 3MB：生成图普遍超过，统一走 JPEG 重编码（q90 后 1-2MB，秒传）
TELEGRAM_PHOTO_MAX_BYTES = int(3 * 1024 * 1024)


def _fit_photo(data: bytes) -> tuple[bytes, str | None]:
    """适配 Telegram sendPhoto 的 10MB 限制。

    返回 (字节, 处理方式)：
    - None：未超限，原样发送
    - "jpeg"：已重编码为 JPEG（缩小 payload，同时避免 413 和上传超时）
    - "document"：重编码后仍超限或无法解码，调用方应改用 send_document 发原字节
    """
    if len(data) <= TELEGRAM_PHOTO_MAX_BYTES:
        return data, None
    try:
        img = Image.open(io.BytesIO(data))
        if img.mode != "RGB":
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90, optimize=True)
        jpeg = buf.getvalue()
    except Exception:
        logger.warning("图片重编码失败，改用文件发送", exc_info=True)
        return data, "document"
    if len(jpeg) <= TELEGRAM_PHOTO_MAX_BYTES:
        logger.info("图片 %d 字节超 sendPhoto 限制，已重编码为 JPEG（%d 字节）",
                    len(data), len(jpeg))
        return jpeg, "jpeg"
    return data, "document"


def _is_reply_not_found(exc: Exception) -> bool:
    """BadRequest "replied message not found"：被回复的原消息已被删除。"""
    return (isinstance(exc, telegram.error.BadRequest)
            and "replied message not found" in str(exc).lower())


def _truncate_escaped(escaped: str, max_chars: int) -> str:
    """截断已转义的 HTML 文本，保证 <= max_chars 且不切断实体。"""
    if max_chars < 1:
        return ""
    if len(escaped) <= max_chars:
        return escaped
    seg = escaped[:max_chars - 1]
    # 末尾若切在实体中间（& 之后没有 ; 闭合），回退到实体起点
    if seg.rfind("&") > seg.rfind(";"):
        seg = seg[:seg.rfind("&")]
    return seg + "…"


def _escape_and_truncate(text: str, max_chars: int) -> str:
    """HTML 转义并截断，保证返回值长度 <= max_chars 且不切断实体。

    截断在转义后进行，因此 max_chars 按最终发送长度计算，
    不受 escape 膨胀（& → &amp; 等）影响。
    """
    return _truncate_escaped(html.escape(text), max_chars)


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


def _build_comfy_info(task, settings: dict, translated: str, seed: int,
                      elapsed: float, wf_config: dict) -> str:
    is_video = wf_config.get("output_type") == "video"
    model_selectable = wf_config.get("model_selectable", True)

    if is_video:
        # 视频工作流：显示比例/画质/长度
        aspect = settings.get("comfy_video_aspect", "9:16")
        aspect_cfg = COMFY_VIDEO_ASPECTS.get(aspect, COMFY_VIDEO_ASPECTS["9:16"])
        resolution = settings.get("comfy_video_resolution", "480p")
        resolution_cfg = COMFY_VIDEO_RESOLUTIONS.get(resolution, COMFY_VIDEO_RESOLUTIONS["480p"])
        frames_key = str(settings.get("comfy_video_frames",
                                      COMFY_VIDEO_FRAMES_PRESETS[DEFAULT_VIDEO_FRAMES_KEY]["frames"]))
        frames_cfg = COMFY_VIDEO_FRAMES_PRESETS.get(frames_key,
                                                    COMFY_VIDEO_FRAMES_PRESETS[DEFAULT_VIDEO_FRAMES_KEY])
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
