# -*- coding: utf-8 -*-
"""
Angelok Hub — Telegram-бот магазина конфигураций и оформления GUI.
Aiogram 3.x · SQLite · OpenRouter · asyncio.
Запуск:  python bot.py
"""

# ╔══════════════════════════════════════════════════════════════════╗
# ║                            НАСТРОЙКИ                              ║
# ╚══════════════════════════════════════════════════════════════════╝
BOT_TOKEN = "9200223510:AAEfFyRxrhWkFKOva02OZ2aspZ1VXOFR_T0"
ADMIN_ID = 123456789
OPENROUTER_API_KEY = "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"
OPENROUTER_MODEL = "liquid/lfm-3-1.7b:free"

CHANNEL_URL = "https://t.me/smertnuy_angelok"
GIFT_RECIPIENT = "@Strashniy_angel"

# ---- Дополнительные параметры (можно не менять) ----
START_IMAGE_NAME = "Start.jpg"   # картинка для /start (лежит рядом с bot.py)
DB_FILENAME = "angelok_hub.db"   # файл базы данных SQLite
TZ_OFFSET_HOURS = 3              # часовой пояс для отображения дат (3 = МСК)
AI_TIMEOUT_SECONDS = 300         # максимум ожидания ответа ИИ (5 минут)
AI_CONTEXT_MESSAGES = 12         # сколько последних сообщений уходит ИИ как контекст
AI_MAX_TOKENS = 700              # максимальная длина ответа ИИ
AI_MAX_INPUT_CHARS = 2000        # максимальная длина вопроса к ИИ
ORDER_MAX_CHARS = 3000           # максимальная длина описания заказа
# ════════════════════════════════════════════════════════════════════

import asyncio
import html
import json
import logging
import sqlite3
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional, Union

import aiohttp
from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatAction, ChatType, ContentType, ParseMode
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BotCommand,
    BotCommandScopeChat,
    CallbackQuery,
    ChatMemberUpdated,
    ErrorEvent,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    User,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / DB_FILENAME

logger = logging.getLogger("angelok")


def setup_logging() -> None:
    fmt = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    try:
        from logging.handlers import RotatingFileHandler

        handlers.append(
            RotatingFileHandler(
                BASE_DIR / "bot.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
            )
        )
    except OSError:
        pass
    logging.basicConfig(level=logging.INFO, format=fmt, handlers=handlers)


# ════════════════════════════════════════════════════════════════════
#                              ТЕКСТЫ
# ════════════════════════════════════════════════════════════════════
SEP = "━━━━━━━━━━━━━━━━━━━━"

AI_SYSTEM_PROMPT = (
    "Представь, что ты вежливый менеджер Angelok Hub — магазина конфигураций и "
    "оформления GUI для Standoff 2. Помогай пользователям разбираться с заказами, "
    "оформлением интерфейсов, цветами, стилями, настройками визуальных элементов. "
    "Отвечай грамотно, элегантно и по существу. Не придумывай наличие товаров и не "
    "обещай того, чего магазин не предоставляет."
)

MAINT_TEXT = "🔧 Бот временно не работает. Попробуйте позже"
AI_UNAVAILABLE = "Временно недоступно, попробуй позже"

WELCOME_TEXT = (
    "🌹 <b>Добро пожаловать в Angelok Hub</b>\n\n"
    "Управляй меню с помощью кнопок ниже!"
)

BTN_BUY = "📁 Купить кфг"
BTN_OWNER = "📞 Обращение владельцу"
BTN_AI = "📞 Обращение к ИИ"
BTN_CHANNEL = "📢 Наш канал"
BTN_PROFILE = "👤 Профиль"

ORDER_STATUS_LABELS = {
    "draft": "ожидает оплаты",
    "pending": "на проверке",
    "completed": "выполнен",
    "rejected": "отклонён",
}

CONTENT_LABELS = {
    "text": "текст",
    "photo": "фото",
    "video": "видео",
    "document": "документ",
    "voice": "голосовое сообщение",
    "audio": "аудио",
    "video_note": "видеосообщение",
    "animation": "анимация",
}

DEFAULT_SETTINGS = {"maintenance": "0", "ai_enabled": "1", "orders_enabled": "1"}


def title(text: str) -> str:
    return f"🌹 <b>{html.escape(text)}</b>\n{SEP}"


def esc(text: Optional[str]) -> str:
    return html.escape(text or "")


def esc_limit(text: str, limit: int) -> str:
    """Экранирует HTML и обрезает так, чтобы результат не превысил limit символов."""
    out: list[str] = []
    size = 0
    for ch in text or "":
        piece = html.escape(ch)
        if size + len(piece) > limit:
            out.append("…")
            break
        out.append(piece)
        size += len(piece)
    return "".join(out)


def user_link(user_id: int, name: Optional[str]) -> str:
    return '<a href="tg://user?id=%d">%s</a>' % (user_id, html.escape(name or "пользователь"))


def uname(username: Optional[str]) -> str:
    return f"@{html.escape(username)}" if username else "нет"


def split_text(text: str, limit: int = 2500) -> list[str]:
    parts: list[str] = []
    text = text.strip()
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = text.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        parts.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text:
        parts.append(text)
    return parts


# ════════════════════════════════════════════════════════════════════
#                              ВРЕМЯ
# ════════════════════════════════════════════════════════════════════
def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now_utc().isoformat(timespec="seconds")


def parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def fmt_dt(value: Optional[str]) -> str:
    dt = parse_iso(value)
    if dt is None:
        return "—"
    return (dt + timedelta(hours=TZ_OFFSET_HOURS)).strftime("%d.%m.%Y %H:%M")


# ════════════════════════════════════════════════════════════════════
#                          БАЗА ДАННЫХ (SQLite)
# ════════════════════════════════════════════════════════════════════
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id       INTEGER PRIMARY KEY,
    username      TEXT,
    first_name    TEXT,
    registered_at TEXT NOT NULL,
    last_seen     TEXT NOT NULL,
    start_count   INTEGER NOT NULL DEFAULT 0,
    blocked       INTEGER NOT NULL DEFAULT 0,
    ai_reset_id   INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS orders (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL,
    username     TEXT,
    description  TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'draft',
    created_at   TEXT NOT NULL,
    claimed_at   TEXT,
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(user_id);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
CREATE TABLE IF NOT EXISTS tickets (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL,
    username     TEXT,
    content_type TEXT NOT NULL,
    text         TEXT,
    status       TEXT NOT NULL DEFAULT 'open',
    created_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ticket_msgs (
    admin_msg_id INTEGER PRIMARY KEY,
    ticket_id    INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS ai_history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    role       TEXT NOT NULL,
    content    TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ai_user ON ai_history(user_id, id);
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class Database:
    """Тонкая async-обёртка над sqlite3 (запросы выполняются в потоке, под замком)."""

    def __init__(self, path: Union[str, Path]) -> None:
        self._path = str(path)
        self._lock = threading.Lock()
        self._conn: Optional[sqlite3.Connection] = None

    def connect(self) -> None:
        self._conn = sqlite3.connect(self._path, check_same_thread=False, timeout=30)
        self._conn.row_factory = sqlite3.Row
        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.DatabaseError:
            pass
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        if self._conn is not None:
            with self._lock:
                self._conn.close()
            self._conn = None

    def _exec(self, sql: str, params: tuple) -> tuple[int, int]:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur.rowcount, cur.lastrowid

    def _all(self, sql: str, params: tuple) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    async def execute(self, sql: str, params: tuple = ()) -> int:
        rowcount, _ = await asyncio.to_thread(self._exec, sql, tuple(params))
        return rowcount

    async def insert(self, sql: str, params: tuple = ()) -> int:
        _, last_id = await asyncio.to_thread(self._exec, sql, tuple(params))
        return last_id

    async def fetchall(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        return await asyncio.to_thread(self._all, sql, tuple(params))

    async def fetchone(self, sql: str, params: tuple = ()) -> Optional[sqlite3.Row]:
        rows = await self.fetchall(sql, params)
        return rows[0] if rows else None

    async def scalar(self, sql: str, params: tuple = (), default: int = 0) -> int:
        row = await self.fetchone(sql, params)
        if row is None or row[0] is None:
            return default
        return row[0]


db = Database(DB_PATH)


# ---- пользователи ----
async def touch_user(user: User) -> None:
    now = now_iso()
    await db.execute(
        """
        INSERT INTO users (user_id, username, first_name, registered_at, last_seen)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            username   = excluded.username,
            first_name = excluded.first_name,
            last_seen  = excluded.last_seen,
            blocked    = 0
        """,
        (user.id, user.username, user.full_name, now, now),
    )


async def inc_start(user_id: int) -> None:
    await db.execute("UPDATE users SET start_count = start_count + 1 WHERE user_id = ?", (user_id,))


async def get_user(user_id: int) -> Optional[sqlite3.Row]:
    return await db.fetchone("SELECT * FROM users WHERE user_id = ?", (user_id,))


async def set_blocked(user_id: int, flag: int) -> None:
    await db.execute("UPDATE users SET blocked = ? WHERE user_id = ?", (flag, user_id))


# ---- настройки ----
async def get_setting(key: str) -> str:
    row = await db.fetchone("SELECT value FROM settings WHERE key = ?", (key,))
    if row is None:
        return DEFAULT_SETTINGS.get(key, "0")
    return row["value"]


async def set_setting(key: str, value: str) -> None:
    await db.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


async def flip_setting(key: str) -> bool:
    new_value = "0" if await get_setting(key) == "1" else "1"
    await set_setting(key, new_value)
    return new_value == "1"


# ---- заказы ----
async def create_draft(user: User, description: str) -> int:
    return await db.insert(
        "INSERT INTO orders (user_id, username, description, status, created_at) "
        "VALUES (?, ?, ?, 'draft', ?)",
        (user.id, user.username, description, now_iso()),
    )


async def get_order(order_id: int) -> Optional[sqlite3.Row]:
    return await db.fetchone("SELECT * FROM orders WHERE id = ?", (order_id,))


async def claim_order(order_id: int, user_id: int) -> bool:
    """draft -> pending (атомарно: сработает только один раз)."""
    changed = await db.execute(
        "UPDATE orders SET status = 'pending', claimed_at = ? "
        "WHERE id = ? AND user_id = ? AND status = 'draft'",
        (now_iso(), order_id, user_id),
    )
    return changed > 0


async def revert_claim(order_id: int) -> None:
    await db.execute(
        "UPDATE orders SET status = 'draft', claimed_at = NULL WHERE id = ? AND status = 'pending'",
        (order_id,),
    )


async def complete_order(order_id: int) -> bool:
    changed = await db.execute(
        "UPDATE orders SET status = 'completed', completed_at = ? WHERE id = ? AND status = 'pending'",
        (now_iso(), order_id),
    )
    return changed > 0


async def reject_order(order_id: int) -> bool:
    changed = await db.execute(
        "UPDATE orders SET status = 'rejected' WHERE id = ? AND status = 'pending'",
        (order_id,),
    )
    return changed > 0


async def user_order_counts(user_id: int) -> tuple[int, int]:
    total = await db.scalar(
        "SELECT COUNT(*) FROM orders WHERE user_id = ? AND status != 'draft'", (user_id,)
    )
    done = await db.scalar(
        "SELECT COUNT(*) FROM orders WHERE user_id = ? AND status = 'completed'", (user_id,)
    )
    return total, done


# ---- обращения ----
async def create_ticket(user: User, content_type: str, text: str) -> int:
    return await db.insert(
        "INSERT INTO tickets (user_id, username, content_type, text, status, created_at) "
        "VALUES (?, ?, ?, ?, 'open', ?)",
        (user.id, user.username, content_type, text, now_iso()),
    )


async def get_ticket(ticket_id: int) -> Optional[sqlite3.Row]:
    return await db.fetchone("SELECT * FROM tickets WHERE id = ?", (ticket_id,))


async def set_ticket_status(ticket_id: int, new: str, only_from: Optional[str] = None) -> bool:
    if only_from is None:
        changed = await db.execute("UPDATE tickets SET status = ? WHERE id = ?", (new, ticket_id))
    else:
        changed = await db.execute(
            "UPDATE tickets SET status = ? WHERE id = ? AND status = ?",
            (new, ticket_id, only_from),
        )
    return changed > 0


async def link_ticket_msg(admin_msg_id: int, ticket_id: int) -> None:
    await db.execute(
        "INSERT OR REPLACE INTO ticket_msgs (admin_msg_id, ticket_id) VALUES (?, ?)",
        (admin_msg_id, ticket_id),
    )


async def ticket_by_admin_msg(admin_msg_id: int) -> Optional[int]:
    row = await db.fetchone(
        "SELECT ticket_id FROM ticket_msgs WHERE admin_msg_id = ?", (admin_msg_id,)
    )
    return row["ticket_id"] if row else None


# ---- история диалога с ИИ ----
async def ai_context(user_id: int) -> list[dict]:
    user = await get_user(user_id)
    reset_id = user["ai_reset_id"] if user else 0
    rows = await db.fetchall(
        "SELECT role, content FROM ai_history WHERE user_id = ? AND id > ? "
        "ORDER BY id DESC LIMIT ?",
        (user_id, reset_id, AI_CONTEXT_MESSAGES),
    )
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


async def ai_save(user_id: int, user_text: str, answer: str) -> None:
    now = now_iso()
    await db.insert(
        "INSERT INTO ai_history (user_id, role, content, created_at) VALUES (?, 'user', ?, ?)",
        (user_id, user_text, now),
    )
    await db.insert(
        "INSERT INTO ai_history (user_id, role, content, created_at) VALUES (?, 'assistant', ?, ?)",
        (user_id, answer, now),
    )


async def ai_reset(user_id: int) -> None:
    """Начинает новый диалог: история остаётся в БД, но в контекст больше не попадает."""
    await db.execute(
        "UPDATE users SET ai_reset_id = (SELECT COALESCE(MAX(id), 0) FROM ai_history) "
        "WHERE user_id = ?",
        (user_id,),
    )


# ---- статистика ----
async def build_stats() -> str:
    now = now_utc()
    d1 = (now - timedelta(days=1)).isoformat(timespec="seconds")
    d7 = (now - timedelta(days=7)).isoformat(timespec="seconds")
    users_total = await db.scalar("SELECT COUNT(*) FROM users")
    active_24h = await db.scalar("SELECT COUNT(*) FROM users WHERE last_seen >= ?", (d1,))
    new_24h = await db.scalar("SELECT COUNT(*) FROM users WHERE registered_at >= ?", (d1,))
    new_7d = await db.scalar("SELECT COUNT(*) FROM users WHERE registered_at >= ?", (d7,))
    blocked = await db.scalar("SELECT COUNT(*) FROM users WHERE blocked = 1")
    orders_total = await db.scalar("SELECT COUNT(*) FROM orders WHERE status != 'draft'")
    orders_pending = await db.scalar("SELECT COUNT(*) FROM orders WHERE status = 'pending'")
    orders_done = await db.scalar("SELECT COUNT(*) FROM orders WHERE status = 'completed'")
    orders_rej = await db.scalar("SELECT COUNT(*) FROM orders WHERE status = 'rejected'")
    tickets_total = await db.scalar("SELECT COUNT(*) FROM tickets")
    tickets_open = await db.scalar("SELECT COUNT(*) FROM tickets WHERE status = 'open'")
    ai_questions = await db.scalar("SELECT COUNT(*) FROM ai_history WHERE role = 'user'")
    return (
        f"{title('Статистика')}\n\n"
        f"<b>Пользователи</b>\n"
        f"• Всего: {users_total}\n"
        f"• Активны за 24 ч: {active_24h}\n"
        f"• Новых за 24 ч: {new_24h}\n"
        f"• Новых за 7 дней: {new_7d}\n"
        f"• Заблокировали бота: {blocked}\n\n"
        f"<b>Заказы</b>\n"
        f"• Всего: {orders_total}\n"
        f"• На проверке: {orders_pending}\n"
        f"• Выполнено: {orders_done}\n"
        f"• Отклонено: {orders_rej}\n\n"
        f"<b>Обращения</b>\n"
        f"• Всего: {tickets_total}\n"
        f"• Без ответа: {tickets_open}\n\n"
        f"<b>ИИ</b>\n"
        f"• Вопросов задано: {ai_questions}"
    )


# ════════════════════════════════════════════════════════════════════
#                  CALLBACK-ДАННЫЕ, СОСТОЯНИЯ, КЛАВИАТУРЫ
# ════════════════════════════════════════════════════════════════════
class OrderCb(CallbackData, prefix="ord"):
    action: str
    id: int


class TicketCb(CallbackData, prefix="tk"):
    action: str
    id: int


class AdminCb(CallbackData, prefix="adm"):
    action: str
    arg: int = 0


class MenuCb(CallbackData, prefix="mn"):
    action: str


class BuyStates(StatesGroup):
    description = State()


class OwnerStates(StatesGroup):
    message = State()


class AIStates(StatesGroup):
    chat = State()


class AdminStates(StatesGroup):
    delivering = State()
    replying = State()
    broadcast = State()


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_BUY)],
            [KeyboardButton(text=BTN_OWNER), KeyboardButton(text=BTN_AI)],
            [KeyboardButton(text=BTN_CHANNEL), KeyboardButton(text=BTN_PROFILE)],
        ],
        resize_keyboard=True,
        input_field_placeholder="🌹 Angelok Hub",
    )


def order_paid_kb(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Готово", callback_data=OrderCb(action="paid", id=order_id).pack()
                )
            ]
        ]
    )


def order_admin_kb(order_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="📤 Отправить заказ", callback_data=OrderCb(action="send", id=order_id))
    b.button(text="❌ Отклонить оплату", callback_data=OrderCb(action="reject", id=order_id))
    b.adjust(1)
    return b.as_markup()


def ticket_kb(ticket_id: int, is_open: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="↩️ Ответить", callback_data=TicketCb(action="reply", id=ticket_id))
    if is_open:
        b.button(text="❌ Отклонить", callback_data=TicketCb(action="reject", id=ticket_id))
    b.adjust(2)
    return b.as_markup()


def admin_menu_kb(maintenance: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="📊 Статистика", callback_data=AdminCb(action="stats"))
    b.button(text="👥 Пользователи", callback_data=AdminCb(action="users", arg=0))
    b.button(text="📨 Рассылка", callback_data=AdminCb(action="broadcast"))
    b.button(text="📦 Заказы", callback_data=AdminCb(action="orders"))
    b.button(text="⚙️ Настройки бота", callback_data=AdminCb(action="settings"))
    b.button(
        text="▶️ Вернуть бота" if maintenance else "⛔ Остановить бота",
        callback_data=AdminCb(action="toggle_maint"),
    )
    b.adjust(2, 2, 1, 1)
    return b.as_markup()


def back_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="◀️ В админ-панель", callback_data=AdminCb(action="home"))
    return b.as_markup()


def settings_kb(ai_on: bool, orders_on: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(
        text=f"ИИ-менеджер: {'🟢 вкл' if ai_on else '🔴 выкл'}",
        callback_data=AdminCb(action="set_ai"),
    )
    b.button(
        text=f"Приём заказов: {'🟢 вкл' if orders_on else '🔴 выкл'}",
        callback_data=AdminCb(action="set_orders"),
    )
    b.button(text="◀️ В админ-панель", callback_data=AdminCb(action="home"))
    b.adjust(1)
    return b.as_markup()


def deliver_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✅ Завершить отправку", callback_data=AdminCb(action="deliv_done"))
    return b.as_markup()


def broadcast_cancel_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✖️ Отмена", callback_data=AdminCb(action="bc_cancel"))
    return b.as_markup()


def broadcast_confirm_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🚀 Начать рассылку", callback_data=AdminCb(action="bc_go"))
    b.button(text="✖️ Отмена", callback_data=AdminCb(action="bc_cancel"))
    b.adjust(1)
    return b.as_markup()


# ════════════════════════════════════════════════════════════════════
#                         ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ════════════════════════════════════════════════════════════════════
def find_start_image() -> Optional[Path]:
    direct = BASE_DIR / START_IMAGE_NAME
    if direct.is_file():
        return direct
    wanted = START_IMAGE_NAME.lower()
    try:
        for item in BASE_DIR.iterdir():
            if item.is_file() and item.name.lower() == wanted:
                return item
    except OSError:
        pass
    return None


async def cb_answer(cb: CallbackQuery, text: Optional[str] = None, alert: bool = False) -> None:
    try:
        await cb.answer(text=text, show_alert=alert)
    except TelegramAPIError:
        pass


async def edit_view(cb: CallbackQuery, text: str, kb: Optional[InlineKeyboardMarkup] = None) -> None:
    """Редактирует сообщение с кнопкой; если нельзя — отправляет новое."""
    msg = cb.message
    if isinstance(msg, Message):
        try:
            await msg.edit_text(text, reply_markup=kb)
            return
        except TelegramBadRequest as exc:
            if "not modified" in str(exc).lower():
                return
            logger.warning("edit_text не удался (%s), отправляю новое сообщение", exc)
        except TelegramAPIError:
            logger.exception("Ошибка при редактировании сообщения")
    try:
        await cb.bot.send_message(cb.from_user.id, text, reply_markup=kb)
    except TelegramAPIError:
        logger.exception("Не удалось отправить сообщение")


async def append_status(cb: CallbackQuery, suffix: str) -> None:
    """Дописывает статус под текстом сообщения и убирает кнопки."""
    msg = cb.message
    if not isinstance(msg, Message):
        return
    try:
        await msg.edit_text(f"{msg.html_text or ''}\n\n{suffix}", reply_markup=None)
    except TelegramAPIError:
        try:
            await msg.edit_reply_markup(reply_markup=None)
        except TelegramAPIError:
            pass


async def safe_send(bot: Bot, chat_id: int, text: str, **kwargs: Any) -> bool:
    """Отправка с обработкой блокировки бота и лимитов Telegram."""
    for _ in range(2):
        try:
            await bot.send_message(chat_id, text, **kwargs)
            return True
        except TelegramRetryAfter as exc:
            await asyncio.sleep(exc.retry_after + 1)
        except TelegramForbiddenError:
            logger.info("Пользователь %s заблокировал бота", chat_id)
            await set_blocked(chat_id, 1)
            return False
        except TelegramAPIError:
            logger.exception("Не удалось отправить сообщение пользователю %s", chat_id)
            return False
    return False


async def deliver_to_user(bot: Bot, chat_id: int, message: Message, header: str) -> str:
    """
    Передаёт сообщение админа пользователю (текст, файл, фото — что угодно).
    Возвращает: "ok" | "blocked" | "error".
    """
    for _ in range(2):
        try:
            if message.content_type == ContentType.TEXT and message.text and len(message.text) <= 3500:
                await bot.send_message(chat_id, f"{header}\n{SEP}\n{html.escape(message.text)}")
            else:
                await bot.send_message(chat_id, header)
                await bot.copy_message(
                    chat_id=chat_id, from_chat_id=message.chat.id, message_id=message.message_id
                )
            return "ok"
        except TelegramRetryAfter as exc:
            await asyncio.sleep(exc.retry_after + 1)
        except TelegramForbiddenError:
            await set_blocked(chat_id, 1)
            return "blocked"
        except TelegramAPIError:
            logger.exception("Не удалось доставить сообщение пользователю %s", chat_id)
            return "error"
    return "error"


def order_admin_text(o: sqlite3.Row) -> str:
    return (
        f"{title('Заказ #' + str(o['id']))}\n\n"
        f"<b>ID пользователя:</b> <code>{o['user_id']}</code>\n"
        f"<b>Username:</b> {uname(o['username'])}\n"
        f"<b>Профиль:</b> {user_link(o['user_id'], 'открыть')}\n"
        f"<b>Создан:</b> {fmt_dt(o['created_at'])}\n"
        f"<b>Оплата заявлена:</b> {fmt_dt(o['claimed_at'])}\n\n"
        f"<b>Описание заказа:</b>\n{esc_limit(o['description'], 2600)}\n\n"
        f"<b>Оплата:</b> пользователь нажал «Готово» и заявил, что отправил подарок "
        f"«Мишка» на {esc(GIFT_RECIPIENT)}.\n"
        f"Автоматической проверки нет — проверьте подарок вручную."
    )


def ticket_header(ticket_id: int, user: User, content_type: str) -> str:
    head = title(f"Обращение #{ticket_id}")
    return (
        f"{head}\n\n"
        f"<b>От:</b> {user_link(user.id, user.full_name)}\n"
        f"<b>ID:</b> <code>{user.id}</code>\n"
        f"<b>Username:</b> {uname(user.username)}\n"
        f"<b>Тип:</b> {CONTENT_LABELS.get(content_type, content_type)}\n"
        f"<b>Время:</b> {fmt_dt(now_iso())}\n\n"
        f"Сообщение ниже ⬇️\n"
        f"<i>Ответить можно кнопкой или свайпом-ответом на сообщение.</i>"
    )


async def build_profile(user: User) -> str:
    row = await get_user(user.id)
    if row is None:
        await touch_user(user)
        row = await get_user(user.id)
    reg = parse_iso(row["registered_at"]) or now_utc()
    days = max(0, (now_utc() - reg).days)
    total, done = await user_order_counts(user.id)
    return (
        f"{title('Профиль')}\n\n"
        f"<b>Telegram ID:</b> <code>{user.id}</code>\n"
        f"<b>Username:</b> {uname(user.username)}\n"
        f"<b>Регистрация:</b> {fmt_dt(row['registered_at'])}\n"
        f"<b>В боте:</b> {days} дн.\n"
        f"<b>Запусков /start:</b> {row['start_count']}\n"
        f"<b>Заказов:</b> {total}\n"
        f"<b>Выполнено:</b> {done}"
    )


async def admin_home_text() -> str:
    maint = await get_setting("maintenance") == "1"
    status = "🔴 остановлен для пользователей" if maint else "🟢 работает"
    return f"{title('Админ-панель')}\n\n<b>Статус бота:</b> {status}\n\nВыберите раздел:"


async def settings_text() -> str:
    ai_on = await get_setting("ai_enabled") == "1"
    orders_on = await get_setting("orders_enabled") == "1"
    maint = await get_setting("maintenance") == "1"
    key_ok = bool(OPENROUTER_API_KEY) and not OPENROUTER_API_KEY.startswith("ВСТАВИТЬ")
    return (
        f"{title('Настройки бота')}\n\n"
        f"<b>Статус:</b> {'🔴 остановлен' if maint else '🟢 работает'}\n"
        f"<b>ИИ-менеджер:</b> {'включён' if ai_on else 'выключен'}\n"
        f"<b>Приём заказов:</b> {'открыт' if orders_on else 'закрыт'}\n\n"
        f"<b>Модель ИИ:</b> <code>{esc(OPENROUTER_MODEL)}</code>\n"
        f"<b>OpenRouter API-ключ:</b> {'задан' if key_ok else 'не задан ⚠️'}\n"
        f"<b>Канал:</b> {esc(CHANNEL_URL)}\n"
        f"<b>Получатель подарков:</b> {esc(GIFT_RECIPIENT)}\n"
        f"<b>ADMIN_ID:</b> <code>{ADMIN_ID}</code>\n\n"
        f"<i>Модель, ключ, ссылки и ID меняются в блоке настроек в начале bot.py.</i>"
    )


USERS_PER_PAGE = 10


async def users_view(page: int) -> tuple[str, InlineKeyboardMarkup]:
    total = await db.scalar("SELECT COUNT(*) FROM users")
    pages = max(1, (total + USERS_PER_PAGE - 1) // USERS_PER_PAGE)
    page = min(max(page, 0), pages - 1)
    rows = await db.fetchall(
        "SELECT user_id, username, first_name, registered_at, blocked FROM users "
        "ORDER BY registered_at DESC, user_id DESC LIMIT ? OFFSET ?",
        (USERS_PER_PAGE, page * USERS_PER_PAGE),
    )
    lines = []
    for r in rows:
        who = f"@{r['username']}" if r["username"] else (r["first_name"] or "без имени")
        mark = " 🚫" if r["blocked"] else ""
        lines.append(
            f"• <code>{r['user_id']}</code> {esc(who)} — {fmt_dt(r['registered_at'])[:10]}{mark}"
        )
    body = "\n".join(lines) if lines else "Пока никого нет."
    text = (
        f"{title('Пользователи')}\n\n"
        f"Всего: {total} · страница {page + 1}/{pages}\n"
        f"<i>🚫 — заблокировал бота</i>\n\n{body}"
    )
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(text="◀️", callback_data=AdminCb(action="users", arg=page - 1).pack())
        )
    nav.append(
        InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data=AdminCb(action="noop").pack())
    )
    if page < pages - 1:
        nav.append(
            InlineKeyboardButton(text="▶️", callback_data=AdminCb(action="users", arg=page + 1).pack())
        )
    back = [InlineKeyboardButton(text="◀️ В админ-панель", callback_data=AdminCb(action="home").pack())]
    return text, InlineKeyboardMarkup(inline_keyboard=[nav, back])


# ════════════════════════════════════════════════════════════════════
#                              OPENROUTER
# ════════════════════════════════════════════════════════════════════
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


class AIError(Exception):
    pass


async def _ask_ai_request(messages: list[dict]) -> str:
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": CHANNEL_URL,
        "X-Title": "Angelok Hub",
    }
    payload = {
        "model": OPENROUTER_MODEL,
        "messages": messages,
        "max_tokens": AI_MAX_TOKENS,
        "temperature": 0.7,
    }
    timeout = aiohttp.ClientTimeout(total=max(30, AI_TIMEOUT_SECONDS - 10), connect=20)
    last_error = "unknown"
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for attempt in range(1, 4):
            status = 0
            raw = ""
            try:
                async with session.post(OPENROUTER_URL, headers=headers, json=payload) as resp:
                    status = resp.status
                    raw = await resp.text()
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                last_error = f"сетевая ошибка: {exc!r}"
                logger.warning("OpenRouter, попытка %d: %s", attempt, last_error)
                await asyncio.sleep(2 * attempt)
                continue

            try:
                data = json.loads(raw)
            except ValueError:
                data = {}
            if not isinstance(data, dict):
                data = {}

            if status == 200 and data.get("choices"):
                try:
                    content = data["choices"][0]["message"]["content"]
                except (KeyError, IndexError, TypeError):
                    content = None
                if isinstance(content, str) and content.strip():
                    return content.strip()
                last_error = "пустой ответ модели"
                logger.warning("OpenRouter, попытка %d: %s", attempt, last_error)
            else:
                err = data.get("error")
                err_msg = err.get("message") if isinstance(err, dict) else (err or raw[:200])
                last_error = f"HTTP {status}: {err_msg}"
                logger.warning("OpenRouter, попытка %d: %s", attempt, last_error)
                if status in (400, 401, 402, 403, 404):
                    logger.error(
                        "OpenRouter вернул постоянную ошибку (%s). Проверьте API-ключ, "
                        "баланс и название модели.",
                        last_error,
                    )
                    break
            await asyncio.sleep(3 * attempt)
    raise AIError(last_error)


async def ask_ai(history: list[dict], user_text: str) -> str:
    if not OPENROUTER_API_KEY or OPENROUTER_API_KEY.startswith("ВСТАВИТЬ"):
        raise AIError("OPENROUTER_API_KEY не задан")
    messages = (
        [{"role": "system", "content": AI_SYSTEM_PROMPT}]
        + history
        + [{"role": "user", "content": user_text}]
    )
    try:
        return await asyncio.wait_for(_ask_ai_request(messages), timeout=AI_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        raise AIError(f"таймаут {AI_TIMEOUT_SECONDS} с") from None


async def typing_loop(bot: Bot, chat_id: int) -> None:
    try:
        while True:
            try:
                await bot.send_chat_action(chat_id, ChatAction.TYPING)
            except TelegramAPIError:
                pass
            await asyncio.sleep(4)
    except asyncio.CancelledError:
        pass


_ai_busy: set[int] = set()


# ════════════════════════════════════════════════════════════════════
#                             MIDDLEWARE
# ════════════════════════════════════════════════════════════════════
class CallbackThrottle(BaseMiddleware):
    """Защита от повторного нажатия одной и той же кнопки."""

    def __init__(self, interval: float = 0.8) -> None:
        self.interval = interval
        self._last: dict[tuple[int, str], float] = {}

    async def __call__(self, handler, event: CallbackQuery, data: dict[str, Any]):
        key = (event.from_user.id, event.data or "")
        now = time.monotonic()
        if now - self._last.get(key, 0.0) < self.interval:
            await cb_answer(event)
            return None
        self._last[key] = now
        if len(self._last) > 5000:
            self._last = {k: v for k, v in self._last.items() if now - v < 60}
        return await handler(event, data)


class GateMiddleware(BaseMiddleware):
    """Регистрирует пользователя и блокирует обычных пользователей в режиме остановки."""

    async def __call__(self, handler, event: Union[Message, CallbackQuery], data: dict[str, Any]):
        user: Optional[User] = data.get("event_from_user")
        if user is None or user.is_bot:
            return await handler(event, data)

        chat = event.chat if isinstance(event, Message) else (
            event.message.chat if isinstance(event.message, Message) else None
        )
        if chat is not None and chat.type != ChatType.PRIVATE:
            return None

        try:
            await touch_user(user)
        except Exception:
            logger.exception("Не удалось обновить пользователя %s", user.id)

        if user.id != ADMIN_ID:
            try:
                maintenance = await get_setting("maintenance") == "1"
            except Exception:
                logger.exception("Не удалось прочитать режим обслуживания")
                maintenance = False
            if maintenance:
                if isinstance(event, Message):
                    try:
                        await event.answer(MAINT_TEXT)
                    except TelegramAPIError:
                        pass
                else:
                    await cb_answer(event, MAINT_TEXT, True)
                return None
        return await handler(event, data)


# ════════════════════════════════════════════════════════════════════
#               РОУТЕР 1: КОМАНДЫ И КНОПКИ ГЛАВНОГО МЕНЮ
# ════════════════════════════════════════════════════════════════════
common = Router(name="common")
common.message.filter(F.chat.type == ChatType.PRIVATE)


@common.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await inc_start(message.from_user.id)
    image = find_start_image()
    if image is not None:
        try:
            await message.answer_photo(
                FSInputFile(image), caption=WELCOME_TEXT, reply_markup=main_menu()
            )
            return
        except TelegramAPIError:
            logger.exception("Не удалось отправить %s", START_IMAGE_NAME)
    else:
        logger.warning("Файл %s не найден рядом с bot.py — отправляю только текст", START_IMAGE_NAME)
    await message.answer(WELCOME_TEXT, reply_markup=main_menu())


@common.message(Command("myid"))
async def cmd_myid(message: Message) -> None:
    await message.answer(f"Ваш Telegram ID: <code>{message.from_user.id}</code>")


@common.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    current = await state.get_state()
    await state.clear()
    if current is None:
        await message.answer("Отменять нечего. Вы в главном меню.", reply_markup=main_menu())
    else:
        await message.answer("Действие отменено.", reply_markup=main_menu())


@common.message(Command("done"))
async def cmd_done(message: Message, state: FSMContext) -> None:
    current = await state.get_state()
    if current == AIStates.chat.state:
        await ai_reset(message.from_user.id)
        await state.clear()
        await message.answer(
            "Диалог с ИИ завершён. Новый диалог начнётся с чистого листа.",
            reply_markup=main_menu(),
        )
    else:
        await message.answer("Сейчас нет активного диалога с ИИ.", reply_markup=main_menu())


@common.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext) -> None:
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ Нет доступа.")
        return
    await state.clear()
    maint = await get_setting("maintenance") == "1"
    await message.answer(await admin_home_text(), reply_markup=admin_menu_kb(maint))


@common.message(F.text == BTN_BUY)
async def menu_buy(message: Message, state: FSMContext) -> None:
    await state.clear()
    if await get_setting("orders_enabled") != "1":
        await message.answer("Приём заказов временно закрыт. Загляните позже.")
        return
    await state.set_state(BuyStates.description)
    await message.answer(
        f"{title('Покупка конфигурации')}\n\n"
        "Опиши, какой должна быть твоя конфигурация:\n\n"
        "• Какое оформление GUI?\n"
        "• Какие цвета?\n"
        "• Какой стиль?\n"
        "• Какие визуальные элементы?\n\n"
        "Напиши всё одним сообщением.\n"
        "<i>Отмена — /cancel</i>"
    )


@common.message(F.text == BTN_OWNER)
async def menu_owner(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(OwnerStates.message)
    await message.answer(
        f"{title('Обращение владельцу')}\n\n"
        "Отправьте сообщение в любом формате. Можно прикрепить материал и описание — "
        "я пойму и передам владельцу.\n\n"
        "<i>Отмена — /cancel</i>"
    )


@common.message(F.text == BTN_AI)
async def menu_ai(message: Message, state: FSMContext) -> None:
    await state.clear()
    if await get_setting("ai_enabled") != "1":
        await message.answer("ИИ-менеджер временно отключён. Попробуйте позже.")
        return
    await state.set_state(AIStates.chat)
    await message.answer(
        f"{title('Менеджер Angelok Hub')}\n\n"
        "Задавайте вопросы о заказах, оформлении GUI, цветах и стилях.\n\n"
        "<i>Завершить диалог — /done</i>"
    )


@common.message(F.text == BTN_CHANNEL)
async def menu_channel(message: Message, state: FSMContext) -> None:
    await state.clear()
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="📢 Открыть канал", url=CHANNEL_URL)]]
    )
    await message.answer(
        f"{title('Наш канал')}\n\nНовости, примеры работ и обновления — в канале Angelok Hub.",
        reply_markup=kb,
    )


@common.message(F.text == BTN_PROFILE)
async def menu_profile(message: Message, state: FSMContext) -> None:
    await state.clear()
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📜 История заказов", callback_data=MenuCb(action="history").pack()
                )
            ]
        ]
    )
    await message.answer(await build_profile(message.from_user), reply_markup=kb)


@common.callback_query(MenuCb.filter(F.action == "history"))
async def cb_history(cb: CallbackQuery) -> None:
    await cb_answer(cb)
    rows = await db.fetchall(
        "SELECT id, description, status, created_at FROM orders "
        "WHERE user_id = ? AND status != 'draft' ORDER BY id DESC LIMIT 10",
        (cb.from_user.id,),
    )
    if not rows:
        text = f"{title('История заказов')}\n\nЗаказов пока нет."
    else:
        lines = []
        for r in rows:
            short = r["description"].replace("\n", " ")
            if len(short) > 50:
                short = short[:50] + "…"
            lines.append(
                f"<b>#{r['id']}</b> · {fmt_dt(r['created_at'])} · "
                f"{ORDER_STATUS_LABELS.get(r['status'], r['status'])}\n<i>{esc(short)}</i>"
            )
        text = f"{title('История заказов')}\n\n" + "\n\n".join(lines)
    try:
        await cb.bot.send_message(cb.from_user.id, text)
    except TelegramAPIError:
        logger.exception("Не удалось отправить историю заказов")


# ════════════════════════════════════════════════════════════════════
#                       РОУТЕР 2: АДМИН-ПАНЕЛЬ
# ════════════════════════════════════════════════════════════════════
admin = Router(name="admin")
admin.message.filter(F.chat.type == ChatType.PRIVATE, F.from_user.id == ADMIN_ID)
admin.callback_query.filter(F.from_user.id == ADMIN_ID)


@admin.callback_query(AdminCb.filter(F.action == "noop"))
async def adm_noop(cb: CallbackQuery) -> None:
    await cb_answer(cb)


@admin.callback_query(AdminCb.filter(F.action == "home"))
async def adm_home(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await cb_answer(cb)
    maint = await get_setting("maintenance") == "1"
    await edit_view(cb, await admin_home_text(), admin_menu_kb(maint))


@admin.callback_query(AdminCb.filter(F.action == "stats"))
async def adm_stats(cb: CallbackQuery) -> None:
    await cb_answer(cb)
    await edit_view(cb, await build_stats(), back_kb())


@admin.callback_query(AdminCb.filter(F.action == "users"))
async def adm_users(cb: CallbackQuery, callback_data: AdminCb) -> None:
    await cb_answer(cb)
    text, kb = await users_view(callback_data.arg)
    await edit_view(cb, text, kb)


@admin.callback_query(AdminCb.filter(F.action == "orders"))
async def adm_orders(cb: CallbackQuery, bot: Bot) -> None:
    await cb_answer(cb)
    pending = await db.scalar("SELECT COUNT(*) FROM orders WHERE status = 'pending'")
    done = await db.scalar("SELECT COUNT(*) FROM orders WHERE status = 'completed'")
    rejected = await db.scalar("SELECT COUNT(*) FROM orders WHERE status = 'rejected'")
    recent = await db.fetchall(
        "SELECT id, user_id, username, status, created_at FROM orders "
        "WHERE status != 'draft' ORDER BY id DESC LIMIT 10"
    )
    lines = [
        f"<b>#{r['id']}</b> · {uname(r['username'])} (<code>{r['user_id']}</code>) · "
        f"{ORDER_STATUS_LABELS.get(r['status'], r['status'])} · {fmt_dt(r['created_at'])}"
        for r in recent
    ]
    body = "\n".join(lines) if lines else "Заказов пока нет."
    text = (
        f"{title('Заказы')}\n\n"
        f"На проверке: {pending} · Выполнено: {done} · Отклонено: {rejected}\n\n"
        f"<b>Последние 10:</b>\n{body}"
    )
    if pending:
        text += "\n\nЗаказы на проверке отправлены ниже отдельными карточками."
    await edit_view(cb, text, back_kb())
    cards = await db.fetchall(
        "SELECT * FROM orders WHERE status = 'pending' ORDER BY id LIMIT 10"
    )
    for order in cards:
        await safe_send(
            bot, ADMIN_ID, order_admin_text(order), reply_markup=order_admin_kb(order["id"])
        )
        await asyncio.sleep(0.05)


@admin.callback_query(AdminCb.filter(F.action == "settings"))
async def adm_settings(cb: CallbackQuery) -> None:
    await cb_answer(cb)
    ai_on = await get_setting("ai_enabled") == "1"
    orders_on = await get_setting("orders_enabled") == "1"
    await edit_view(cb, await settings_text(), settings_kb(ai_on, orders_on))


@admin.callback_query(AdminCb.filter(F.action.in_({"set_ai", "set_orders"})))
async def adm_toggle_setting(cb: CallbackQuery, callback_data: AdminCb) -> None:
    key = "ai_enabled" if callback_data.action == "set_ai" else "orders_enabled"
    await flip_setting(key)
    await cb_answer(cb, "Сохранено")
    ai_on = await get_setting("ai_enabled") == "1"
    orders_on = await get_setting("orders_enabled") == "1"
    await edit_view(cb, await settings_text(), settings_kb(ai_on, orders_on))


@admin.callback_query(AdminCb.filter(F.action == "toggle_maint"))
async def adm_toggle_maintenance(cb: CallbackQuery) -> None:
    now_on = await flip_setting("maintenance")
    await cb_answer(
        cb,
        "Бот остановлен для пользователей" if now_on else "Бот снова работает",
        alert=True,
    )
    await edit_view(cb, await admin_home_text(), admin_menu_kb(now_on))


# ---- рассылка ----
@admin.callback_query(AdminCb.filter(F.action == "broadcast"))
async def adm_broadcast_start(cb: CallbackQuery, state: FSMContext) -> None:
    await cb_answer(cb)
    await state.set_state(AdminStates.broadcast)
    await edit_view(
        cb,
        f"{title('Рассылка')}\n\n"
        "Отправьте сообщение для рассылки: текст, фото или документ.\n"
        "Подпись к фото или документу сохранится.\n\n"
        "<i>Отмена — кнопка ниже или /cancel</i>",
        broadcast_cancel_kb(),
    )


@admin.callback_query(AdminCb.filter(F.action == "bc_cancel"))
async def adm_broadcast_cancel(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await cb_answer(cb, "Рассылка отменена")
    maint = await get_setting("maintenance") == "1"
    await edit_view(cb, await admin_home_text(), admin_menu_kb(maint))


@admin.message(AdminStates.broadcast)
async def adm_broadcast_receive(message: Message, state: FSMContext) -> None:
    if message.content_type not in (ContentType.TEXT, ContentType.PHOTO, ContentType.DOCUMENT):
        await message.answer("Поддерживаются только текст, фото и документ. Пришлите другое сообщение.")
        return
    if message.text and message.text.startswith("/"):
        await message.answer("Это похоже на команду. Пришлите текст рассылки или нажмите /cancel.")
        return
    await state.update_data(src_chat=message.chat.id, src_msg=message.message_id)
    total = await db.scalar("SELECT COUNT(*) FROM users WHERE blocked = 0")
    await message.answer(
        f"{title('Подтверждение рассылки')}\n\n"
        f"Получателей: <b>{total}</b>\n"
        "Сообщение для рассылки — то, которое вы только что отправили.\n"
        "Можно прислать другое сообщение — оно заменит это.",
        reply_markup=broadcast_confirm_kb(),
    )


async def run_broadcast(bot: Bot, src_chat: int, src_msg: int) -> tuple[int, int, int]:
    rows = await db.fetchall("SELECT user_id FROM users WHERE blocked = 0 ORDER BY user_id")
    sent = failed = blocked = 0
    for row in rows:
        uid = row["user_id"]
        for _ in range(2):
            try:
                await bot.copy_message(chat_id=uid, from_chat_id=src_chat, message_id=src_msg)
                sent += 1
                break
            except TelegramRetryAfter as exc:
                await asyncio.sleep(exc.retry_after + 1)
                continue
            except TelegramForbiddenError:
                failed += 1
                blocked += 1
                await set_blocked(uid, 1)
                break
            except TelegramAPIError as exc:
                failed += 1
                logger.warning("Рассылка: ошибка для %s: %s", uid, exc)
                break
        else:
            failed += 1
        await asyncio.sleep(0.05)
    return sent, failed, blocked


@admin.callback_query(AdminCb.filter(F.action == "bc_go"))
async def adm_broadcast_go(cb: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    src_chat = data.get("src_chat")
    src_msg = data.get("src_msg")
    if not src_chat or not src_msg:
        await cb_answer(cb, "Рассылка уже запущена или сообщение не выбрано.", True)
        return
    await state.clear()
    await cb_answer(cb, "Рассылка запущена")
    await edit_view(cb, f"{title('Рассылка')}\n\n⏳ Идёт отправка. Дождитесь отчёта…")
    sent, failed, blocked = await run_broadcast(bot, src_chat, src_msg)
    await safe_send(
        bot,
        ADMIN_ID,
        f"{title('Рассылка завершена')}\n\n"
        f"Успешно отправлено: <b>{sent}</b>\n"
        f"Ошибок: <b>{failed}</b> (из них заблокировали бота: {blocked})",
        reply_markup=back_kb(),
    )


# ---- заказы: отправка и отклонение ----
@admin.callback_query(OrderCb.filter(F.action == "reject"))
async def adm_order_reject(cb: CallbackQuery, callback_data: OrderCb, bot: Bot) -> None:
    order_id = callback_data.id
    order = await get_order(order_id)
    if order is None:
        await cb_answer(cb, "Заказ не найден", True)
        return
    if not await reject_order(order_id):
        label = ORDER_STATUS_LABELS.get(order["status"], order["status"])
        await cb_answer(cb, f"Заказ уже обработан: {label}", True)
        await append_status(cb, f"<i>Статус заказа: {label}</i>")
        return
    await cb_answer(cb, "Оплата отклонена")
    await append_status(cb, "❌ <b>Оплата отклонена</b>")
    await safe_send(
        bot,
        order["user_id"],
        f"{title('Заказ #' + str(order_id))}\n\n"
        "К сожалению, подарок не найден, поэтому оплата не подтверждена.\n"
        "Если ты уверен, что отправил подарок, напиши владельцу через "
        "«📞 Обращение владельцу».",
    )


@admin.callback_query(OrderCb.filter(F.action == "send"))
async def adm_order_send(
    cb: CallbackQuery, callback_data: OrderCb, state: FSMContext, bot: Bot
) -> None:
    order_id = callback_data.id
    order = await get_order(order_id)
    if order is None:
        await cb_answer(cb, "Заказ не найден", True)
        return
    if order["status"] != "pending":
        label = ORDER_STATUS_LABELS.get(order["status"], order["status"])
        await cb_answer(cb, f"Заказ уже обработан: {label}", True)
        return
    await cb_answer(cb)
    origin_id = cb.message.message_id if isinstance(cb.message, Message) else 0
    await state.set_state(AdminStates.delivering)
    await state.update_data(order_id=order_id, origin_msg_id=origin_id)
    await safe_send(
        bot,
        ADMIN_ID,
        f"{title('Отправка заказа #' + str(order_id))}\n\n"
        "Пришлите сообщение и/или файл (ZIP, APK, изображение и т.д.). "
        "Каждое ваше сообщение будет передано пользователю через бота.\n"
        "После первой успешной доставки заказ считается выполненным.\n\n"
        "Когда закончите — нажмите кнопку ниже. Отмена — /cancel",
        reply_markup=deliver_kb(),
    )


@admin.callback_query(AdminCb.filter(F.action == "deliv_done"))
async def adm_deliver_done(cb: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    order_id = data.get("order_id")
    await state.clear()
    await cb_answer(cb)
    if order_id:
        order = await get_order(order_id)
        if order is not None and order["status"] == "completed":
            result = f"Отправка завершена. Заказ #{order_id} выполнен."
        else:
            result = f"Ничего не отправлено. Заказ #{order_id} остаётся на проверке."
    else:
        result = "Отправка завершена."
    await append_status(cb, f"<i>{result}</i>")


@admin.message(AdminStates.delivering)
async def adm_deliver(message: Message, state: FSMContext, bot: Bot) -> None:
    if message.text and message.text.startswith("/"):
        await message.answer("Неизвестная команда. Для отмены — /cancel.")
        return
    data = await state.get_data()
    order_id = data.get("order_id")
    order = await get_order(order_id) if order_id else None
    if order is None:
        await state.clear()
        await message.answer("Заказ не найден. Отправка отменена.")
        return
    result = await deliver_to_user(
        bot, order["user_id"], message, f"📦 <b>Ваш заказ #{order_id}</b>"
    )
    if result == "ok":
        first = await complete_order(order_id)
        if first:
            origin_id = data.get("origin_msg_id")
            if origin_id:
                try:
                    await bot.edit_message_reply_markup(
                        chat_id=ADMIN_ID, message_id=origin_id, reply_markup=None
                    )
                except TelegramAPIError:
                    pass
            note = f"✅ Доставлено. Заказ #{order_id} отмечен выполненным."
        else:
            note = "✅ Доставлено."
        await message.answer(
            f"{note}\nМожно отправить ещё сообщение или файл либо завершить отправку.",
            reply_markup=deliver_kb(),
        )
    elif result == "blocked":
        await message.answer(
            "⚠️ Пользователь заблокировал бота — доставить заказ не удалось. "
            "Заказ остаётся на проверке."
        )
    else:
        await message.answer(
            "⚠️ Не удалось доставить сообщение (подробности в bot.log). Попробуйте ещё раз."
        )


# ---- обращения: ответ и отклонение ----
@admin.callback_query(TicketCb.filter(F.action == "reply"))
async def adm_ticket_reply_start(
    cb: CallbackQuery, callback_data: TicketCb, state: FSMContext, bot: Bot
) -> None:
    ticket = await get_ticket(callback_data.id)
    if ticket is None:
        await cb_answer(cb, "Обращение не найдено", True)
        return
    await cb_answer(cb)
    origin_id = cb.message.message_id if isinstance(cb.message, Message) else 0
    await state.set_state(AdminStates.replying)
    await state.update_data(ticket_id=ticket["id"], origin_msg_id=origin_id)
    await safe_send(
        bot,
        ADMIN_ID,
        f"{title('Ответ на обращение #' + str(ticket['id']))}\n\n"
        "Пришлите ответ (текст, фото, файл — что угодно). Он будет передан пользователю.\n"
        "<i>Отмена — /cancel</i>",
    )


@admin.callback_query(TicketCb.filter(F.action == "reject"))
async def adm_ticket_reject(cb: CallbackQuery, callback_data: TicketCb, bot: Bot) -> None:
    ticket = await get_ticket(callback_data.id)
    if ticket is None:
        await cb_answer(cb, "Обращение не найдено", True)
        return
    if not await set_ticket_status(ticket["id"], "rejected", only_from="open"):
        await cb_answer(cb, "Обращение уже обработано", True)
    else:
        await cb_answer(cb, "Обращение отклонено")
        await safe_send(
            bot,
            ticket["user_id"],
            f"{title('Обращение #' + str(ticket['id']))}\n\n"
            "Владелец закрыл ваше обращение без ответа. "
            "Если вопрос остался — отправьте новое обращение.",
        )
    if isinstance(cb.message, Message):
        try:
            await cb.message.edit_reply_markup(reply_markup=ticket_kb(ticket["id"], False))
        except TelegramAPIError:
            pass


async def send_ticket_reply(
    message: Message, bot: Bot, ticket_id: int, origin_msg_id: Optional[int]
) -> bool:
    ticket = await get_ticket(ticket_id)
    if ticket is None:
        await message.answer("Обращение не найдено.")
        return False
    result = await deliver_to_user(
        bot, ticket["user_id"], message, f"💬 <b>Ответ владельца · обращение #{ticket_id}</b>"
    )
    if result == "ok":
        await set_ticket_status(ticket_id, "answered")
        await message.answer(f"✅ Ответ по обращению #{ticket_id} отправлен.")
        if origin_msg_id:
            try:
                await bot.edit_message_reply_markup(
                    chat_id=ADMIN_ID, message_id=origin_msg_id, reply_markup=ticket_kb(ticket_id, False)
                )
            except TelegramAPIError:
                pass
        return True
    if result == "blocked":
        await message.answer("⚠️ Пользователь заблокировал бота — ответ не доставлен.")
    else:
        await message.answer("⚠️ Не удалось отправить ответ (подробности в bot.log).")
    return False


@admin.message(AdminStates.replying)
async def adm_ticket_reply_send(message: Message, state: FSMContext, bot: Bot) -> None:
    if message.text and message.text.startswith("/"):
        await message.answer("Неизвестная команда. Для отмены — /cancel.")
        return
    data = await state.get_data()
    ticket_id = data.get("ticket_id")
    if not ticket_id:
        await state.clear()
        await message.answer("Обращение не выбрано.")
        return
    if await send_ticket_reply(message, bot, ticket_id, data.get("origin_msg_id")):
        await state.clear()


async def ticket_reply_filter(message: Message) -> Union[bool, dict]:
    """Срабатывает, когда админ делает reply на сообщение, связанное с обращением."""
    if not message.reply_to_message:
        return False
    ticket_id = await ticket_by_admin_msg(message.reply_to_message.message_id)
    if ticket_id is None:
        return False
    return {"swipe_ticket_id": ticket_id}


@admin.message(StateFilter(None), ticket_reply_filter)
async def adm_ticket_swipe_reply(message: Message, bot: Bot, swipe_ticket_id: int) -> None:
    await send_ticket_reply(message, bot, swipe_ticket_id, None)


# ════════════════════════════════════════════════════════════════════
#                  РОУТЕР 3: ПОЛЬЗОВАТЕЛЬСКИЕ СЦЕНАРИИ
# ════════════════════════════════════════════════════════════════════
user_router = Router(name="user")
user_router.message.filter(F.chat.type == ChatType.PRIVATE)


# ---- покупка конфигурации ----
@user_router.message(BuyStates.description, F.text)
async def buy_description(message: Message, state: FSMContext) -> None:
    text = message.text.strip()
    if text.startswith("/"):
        await message.answer("Неизвестная команда. Опиши пожелания текстом или нажми /cancel.")
        return
    if len(text) < 5:
        await message.answer("Опиши пожелания чуть подробнее — минимум несколько слов.")
        return
    if len(text) > ORDER_MAX_CHARS:
        await message.answer(
            f"Описание слишком длинное. Сократи его до {ORDER_MAX_CHARS} символов."
        )
        return
    if await get_setting("orders_enabled") != "1":
        await state.clear()
        await message.answer("Приём заказов временно закрыт. Загляните позже.")
        return
    order_id = await create_draft(message.from_user, text)
    await state.clear()
    await message.answer(
        f"{title('Заказ #' + str(order_id))}\n\n"
        "Отлично! Стоимость — один подарок «Мишка».\n\n"
        f"Отправь подарок пользователю {esc(GIFT_RECIPIENT)}.\n\n"
        "<i>После отправки нажми кнопку «Готово».</i>",
        reply_markup=order_paid_kb(order_id),
    )


@user_router.message(BuyStates.description)
async def buy_description_wrong_type(message: Message) -> None:
    await message.answer("Опиши пожелания к конфигурации текстом. Отмена — /cancel.")


@user_router.callback_query(OrderCb.filter(F.action == "paid"))
async def order_paid(cb: CallbackQuery, callback_data: OrderCb, bot: Bot) -> None:
    order_id = callback_data.id
    if not await claim_order(order_id, cb.from_user.id):
        await cb_answer(cb, "Эта заявка уже отправлена владельцу.", True)
        if isinstance(cb.message, Message):
            try:
                await cb.message.edit_reply_markup(reply_markup=None)
            except TelegramAPIError:
                pass
        return
    order = await get_order(order_id)
    try:
        await bot.send_message(
            ADMIN_ID, order_admin_text(order), reply_markup=order_admin_kb(order_id)
        )
    except TelegramAPIError:
        logger.exception("Не удалось уведомить администратора о заказе #%s", order_id)
        await revert_claim(order_id)
        await cb_answer(cb, "Не удалось связаться с владельцем. Попробуй чуть позже.", True)
        return
    await cb_answer(cb, "Заявка отправлена")
    await edit_view(
        cb,
        f"{title('Заказ #' + str(order_id))}\n\n"
        "Заявка отправлена владельцу.\n"
        "Он вручную проверит подарок и пришлёт конфигурацию прямо сюда, в этот чат.\n\n"
        "<i>Статус заказов — в разделе «👤 Профиль».</i>",
    )


# ---- обращение владельцу ----
_album_locks: dict[str, asyncio.Lock] = {}
_album_tickets: dict[str, tuple[int, float]] = {}

OWNER_CONTENT_TYPES = {
    ContentType.TEXT,
    ContentType.PHOTO,
    ContentType.VIDEO,
    ContentType.DOCUMENT,
    ContentType.VOICE,
    ContentType.AUDIO,
    ContentType.VIDEO_NOTE,
    ContentType.ANIMATION,
}


def _prune_albums() -> None:
    now = time.monotonic()
    if len(_album_tickets) > 200:
        for gid in [g for g, (_, ts) in _album_tickets.items() if now - ts > 600]:
            _album_tickets.pop(gid, None)
            _album_locks.pop(gid, None)


async def attach_to_ticket(bot: Bot, message: Message, ticket_id: int) -> None:
    """Дополнительные файлы альбома — к уже созданному обращению."""
    try:
        copied = await bot.copy_message(
            chat_id=ADMIN_ID, from_chat_id=message.chat.id, message_id=message.message_id
        )
        await link_ticket_msg(copied.message_id, ticket_id)
    except TelegramAPIError:
        logger.exception("Не удалось передать часть альбома по обращению #%s", ticket_id)


async def album_follow_filter(message: Message) -> bool:
    gid = message.media_group_id
    return bool(gid) and gid in _album_tickets


@user_router.message(album_follow_filter)
async def owner_album_follow(message: Message, bot: Bot) -> None:
    ticket_id = _album_tickets[message.media_group_id][0]
    await attach_to_ticket(bot, message, ticket_id)


@user_router.message(OwnerStates.message)
async def owner_message(message: Message, state: FSMContext, bot: Bot) -> None:
    if message.text and message.text.startswith("/"):
        await message.answer("Неизвестная команда. Для отмены — /cancel.")
        return
    if message.content_type not in OWNER_CONTENT_TYPES:
        await message.answer(
            "Этот формат не поддерживается. Отправьте текст, фото, видео, документ "
            "или голосовое сообщение."
        )
        return

    gid = message.media_group_id
    lock = _album_locks.setdefault(gid, asyncio.Lock()) if gid else None
    if lock is not None:
        await lock.acquire()
    try:
        if gid and gid in _album_tickets:
            await attach_to_ticket(bot, message, _album_tickets[gid][0])
            return

        ctype = message.content_type.value
        text = message.text or message.caption or ""
        ticket_id = await create_ticket(message.from_user, ctype, text)
        try:
            header = await bot.send_message(
                ADMIN_ID, ticket_header(ticket_id, message.from_user, ctype)
            )
            copied = await bot.copy_message(
                chat_id=ADMIN_ID,
                from_chat_id=message.chat.id,
                message_id=message.message_id,
                reply_markup=ticket_kb(ticket_id, True),
            )
        except TelegramAPIError:
            logger.exception("Не удалось передать обращение #%s владельцу", ticket_id)
            await set_ticket_status(ticket_id, "failed")
            await message.answer(
                "Не удалось передать сообщение владельцу. Попробуйте чуть позже."
            )
            return
        await link_ticket_msg(header.message_id, ticket_id)
        await link_ticket_msg(copied.message_id, ticket_id)
        if gid:
            _album_tickets[gid] = (ticket_id, time.monotonic())
            _prune_albums()
        await state.clear()
        await message.answer(
            f"{title('Обращение #' + str(ticket_id))}\n\n"
            "Сообщение передано владельцу. Ответ придёт прямо в этот чат.",
            reply_markup=main_menu(),
        )
    finally:
        if lock is not None:
            lock.release()


# ---- обращение к ИИ ----
@user_router.message(AIStates.chat, F.text)
async def ai_message(message: Message, bot: Bot) -> None:
    text = message.text.strip()
    if text.startswith("/"):
        await message.answer("Неизвестная команда. Завершить диалог с ИИ — /done.")
        return
    if len(text) > AI_MAX_INPUT_CHARS:
        await message.answer(f"Вопрос слишком длинный. Сократите его до {AI_MAX_INPUT_CHARS} символов.")
        return
    if await get_setting("ai_enabled") != "1":
        await message.answer("ИИ-менеджер временно отключён. Попробуйте позже.")
        return

    uid = message.from_user.id
    if uid in _ai_busy:
        await message.answer("Подожди немного — я ещё отвечаю на предыдущий вопрос.")
        return
    _ai_busy.add(uid)
    typing_task = asyncio.create_task(typing_loop(bot, message.chat.id))
    try:
        history = await ai_context(uid)
        answer = await ask_ai(history, text)
        await ai_save(uid, text, answer)
        for chunk in split_text(answer):
            await message.answer(html.escape(chunk))
    except AIError as exc:
        logger.warning("ИИ недоступен для %s: %s", uid, exc)
        await message.answer(AI_UNAVAILABLE)
    except TelegramAPIError:
        logger.exception("Не удалось отправить ответ ИИ пользователю %s", uid)
    except Exception:
        logger.exception("Непредвиденная ошибка в диалоге с ИИ")
        await message.answer(AI_UNAVAILABLE)
    finally:
        _ai_busy.discard(uid)
        typing_task.cancel()


@user_router.message(AIStates.chat)
async def ai_wrong_type(message: Message) -> None:
    await message.answer("Пока я принимаю только текстовые вопросы. Завершить диалог — /done.")


# ---- прочее ----
@user_router.my_chat_member()
async def on_my_chat_member(event: ChatMemberUpdated) -> None:
    if event.chat.type != ChatType.PRIVATE:
        return
    status = event.new_chat_member.status
    if status == "kicked":
        await set_blocked(event.from_user.id, 1)
        logger.info("Пользователь %s заблокировал бота", event.from_user.id)
    elif status == "member":
        await set_blocked(event.from_user.id, 0)


@user_router.callback_query()
async def cb_unknown(cb: CallbackQuery) -> None:
    await cb_answer(cb, "Эта кнопка больше не активна.", True)


@user_router.message()
async def fallback(message: Message) -> None:
    await message.answer(
        "Не понял вас. Выберите действие в меню ниже.", reply_markup=main_menu()
    )


# ════════════════════════════════════════════════════════════════════
#                          ОШИБКИ И ЗАПУСК
# ════════════════════════════════════════════════════════════════════
async def on_error(event: ErrorEvent) -> bool:
    logger.error(
        "Ошибка при обработке апдейта %s: %r",
        event.update.update_id,
        event.exception,
        exc_info=event.exception,
    )
    try:
        update = event.update
        if update.callback_query:
            await update.callback_query.answer("Произошла ошибка. Попробуйте ещё раз.", show_alert=True)
        elif update.message:
            await update.message.answer("Произошла ошибка. Попробуйте ещё раз чуть позже.")
    except Exception:
        pass
    return True


async def setup_commands(bot: Bot) -> None:
    user_cmds = [
        BotCommand(command="start", description="Главное меню"),
        BotCommand(command="myid", description="Показать мой Telegram ID"),
        BotCommand(command="cancel", description="Отменить текущее действие"),
        BotCommand(command="done", description="Завершить диалог с ИИ"),
    ]
    try:
        await bot.set_my_commands(user_cmds)
        await bot.set_my_commands(
            user_cmds + [BotCommand(command="admin", description="Админ-панель")],
            scope=BotCommandScopeChat(chat_id=ADMIN_ID),
        )
    except TelegramAPIError as exc:
        logger.warning("Не удалось установить список команд: %s", exc)


async def main() -> None:
    db.connect()
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())

    dp.message.outer_middleware(GateMiddleware())
    dp.callback_query.outer_middleware(CallbackThrottle())
    dp.callback_query.outer_middleware(GateMiddleware())

    dp.include_routers(common, admin, user_router)
    dp.errors.register(on_error)

    if ADMIN_ID == 123456789:
        logger.warning("ADMIN_ID не изменён — укажите свой Telegram ID (узнать: команда /myid)")
    if not OPENROUTER_API_KEY or OPENROUTER_API_KEY.startswith("ВСТАВИТЬ"):
        logger.warning("OPENROUTER_API_KEY не задан — раздел ИИ будет отвечать «Временно недоступно»")

    try:
        await bot.delete_webhook(drop_pending_updates=False)
        await setup_commands(bot)
        me = await bot.get_me()
        logger.info("Бот @%s запущен", me.username)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()
        db.close()
        logger.info("Бот остановлен")


if __name__ == "__main__":
    setup_logging()
    if not BOT_TOKEN or BOT_TOKEN.startswith("ВСТАВИТЬ"):
        print("Укажите BOT_TOKEN в блоке настроек в начале bot.py")
        sys.exit(1)
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен")
