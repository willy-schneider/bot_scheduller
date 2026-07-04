import os
import asyncio
import logging
import html
from datetime import datetime
from zoneinfo import ZoneInfo

import aiosqlite
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import Message, BotCommand
from aiogram.filters import Command
from aiogram.exceptions import TelegramAPIError
from aiogram.client.default import DefaultBotProperties  # добавлено

# ========== НАСТРОЙКИ ==========
TOKEN = os.environ["TOKEN"]
DEFAULT_REMINDER_HOUR = int(os.environ.get("DEFAULT_REMINDER_HOUR", 9))
DEFAULT_REMINDER_MINUTE = 0
TIMEZONE = "Europe/Moscow"
# ================================

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

DB_NAME = "/app/data/prayers.db"

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

async def add_prayer(user_id: int, text: str, sender_link: str | None, from_user: str) -> int:
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    async with aiosqlite.connect(DB_NAME) as conn:
        cursor = await conn.execute(
            "INSERT INTO prayers (user_id, text, sender_link, from_user, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, text, sender_link, from_user, created_at),
        )
        await conn.commit()
        return cursor.lastrowid

async def get_undone_prayers(user_id: int) -> list:
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
    # Без <> в описаниях команд
    text = (
        "🙏 <b>Молитвенный бот для личного использования</b>\n\n"
        "Просто перешлите мне любое сообщение (или напишите текст) — я сохраню его как молитвенную нужду.\n"
        "Если вы <b>пересылаете</b> сообщение, в конце текста автоматически добавится ссылка на профиль автора.\n"
        "Каждый день в выбранное время я буду присылать список всех нужд для молитвы.\n\n"
        "Команды:\n"
        "/list — показать список текущих нужд\n"
        "/done номер — удалить нужду (исполнена)\n"
        "/help — справка\n"
        "/settime час минута — установить время напоминания (например /settime 7 30)"
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
        "/list — список неисполненных нужд\n"
        "/done номер — удалить нужду (она больше не будет показываться)\n"
        "/settime час минута — изменить время напоминания (Московское время)\n"
        "Пересылайте сообщения, чтобы добавить нужду (в конце будет ссылка на автора)."
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
    for pid, req_text, sender_link in prayers:
        safe_text = html.escape(req_text)
        line = f"<code>{pid}</code>. {safe_text}"
        if sender_link:
            line += f"\n— {sender_link}"
        lines.append(line)
        lines.append("")
    full_message = "\n".join(lines)
    try:
        await message.answer(full_message)
    except TelegramAPIError as e:
        logger.error(f"Ошибка отправки списка: {e}")
        plain_lines = ["Ваши текущие молитвенные нужды:", ""]
        for pid, req_text, _ in prayers:
            plain_lines.append(f"{pid}. {req_text}")
        await message.answer("\n".join(plain_lines))

@router.message(Command("done"))
async def done_cmd(message: Message):
    user_id = message.from_user.id
    args = message.text.split()
    if len(args) < 2:
        await message.answer("Укажите номер нужды: <code>/done 3</code>")
        return
    try:
        prayer_id = int(args[1])
    except ValueError:
        await message.answer("Номер должен быть числом.")
        return

    try:
        deleted = await delete_prayer(user_id, prayer_id)
    except Exception as e:
        logger.error(f"Ошибка удаления нужды: {e}")
        await message.answer("⚠️ Не удалось выполнить операцию.")
        return

    if deleted:
        await message.answer(f"✅ Нужда #{prayer_id} удалена (молитва исполнена). Слава Богу!")
    else:
        await message.answer(
            f"❌ Не удалось удалить #{prayer_id}. Возможно, такой нужды нет или она уже была удалена."
        )

@router.message(Command("settime"))
async def settime_cmd(message: Message):
    user_id = message.from_user.id
    args = message.text.split()
    if len(args) != 3:
        await message.answer(
            "Используйте: <code>/settime час минута</code>\nПример: <code>/settime 7 30</code>"
        )
        return
    try:
        hour = int(args[1])
        minute = int(args[2])
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
    except ValueError:
        await message.answer("Час от 0 до 23, минута от 0 до 59.")
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
        return

    await message.answer(f"⏰ Время ежедневного напоминания установлено на {hour:02d}:{minute:02d} (Москва).")

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
        source_name = html.escape(message.forward_from.full_name or str(message.forward_from.id))
        sender_link = f'<a href="tg://user?id={message.forward_from.id}">{source_name}</a>'

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

    preview = text[:150] + "..." if len(text) > 150 else text
    safe_preview = html.escape(preview)
    try:
        await message.answer(
            f"🙏 Сохранил молитвенную нужду #{prayer_id}.\n\n"
            f"<b>Текст:</b> {safe_preview}\n\n"
            f"Я напомню о ней в ваше время молитвы."
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
        prayer_id = await add_prayer(user.id, text, sender_link=None, from_user=from_user)
    except Exception as e:
        logger.error(f"Ошибка добавления нужды: {e}")
        await message.answer("⚠️ Не удалось сохранить нужду.")
        return

    preview = text[:150] + "..." if len(text) > 150 else text
    safe_preview = html.escape(preview)
    try:
        await message.answer(
            f"🙏 Сохранил молитвенную нужду #{prayer_id}.\n\n"
            f"<b>Текст:</b> {safe_preview}\n\n"
            f"Я напомню о ней в ваше время молитвы."
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

        lines = ["<b>🕊 Ежедневное напоминание о молитве</b>", "", "Помолитесь сегодня за эти нужды:", ""]
        for pid, req_text, sender_link in prayers:
            safe_text = html.escape(req_text)
            line = f"<code>{pid}</code>. {safe_text}"
            if sender_link:
                line += f"\n— {sender_link}"
            lines.append(line)
            lines.append("")
        lines.append("После исполнения удалите командой <code>/done номер</code>")

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
    # Исправлено: используем DefaultBotProperties для parse_mode
    bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher()
    dp.include_router(router)

    scheduler = AsyncIOScheduler(timezone=TIMEZONE)

    @dp.startup()
    async def on_startup():
        commands = [
            BotCommand(command="start", description="Начать работу с ботом"),
            BotCommand(command="list", description="Показать список молитвенных нужд"),
            BotCommand(command="done", description="Удалить нужду (исполнена)"),
            BotCommand(command="settime", description="Установить время напоминания"),
            BotCommand(command="help", description="Помощь"),
        ]
        await bot.set_my_commands(commands)
        logger.info("Меню команд установлено")

        scheduler.add_job(check_and_send, "interval", minutes=1, args=[bot], id="minute_check")
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
