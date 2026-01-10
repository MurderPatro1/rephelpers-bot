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
ADMIN_ID = 6262540190  # ← твой ID

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
    result = urlparse(DATABASE_URL)
    return psycopg2.connect(
        dbname=result.path[1:],
        user=result.username,
        password=result.password,
        host=result.hostname,
        port=result.port,
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

def is_username(t): return t.startswith("@")
def is_link(t): return t.startswith("http://") or t.startswith("https://")
def is_phone(t): return bool(re.fullmatch(r"\+\d{10,15}", t))

def format_rating(score):
    if score > 0: return f"👍 {score}"
    if score < 0: return f"👎 {score}"
    return f"➖ {score}"

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
            InlineKeyboardButton("🏷 Теги", callback_data=f"tags|{obj_id}")
        ]
    ])

def tags_keyboard(obj_id):
    rows = []
    row = []
    for tag, emoji in TAG_EMOJIS.items():
        row.append(InlineKeyboardButton(f"{emoji} {tag}", callback_data=f"tag|{obj_id}|{tag}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=f"back|{obj_id}")])
    return InlineKeyboardMarkup(rows)

# ================= СТАРТ =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO users (id) VALUES (%s) ON CONFLICT DO NOTHING",
                    (update.effective_user.id,))
        conn.commit()

    await update.message.reply_text(
        "👋 Добро пожаловать\n\n"
        "Отправь:\n"
        "• @username\n"
        "• ссылку t.me\n"
        "• номер телефона +79998887766\n\n"
        "Голосуй 👍👎, добавляй теги и комментарии"
    )

# ================= ТЕКСТ =================

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    if context.user_data.get("comment_mode"):
        obj_id = context.user_data["obj_id"]
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO comments (object_id, text) VALUES (%s,%s)",
                        (obj_id, text))
            conn.commit()
        context.user_data.clear()
        await update.message.reply_text("✅ Комментарий добавлен")
        return

    if not (is_username(text) or is_link(text) or is_phone(text)):
        return

    key = text.lower()

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            INSERT INTO objects (key, title)
            VALUES (%s,%s)
            ON CONFLICT (key) DO UPDATE SET title = EXCLUDED.title
            RETURNING id, title, score
        """, (key, text))
        obj_id, title, score = cur.fetchone()

        cur.execute("SELECT tag, count FROM tags WHERE object_id=%s", (obj_id,))
        tags = cur.fetchall()

    tag_text = "\n".join(f"{TAG_EMOJIS.get(t,'🏷')} {t} — {c}" for t, c in tags) or "—"

    await update.message.reply_text(
        f"⭐ Объект:\n{title}\n\n"
        f"Рейтинг: {format_rating(score)}\n\n"
        f"🏷 Теги:\n{tag_text}",
        reply_markup=main_keyboard(obj_id)
    )

# ================= CALLBACKS =================

async def vote_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    _, obj_id, value = q.data.split("|")
    user_id = q.from_user.id
    value = int(value)

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            INSERT INTO votes (user_id, object_id, value)
            VALUES (%s,%s,%s)
            ON CONFLICT DO NOTHING
        """, (user_id, obj_id, value))
        if cur.rowcount == 0:
            await q.answer("❌ Вы уже голосовали", show_alert=True)
            return
        cur.execute("UPDATE objects SET score = score + %s WHERE id=%s",
                    (value, obj_id))
        conn.commit()

    await q.answer("✅ Голос учтён")
    await back_handler(update, context)

async def open_tags(update, context):
    q = update.callback_query
    _, obj_id = q.data.split("|")
    await q.edit_message_reply_markup(reply_markup=tags_keyboard(obj_id))

async def add_tag(update, context):
    q = update.callback_query
    _, obj_id, tag = q.data.split("|")
    user_id = q.from_user.id

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            INSERT INTO tag_voters (user_id, object_id)
            VALUES (%s,%s) ON CONFLICT DO NOTHING
        """, (user_id, obj_id))
        if cur.rowcount == 0:
            await q.answer("❌ Вы уже добавляли тег", show_alert=True)
            return

        cur.execute("""
            INSERT INTO tags (object_id, tag, count)
            VALUES (%s,%s,1)
            ON CONFLICT (object_id, tag)
            DO UPDATE SET count = tags.count + 1
        """, (obj_id, tag))
        conn.commit()

    await q.answer("✅ Тег добавлен")
    await open_tags(update, context)

async def back_handler(update, context):
    q = update.callback_query
    _, obj_id = q.data.split("|")

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT title, score FROM objects WHERE id=%s", (obj_id,))
        title, score = cur.fetchone()
        cur.execute("SELECT tag, count FROM tags WHERE object_id=%s", (obj_id,))
        tags = cur.fetchall()

    tag_text = "\n".join(f"{TAG_EMOJIS.get(t,'🏷')} {t} — {c}" for t, c in tags) or "—"

    await q.edit_message_text(
        f"⭐ Объект:\n{title}\n\n"
        f"Рейтинг: {format_rating(score)}\n\n"
        f"🏷 Теги:\n{tag_text}",
        reply_markup=main_keyboard(obj_id)
    )

async def comment_button(update, context):
    q = update.callback_query
    _, obj_id = q.data.split("|")
    context.user_data["comment_mode"] = True
    context.user_data["obj_id"] = obj_id
    await q.edit_message_text(
        "💬 Напишите комментарий\n\n⚠️ Анонимно",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Отмена", callback_data=f"back|{obj_id}")]
        ])
    )

async def view_comments(update, context):
    q = update.callback_query
    _, obj_id = q.data.split("|")

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT text FROM comments WHERE object_id=%s ORDER BY id DESC LIMIT 10",
                    (obj_id,))
        comments = cur.fetchall()

    text = "💬 Комментарии:\n\n" + "\n\n".join(f"• {c[0]}" for c in comments) if comments else "💬 Комментариев нет"

    await q.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Назад", callback_data=f"back|{obj_id}")]
        ])
    )

# ================= /STATS =================

async def stats_cmd(update, context):
    if update.effective_user.id != ADMIN_ID:
        return

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM users")
        users = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM objects")
        objects = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM votes")
        votes = cur.fetchone()[0]
        cur.execute("SELECT SUM(count) FROM tags")
        tags = cur.fetchone()[0] or 0
        cur.execute("SELECT COUNT(*) FROM comments")
        comments = cur.fetchone()[0]

    await update.message.reply_text(
        f"📊 Статистика\n\n"
        f"👤 Пользователей: {users}\n"
        f"⭐ Объектов: {objects}\n"
        f"👍 Голосов: {votes}\n"
        f"🏷 Тегов: {tags}\n"
        f"💬 Комментариев: {comments}"
    )

# ================= MAIN =================

def main():
    init_db()
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    app.add_handler(CallbackQueryHandler(vote_handler, pattern="^vote"))
    app.add_handler(CallbackQueryHandler(open_tags, pattern="^tags"))
    app.add_handler(CallbackQueryHandler(add_tag, pattern="^tag"))
    app.add_handler(CallbackQueryHandler(back_handler, pattern="^back"))
    app.add_handler(CallbackQueryHandler(comment_button, pattern="^comment"))
    app.add_handler(CallbackQueryHandler(view_comments, pattern="^view"))

    app.run_polling()

if __name__ == "__main__":
    main()
