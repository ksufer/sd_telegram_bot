"""提示词日志：按日记录每次成功生成的完整提示词 + 缩略图。

存储结构（Bot 与 Admin 网页端共用同一目录）：
    data/prompt_log/<YYYY-MM-DD>/
        HHMMSS-xxxxxx.jpg   压缩缩略图（视频输出无图）
        HHMMSS-xxxxxx.txt   完整未截断的最终提示词
        HHMMSS-xxxxxx.json  元数据（Admin Web 管理页数据源）

记录失败只记 warning 日志，绝不影响生成主流程。
"""

import io
import json
import logging
import os
import re
import tempfile
import time
import uuid
from datetime import datetime
from pathlib import Path

from PIL import Image

from config import PROMPT_LOG_ENABLED, PROMPT_LOG_THUMB_SIZE, PROMPT_LOG_JPEG_QUALITY

logger = logging.getLogger(__name__)

LOG_DIR = Path(os.getenv("DATA_DIR", "data")) / "prompt_log"

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ID_RE = re.compile(r"^\d{6}-[0-9a-f]{6}$")


def _atomic_write(path: Path, content: str) -> None:
    """同目录 tmp + os.replace，避免半写文件。"""
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _save_thumbnail(image_bytes: bytes, path: Path) -> bool:
    """压缩为 JPEG 缩略图（长边 PROMPT_LOG_THUMB_SIZE）。失败返回 False。"""
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img.thumbnail((PROMPT_LOG_THUMB_SIZE, PROMPT_LOG_THUMB_SIZE))
        if img.mode != "RGB":
            img = img.convert("RGB")
        img.save(path, format="JPEG", quality=PROMPT_LOG_JPEG_QUALITY, optimize=True)
        return True
    except Exception:
        logger.warning("提示词日志缩略图保存失败", exc_info=True)
        return False


def log_generation(*, prompt: str, final_prompt: str, seed: int, model: str,
                   wf_key: str, label: str, source: str, user_id: int,
                   elapsed: float, image_bytes: bytes | None) -> None:
    """记录一次成功生成。任何失败仅记日志，不向调用方抛异常。"""
    if not PROMPT_LOG_ENABLED:
        return
    try:
        now = datetime.now()
        date = now.strftime("%Y-%m-%d")
        record_id = f"{now.strftime('%H%M%S')}-{uuid.uuid4().hex[:6]}"
        day_dir = LOG_DIR / date
        day_dir.mkdir(parents=True, exist_ok=True)

        image_name = None
        if image_bytes:
            if _save_thumbnail(image_bytes, day_dir / f"{record_id}.jpg"):
                image_name = f"{record_id}.jpg"

        (day_dir / f"{record_id}.txt").write_text(final_prompt or "", encoding="utf-8")

        meta = {
            "id": record_id,
            "ts": int(now.timestamp()),
            "prompt": prompt,
            "final_prompt": final_prompt,
            "seed": seed,
            "model": model,
            "workflow": wf_key,
            "label": label,
            "source": source,  # "bot" | "web"
            "user_id": user_id,
            "elapsed": round(elapsed, 1),
            "image": image_name,
            "favorite": False,
        }
        _atomic_write(day_dir / f"{record_id}.json",
                      json.dumps(meta, ensure_ascii=False, indent=2))
    except Exception:
        logger.warning("提示词日志记录失败", exc_info=True)


# ── Admin Web 查询/管理 ──────────────────────────────────────

def list_days() -> list[str]:
    """所有日志日期（新→旧）。"""
    if not LOG_DIR.exists():
        return []
    return sorted(
        (d.name for d in LOG_DIR.iterdir()
         if d.is_dir() and _DATE_RE.match(d.name)),
        reverse=True,
    )


def list_records(date: str) -> list[dict]:
    """某天的全部记录（新→旧）。非法日期返回空列表。"""
    if not _DATE_RE.match(date or ""):
        return []
    day_dir = LOG_DIR / date
    if not day_dir.is_dir():
        return []
    items = []
    for f in day_dir.glob("*.json"):
        try:
            with open(f, encoding="utf-8") as fp:
                data = json.load(fp)
            if isinstance(data, dict):
                items.append(data)
        except Exception:
            continue
    items.sort(key=lambda x: (x.get("ts", 0), x.get("id", "")), reverse=True)
    return items


def _load_record(date: str, record_id: str) -> dict | None:
    if not (_DATE_RE.match(date or "") and _ID_RE.match(record_id or "")):
        return None
    path = LOG_DIR / date / f"{record_id}.json"
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def get_image_path(date: str, record_id: str) -> Path | None:
    """记录的缩略图路径（无图或非法 id 返回 None）。"""
    record = _load_record(date, record_id)
    if not record:
        return None
    image = record.get("image")
    if not image or Path(image).name != image or not image.endswith(".jpg"):
        return None
    path = LOG_DIR / date / image
    return path if path.exists() else None


def set_favorite(date: str, record_id: str, favorite: bool) -> bool:
    """切换收藏标记。记录不存在返回 False。"""
    record = _load_record(date, record_id)
    if record is None:
        return False
    record["favorite"] = bool(favorite)
    try:
        _atomic_write(LOG_DIR / date / f"{record_id}.json",
                      json.dumps(record, ensure_ascii=False, indent=2))
        return True
    except OSError:
        logger.warning("提示词日志收藏标记写入失败", exc_info=True)
        return False


def delete_record(date: str, record_id: str) -> bool:
    """删除一条记录（json + txt + jpg）。记录不存在返回 False。"""
    record = _load_record(date, record_id)
    if record is None:
        return False
    day_dir = LOG_DIR / date
    names = [f"{record_id}.json", f"{record_id}.txt"]
    image = record.get("image")
    if image and Path(image).name == image:
        names.append(image)
    for name in names:
        try:
            (day_dir / name).unlink()
        except OSError:
            pass
    return True
