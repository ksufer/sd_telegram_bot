import logging
import re

from openai import AsyncOpenAI

from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, NSFW_BODY_KEYWORDS
from services.network import retry_on_network_error

logger = logging.getLogger(__name__)

_client = None


def _get_client() -> AsyncOpenAI:
    """延迟初始化客户端：避免缺少 API key 时 import 即崩溃。"""
    global _client
    if _client is None:
        _client = AsyncOpenAI(
            api_key=DEEPSEEK_API_KEY,
            base_url=DEEPSEEK_BASE_URL,
            timeout=30,
            max_retries=0,  # 重试交给外层 retry_on_network_error
        )
    return _client

FACE_EXTRACT_PROMPT = (
    "You are a Stable Diffusion prompt editor for FaceDetailer re-draw. "
    "From the given prompt, extract ONLY: "
    "- Character identity (name, who they are) "
    "- Face/appearance traits (eyes, hair, skin, expressions, glasses, etc.) "
    "- Artistic style keywords (lighting, photography style, filters, color grading, composition) "
    "REMOVE: nudity/NSFW terms, body parts below neck, clothing details, poses, scene/setting. "
    "Keep the original language. Output ONLY the extracted prompt, nothing else."
)


def _sanitize_nsfw(text: str) -> str:
    """将 NSFW 敏感词替换为 [body] 占位符，避免发送到第三方 API。"""
    for kw in NSFW_BODY_KEYWORDS:
        # 英文（纯 ASCII）加词边界，避免误伤 glasses/button 等正常词汇；
        # 中文无词边界概念，保持子串匹配
        pattern = rf"\b{re.escape(kw)}\b" if kw.isascii() else re.escape(kw)
        text = re.sub(pattern, "[body]", text, flags=re.IGNORECASE)
    return text


async def extract_face_prompt(text: str) -> str:
    """从主提示词中提取人物+画风关键词。发送前脱敏，失败或未配置 API key 时返回空字符串。"""
    if not DEEPSEEK_API_KEY:
        return ""
    try:
        response = await retry_on_network_error(
            lambda: _get_client().chat.completions.create(
                model="deepseek-v4-flash",
                messages=[
                    {"role": "system", "content": FACE_EXTRACT_PROMPT},
                    {"role": "user", "content": _sanitize_nsfw(text)},
                ],
                temperature=0.3,
                max_tokens=1024,
            ),
        )
        return (response.choices[0].message.content or "").strip()
    except Exception:
        logger.warning("脸部提示词提取失败，留空", exc_info=True)
        return ""
