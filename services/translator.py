import logging

from openai import AsyncOpenAI

from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL
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

TRANSLATE_PROMPT = (
    "You are a Stable Diffusion prompt translator. "
    "Translate the following Chinese prompt into English for use with Stable Diffusion. "
    "Output ONLY the translated English prompt, nothing else. "
    "Preserve any formatting like parentheses for emphasis, aspect ratios, or SD-specific syntax. "
    "If the text is already in English, return it unchanged."
)


async def translate(text: str) -> str:
    """将中文提示词翻译为英文，失败或未配置 API key 时返回原文。"""
    if not DEEPSEEK_API_KEY:
        return text
    try:
        response = await retry_on_network_error(
            lambda: _get_client().chat.completions.create(
                model="deepseek-v4-flash",
                messages=[
                    {"role": "system", "content": TRANSLATE_PROMPT},
                    {"role": "user", "content": text},
                ],
                temperature=0.3,
                max_tokens=2048,
            ),
        )
        result = (response.choices[0].message.content or "").strip()
        return result if result else text
    except Exception:
        logger.warning("翻译失败，使用原文", exc_info=True)
        return text
