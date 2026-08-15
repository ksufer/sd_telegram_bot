"""Ollama 本地大模型调用 — 图片提示词反推。

入口：handlers/rev_prompt.py，经 GenerationQueue 串行执行（与 ComfyUI 互斥，
防止共享 GPU 显存冲突 OOM）。单次 /api/chat 调用同时产出 SD 标签词 + Krea 2
句子版两种格式，JSON 解析失败时追加修复指令重试一次。
"""

import base64
import io
import json
import logging

import httpx
from PIL import Image

from config import OLLAMA_BASE_URL, OLLAMA_MODEL, OLLAMA_TIMEOUT, REV_PROMPT_SYSTEM

logger = logging.getLogger(__name__)

# 防御性缩放上限（Telegram photo 本身最长边 ≤1280px，此步通常为 no-op）
_MAX_EDGE = 1568


class OllamaError(Exception):
    """Ollama 调用失败（网络错误、JSON 解析失败、模型无响应）。"""


def _prepare_image(image_bytes: bytes) -> str:
    """图片预处理：转 RGB、缩到最长边 ≤1568px、JPEG q85，返回 base64。"""
    img = Image.open(io.BytesIO(image_bytes))
    if img.mode != "RGB":
        img = img.convert("RGB")
    w, h = img.size
    if max(w, h) > _MAX_EDGE:
        scale = _MAX_EDGE / max(w, h)
        img = img.resize((round(w * scale), round(h * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85, optimize=True)
    return base64.b64encode(buf.getvalue()).decode()


def _parse_result(content: str) -> tuple[str, str]:
    """解析模型输出 JSON，返回 (sd_tags, krea2_prompt)。失败抛 OllamaError。"""
    text = content.strip()
    # 兼容 ```json ... ``` 围栏
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise OllamaError(f"反推结果 JSON 解析失败: {e}") from e
    if not isinstance(data, dict):
        raise OllamaError("反推结果不是 JSON 对象")
    sd_tags = data.get("sd_tags")
    krea2 = data.get("krea2_prompt")
    if (not isinstance(sd_tags, str) or not isinstance(krea2, str)
            or not sd_tags.strip() or not krea2.strip()):
        raise OllamaError("反推结果缺少 sd_tags/krea2_prompt 字段")
    return sd_tags.strip(), krea2.strip()


async def _chat(client: httpx.AsyncClient, messages: list) -> str:
    """调用 /api/chat（非流式），返回 message.content。失败抛 OllamaError。"""
    try:
        resp = await client.post("/api/chat", json={
            "model": OLLAMA_MODEL,
            "messages": messages,
            "stream": False,
            "format": "json",
            "think": False,
            # 保持模型常驻以便失败重试时免于重新加载 17GB；调用结束后显式卸载
            "keep_alive": "5m",
            "options": {"temperature": 0.2},
        })
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPStatusError as e:
        raise OllamaError(
            f"Ollama 返回错误 ({e.response.status_code}): {e.response.text[:200]}"
        ) from e
    except httpx.HTTPError as e:
        raise OllamaError(f"Ollama 服务不可用: {e}") from e
    content = (data.get("message") or {}).get("content", "")
    if not content:
        raise OllamaError("Ollama 返回空内容")
    return content


async def _unload_model(client: httpx.AsyncClient) -> None:
    """显式卸载模型，归还显存给 ComfyUI。失败仅告警（调用方继续）。"""
    try:
        await client.post("/api/generate", json={
            "model": OLLAMA_MODEL,
            "keep_alive": 0,
        })
    except Exception:
        logger.warning("Ollama 模型卸载失败（下次生成前 ComfyUI 可能 OOM）", exc_info=True)


async def reverse_prompt(image_bytes: bytes, extra: str = "") -> tuple[str, str]:
    """反推图片提示词。返回 (sd_tags, krea2_prompt)。失败抛 OllamaError。

    extra: 用户额外要求（如「写实风格」「去掉眼镜」），非空时附加到请求中。
    """
    image_b64 = _prepare_image(image_bytes)
    user_text = "请反推这张图片。"
    if extra and extra.strip():
        user_text += f"\n\n额外要求：{extra.strip()}"
    messages = [
        {"role": "system", "content": REV_PROMPT_SYSTEM},
        {"role": "user", "content": user_text, "images": [image_b64]},
    ]

    timeout = httpx.Timeout(connect=10, read=OLLAMA_TIMEOUT, write=30, pool=10)
    async with httpx.AsyncClient(base_url=OLLAMA_BASE_URL, timeout=timeout) as client:
        try:
            content = await _chat(client, messages)
            try:
                return _parse_result(content)
            except OllamaError:
                # 追加修复指令重试一次（同一会话，模型已常驻无需重新加载）
                logger.warning("反推结果解析失败，追加修复指令重试")
                messages.append({"role": "assistant", "content": content})
                messages.append({
                    "role": "user",
                    "content": (
                        "你的输出不符合格式要求。请只输出一个合法的 JSON 对象，"
                        "包含 sd_tags 和 krea2_prompt 两个字符串字段，不要输出其他任何内容。"
                    ),
                })
                content = await _chat(client, messages)
                return _parse_result(content)
        finally:
            await _unload_model(client)
