"""Admin 侧统一路径常量。Docker 通过 DATA_DIR 环境变量注入。"""
import os
from pathlib import Path

DATA_DIR = Path(os.getenv("DATA_DIR", "data"))
WORKFLOW_DIR = DATA_DIR / "workflows"
COMFY_WORKFLOW_DIR = DATA_DIR / "comfy_workflows"
