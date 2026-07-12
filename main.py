import os
import asyncio
import logging
import html
from datetime import datetime
from zoneinfo import ZoneInfo

import aiosqlite
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import (
    Message,
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    CallbackQuery,
)
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.exceptions import TelegramAPIError
from aiogram.client.default import DefaultBotProperties

# ========== НАСТРОЙКИ ==========
TOKEN = os.environ["TOKEN"]
DEFAULT_REMINDER_HOUR = int(os.environ.get("DEFAULT_REMINDER_HOUR", 9))
DEFAULT_REMINDER_MINUTE = 0
TIMEZONE = "Europe/Moscow"
# ================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

DB_NAME = "/app/data/prayers.db"


# ---------- Состояния FSM ----------
class PrayerStates(StatesGroup):
    waiting_for_settime_time = State()


# ---------- База данных ----------
async def init_db():
    async with aiosqlite.connect(DB_NAME) as conn:
        await conn.execute(
            f"""CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                reminder_hour INTEGER DEFAULT {DEFAULT_REMINDER_HOUR},
                reminder_minute INTEGER DEFAULT {DEFAULT_REMINDER_MINUTE},
                timezone TEXT DEFAULT '{TIMEZONE}'
            )"""
        )
        await conn.execute(
            """CREATE TABLE IF NOT EXISTS prayers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                text TEXT,
                sender_link TEXT,
                from_user TEXT,
                created_at TEXT,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            )"""
        )
        await conn.commit()


async def register_user(user_id: int, username: str, full_name: str):
    async with aiosqlite.connect(DB_NAME) as conn:
        await conn.execute(
            "INSERT OR IGNORE INTO users (user_id, username, full_name) VALUES (?, ?, ?)",
            (user_id, username, full_name),
        )
        await conn.commit()


async def add_prayer(
    user_id: int, text: str, sender_link: str | None, from_user: str
) -> int:
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    async with aiosqlite.connect(DB_NAME) as conn:
        cursor = await conn.execute(
            "INSERT INTO prayers (user_id, text, sender_link, from_user, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, text, sender_link, from_user, created_at),
        )
        await conn.commit()
        return cursor.lastrowid


async def get_undone_prayers(user_id: int) -> list:
    """Возвращает список кортежей (id, text, sender_link) в порядке создания."""
    async with aiosqlite.connect(DB_NAME) as conn:
        cursor = await conn.execute(
            "SELECT id, text, sender_link FROM prayers WHERE user_id = ? ORDER BY created_at",
            (user_id,),
        )
        return await cursor.fetchall()


async def delete_prayer(user_id: int, prayer_id: int) -> bool:
    async with aiosqlite.connect(DB_NAME) as conn:
        cursor = await conn.execute(
            "DELETE FROM prayers WHERE id = ? AND user_id = ?",
            (prayer_id, user_id),
        )
        await conn.commit()
        return cursor.rowcount > 0


# ---------- Хендлеры ----------
router = Router()


@router.message(Command("start"))
async def start_cmd(message: Message):
    user = message.from_user
    await register_user(user.id, user.username, user.full_name)
    text = (
        "🙏 <b>Молитвенный бот для личного использования</b>\n\n"
        "Просто перешлите мне любое сообщение (или напишите текст) — я сохраню его как молитвенную нужду.\n"
        "Каждый день в выбранное время я буду присылать список всех нужд для молитвы.\n\n"
        "Команды:\n"
        "/list — показать список текущих нужд\n"
        "/done — удалить нужду (исполнена)\n"
        "/help — справка\n"
        "/settime — установить время напоминания\n"
        "/cancel — отменить текущее действие\n\n"
        "Прекрасных вам молитв и свидетельств Божьей славы!"
    )
    try:
        await message.answer(text)
    except TelegramAPIError as e:
        logger.error(f"Ошибка отправки start: {e}")
        await message.answer("Произошла ошибка. Попробуйте позже.")


@router.message(Command("help"))
async def help_cmd(message: Message):
    text = (
        "📖 <b>Справка</b>\n"
        "/list — список нужд\n"
        "/done — удалить нужду. Бот покажет кнопки для выбора.\n"
        "/settime — изменить время напоминания (Московское время).\n"
        "/cancel — отменить текущее действие.\n"
        "\n"
        "Пересылайте сообщения, чтобы добавить нужду или просто напишите текстом.\n\n"
    )
    try:
        await message.answer(text)
    except TelegramAPIError as e:
        logger.error(f"Ошибка отправки help: {e}")
        await message.answer("Произошла ошибка. Попробуйте позже.")


@router.message(Command("list"))
async def list_cmd(message: Message):
    user_id = message.from_user.id
    try:
        prayers = await get_undone_prayers(user_id)
    except Exception as e:
        logger.error(f"Ошибка получения списка нужд: {e}")
        await message.answer("⚠️ Не удалось получить список нужд.")
        return

    if not prayers:
        await message.answer("✅ У вас нет активных молитвенных нужд.")
        return

    lines = ["<b>Ваши текущие молитвенные нужды:</b>", ""]
    for idx, (pid, req_text, sender_link) in enumerate(prayers, start=1):
        safe_text = html.escape(req_text)
        line = f"<code>{idx}</code>. {safe_text}"
        if sender_link:
            line += f"\n— {sender_link}"
        lines.append(line)
        lines.append("")
    lines.append("Если вам нужно удалить нужду, используйте команду /done")
    full_message = "\n".join(lines)
    try:
        await message.answer(full_message)
    except TelegramAPIError as e:
        logger.error(f"Ошибка отправки списка: {e}")
        plain_lines = ["Ваши текущие молитвенные нужды:", ""]
        for idx, (_, req_text, _) in enumerate(prayers, start=1):
            plain_lines.append(f"{idx}. {req_text}")
        await message.answer("\n".join(plain_lines))


# --- /done – инлайн-кнопки, удаление без подтверждения ---
@router.message(Command("done"))
async def done_cmd(message: Message):
    user_id = message.from_user.id
    try:
        prayers = await get_undone_prayers(user_id)
    except Exception as e:
        logger.error(f"Ошибка получения списка для удаления: {e}")
        await message.answer("⚠️ Не удалось загрузить список.")
        return

    if not prayers:
        await message.answer("✅ У вас нет активных молитвенных нужд.")
        return

    # Формируем инлайн-клавиатуру с кнопками для каждой нужды
    keyboard = InlineKeyboardMarkup(inline_keyboard=[])
    for idx, (prayer_id, text, _) in enumerate(prayers, start=1):
        btn_text = text[:30] + ("..." if len(text) > 30 else "")
        keyboard.inline_keyboard.append(
            [InlineKeyboardButton(text=f"{idx}. {btn_text}", callback_data=f"del_{prayer_id}")]
        )
    keyboard.inline_keyboard.append(
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_del")]
    )

    lines = ["<b>Выберите нужду для удаления:</b>", ""]
    for idx, (pid, req_text, sender_link) in enumerate(prayers, start=1):
        safe_text = html.escape(req_text)
        line = f"<code>{idx}</code>. {safe_text}"
        if sender_link:
            line += f"\n— {sender_link}"
        lines.append(line)
        lines.append("")
    try:
        await message.answer("\n".join(lines), reply_markup=keyboard)
    except TelegramAPIError as e:
        logger.error(f"Ошибка отправки списка удаления: {e}")
        await message.answer("Не удалось отобразить список.")


# Колбэк удаления – сразу удаляет и обновляет список
@router.callback_query(F.data.startswith("del_"))
async def del_prayer_callback(callback: CallbackQuery):
    user_id = callback.from_user.id
    prayer_id = int(callback.data.split("_")[1])

    try:
        deleted = await delete_prayer(user_id, prayer_id)
    except Exception as e:
        logger.error(f"Ошибка удаления: {e}")
        await callback.answer("⚠️ Не удалось выполнить удаление.", show_alert=True)
        return

    if not deleted:
        await callback.answer("❌ Нужда не найдена или уже удалена.", show_alert=True)
        # на всякий случай перестраиваем клавиатуру (вдруг она устарела)
        await rebuild_done_message(callback)
        return

    await callback.answer("✅ Нужда удалена. Слава Богу!")
    # Обновляем сообщение: убираем удалённый элемент из списка и кнопок
    await rebuild_done_message(callback)


async def rebuild_done_message(callback: CallbackQuery):
    """Перестраивает сообщение со списком нужд после удаления."""
    user_id = callback.from_user.id
    try:
        prayers = await get_undone_prayers(user_id)
    except Exception:
        await callback.message.edit_text("⚠️ Не удалось обновить список.")
        return

    if not prayers:
        await callback.message.edit_text("✅ У вас нет активных молитвенных нужд.")
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[])
    for idx, (prayer_id, text, _) in enumerate(prayers, start=1):
        btn_text = text[:30] + ("..." if len(text) > 30 else "")
        keyboard.inline_keyboard.append(
            [InlineKeyboardButton(text=f"{idx}. {btn_text}", callback_data=f"del_{prayer_id}")]
        )
    keyboard.inline_keyboard.append(
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_del")]
    )

    lines = ["<b>Выберите нужду для удаления:</b>", ""]
    for idx, (pid, req_text, sender_link) in enumerate(prayers, start=1):
        safe_text = html.escape(req_text)
        line = f"<code>{idx}</code>. {safe_text}"
        if sender_link:
            line += f"\n— {sender_link}"
        lines.append(line)
        lines.append("")

    try:
        await callback.message.edit_text(
            "\n".join(lines),
            reply_markup=keyboard,
        )
    except TelegramAPIError as e:
        logger.error(f"Ошибка обновления сообщения: {e}")


@router.callback_query(F.data == "cancel_del")
async def cancel_del_callback(callback: CallbackQuery):
    await callback.message.edit_text("❌ Удаление отменено.")
    await callback.answer()


# --- /settime – с инлайн-кнопкой отмены и очисткой клавиатуры ---
@router.message(Command("settime"))
async def settime_cmd(message: Message, state: FSMContext, bot: Bot):
    await state.set_state(PrayerStates.waiting_for_settime_time)
    cancel_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_settime")]
        ]
    )
    msg = await message.answer(
        "Введите час и минуту для ежедневного напоминания (Москва) в формате: ЧЧ ММ\n"
        "Например: 7 30",
        reply_markup=cancel_keyboard,
    )
    await state.update_data(bot_message_id=msg.message_id)


@router.callback_query(F.data == "cancel_settime")
async def cancel_settime_callback(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("Установка времени отменена.")
    await callback.answer()


@router.message(PrayerStates.waiting_for_settime_time)
async def process_settime_time(message: Message, state: FSMContext, bot: Bot):
    user_id = message.from_user.id
    text = message.text or ""

    parts = text.strip().split()
    if len(parts) != 2:
        await message.answer(
            "Пожалуйста, введите два числа через пробел: час (0–23) и минуты (0–59). Например: 7 30"
        )
        return

    try:
        hour = int(parts[0])
        minute = int(parts[1])
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
    except ValueError:
        await message.answer("Час от 0 до 23, минута от 0 до 59. Попробуйте снова.")
        return

    try:
        async with aiosqlite.connect(DB_NAME) as conn:
            await conn.execute(
                "UPDATE users SET reminder_hour = ?, reminder_minute = ? WHERE user_id = ?",
                (hour, minute, user_id),
            )
            await conn.commit()
    except Exception as e:
        logger.error(f"Ошибка установки времени: {e}")
        await message.answer("⚠️ Не удалось установить время.")
        await state.clear()
        return

    data = await state.get_data()
    bot_message_id = data.get("bot_message_id")
    if bot_message_id:
        try:
            await bot.edit_message_reply_markup(
                chat_id=message.chat.id,
                message_id=bot_message_id,
                reply_markup=None,
            )
        except Exception as e:
            logger.debug(f"Не удалось убрать клавиатуру: {e}")

    await message.answer(
        f"⏰ Время ежедневного напоминания установлено на {hour:02d}:{minute:02d} (Москва)."
    )
    await state.clear()


# --- Глобальная команда отмены ---
@router.message(Command("cancel"))
async def cancel_cmd(message: Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is None:
        await message.answer("Нет активных действий для отмены.")
        return
    data = await state.get_data()
    bot_message_id = data.get("bot_message_id")
    if bot_message_id:
        try:
            await message.bot.edit_message_reply_markup(
                chat_id=message.chat.id,
                message_id=bot_message_id,
                reply_markup=None,
            )
        except Exception:
            pass
    await state.clear()
    await message.answer("Действие отменено.")


# --- Обработка пересланных сообщений ---
@router.message(F.forward_date)
async def handle_forwarded(message: Message):
    user = message.from_user
    await register_user(user.id, user.username, user.full_name)

    text = message.caption or message.text
    if not text:
        text = "[Сообщение без текста (фото/аудио/стикер)]"

    sender_link = None
    if message.forward_from and message.forward_from.id:
        source_name = html.escape(
            message.forward_from.full_name or str(message.forward_from.id)
        )
        sender_link = (
            f'<a href="tg://user?id={message.forward_from.id}">{source_name}</a>'
        )

    from_user = (
        f"переслано от {html.escape(message.forward_sender_name or 'неизвестный источник')} "
        f"(добавил {html.escape(user.full_name or user.username or '')})"
    )

    try:
        prayer_id = await add_prayer(user.id, text, sender_link, from_user)
    except Exception as e:
        logger.error(f"Ошибка добавления нужды: {e}")
        await message.answer("⚠️ Не удалось сохранить нужду.")
        return

    try:
        await message.answer(
            "🙏 Сохранил молитвенную нужду\n\n"
            "Я напомню о ней в ваше время молитвы"
        )
    except TelegramAPIError as e:
        logger.error(f"Ошибка отправки подтверждения: {e}")
        await message.answer("Нужда сохранена, но возникла ошибка при отображении.")


# --- Обработка обычных текстовых сообщений ---
@router.message(F.text, ~F.forward_date)
async def handle_plain_text(message: Message):
    user = message.from_user
    await register_user(user.id, user.username, user.full_name)

    text = message.text
    if not text:
        await message.answer("Пожалуйста, отправьте текст или перешлите сообщение.")
        return

    from_user = html.escape(user.full_name or user.username or str(user.id))

    try:
        prayer_id = await add_prayer(
            user.id, text, sender_link=None, from_user=from_user
        )
    except Exception as e:
        logger.error(f"Ошибка добавления нужды: {e}")
        await message.answer("⚠️ Не удалось сохранить нужду.")
        return

    try:
        await message.answer(
            "🙏 Сохранил молитвенную нужду\n\n"
            "Я напомню о ней в ваше время молитвы"
        )
    except TelegramAPIError as e:
        logger.error(f"Ошибка отправки подтверждения: {e}")
        await message.answer("Нужда сохранена, но возникла ошибка при отображении.")


# ---------- Планировщик ----------
async def check_and_send(bot: Bot):
    now = datetime.now(ZoneInfo(TIMEZONE))
    current_hour, current_minute = now.hour, now.minute

    async with aiosqlite.connect(DB_NAME) as conn:
        cursor = await conn.execute(
            "SELECT user_id FROM users WHERE reminder_hour = ? AND reminder_minute = ?",
            (current_hour, current_minute),
        )
        user_ids = [row[0] for row in await cursor.fetchall()]

    for user_id in user_ids:
        try:
            prayers = await get_undone_prayers(user_id)
        except Exception as e:
            logger.error(f"Ошибка получения нужд для {user_id}: {e}")
            continue

        if not prayers:
            continue

        lines = [
            "<b>🕊 Ежедневное напоминание о молитве</b>",
            "",
            "Помолитесь сегодня за эти нужды:",
            "",
        ]
        for idx, (pid, req_text, sender_link) in enumerate(prayers, start=1):
            safe_text = html.escape(req_text)
            line = f"<code>{idx}</code>. {safe_text}"
            if sender_link:
                line += f"\n— {sender_link}"
            lines.append(line)
            lines.append("")
        lines.append("Удалить нужду можно командой /done")
        full_message = "\n".join(lines)

        try:
            await bot.send_message(chat_id=user_id, text=full_message)
        except TelegramAPIError as e:
            logger.error(f"Ошибка отправки напоминания {user_id}: {e}")
        except Exception as e:
            logger.error(f"Неизвестная ошибка отправки {user_id}: {e}")


# ---------- Запуск ----------
async def main():
    await init_db()
    bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    scheduler = AsyncIOScheduler(timezone=TIMEZONE)

    @dp.startup()
    async def on_startup():
        commands = [
            BotCommand(command="list", description="Показать список молитвенных нужд"),
            BotCommand(command="done", description="Удалить нужду (исполнена)"),
            BotCommand(command="settime", description="Установить время напоминания"),
            BotCommand(command="help", description="Помощь"),
            BotCommand(command="cancel", description="Отменить текущее действие"),
        ]
        await bot.set_my_commands(commands)
        logger.info("Меню команд установлено")

        scheduler.add_job(
            check_and_send, "interval", minutes=1, args=[bot], id="minute_check"
        )
        scheduler.start()
        logger.info("Планировщик запущен")

    @dp.shutdown()
    async def on_shutdown():
        scheduler.shutdown(wait=False)
        logger.info("Планировщик остановлен")

    logger.info("Бот запущен...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
