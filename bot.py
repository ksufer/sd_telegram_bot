import asyncio
from telegram.ext import Application, ApplicationBuilder
from telegram.request import HTTPXRequest
from config import TELEGRAM_TOKEN, PROXY_URL
from handlers import settings as settings_handler
from handlers import generation as generation_handler


async def main():
    if not TELEGRAM_TOKEN:
        print("错误: 请在 .env 中设置 TELEGRAM_TOKEN")
        return

    if not PROXY_URL:
        print("警告: 未设置 PROXY_URL，可能无法连接 Telegram API")

    request = HTTPXRequest(proxy=PROXY_URL)

    app = (
        ApplicationBuilder()
        .token(TELEGRAM_TOKEN)
        .request(request)
        .get_updates_request(request)
        .concurrent_updates(False)
        .build()
    )

    # 注册 handlers
    app.add_handlers(settings_handler.get_handlers())
    app.add_handlers(generation_handler.get_handlers())

    print("Bot 已启动，正在轮询...")
    await app.run_polling()


if __name__ == "__main__":
    asyncio.run(main())
