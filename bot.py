import logging

from telegram.ext import ApplicationBuilder
from telegram.request import HTTPXRequest

from config import (
    TELEGRAM_TOKEN, PROXY_URL, LOG_LEVEL, LOG_DIR,
    ALLOWED_USER_IDS, ALLOWED_CHAT_IDS, ADMIN_USER_ID,
)
from services.logger import setup_logging
from services.queue import GenerationQueue
from handlers import settings as settings_handler
from handlers import generation as generation_handler
from handlers import credits as credits_handler
from handlers import comfy_settings as comfy_settings_handler
from handlers import workflow_menu as workflow_menu_handler


def main():
    setup_logging(LOG_LEVEL, LOG_DIR)
    logger = logging.getLogger(__name__)

    if not TELEGRAM_TOKEN:
        logger.critical("TELEGRAM_TOKEN 未配置")
        return

    if not PROXY_URL:
        logger.warning("未设置 PROXY_URL，可能无法连接 Telegram API")

    if not ALLOWED_USER_IDS:
        logger.warning("ALLOWED_USER_IDS 为空，当前 Bot 不限制用户访问")
    if not ALLOWED_CHAT_IDS:
        logger.warning("ALLOWED_CHAT_IDS 为空，当前 Bot 不限制群组访问")
    if ADMIN_USER_ID:
        logger.info("管理员用户 ID: %s", ADMIN_USER_ID)

    logger.info("Bot 启动中...")

    request = HTTPXRequest(proxy=PROXY_URL, read_timeout=120, write_timeout=60, connect_timeout=10) if PROXY_URL else None

    builder = (
        ApplicationBuilder()
        .token(TELEGRAM_TOKEN)
        .concurrent_updates(False)
    )
    if request:
        builder.request(request).get_updates_request(request)

    async def post_shutdown(app):
        queue = app.bot_data.get("queue")
        if queue:
            await queue.stop_worker()

    builder.post_shutdown(post_shutdown)

    app = builder.build()

    queue = GenerationQueue(app)
    app.bot_data["queue"] = queue

    app.add_handlers(workflow_menu_handler.get_handlers())
    app.add_handlers(settings_handler.get_handlers())
    app.add_handlers(generation_handler.get_handlers())
    app.add_handlers(credits_handler.get_handlers())
    app.add_handlers(comfy_settings_handler.get_handlers())

    logger.info("Bot 已启动，开始轮询")
    app.run_polling()
    logger.info("Bot 已停止")


if __name__ == "__main__":
    main()
