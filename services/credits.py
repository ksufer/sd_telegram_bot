"""额度管理：读写用户额度，async + asyncio.Lock + 原子写入。"""

import asyncio
import json
import logging
import os
from pathlib import Path

from config import DEFAULT_CREDIT_QUOTA

logger = logging.getLogger(__name__)

CREDITS_DIR = Path("data/credits")
_credit_lock = asyncio.Lock()


def _filepath(user_id: int) -> Path:
    return CREDITS_DIR / f"{user_id}.json"


def _load(user_id: int) -> dict:
    """内部同步读（调用方需持有锁）。损坏文件返回默认值。"""
    filepath = _filepath(user_id)
    if not filepath.exists():
        return {"total_quota": DEFAULT_CREDIT_QUOTA, "used": 0}

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("用户 %s 额度文件损坏: %s，使用默认额度", user_id, e)
        return {"total_quota": DEFAULT_CREDIT_QUOTA, "used": 0}

    # 补全缺失字段
    data.setdefault("total_quota", DEFAULT_CREDIT_QUOTA)
    data.setdefault("used", 0)
    return data


def _save(user_id: int, data: dict) -> None:
    """内部同步写（调用方需持有锁）。原子写入。"""
    CREDITS_DIR.mkdir(parents=True, exist_ok=True)
    filepath = _filepath(user_id)
    tmp_path = filepath.with_suffix(".json.tmp")

    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, filepath)
    except OSError as e:
        logger.error("保存用户 %s 额度失败: %s", user_id, e)
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


async def get_remaining(user_id: int) -> int:
    async with _credit_lock:
        data = _load(user_id)
    return data["total_quota"] - data["used"]


async def use_one(user_id: int) -> bool:
    """扣减 1 额度。返回 True 表示扣减成功。"""
    async with _credit_lock:
        data = _load(user_id)
        remaining = data["total_quota"] - data["used"]
        if remaining <= 0:
            return False
        data["used"] += 1
        _save(user_id, data)
        return True


async def refund_one(user_id: int) -> None:
    """返还 1 额度。"""
    async with _credit_lock:
        data = _load(user_id)
        if data["used"] > 0:
            data["used"] -= 1
            _save(user_id, data)


async def set_quota(user_id: int, total: int) -> None:
    """设置总配额。"""
    async with _credit_lock:
        data = _load(user_id)
        data["total_quota"] = total
        _save(user_id, data)


async def add_quota(user_id: int, amount: int) -> None:
    """增加总配额。"""
    async with _credit_lock:
        data = _load(user_id)
        data["total_quota"] += amount
        _save(user_id, data)


async def get_stats(user_id: int) -> dict:
    """返回额度统计信息。"""
    async with _credit_lock:
        data = _load(user_id)
    return {
        "total_quota": data["total_quota"],
        "used": data["used"],
        "remaining": data["total_quota"] - data["used"],
    }
