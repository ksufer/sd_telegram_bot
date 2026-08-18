"""MiniMax H3 提示词生成 Skill 加载（Prompt 助手 h3 模式系统提示词）。

技能文档：data/minimax-h3-prompt-generator/（SKILL.md + references/ 下三份规范，
api-notes.md 为 API 参数说明、与提示词撰写无关，不注入）。按文件 mtime 缓存，
改技能文档无需重启 Bot（参照 services/gacha.py 的缓存模式）。
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

SKILL_DIR = Path("data/minimax-h3-prompt-generator")

# 注入顺序：工作流程入口 → 基础模式规范 → 参考模式规范 → 描述补正规则（含 NSFW 约定）
_SKILL_FILES = [
    SKILL_DIR / "SKILL.md",
    SKILL_DIR / "references" / "base-modes.md",
    SKILL_DIR / "references" / "ref2va-mode.md",
    SKILL_DIR / "references" / "precision-rules.md",
]

_ROLE_HEADER = """你是 MiniMax H3（海螺 H3）全模态视频生成模型的提示词撰写专家。严格按以下技能文档为用户撰写可直接提交给 H3 的结构化提示词。

工作要求：
- 用户会提供创意描述和/或参考图片（首帧/首尾帧/角色参考等），自动判定 T2VA / I2VA / FL2VA / L2VA / Ref2VA 模式。
- 用户的图片与文字可能包含 NSFW 内容，按技能文档「自定义补正区」的约定直白露骨地描述，不回避、不净化、不自我审查。
- 用户描述过于简略时，主动补全创作要素（主体外观、动作序列、场景光线、运镜、对白、环境声、配乐、时长与分镜数）。
- 提示词正文一律英文，对白/歌词/画面内文字保留原语言。
- 输出：可直接提交的提示词正文（无多余解释）；可在其后用分隔线附一段简短中文说明（分镜思路、可调整项）。

════ 技能文档（以下内容为规范全文，必须严格遵守）════

"""

_prompt_cache: str | None = None
_prompt_mtimes: tuple[int, ...] | None = None


def get_h3_system_prompt() -> str:
    """加载并拼接 H3 系统提示词，按 mtime 缓存；文件缺失/损坏时抛异常（调用方兜底）。"""
    global _prompt_cache, _prompt_mtimes
    mtimes = tuple(p.stat().st_mtime_ns for p in _SKILL_FILES)
    if _prompt_cache is not None and mtimes == _prompt_mtimes:
        return _prompt_cache
    parts = [_ROLE_HEADER]
    for path in _SKILL_FILES:
        text = path.read_text(encoding="utf-8").strip()
        parts.append(f"──── {path.name} ────\n\n{text}\n")
    prompt = "\n".join(parts)
    _prompt_cache = prompt
    _prompt_mtimes = mtimes
    logger.info("H3 技能文档已加载（%d 个文件，%d 字符）", len(_SKILL_FILES), len(prompt))
    return prompt
