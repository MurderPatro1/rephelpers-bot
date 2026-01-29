import os
import re
import logging
import uuid
import asyncpg
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
ADMIN_ID = int(os.getenv("ADMIN_ID", 6262540190))

TAG_EMOJIS = {
    "Бизнес": "💼",
    "Криминал": "🔫",
    "Полиция": "👮‍♂️",
    "Легкодоступная": "👱‍♀️",
    "Мошенник": "⚠️",
    "Балабол": "🤥",
}

logging.basicConfig(level=logging.INFO)

# ================= БАЗА =================

async def init_db():
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS users (id BIGINT PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS objects (
            id SERIAL PRIMARY KEY,
            key TEXT UNIQUE,
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
        logging.info("Database initialized successfully.")
    finally:
        await conn.close()

async def get_conn():
    return await asyncpg.connect(DATABASE_URL)

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

def normalize_tg(text):
    text = text.lower().strip()
    if text.startswith("https://t.me/"):
        return text.replace("https://t.me/", "")
    if text.startswith("t.me/"):
        return text.replace("t.me/", "")
    if text.startswith("@"):
        return text[1:]
    return None

def normalize_vk(text):
    text = text.strip()
    m = re.match(r"^(https?://)?(www\.|m\.)?(vk\.com|vk\.ru)/([\w\d_.]+)$", text, re.IGNORECASE)
    return m.group(4).lower() if m else None

def format_rating(score):
    if score > 0:
        return f"👍 {score}"
    if score < 0:
        return f"👎 {score}"
    return f"➖ {score}"

def main_keyboard(obj_id):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("👍 +1", callback_data=f"vote|{obj_id}|1"),
            InlineKeyboardButton("👎 -1", callback_data=f"vote|{obj_id}|-1"),
        ],
        [InlineKeyboardButton("🏷 Теги", callback_data=f"tags|{obj_id}")],
        [
            InlineKeyboardButton("💬 Добавить комментарий", callback_data=f"comment|{obj_id}"),
            InlineKeyboardButton("📖 Смотреть комментарии", callback_data=f"view|{obj_id}")
        ],
        [InlineKeyboardButton("➕ Связать объект", callback_data=f"link|{obj_id}")]
    ])

def tags_keyboard(obj_id):
    rows, row = [], []
    for tag, emoji in TAG_EMOJIS.items():
        row.append(InlineKeyboardButton(f"{emoji} {tag}", callback_data=f"tag|{obj_id}|{tag}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=f"back|{obj_id}")])
    return InlineKeyboardMarkup(rows)

# ================= ОБРАБОТЧИКИ =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    async with await get_conn() as conn:
        await conn.execute("INSERT INTO users (id) VALUES ($1) ON CONFLICT DO NOTHING", user_id)
    await update.message.reply_text(
        "👋 Добро пожаловать!\n\n"
        "Отправьте номер телефона, @username, ссылку VK или Telegram."
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    phone = normalize_phone(text)
    tg = normalize_tg(text)
    vk = normalize_vk(text)

    if phone:
        ltype, lval, title = "phone", phone, phone
    elif tg:
        ltype, lval, title = "tg", tg, f"@{tg}"
    elif vk:
        ltype, lval, title = "vk", vk, f"https://vk.com/{vk}"
    else:
        await update.message.reply_text("❌ Неподдерживаемый формат")
        return

    async with await get_conn() as conn:
        obj_id = await conn.fetchval(
            "SELECT object_id FROM object_links WHERE type = $1 AND value = $2",
            ltype, lval
        )
        if not obj_id:
            obj_id = await conn.fetchval(
                "INSERT INTO objects (key, title) VALUES ($1, $2) RETURNING id",
                f"{ltype}:{lval}", title
            )
            await conn.execute(
                "INSERT INTO object_links (object_id, type, value) VALUES ($1, $2, $3)",
                obj_id, ltype, lval
            )

        title, score = await conn.fetchrow("SELECT title, score FROM objects WHERE id = $1", obj_id)
        links = await conn.fetch("SELECT type, value FROM object_links WHERE object_id = $1", obj_id)
        tags = await conn.fetch("SELECT tag, count FROM tags WHERE object_id = $1", obj_id)

    links_text = "\n".join(f"• {t}: {v}" for t, v in links) or "—"
    tags_text = "\n".join(f"{t} — {c}" for t, c in tags) or "—"

    await update.message.reply_text(
        f"⭐ Объект: {title}\n\n"
        f"Рейтинг: {format_rating(score)}\n\n"
        f"🔗 Связанные данные:\n{links_text}\n\n"
        f"🏷 Теги:\n{tags_text}",
        reply_markup=main_keyboard(obj_id)
    )

# ================= MAIN =================

async def main():
    await init_db()
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logging.info("Bot started.")
    await app.run_polling()

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
