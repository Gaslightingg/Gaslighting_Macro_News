from __future__ import annotations

import logging
import os
from typing import Iterable

from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from .api_client import ApiClient
from .config import BotConfig, load_config_optional
from dotenv import load_dotenv
from .handlers import handle_text, help_command, menu, on_callback, start, status_command

logger = logging.getLogger(__name__)

ALLOWED_UPDATES: Iterable[str] = ("message", "callback_query")


def build_application(config: BotConfig) -> Application:
    application = Application.builder().token(config.token).build()
    application.bot_data["config"] = config
    application.bot_data["api"] = ApiClient(config.api_base_url)

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("menu", menu))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CallbackQueryHandler(on_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    return application


async def start_bot() -> Application | None:
    load_dotenv()
    enabled_raw = os.getenv("TELEGRAM_ENABLED", "true")
    token_present = bool(os.getenv("TELEGRAM_BOT_TOKEN", "").strip())
    allowed_user_id = os.getenv("TELEGRAM_ALLOWED_USER_ID", "").strip() or "missing"
    logger.info(
        "Telegram config: enabled=%s, token_present=%s, allowed_user_id=%s",
        enabled_raw,
        "yes" if token_present else "no",
        allowed_user_id,
    )
    if enabled_raw.strip().lower() in {"0", "false", "no", "off"}:
        logger.info("Telegram bot disabled via TELEGRAM_ENABLED")
        return None
    if not os.getenv("TELEGRAM_BOT_TOKEN", "").strip():
        logger.info("Telegram bot disabled: TELEGRAM_BOT_TOKEN not set")
        return None
    config = load_config_optional()
    if config is None or not config.allowed_user_ids:
        logger.info("Telegram bot disabled: TELEGRAM_ALLOWED_USER_ID not set")
        return None

    logger.info("Starting Telegram bot…")
    application = build_application(config)
    await application.initialize()
    await application.start()
    await application.updater.start_polling(allowed_updates=list(ALLOWED_UPDATES))
    return application


async def stop_bot(application: Application | None) -> None:
    if application is None:
        return
    logger.info("Stopping Telegram bot…")
    await application.updater.stop()
    await application.stop()
    await application.shutdown()
