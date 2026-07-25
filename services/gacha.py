"""灵感抽卡词库加载与抽词逻辑（纯逻辑，无 Telegram 依赖）。

词库：data/prompt_gacha.json，按文件 mtime 缓存，改词无需重启 Bot。
结构见文件内 schema_version=1：dimensions[]，每个维度含
key/label/skip_chance/nsfw_only/sfw[]/nsfw[]，词为 {"en", "zh"}。
"""

import json
import logging
import random
from pathlib import Path

logger = logging.getLogger(__name__)

POOL_PATH = Path("data/prompt_gacha.json")

MODE_SFW = "sfw"
MODE_NSFW = "nsfw"

_pool_cache: dict | None = None
_pool_mtime_ns: int | None = None


def load_pool() -> dict:
    """加载词库，按 mtime 缓存；文件缺失/损坏时抛异常（调用方兜底）。"""
    global _pool_cache, _pool_mtime_ns
    mtime = POOL_PATH.stat().st_mtime_ns
    if _pool_cache is not None and mtime == _pool_mtime_ns:
        return _pool_cache
    with open(POOL_PATH, encoding="utf-8") as f:
        data = json.load(f)
    _pool_cache = data
    _pool_mtime_ns = mtime
    logger.info("抽卡词库已加载（%d 个维度）", len(data.get("dimensions", [])))
    return data


def _dim_words(dim: dict, mode: str) -> list[dict]:
    """维度在当前模式下的可抽词列表。"""
    words = list(dim.get("sfw", []))
    if mode == MODE_NSFW:
        words += dim.get("nsfw", [])
    return words


def _pick(dim: dict, mode: str) -> dict | None:
    """从维度抽一个词，返回卡片条目；不可用返回 None。"""
    words = _dim_words(dim, mode)
    if not words:
        return None
    w = random.choice(words)
    return {"label": dim["label"], "en": w["en"], "zh": w["zh"]}


def draw(pool: dict, mode: str) -> dict:
    """整卡抽取：{dim_key: {"label", "en", "zh"}}，按维度顺序。"""
    card: dict = {}
    for dim in pool.get("dimensions", []):
        if dim.get("nsfw_only") and mode != MODE_NSFW:
            continue
        if random.random() < dim.get("skip_chance", 0.0):
            continue
        entry = _pick(dim, mode)
        if entry:
            card[dim["key"]] = entry
    return card


def reroll(pool: dict, mode: str, card: dict, dim_key: str) -> dict:
    """重抽单个维度（原地保持顺序）；维度不存在或模式不可用则原样返回。"""
    for dim in pool.get("dimensions", []):
        if dim.get("key") != dim_key:
            continue
        if dim.get("nsfw_only") and mode != MODE_NSFW:
            return card
        entry = _pick(dim, mode)
        if entry:
            card[dim_key] = entry
        return card
    return card


def build_prompt(card: dict, lang: str = "zh") -> str:
    """拼接组合 prompt：zh=中文（默认，当前模型中文支持更好），en=英文。"""
    if lang == "en":
        return ", ".join(w["en"] for w in card.values())
    return "，".join(w["zh"] for w in card.values())
