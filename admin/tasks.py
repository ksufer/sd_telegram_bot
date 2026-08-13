"""网页端生成任务管理：镜像 Bot 端 queue.py 的 ComfyUI 生成流程。

阶段：翻译(可选) → 脸部提示词提取(与 queue.py 相同的门控) → 上传图片 →
ComfyUI 生成（心跳更新已用时间）→ 落盘历史。免额度（仅管理员可用）。
"""

import asyncio
import logging
import random
import time
import uuid

from services import comfy_api, prompt_log
from services.face_prompt import extract_face_prompt
from services.translator import translate

from admin import store

logger = logging.getLogger(__name__)

_tasks: dict[str, dict] = {}


def get_task(task_id: str) -> dict | None:
    return _tasks.get(task_id)


def _public(task: dict) -> dict:
    return {
        "id": task["id"],
        "status": task["status"],
        "stage": task["stage"],
        "elapsed": int(time.monotonic() - task["started_at"]) if task["started_at"] else 0,
        "error": task["error"],
        "result_id": task["result_id"],
    }


async def create_task(wf_key: str, prompt: str, settings: dict,
                      images: dict[str, bytes]) -> dict:
    """创建并启动生成任务，立即返回任务快照。"""
    task_id = uuid.uuid4().hex[:12]
    task = {
        "id": task_id,
        "status": "running",
        "stage": "排队中...",
        "started_at": time.monotonic(),
        "error": None,
        "result_id": None,
    }
    _tasks[task_id] = task
    # 最多保留 100 个任务记录，防内存膨胀
    if len(_tasks) > 100:
        for old_id in sorted(_tasks, key=lambda i: _tasks[i]["started_at"])[:-100]:
            _tasks.pop(old_id, None)
    asyncio.create_task(_run(task, wf_key, prompt, settings, images))
    return _public(task)


async def _set_stage(task: dict, stage: str) -> None:
    task["stage"] = stage


async def _run(task: dict, wf_key: str, prompt: str, settings: dict,
               images: dict[str, bytes]) -> None:
    start = time.monotonic()

    async def on_progress(elapsed: int) -> None:
        task["stage"] = f"正在生成（ComfyUI）... 已用 {elapsed // 60}分{elapsed % 60:02d}秒"

    try:
        _, wf_config = comfy_api._get_wf_config(settings)

        # 翻译（与 bot 一致：comfy_translate 开关 + img2img 空 prompt 跳过）
        translated = prompt
        if settings.get("comfy_translate") and not (images and not prompt):
            await _set_stage(task, "正在翻译提示词...")
            translated = await translate(prompt)

        # 脸部提示词（与 queue.py 相同门控：开关关闭时不提取）
        face_prompt = None
        manual_face = settings.get("comfy_face_prompt", "")
        facedetailer_off = ("facedetailer_switch_node" in wf_config
                            and not settings.get("comfy_facedetailer_enabled", True))
        if wf_config.get("face_detailer_prompt_node") and not facedetailer_off:
            if manual_face:
                face_prompt = manual_face
            else:
                await _set_stage(task, "正在提取脸部提示词...")
                face_prompt = await extract_face_prompt(prompt)

        # 上传图片（单图/多图角色）
        uploaded_image = None
        uploaded_images = None
        if images:
            await _set_stage(task, "正在上传图片...")
            if "load_image_nodes" in wf_config:
                uploaded_images = {}
                for role, content in images.items():
                    uploaded_images[role] = await comfy_api.upload_image(content)
            else:
                first = next(iter(images.values()))
                uploaded_image = await comfy_api.upload_image(first)

        seed = int(settings.get("comfy_seed", -1))
        if seed == -1:
            seed = random.randint(0, 1125899906842624)

        await _set_stage(task, "正在生成（ComfyUI）...")
        output, actual_seed, optimized_prompt = await comfy_api.generate(
            translated, settings, seed,
            uploaded_image=uploaded_image,
            uploaded_images=uploaded_images,
            face_prompt=face_prompt,
            progress_callback=on_progress,
        )

        await _set_stage(task, "正在保存结果...")
        elapsed = round(time.monotonic() - start, 1)
        ext = "." + output.filename.rsplit(".", 1)[-1].lower() if "." in output.filename else ".png"
        meta = {
            "wf_key": wf_key,
            "label": wf_config.get("label", wf_key),
            "prompt": prompt,
            "translated": translated,
            "optimized_prompt": optimized_prompt or "",
            "seed": actual_seed,
            "elapsed": elapsed,
            "kind": output.kind,
            "settings": {k: v for k, v in settings.items() if not k.startswith("_")},
        }
        task["result_id"] = store.save_result(meta, output.data, ext)
        # 提示词日志：完整提示词 + 缩略图按日落盘（与 Bot 端同一目录），失败不影响主流程
        prompt_log.log_generation(
            prompt=prompt,
            final_prompt=optimized_prompt or translated,
            seed=actual_seed,
            model=settings.get("comfy_model", ""),
            wf_key=wf_key,
            label=wf_config.get("label", wf_key),
            source="web",
            user_id=0,
            elapsed=elapsed,
            image_bytes=output.data if output.kind == "image" else None,
        )
        task["status"] = "done"
        task["stage"] = f"完成，用时 {elapsed}秒"
        logger.info("网页端生成完成: wf=%s, 用时 %ss", wf_key, elapsed)
    except Exception as e:
        logger.error("网页端生成失败: %s", e, exc_info=True)
        task["status"] = "error"
        task["error"] = str(e)[:500]
        task["stage"] = "生成失败"
