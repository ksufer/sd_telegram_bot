import asyncio
import base64
import logging

import httpx
from config import SD_API_BASE, SAMPLER_PRESETS

logger = logging.getLogger(__name__)


async def get_models() -> list[dict]:
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(f"{SD_API_BASE}/sdapi/v1/sd-models")
        r.raise_for_status()
        return r.json()


async def get_current_model() -> str:
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(f"{SD_API_BASE}/sdapi/v1/options")
        r.raise_for_status()
        return r.json()["sd_model_checkpoint"]


async def set_model(model_name: str) -> dict:
    payload = {"sd_model_checkpoint": model_name}
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(f"{SD_API_BASE}/sdapi/v1/options", json=payload)
        r.raise_for_status()
        return r.json()


async def get_samplers() -> list[str]:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{SD_API_BASE}/sdapi/v1/samplers")
            r.raise_for_status()
            data = r.json()
            if isinstance(data, list):
                if data and isinstance(data[0], dict):
                    return [s["name"] for s in data]
                return data
    except Exception:
        logger.debug("获取采样器列表失败，使用静态回退列表")
    return list(SAMPLER_PRESETS)


async def get_progress() -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{SD_API_BASE}/sdapi/v1/progress")
            r.raise_for_status()
            return r.json()
    except Exception:
        return None


async def txt2img(params: dict, progress_callback=None) -> bytes:
    async with httpx.AsyncClient(timeout=180) as client:
        post_task = asyncio.create_task(
            client.post(f"{SD_API_BASE}/sdapi/v1/txt2img", json=params)
        )

        if progress_callback:
            poll_failures = 0
            while not post_task.done():
                await asyncio.sleep(2)
                progress = await get_progress()
                if progress is not None:
                    poll_failures = 0
                    try:
                        ratio = progress.get("progress", 0)
                        eta = progress.get("eta_relative", None)
                        progress_callback(ratio, eta)
                    except Exception:
                        logger.debug("进度回调异常", exc_info=True)
                else:
                    poll_failures += 1
                    if poll_failures == 3:
                        logger.warning("连续 %s 次进度轮询失败", poll_failures)
        else:
            await post_task

        r = await post_task
        r.raise_for_status()
        data = r.json()
        img_base64 = data["images"][0]
        return base64.b64decode(img_base64)
