import logging
import logging.handlers
from pathlib import Path


def setup_logging(level: str = "INFO", log_dir: str = "logs") -> None:
    Path(log_dir).mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.strip().upper(), logging.INFO))

    if root.handlers:
        root.handlers.clear()

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)

    file_handler = logging.handlers.RotatingFileHandler(
        Path(log_dir) / "bot.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    # 第三方 HTTP 库降噪：INFO 级会记录完整请求 URL，
    # 其中 Telegram API 的 URL 包含 bot token，必须避免写入日志
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
