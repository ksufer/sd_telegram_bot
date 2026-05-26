import copy
import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

DATA_DIR = Path("data/user_settings")


def _filepath(user_id: int) -> Path:
    return DATA_DIR / f"{user_id}.json"


def load(user_id: int, defaults: dict) -> dict:
    filepath = _filepath(user_id)
    if not filepath.exists():
        return copy.deepcopy(defaults)

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("加载用户 %s 设置失败: %s，使用默认配置", user_id, e)
        return copy.deepcopy(defaults)

    # 浅合并：用文件值覆盖默认值，补全未来新增的字段
    # 当前 settings 为一层 dict；若未来出现嵌套结构再考虑 deep merge
    merged = copy.deepcopy(defaults)
    merged.update(data)
    return merged


def save(user_id: int, settings: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    filepath = _filepath(user_id)
    tmp_path = filepath.with_suffix(".json.tmp")

    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, filepath)
    except OSError as e:
        logger.error("保存用户 %s 设置失败: %s", user_id, e)
