import os
import asyncio
import logging
from datetime import datetime

import aiosqlite
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import Message, BotCommand
from aiogram.filters import Command

# ========== НАСТРОЙКИ ==========
TOKEN = os.environ["TOKEN"]
DEFAULT_REMINDER_HOUR = int(os.environ.get("DEFAULT_REMINDER_HOUR", 9))
DEFAULT_REMINDER_MINUTE = 0
TIMEZONE = "Europe/Moscow"

# Общее хранилище (persistent storage)
SHARED_DIR = os.environ.get("SHARED_DIR", "/app/shared")
os.makedirs(SHARED_DIR, exist_ok=True)   # создаём папку, если её ещё нет
DB_NAME = os.path.join(SHARED_DIR, "prayers.db")
# ================================

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# --- База данных (aiosqlite) ---
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

# ========== Хендлеры ==========
router = Router()

@router.message(Command("start"))
async def start_cmd(message: Message):
    user = message.from_user
    await register_user(user.id, user.username, user.full_name)
    await message.answer(
        "🙏 <b>Молитвенный бот для личного использования</b>\n\n"
        "Просто перешлите мне любое сообщение (или напишите текст) — я сохраню его как молитвенную нужду.\n"
        "Если вы <b>пересылаете</b> сообщение, в конце текста автоматически добавится ссылка на профиль автора.\n"
        "Каждый день в выбранное время я буду присылать список всех нужд для молитвы.\n\n"
        "Команды:\n"
        "/list — показать список текущих нужд\n"
        "/done <номер> — удалить нужду (исполнена)\n"
        "/help — справка\n"
        "/settime <час> <минута> — установить время напоминания (например /settime 7 30)",
        parse_mode="HTML",
    )

@router.message(Command("help"))
async def help_cmd(message: Message):
    await message.answer(
        "📖 <b>Справка</b>\n"
        "/list — список неисполненных нужд\n"
        "/done <номер> — удалить нужду (она больше не будет показываться)\n"
        "/settime <час> <минута> — изменить время напоминания (Московское время)\n"
        "Пересылайте сообщения, чтобы добавить нужду (в конце будет ссылка на автора).",
        parse_mode="HTML",
    )

@router.message(Command("list"))
async def list_cmd(message: Message):
    user_id = message.from_user.id
    prayers = await get_undone_prayers(user_id)
    if not prayers:
        await message.answer("✅ У вас нет активных молитвенных нужд.")
        return

    lines = ["<b>Ваши текущие молитвенные нужды:</b>", ""]
    for pid, req_text, sender_link in prayers:
        safe_text = req_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        line = f"<code>{pid}</code>. {safe_text}"
        if sender_link:
            line += f"\n— {sender_link}"
        lines.append(line)
        lines.append("")

    await message.answer("\n".join(lines), parse_mode="HTML")

@router.message(Command("done"))
async def done_cmd(message: Message):
    user_id = message.from_user.id
    args = message.text.split()
    if len(args) < 2:
        await message.answer("Укажите номер нужды: <code>/done 3</code>", parse_mode="HTML")
        return
    try:
        prayer_id = int(args[1])
    except ValueError:
        await message.answer("Номер должен быть числом.")
        return

    if await delete_prayer(user_id, prayer_id):
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
            "Используйте: <code>/settime &lt;час&gt; &lt;минута&gt;</code>\nПример: <code>/settime 7 30</code>",
            parse_mode="HTML",
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

    async with aiosqlite.connect(DB_NAME) as conn:
        await conn.execute(
            "UPDATE users SET reminder_hour = ?, reminder_minute = ? WHERE user_id = ?",
            (hour, minute, user_id),
        )
        await conn.commit()

    await message.answer(
        f"⏰ Время ежедневного напоминания установлено на {hour:02d}:{minute:02d} (Москва).",
    )

# --- Обработка пересланных сообщений ---
@router.message(F.forward_date)
async def handle_forwarded(message: Message):
    user = message.from_user
    await register_user(user.id, user.username, user.full_name)

    text = message.caption or message.text
    if not text:
        text = "[Сообщение без текста (фото/аудио/стикер)]"

    # Определяем, от кого переслано
    if message.forward_from:
        source = message.forward_from.full_name or str(message.forward_from.id)
        # Ссылка на исходного отправителя (тот, от кого переслано)
        sender_link = f'<a href="tg://user?id={message.forward_from.id}">{source}</a>'
    else:
        source = message.forward_sender_name or "неизвестный источник"
        sender_link = None  # анонимная пересылка — ссылку не даём

    from_user = f"переслано от {source} (добавил {user.full_name or user.username})"

    prayer_id = await add_prayer(user.id, text, sender_link, from_user)
    preview = text[:150] + "..." if len(text) > 150 else text
    safe_preview = preview.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    await message.answer(
        f"🙏 Сохранил молитвенную нужду #{prayer_id}.\n\n"
        f"<b>Текст:</b> {safe_preview}\n\n"
        f"Я напомню о ней в ваше время молитвы.",
        parse_mode="HTML",
    )

# --- Обработка обычных текстовых сообщений ---
@router.message(F.text, ~F.forward_date)
async def handle_plain_text(message: Message):
    user = message.from_user
    await register_user(user.id, user.username, user.full_name)

    text = message.text
    if not text:
        await message.answer("Пожалуйста, отправьте текст или перешлите сообщение.")
        return

    from_user = user.full_name or user.username or str(user.id)
    prayer_id = await add_prayer(user.id, text, sender_link=None, from_user=from_user)
    preview = text[:150] + "..." if len(text) > 150 else text
    safe_preview = preview.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    await message.answer(
        f"🙏 Сохранил молитвенную нужду #{prayer_id}.\n\n"
        f"<b>Текст:</b> {safe_preview}\n\n"
        f"Я напомню о ней в ваше время молитвы.",
        parse_mode="HTML",
    )

# ========== Планировщик ==========
async def check_and_send(bot: Bot):
    now = datetime.now()
    current_hour, current_minute = now.hour, now.minute

    async with aiosqlite.connect(DB_NAME) as conn:
        cursor = await conn.execute(
            "SELECT user_id FROM users WHERE reminder_hour = ? AND reminder_minute = ?",
            (current_hour, current_minute),
        )
        user_ids = [row[0] for row in await cursor.fetchall()]

    for user_id in user_ids:
        prayers = await get_undone_prayers(user_id)
        if not prayers:
            continue

        lines = ["<b>🕊 Ежедневное напоминание о молитве</b>", "", "Помолитесь сегодня за эти нужды:", ""]
        for pid, req_text, sender_link in prayers:
            safe_text = req_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            line = f"<code>{pid}</code>. {safe_text}"
            if sender_link:
                line += f"\n— {sender_link}"
            lines.append(line)
            lines.append("")
        lines.append("После исполнения удалите командой <code>/done N</code>")

        try:
            await bot.send_message(chat_id=user_id, text="\n".join(lines), parse_mode="HTML")
        except Exception as e:
            logger.error(f"Ошибка отправки {user_id}: {e}")

# ========== Запуск ==========
async def main():
    await init_db()

    bot = Bot(token=TOKEN)
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
