from telegram.ext import ApplicationBuilder
from telegram.request import HTTPXRequest
from config import TELEGRAM_TOKEN, PROXY_URL
from handlers import settings as settings_handler
from handlers import generation as generation_handler


def main():
    if not TELEGRAM_TOKEN:
        print("错误: 请在 .env 中设置 TELEGRAM_TOKEN")
        return

    if not PROXY_URL:
        print("警告: 未设置 PROXY_URL，可能无法连接 Telegram API")

    request = HTTPXRequest(proxy=PROXY_URL) if PROXY_URL else None

    builder = (
        ApplicationBuilder()
        .token(TELEGRAM_TOKEN)
        .concurrent_updates(False)
    )
    if request:
        builder.request(request).get_updates_request(request)

    app = builder.build()

    # 注册 handlers
    app.add_handlers(settings_handler.get_handlers())
    app.add_handlers(generation_handler.get_handlers())

    print("Bot 已启动，正在轮询...")
    app.run_polling()


if __name__ == "__main__":
    main()
