import base64
import httpx
from config import SD_API_BASE


async def get_models() -> list[dict]:
    """获取可用模型列表。"""
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(f"{SD_API_BASE}/sdapi/v1/sd-models")
        r.raise_for_status()
        return r.json()


async def get_current_model() -> str:
    """获取当前加载的模型名称。"""
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(f"{SD_API_BASE}/sdapi/v1/options")
        r.raise_for_status()
        return r.json()["sd_model_checkpoint"]


async def set_model(model_name: str) -> dict:
    """切换模型。"""
    payload = {"sd_model_checkpoint": model_name}
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(f"{SD_API_BASE}/sdapi/v1/options", json=payload)
        r.raise_for_status()
        return r.json()


async def txt2img(params: dict) -> bytes:
    """调用 txt2img API，返回生成的图片 bytes。"""
    async with httpx.AsyncClient(timeout=180) as client:
        r = await client.post(f"{SD_API_BASE}/sdapi/v1/txt2img", json=params)
        r.raise_for_status()
        data = r.json()
        # API 返回 base64 编码的图片列表
        img_base64 = data["images"][0]
        return base64.b64decode(img_base64)
