import os
import re
import logging
import psycopg2
from urllib.parse import urlparse
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ================= НАСТРОЙКИ =================

TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
ADMIN_ID = 6262540190

logging.basicConfig(level=logging.INFO)

TAG_EMOJIS = {
    "Бизнес": "💼",
    "Криминал": "🔫",
    "Полиция": "👮‍♂️",
    "Легкодоступная": "👱‍♀️",
    "Мошенник": "⚠️",
    "Балабол": "🤥",
}

# ================= БАЗА =================

def get_conn():
    r = urlparse(DATABASE_URL)
    return psycopg2.connect(
        dbname=r.path[1:],
        user=r.username,
        password=r.password,
        host=r.hostname,
        port=r.port,
    )

def init_db():
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id BIGINT PRIMARY KEY
        );
        CREATE TABLE IF NOT EXISTS objects (
            id SERIAL PRIMARY KEY,
            title TEXT,
            score INT DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS object_links (
            id SERIAL PRIMARY KEY,
            object_id INT REFERENCES objects(id) ON DELETE CASCADE,
            type TEXT,
            value TEXT,
            UNIQUE(type, value)
        );
        CREATE TABLE IF NOT EXISTS votes (
            user_id BIGINT,
            object_id INT,
            value INT,
            UNIQUE(user_id, object_id)
        );
        CREATE TABLE IF NOT EXISTS tags (
            object_id INT,
            tag TEXT,
            count INT DEFAULT 1,
            UNIQUE(object_id, tag)
        );
        CREATE TABLE IF NOT EXISTS tag_voters (
            user_id BIGINT,
            object_id INT,
            UNIQUE(user_id, object_id)
        );
        CREATE TABLE IF NOT EXISTS comments (
            id SERIAL PRIMARY KEY,
            object_id INT,
            text TEXT
        );
        """)
        conn.commit()

# ================= УТИЛИТЫ =================

def normalize_phone(text):
    d = re.sub(r"\D", "", text)
    if len(d) == 11 and d.startswith("8"):
        d = "7" + d[1:]
    if len(d) == 11 and d.startswith("7"):
        return f"+{d}"
    if len(d) == 10:
        return f"+7{d}"
    return None

def normalize_vk(text):
    m = re.match(r"(https?://)?(www\.)?vk\.com/([\w\d_.]+)", text)
    return m.group(3).lower() if m else None

def format_rating(score):
    return f"👍 {score}" if score > 0 else f"👎 {score}" if score < 0 else "➖ 0"

# ================= КЛАВИАТУРЫ =================

def main_keyboard(obj_id):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("👍", callback_data=f"vote|{obj_id}|1"),
            InlineKeyboardButton("👎", callback_data=f"vote|{obj_id}|-1"),
        ],
        [
            InlineKeyboardButton("🏷 Теги", callback_data=f"tags|{obj_id}"),
            InlineKeyboardButton("➕ Связать", callback_data=f"link|{obj_id}"),
        ],
        [
            InlineKeyboardButton("💬 Комментарий", callback_data=f"comment|{obj_id}"),
            InlineKeyboardButton("📖 Комменты", callback_data=f"view|{obj_id}"),
        ]
    ])

# ================= START =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO users (id) VALUES (%s) ON CONFLICT DO NOTHING",
                    (update.effective_user.id,))
        conn.commit()

    await update.message.reply_text(
        "Отправь телефон, @username или ссылку VK / t.me"
    )

# ================= HANDLE TEXT =================

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        text = update.message.text.strip()

        if context.user_data.get("link_mode"):
            obj_id = context.user_data.pop("link_mode")
            await create_or_link_object(update, obj_id, text, link_only=True)
            return

        await create_or_link_object(update, None, text)

    except Exception:
        logging.exception("HANDLE_TEXT ERROR")
        await update.message.reply_text("❌ Внутренняя ошибка")

async def create_or_link_object(update, base_obj_id, text, link_only=False):
    phone = normalize_phone(text)
    vk = normalize_vk(text)
    tg = text.lower() if text.startswith("@") or "t.me" in text else None

    if phone:
        link_type, value, title = "phone", phone, phone
    elif vk:
        link_type, value, title = "vk", vk, f"https://vk.com/{vk}"
    elif tg:
        link_type, value, title = "tg", tg, tg
    else:
        await update.message.reply_text("❌ Неверный формат")
        return

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT object_id FROM object_links WHERE type=%s AND value=%s",
                    (link_type, value))
        row = cur.fetchone()

        if row:
            obj_id = row[0]
        else:
            if base_obj_id:
                obj_id = base_obj_id
            else:
                cur.execute("INSERT INTO objects (title) VALUES (%s) RETURNING id", (title,))
                obj_id = cur.fetchone()[0]

            cur.execute(
                "INSERT INTO object_links (object_id, type, value) VALUES (%s,%s,%s)",
                (obj_id, link_type, value)
            )

        cur.execute("SELECT title, score FROM objects WHERE id=%s", (obj_id,))
        title, score = cur.fetchone()

        cur.execute("SELECT type, value FROM object_links WHERE object_id=%s", (obj_id,))
        links = cur.fetchall()

        conn.commit()

    links_text = "\n".join(f"• {t}: {v}" for t, v in links)

    await update.message.reply_text(
        f"⭐ {title}\n"
        f"Рейтинг: {format_rating(score)}\n\n"
        f"🔗 Связи:\n{links_text}",
        reply_markup=main_keyboard(obj_id)
    )

# ================= CALLBACKS =================

async def link_button(update, context):
    q = update.callback_query
    _, obj_id = q.data.split("|")
    context.user_data["link_mode"] = int(obj_id)
    await q.edit_message_text("Отправь данные для привязки")

# ================= MAIN =================

def main():
    init_db()
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(CallbackQueryHandler(link_button, pattern="^link"))

    app.run_polling()

if __name__ == "__main__":
    main()
