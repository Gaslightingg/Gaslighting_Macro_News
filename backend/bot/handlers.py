from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from .api_client import ApiClient
from .config import BotConfig
from .utils.formatting import (
    format_macro_summary,
    format_news,
    format_prices,
    format_signal_detail,
    format_signal_summary,
    format_signals,
    format_status,
)

logger = logging.getLogger(__name__)

MENU_MAIN = "menu:main"
MENU_NEWS = "menu:news"
MENU_SIGNALS = "menu:signals"
MENU_PRICES = "menu:prices"
MENU_STATUS = "menu:status"
MENU_MACRO = "menu:macro"
MENU_HELP = "menu:help"
NEWS_PAGE_PREFIX = "news:page:"
SIGNALS_PAGE_PREFIX = "signals:page:"
SIGNAL_PICK_PREFIX = "signal:pick:"
SIGNAL_DETAIL_PREFIX = "signal:detail:"

NEWS_PAGE_SIZE = 5
SIGNALS_PAGE_SIZE = 5


def build_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Signals", callback_data=MENU_SIGNALS),
                InlineKeyboardButton("Macro", callback_data=MENU_MACRO),
            ],
            [
                InlineKeyboardButton("Prices", callback_data=MENU_PRICES),
                InlineKeyboardButton("News", callback_data=MENU_NEWS),
            ],
            [
                InlineKeyboardButton("Status", callback_data=MENU_STATUS),
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
        await update.effective_message.reply_text("Access denied.")
        return
    text = "Welcome! Use the buttons below to navigate."
    await update.effective_message.reply_text(text, reply_markup=build_menu(), parse_mode=ParseMode.HTML)


async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: BotConfig = context.bot_data["config"]
    if not _allowed(update, config):
        await update.effective_message.reply_text("Access denied.")
        return
    await update.effective_message.reply_text("Menu:", reply_markup=build_menu(), parse_mode=ParseMode.HTML)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: BotConfig = context.bot_data["config"]
    if not _allowed(update, config):
        await update.effective_message.reply_text("Access denied.")
        return
    text = (
        "ℹ️ <b>Help</b>\n"
        "Just tap the buttons below — commands are optional.\n\n"
        "<b>Commands:</b>\n"
        "/start — main menu\n"
        "/help — this help message\n"
        "/status — system status"
    )
    await update.effective_message.reply_text(text, reply_markup=build_menu(), parse_mode=ParseMode.HTML)


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: BotConfig = context.bot_data["config"]
    if not _allowed(update, config):
        await update.effective_message.reply_text("Access denied.")
        return
    api: ApiClient = context.bot_data["api"]
    try:
        health = await api.get_health()
        signals = await api.get_signals()
        macro_latest = await api.get_macro_latest()
        await update.effective_message.reply_text(
            format_status(health, signals=signals, macro_latest=macro_latest),
            reply_markup=build_menu(),
            parse_mode=ParseMode.HTML,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Bot status failed: %s", exc)
        await update.effective_message.reply_text("API unavailable. Please try again later.", reply_markup=build_menu(), parse_mode=ParseMode.HTML)


def _reply_markup_equal(left: InlineKeyboardMarkup | None, right: InlineKeyboardMarkup | None) -> bool:
    if left is None and right is None:
        return True
    if left is None or right is None:
        return False
    return left.to_dict() == right.to_dict()


async def _safe_edit_message(
    query,  # noqa: ANN001
    text: str,
    reply_markup: InlineKeyboardMarkup | None,
) -> None:
    current_text = query.message.text_html if query.message else None
    current_markup = query.message.reply_markup if query.message else None
    if current_text == text and _reply_markup_equal(current_markup, reply_markup):
        return
    try:
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
    except BadRequest as exc:
        if "Message is not modified" in str(exc):
            return
        raise


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
            await _safe_edit_message(query, "Menu:", build_menu())
            return
        if data == MENU_PRICES:
            payload = await api.get_prices()
            prices = (payload.get("tickers") or [])[:10]
            text = format_prices(prices, errors=payload.get("errors"))
            await _safe_edit_message(query, text, build_menu())
            return
        if data == MENU_MACRO:
            payload = await api.get_macro_latest()
            text = format_macro_summary(payload)
            await _safe_edit_message(query, text, build_menu())
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
            await _safe_edit_message(query, text, InlineKeyboardMarkup(keyboard))
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
            await _safe_edit_message(query, text, InlineKeyboardMarkup(keyboard))
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
                [InlineKeyboardButton(sig["ticker"], callback_data=f"{SIGNAL_PICK_PREFIX}{sig['ticker']}")]
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
            await _safe_edit_message(query, text, InlineKeyboardMarkup(buttons))
            return
        if data.startswith(SIGNAL_PICK_PREFIX):
            ticker = data.replace(SIGNAL_PICK_PREFIX, "")
            payload = await api.get_signal(ticker)
            text = format_signal_summary(payload)
            keyboard = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("Details", callback_data=f"{SIGNAL_DETAIL_PREFIX}{ticker}"),
                        InlineKeyboardButton("Back", callback_data=MENU_SIGNALS),
                    ],
                    [InlineKeyboardButton("Menu", callback_data=MENU_MAIN)],
                ]
            )
            await _safe_edit_message(query, text, keyboard)
            return
        if data.startswith(SIGNAL_DETAIL_PREFIX):
            ticker = data.replace(SIGNAL_DETAIL_PREFIX, "")
            payload = await api.get_signal(ticker)
            await query.message.reply_text(format_signal_detail(payload), parse_mode=ParseMode.HTML)
            return
        if data == MENU_STATUS:
            health = await api.get_health()
            signals = await api.get_signals()
            macro_latest = await api.get_macro_latest()
            text = format_status(health, signals=signals, macro_latest=macro_latest)
            await _safe_edit_message(query, text, build_menu())
            return
        if data == MENU_HELP:
            await _safe_edit_message(query, "ℹ️ Help\nJust tap the buttons below — commands are optional.", build_menu())
            return
    except Exception as exc:  # noqa: BLE001
        logger.warning("Bot callback failed: %s", exc)
        try:
            await _safe_edit_message(query, "API unavailable. Please try again later.", build_menu())
        except BadRequest:
            return


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: BotConfig = context.bot_data["config"]
    if not _allowed(update, config):
        await update.effective_message.reply_text("Access denied.")
        return
    text = update.effective_message.text.strip()
    if not text:
        return
    api: ApiClient = context.bot_data["api"]
    try:
        payload = await api.get_signal(text.lower())
        await update.effective_message.reply_text(format_signal_detail(payload), parse_mode=ParseMode.HTML)
    except Exception as exc:
        logger.warning("Bot text lookup failed: %s", exc)
        await update.effective_message.reply_text("Ticker not found or API unavailable. Try again.", parse_mode=ParseMode.HTML)
