from __future__ import annotations

import logging

from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from .api_client import ApiClient
from .config import load_config
from .handlers import handle_text, menu, on_callback, start


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    config = load_config()
    application = Application.builder().token(config.token).build()
    application.bot_data["config"] = config
    application.bot_data["api"] = ApiClient(config.api_base_url)

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("menu", menu))
    application.add_handler(CallbackQueryHandler(on_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    application.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
