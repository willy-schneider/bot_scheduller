import sqlite3

import logging

from datetime import datetime

import os

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from telegram.constants import ParseMode
from telegram import Update

from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext





# ========== НАСТРОЙКИ ==========

TOKEN = os.environ['TOKEN']  # Замените на свой токен

DEFAULT_REMINDER_HOUR = os.environ['DEFAULT_REMINDER_HOUR']

DEFAULT_REMINDER_MINUTE = 0

TIMEZONE = "Europe/Moscow"     # Можно сменить

# ================================



logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)

logger = logging.getLogger(__name__)



# --- База данных ---

def init_db():

    conn = sqlite3.connect("prayers.db")

    c = conn.cursor()

    c.execute(f'''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        full_name TEXT,
        reminder_hour INTEGER DEFAULT {int(DEFAULT_REMINDER_HOUR)},
        reminder_minute INTEGER DEFAULT {DEFAULT_REMINDER_MINUTE},
        timezone TEXT DEFAULT '{TIMEZONE}'
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS prayers (

        id INTEGER PRIMARY KEY AUTOINCREMENT,

        user_id INTEGER,

        text TEXT,

        from_user TEXT,

        created_at TEXT,

        FOREIGN KEY(user_id) REFERENCES users(user_id)

    )''')

    conn.commit()

    conn.close()



def register_user(user_id, username, full_name):

    conn = sqlite3.connect("prayers.db")

    c = conn.cursor()

    c.execute("INSERT OR IGNORE INTO users (user_id, username, full_name) VALUES (?, ?, ?)",

              (user_id, username, full_name))

    conn.commit()

    conn.close()



def add_prayer(user_id, text, from_user):

    conn = sqlite3.connect("prayers.db")

    c = conn.cursor()

    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    c.execute("INSERT INTO prayers (user_id, text, from_user, created_at) VALUES (?, ?, ?, ?)",

              (user_id, text, from_user, created_at))

    prayer_id = c.lastrowid

    conn.commit()

    conn.close()

    return prayer_id



def get_undone_prayers(user_id):

    conn = sqlite3.connect("prayers.db")

    c = conn.cursor()

    c.execute("SELECT id, text, from_user FROM prayers WHERE user_id = ? ORDER BY created_at", (user_id,))

    rows = c.fetchall()

    conn.close()

    return rows



def delete_prayer(user_id, prayer_id):

    conn = sqlite3.connect("prayers.db")

    c = conn.cursor()

    c.execute("DELETE FROM prayers WHERE id = ? AND user_id = ?", (prayer_id, user_id))

    affected = c.rowcount

    conn.commit()

    conn.close()

    return affected > 0



def get_all_users_with_undone():

    conn = sqlite3.connect("prayers.db")

    c = conn.cursor()

    c.execute("SELECT DISTINCT user_id FROM prayers")

    users = [row[0] for row in c.fetchall()]

    conn.close()

    return users



# --- Обработчики команд ---

async def start(update: Update, context: CallbackContext):

    user = update.effective_user

    register_user(user.id, user.username, user.full_name)

    await update.message.reply_text(

        "🙏 *Молитвенный бот для личного использования*\n\n"

        "Просто перешлите мне любое сообщение (или напишите текст) — я сохраню его как молитвенную нужду.\n"

        "Если вы *пересылаете* сообщение, в конце текста автоматически добавится ссылка на ваш профиль.\n"

        "Каждый день в выбранное время я буду присылать список всех нужд для молитвы.\n\n"

        "Команды:\n"

        "/list — показать список текущих нужд\n"

        "/done <номер> — удалить нужду (исполнена)\n"

        "/help — справка\n"

        "/settime <час> <минута> — установить время напоминания (например /settime 7 30)",

        parse_mode=ParseMode.MARKDOWN

    )



async def help_command(update: Update, context: CallbackContext):

    await update.message.reply_text(

        "📖 *Справка*\n"

        "/list — список неисполненных нужд\n"

        "/done <номер> — удалить нужду (она больше не будет показываться)\n"

        "/settime <час> <минута> — изменить время напоминания (Московское время)\n"

        "Пересылайте сообщения, чтобы добавить нужду (в конце будет ссылка на вас).",

        parse_mode=ParseMode.MARKDOWN

    )



async def list_requests(update: Update, context: CallbackContext):

    user_id = update.effective_user.id

    prayers = get_undone_prayers(user_id)

    if not prayers:

        await update.message.reply_text("✅ У вас нет активных молитвенных нужд.")

        return

    text = "*Ваши текущие молитвенные нужды:*\n\n"

    for pid, req, sender in prayers:

        # Выводим полный текст (в нём уже может быть ссылка на отправителя, если переслано)

        text += f"`{pid}`. {req}\n\n"

    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)



async def done_command(update: Update, context: CallbackContext):

    user_id = update.effective_user.id

    args = context.args

    if not args:

        await update.message.reply_text("Укажите номер нужды: `/done 3`", parse_mode=ParseMode.MARKDOWN)

        return

    try:

        prayer_id = int(args[0])

    except ValueError:

        await update.message.reply_text("Номер должен быть числом.")

        return

    if delete_prayer(user_id, prayer_id):

        await update.message.reply_text(f"✅ Нужда #{prayer_id} удалена (молитва исполнена). Слава Богу!")

    else:

        await update.message.reply_text(f"❌ Не удалось удалить #{prayer_id}. Возможно, такой нужды нет или она уже была удалена.")



async def set_time_command(update: Update, context: CallbackContext):

    user_id = update.effective_user.id

    args = context.args

    if len(args) != 2:

        await update.message.reply_text("Используйте: `/settime <час> <минута>`\nПример: `/settime 7 30`", parse_mode=ParseMode.MARKDOWN)

        return

    try:

        hour = int(args[0])

        minute = int(args[1])

        if not (0 <= hour <= 23 and 0 <= minute <= 59):

            raise ValueError

    except ValueError:

        await update.message.reply_text("Час от 0 до 23, минута от 0 до 59.")

        return

    conn = sqlite3.connect("prayers.db")

    c = conn.cursor()

    c.execute("UPDATE users SET reminder_hour = ?, reminder_minute = ? WHERE user_id = ?", (hour, minute, user_id))

    conn.commit()

    conn.close()

    await update.message.reply_text(f"⏰ Время ежедневного напоминания установлено на {hour:02d}:{minute:02d} (Москва).")



async def handle_message(update: Update, context: CallbackContext):

    user = update.effective_user

    if not user:

        return

    register_user(user.id, user.username, user.full_name)



    # Проверяем, переслано ли сообщение

    is_forwarded = update.message.forward_from or update.message.forward_sender_name



    if is_forwarded:

        # Берём текст из пересланного сообщения

        text = update.message.caption or update.message.text

        if not text:

            text = "[Сообщение без текста (фото/аудио/стикер)]"

        # Определяем имя исходного отправителя

        if update.message.forward_from:

            source = update.message.forward_from.full_name or str(update.message.forward_from.id)

        else:

            source = update.message.forward_sender_name

        from_user = f"переслано от {source} (добавил {user.full_name or user.username})"



        # Добавляем в конец текста активную ссылку на текущего пользователя (того, кто переслал)

        user_link = f"[{user.full_name or user.username or 'пользователь'}](tg://user?id={user.id})"

        text += f"\n\n— от {user_link}"

    else:

        # Обычное текстовое сообщение (не пересланное)

        text = update.message.text

        if not text:

            await update.message.reply_text("Пожалуйста, отправьте текст или перешлите сообщение.")

            return

        from_user = user.full_name or user.username or str(user.id)

        # Для обычных сообщений ссылку НЕ добавляем (по условию)



    prayer_id = add_prayer(user.id, text, from_user)

    preview = text[:150] + "..." if len(text) > 150 else text

    await update.message.reply_text(

        f"🙏 Сохранил молитвенную нужду #{prayer_id}.\n\n"

        f"*Текст:* {preview}\n\n"

        f"Я напомню о ней в ваше время молитвы.",

        parse_mode=ParseMode.MARKDOWN

    )



# --- Ежедневная рассылка (каждую минуту проверяем, кому пора) ---

async def main():
    init_db()

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("list", list_requests))
    app.add_handler(CommandHandler("done", done_command))
    app.add_handler(CommandHandler("settime", set_time_command))
    app.add_handler(MessageHandler(filters.TEXT | filters.FORWARDED, handle_message))

    # Настройка и запуск планировщика внутри активного event loop
    scheduler = AsyncIOScheduler(timezone=TIMEZONE)

    async def check_and_send():
        now = datetime.now()
        current_hour = now.hour
        current_minute = now.minute
        conn = sqlite3.connect("prayers.db")
        c = conn.cursor()
        c.execute(
            "SELECT user_id FROM users WHERE reminder_hour = ? AND reminder_minute = ?",
            (current_hour, current_minute)
        )
        user_ids = [row[0] for row in c.fetchall()]
        conn.close()

        for user_id in user_ids:
            prayers = get_undone_prayers(user_id)
            if prayers:
                text = "*🕊 Ежедневное напоминание о молитве*\n\nПомолитесь сегодня за эти нужды:\n\n"
                for pid, req, sender in prayers:
                    text += f"`{pid}`. {req}\n\n"
                text += "После исполнения удалите командой `/done N`"
                try:
                    await app.bot.send_message(
                        chat_id=user_id,
                        text=text,
                        parse_mode=ParseMode.MARKDOWN
                    )
                except Exception as e:
                    logger.error(f"Ошибка отправки {user_id}: {e}")

    scheduler.add_job(check_and_send, 'interval', minutes=1, id='minute_check')
    scheduler.start()

    logger.info("Бот запущен...")
    await app.run_polling()


if __name__ == "__main__":
    asyncio.run(main())
