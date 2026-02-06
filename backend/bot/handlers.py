from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from .api_client import ApiClient
from .config import BotConfig
from .utils.formatting import format_news, format_prices, format_signal_detail, format_signals, format_status

logger = logging.getLogger(__name__)

MENU_MAIN = "menu:main"
MENU_NEWS = "menu:news"
MENU_SIGNALS = "menu:signals"
MENU_PRICES = "menu:prices"
MENU_STATUS = "menu:status"
MENU_SETTINGS = "menu:settings"
MENU_HELP = "menu:help"
NEWS_PAGE_PREFIX = "news:page:"
SIGNALS_PAGE_PREFIX = "signals:page:"
SIGNAL_DETAIL_PREFIX = "signal:"

NEWS_PAGE_SIZE = 5
SIGNALS_PAGE_SIZE = 5


def build_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Signals", callback_data=MENU_SIGNALS),
                InlineKeyboardButton("News", callback_data=MENU_NEWS),
            ],
            [
                InlineKeyboardButton("Prices", callback_data=MENU_PRICES),
                InlineKeyboardButton("Status/Refresh", callback_data=MENU_STATUS),
            ],
            [
                InlineKeyboardButton("Settings", callback_data=MENU_SETTINGS),
                InlineKeyboardButton("Help", callback_data=MENU_HELP),
            ],
        ]
    )


def _allowed(update: Update, config: BotConfig) -> bool:
    user_id = update.effective_user.id if update.effective_user else None
    if user_id not in config.allowed_user_ids:
        return False
    if config.allowed_chat_id and update.effective_chat:
        return update.effective_chat.id == config.allowed_chat_id
    return True


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: BotConfig = context.bot_data["config"]
    if not _allowed(update, config):
        await update.message.reply_text("Access denied.")
        return
    text = "Welcome! Choose a section:"
    await update.message.reply_text(text, reply_markup=build_menu())


async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: BotConfig = context.bot_data["config"]
    if not _allowed(update, config):
        await update.message.reply_text("Access denied.")
        return
    await update.message.reply_text("Menu:", reply_markup=build_menu())


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: BotConfig = context.bot_data["config"]
    if not _allowed(update, config):
        await update.message.reply_text("Access denied.")
        return
    text = (
        "ℹ️ *Help*\n"
        "Use the buttons below or send a ticker (e.g. `sp500`, `xauusd`).\n\n"
        "Commands:\n"
        "/start — main menu\n"
        "/menu — show menu\n"
        "/help — this help message"
    )
    await update.message.reply_text(text, reply_markup=build_menu(), parse_mode=ParseMode.MARKDOWN)


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    config: BotConfig = context.bot_data["config"]
    if not _allowed(update, config):
        await query.edit_message_text("Access denied.")
        return

    data = query.data or ""
    api: ApiClient = context.bot_data["api"]
    try:
        if data == MENU_MAIN:
            await query.edit_message_text("Menu:", reply_markup=build_menu())
            return
        if data == MENU_PRICES:
            payload = await api.get_prices()
            prices = (payload.get("tickers") or [])[:10]
            text = format_prices(prices)
            await query.edit_message_text(text, reply_markup=build_menu(), parse_mode=ParseMode.MARKDOWN)
            return
        if data == MENU_NEWS:
            payload = await api.get_news(page=1, page_size=NEWS_PAGE_SIZE)
            text = format_news(payload.get("events", []), payload.get("total", 0))
            keyboard = [
                [
                    InlineKeyboardButton("Next", callback_data=f"{NEWS_PAGE_PREFIX}2"),
                    InlineKeyboardButton("Menu", callback_data=MENU_MAIN),
                ]
            ]
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
            return
        if data.startswith(NEWS_PAGE_PREFIX):
            page = int(data.replace(NEWS_PAGE_PREFIX, "") or "1")
            payload = await api.get_news(page=page, page_size=NEWS_PAGE_SIZE)
            text = format_news(payload.get("events", []), payload.get("total", 0))
            keyboard = [
                [
                    InlineKeyboardButton("Prev", callback_data=f"{NEWS_PAGE_PREFIX}{max(1, page - 1)}"),
                    InlineKeyboardButton("Next", callback_data=f"{NEWS_PAGE_PREFIX}{page + 1}"),
                ],
                [InlineKeyboardButton("Menu", callback_data=MENU_MAIN)],
            ]
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
            return
        if data == MENU_SIGNALS or data.startswith(SIGNALS_PAGE_PREFIX):
            payload = await api.get_signals()
            all_signals = payload.get("signals") or []
            page = 1
            if data.startswith(SIGNALS_PAGE_PREFIX):
                page = int(data.replace(SIGNALS_PAGE_PREFIX, "") or "1")
            start = (page - 1) * SIGNALS_PAGE_SIZE
            signals = all_signals[start : start + SIGNALS_PAGE_SIZE]
            text = format_signals(signals)
            buttons = [
                [InlineKeyboardButton(sig["ticker"], callback_data=f"{SIGNAL_DETAIL_PREFIX}{sig['ticker']}")]
                for sig in signals
            ]
            nav_buttons = []
            if start > 0:
                nav_buttons.append(InlineKeyboardButton("Prev", callback_data=f"{SIGNALS_PAGE_PREFIX}{page - 1}"))
            if start + SIGNALS_PAGE_SIZE < len(all_signals):
                nav_buttons.append(InlineKeyboardButton("Next", callback_data=f"{SIGNALS_PAGE_PREFIX}{page + 1}"))
            if nav_buttons:
                buttons.append(nav_buttons)
            buttons.append([InlineKeyboardButton("Back", callback_data=MENU_MAIN)])
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode=ParseMode.MARKDOWN)
            return
        if data.startswith(SIGNAL_DETAIL_PREFIX):
            ticker = data.replace(SIGNAL_DETAIL_PREFIX, "")
            payload = await api.get_signal(ticker)
            text = format_signal_detail(payload)
            keyboard = InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("Back", callback_data=MENU_SIGNALS)],
                    [InlineKeyboardButton("Menu", callback_data=MENU_MAIN)],
                ]
            )
            await query.edit_message_text(text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)
            return
        if data == MENU_STATUS:
            health = await api.get_health()
            refresh = await api.refresh_macro()
            text = format_status(health, refresh)
            await query.edit_message_text(text, reply_markup=build_menu(), parse_mode=ParseMode.MARKDOWN)
            return
        if data == MENU_SETTINGS:
            config: BotConfig = context.bot_data["config"]
            allowed_ids = ", ".join(str(item) for item in sorted(config.allowed_user_ids))
            text = f"⚙️ *Settings*\nAPI: `{config.api_base_url}`\nAllowed user IDs: `{allowed_ids}`"
            await query.edit_message_text(text, reply_markup=build_menu(), parse_mode=ParseMode.MARKDOWN)
            return
        if data == MENU_HELP:
            await query.edit_message_text(
                "ℹ️ *Help*\nUse buttons or send a ticker (e.g. `sp500`).",
                reply_markup=build_menu(),
                parse_mode=ParseMode.MARKDOWN,
            )
            return
    except Exception as exc:  # noqa: BLE001
        logger.exception("Bot callback failed: %s", exc)
        await query.edit_message_text("API unavailable. Please try again later.", reply_markup=build_menu())


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: BotConfig = context.bot_data["config"]
    if not _allowed(update, config):
        await update.message.reply_text("Access denied.")
        return
    text = update.message.text.strip()
    if not text:
        return
    api: ApiClient = context.bot_data["api"]
    try:
        payload = await api.get_signal(text.lower())
        await update.message.reply_text(format_signal_detail(payload), parse_mode=ParseMode.MARKDOWN)
    except Exception:
        await update.message.reply_text("Ticker not found or API unavailable. Try again.")
