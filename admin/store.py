"""工作流配置 CRUD（原子写/归档）+ 网页端生成历史持久化。"""

import json
import logging
import os
import re
import tempfile
import time
import uuid
from pathlib import Path

from admin.paths import DATA_DIR, WORKFLOW_DIR, COMFY_WORKFLOW_DIR

logger = logging.getLogger(__name__)

KEY_RE = re.compile(r"^[a-z0-9_-]+$")
HISTORY_DIR = DATA_DIR / "web_generations"
HISTORY_LIMIT = 200

# 历史结果允许的扩展名（来自 ComfyUI 输出文件名）
_RESULT_EXTS = {".png", ".webp", ".jpg", ".jpeg", ".gif", ".mp4", ".webm"}


def _atomic_write(path: Path, content: str) -> None:
    """同目录唯一 tmp + os.replace，保证原子写并触发 mtime 热重载。"""
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


# ── 工作流配置 ──────────────────────────────────────────────

def list_workflow_configs() -> list[dict]:
    """列出 data/workflows/ 下全部配置（含禁用），按文件名排序。"""
    items = []
    for f in sorted(WORKFLOW_DIR.glob("*.json")):
        try:
            with open(f, encoding="utf-8") as fp:
                data = json.load(fp)
            items.append({
                "key": f.stem,
                "enabled": data.get("enabled", True),
                "label": data.get("menu", {}).get("label", f.stem),
                "emoji": data.get("menu", {}).get("emoji", ""),
            })
        except Exception as e:
            items.append({"key": f.stem, "enabled": False, "label": f.stem,
                          "emoji": "⚠️", "error": f"JSON 解析失败: {e}"})
    return items


def load_workflow_config(key: str) -> dict:
    if not KEY_RE.match(key):
        raise FileNotFoundError(key)
    path = WORKFLOW_DIR / f"{key}.json"
    if not path.exists():
        raise FileNotFoundError(key)
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("配置顶层必须是 JSON object")
    return data


def save_workflow_config(key: str, data: dict) -> None:
    if not KEY_RE.match(key):
        raise ValueError("key 只能包含小写字母、数字、下划线、短横线")
    if data.get("key") != key:
        raise ValueError(f"配置 key '{data.get('key')}' 与文件名 '{key}' 不一致")
    if data.get("schema_version") != 1:
        raise ValueError("schema_version 必须为 1")
    WORKFLOW_DIR.mkdir(parents=True, exist_ok=True)
    path = WORKFLOW_DIR / f"{key}.json"
    content = json.dumps(data, ensure_ascii=False, indent=2)
    if path.exists():
        raw = path.read_bytes()
        # 内容未变化时不写盘：保留原格式（手写紧凑数组等），也避免无谓的 mtime 热重载
        try:
            if json.loads(raw.decode("utf-8")) == data:
                return
        except Exception:
            pass
        # 保留既有文件的 CRLF 换行风格与末尾换行（配置文件受 git 跟踪，避免噪音 diff）
        if b"\r\n" in raw[:4096]:
            content = content.replace("\n", "\r\n")
        nl = b"\r\n" if b"\r\n" in raw[:4096] else b"\n"
        if raw.endswith(nl):
            content += nl.decode()
    _atomic_write(path, content)


def create_workflow_config(key: str) -> None:
    if not KEY_RE.match(key):
        raise ValueError("key 只能包含小写字母、数字、下划线、短横线")
    path = WORKFLOW_DIR / f"{key}.json"
    if path.exists():
        raise FileExistsError(key)
    template = {
        "schema_version": 1,
        "key": key,
        "enabled": False,
        "menu": {
            "emoji": "🆕",
            "label": key,
            "description": "",
            "how_to": "",
            "backend": "comfyui",
            "input_type": "text",
        },
        "comfy": {
            "label": key,
            "workflow_file": "",
            "is_img2img": False,
            "prompt_node": "",
            "prompt_key": "",
            "seed_node": "",
            "seed_key": "",
        },
        "user_configurable": ["comfy_seed", "comfy_translate", "comfy_prompt"],
    }
    save_workflow_config(key, template)


def set_workflow_enabled(key: str, enabled: bool) -> None:
    data = load_workflow_config(key)
    data["enabled"] = enabled
    save_workflow_config(key, data)


def archive_workflow(key: str) -> None:
    if not KEY_RE.match(key):
        raise FileNotFoundError(key)
    src = WORKFLOW_DIR / f"{key}.json"
    if not src.exists():
        raise FileNotFoundError(key)
    trash = WORKFLOW_DIR / ".trash"
    trash.mkdir(exist_ok=True)
    dst = trash / src.name
    if dst.exists():
        dst = trash / f"{key}-{time.strftime('%Y%m%d%H%M%S')}.json"
    src.rename(dst)


def save_comfy_upload(filename: str, content: bytes) -> str:
    """保存上传的 ComfyUI workflow JSON 到 data/comfy_workflows/，返回文件名。"""
    name = Path(filename).name  # 去路径
    if not name.endswith(".json"):
        raise ValueError("只接受 .json 文件")
    if "/" in name or "\\" in name or ".." in Path(name).parts:
        raise ValueError("文件名不允许包含路径")
    try:
        data = json.loads(content.decode("utf-8"))
    except Exception as e:
        raise ValueError(f"JSON 解析失败: {e}") from e
    if not isinstance(data, dict):
        raise ValueError("workflow JSON 顶层必须是 object")
    COMFY_WORKFLOW_DIR.mkdir(parents=True, exist_ok=True)
    _atomic_write(COMFY_WORKFLOW_DIR / name, json.dumps(data, ensure_ascii=False, indent=2))
    return name


# ── 生成历史 ──────────────────────────────────────────────

def save_result(meta: dict, data: bytes, ext: str) -> str:
    """保存生成结果字节 + 元数据，返回历史 id。"""
    ext = ext.lower()
    if ext not in _RESULT_EXTS:
        ext = ".png"
    result_id = uuid.uuid4().hex[:12]
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{result_id}{ext}"
    (HISTORY_DIR / filename).write_bytes(data)
    record = {**meta, "id": result_id, "filename": filename, "ts": int(time.time())}
    _atomic_write(HISTORY_DIR / f"{result_id}.json",
                  json.dumps(record, ensure_ascii=False, indent=2))
    _enforce_history_limit()
    return result_id


def list_history() -> list[dict]:
    if not HISTORY_DIR.exists():
        return []
    items = []
    for f in HISTORY_DIR.glob("*.json"):
        try:
            with open(f, encoding="utf-8") as fp:
                items.append(json.load(fp))
        except Exception:
            continue
    items.sort(key=lambda x: x.get("ts", 0), reverse=True)
    return items


def get_history(result_id: str) -> dict | None:
    path = HISTORY_DIR / f"{result_id}.json"
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def get_history_file(result_id: str) -> Path | None:
    record = get_history(result_id)
    if not record:
        return None
    path = HISTORY_DIR / record.get("filename", "")
    return path if path.exists() and path.suffix.lower() in _RESULT_EXTS else None


def delete_history(result_id: str) -> bool:
    record = get_history(result_id)
    if not record:
        return False
    for suffix in (".json", Path(record.get("filename", "")).suffix):
        try:
            (HISTORY_DIR / f"{result_id}{suffix}").unlink()
        except OSError:
            pass
    return True


def _enforce_history_limit() -> None:
    files = sorted(HISTORY_DIR.glob("*.json"),
                   key=lambda f: f.stat().st_mtime, reverse=True)
    for meta_file in files[HISTORY_LIMIT:]:
        try:
            record = json.loads(meta_file.read_text(encoding="utf-8"))
            result_file = HISTORY_DIR / record.get("filename", "")
            if result_file.exists():
                result_file.unlink()
        except Exception:
            pass
        try:
            meta_file.unlink()
        except OSError:
            pass
