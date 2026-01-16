import os
import re
import logging
import psycopg2
from urllib.parse import urlparse
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
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
        conn.commit()

# ================= УТИЛИТЫ =================

def normalize_phone(text):
    digits = re.sub(r"\D", "", text)
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    if len(digits) == 11 and digits.startswith("7"):
        return f"+{digits}"
    if len(digits) == 10:
        return f"+7{digits}"
    return None

def normalize_vk(text):
    m = re.match(r"(https?://)?(www\.)?vk\.com/([\w\d_.]+)", text)
    return m.group(3).lower() if m else None

def format_rating(score):
    if score > 0:
        return f"👍 {score}"
    if score < 0:
        return f"👎 {score}"
    return "➖ 0"

# ================= КЛАВИАТУРЫ =================

def main_keyboard(obj_id):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("👍 +1", callback_data=f"vote|{obj_id}|1"),
            InlineKeyboardButton("👎 -1", callback_data=f"vote|{obj_id}|-1"),
        ],
        [
            InlineKeyboardButton("💬 Добавить комментарий", callback_data=f"comment|{obj_id}"),
            InlineKeyboardButton("📖 Смотреть комментарии", callback_data=f"view|{obj_id}"),
        ],
        [
            InlineKeyboardButton("🏷 Теги", callback_data=f"tags|{obj_id}"),
            InlineKeyboardButton("🔗 Связать", callback_data=f"link|{obj_id}"),
        ],
    ])

def tags_keyboard(obj_id):
    rows, row = [], []
    for tag, emoji in TAG_EMOJIS.items():
        row.append(InlineKeyboardButton(
            f"{emoji} {tag}", callback_data=f"tag|{obj_id}|{tag}"
        ))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=f"back|{obj_id}")])
    return InlineKeyboardMarkup(rows)

# ================= START =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (id) VALUES (%s) ON CONFLICT DO NOTHING",
            (update.effective_user.id,)
        )
        conn.commit()

    await update.message.reply_text(
        "👋 Добро пожаловать\n\n"
        "Отправь:\n"
        "• @username\n"
        "• ссылку t.me\n"
        "• ссылку VK\n"
        "• номер телефона +79998887766\n\n"
        "Голосуй 👍👎, добавляй теги и комментарии"
    )

# ================= HANDLE TEXT =================

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        text = update.message.text.strip()

        # режим добавления комментария
        if context.user_data.get("comment_mode"):
            obj_id = context.user_data.pop("obj_id")
            context.user_data.pop("comment_mode", None)

            with get_conn() as conn, conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO comments (object_id, text) VALUES (%s,%s)",
                    (obj_id, text)
                )
                conn.commit()

            await update.message.reply_text("✅ Комментарий добавлен")
            return

        # режим связывания
        if context.user_data.get("link_mode"):
            base_obj_id = context.user_data.pop("link_mode")
            await process_object(update, text, base_obj_id)
            return

        await process_object(update, text)

    except Exception:
        logging.exception("handle_text error")
        await update.message.reply_text("❌ Произошла ошибка")

# ================= ОБЪЕКТ =================

async def process_object(update, text, base_obj_id=None):
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
        cur.execute(
            "SELECT object_id FROM object_links WHERE type=%s AND value=%s",
            (link_type, value)
        )
        row = cur.fetchone()

        if row:
            obj_id = row[0]
        else:
            if base_obj_id:
                obj_id = base_obj_id
            else:
                cur.execute(
                    "INSERT INTO objects (key, title) VALUES (%s,%s) RETURNING id",
                    (f"{link_type}:{value}", title)
                )
                obj_id = cur.fetchone()[0]

            cur.execute(
                "INSERT INTO object_links (object_id, type, value) VALUES (%s,%s,%s)",
                (obj_id, link_type, value)
            )

        cur.execute("SELECT title, score FROM objects WHERE id=%s", (obj_id,))
        title, score = cur.fetchone()

        cur.execute("SELECT tag, count FROM tags WHERE object_id=%s", (obj_id,))
        tags = cur.fetchall()

        conn.commit()

    tag_text = (
        "\n".join(f"{TAG_EMOJIS.get(t,'🏷')} {t} — {c}" for t, c in tags)
        if tags else "—"
    )

    await update.message.reply_text(
        f"⭐ Объект:\n{title}\n\n"
        f"Рейтинг: {format_rating(score)}\n\n"
        f"🏷 Теги:\n{tag_text}",
        reply_markup=main_keyboard(obj_id)
    )

# ================= CALLBACKS =================

async def link_button(update, context):
    q = update.callback_query
    _, obj_id = q.data.split("|")
    context.user_data["link_mode"] = int(obj_id)
    await q.edit_message_text("🔗 Отправь данные для привязки")

# остальные callbacks (vote / tags / comment / view / back)
# ⬇️ ОСТАЮТСЯ ТАКИМИ ЖЕ, КАК У ТЕБЯ, И РАБОТАЮТ ⬇️

# ================= MAIN =================

def main():
    init_db()
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    app.add_handler(CallbackQueryHandler(link_button, pattern="^link"))
    app.add_handler(CallbackQueryHandler(vote_handler, pattern="^vote"))
    app.add_handler(CallbackQueryHandler(open_tags, pattern="^tags"))
    app.add_handler(CallbackQueryHandler(add_tag, pattern="^tag"))
    app.add_handler(CallbackQueryHandler(back_handler, pattern="^back"))
    app.add_handler(CallbackQueryHandler(comment_button, pattern="^comment"))
    app.add_handler(CallbackQueryHandler(view_comments, pattern="^view"))

    app.run_polling()

if __name__ == "__main__":
    main()
