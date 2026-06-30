import logging

from openai import AsyncOpenAI

from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL
from services.network import retry_on_network_error

logger = logging.getLogger(__name__)

_client = AsyncOpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)

FACE_EXTRACT_PROMPT = (
    "You are a Stable Diffusion prompt editor for FaceDetailer re-draw. "
    "From the given prompt, extract ONLY: "
    "- Character identity (name, who they are) "
    "- Face/appearance traits (eyes, hair, skin, expressions, glasses, etc.) "
    "- Artistic style keywords (lighting, photography style, filters, color grading, composition) "
    "REMOVE: nudity/NSFW terms, body parts below neck, clothing details, poses, scene/setting. "
    "Keep the original language. Output ONLY the extracted prompt, nothing else."
)


async def extract_face_prompt(text: str) -> str:
    """从主提示词中提取人物+画风关键词，失败时返回原文。"""
    try:
        response = await retry_on_network_error(
            lambda: _client.chat.completions.create(
                model="deepseek-v4-flash",
                messages=[
                    {"role": "system", "content": FACE_EXTRACT_PROMPT},
                    {"role": "user", "content": text},
                ],
                temperature=0.3,
                max_tokens=1024,
            ),
        )
        return response.choices[0].message.content.strip()
    except Exception:
        logger.warning("脸部提示词提取失败，使用原文", exc_info=True)
        return text
