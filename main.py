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
        CREATE TABLE IF NOT EXISTS object_links (
            id SERIAL PRIMARY KEY,
            object_id INT REFERENCES objects(id) ON DELETE CASCADE,
            type TEXT,        -- phone | tg | vk
            value TEXT,
            UNIQUE(type, value)
        );

        """)
        conn.commit()

# ================= УТИЛИТЫ =================

def is_username(t): return t.startswith("@")
def is_link(t): return t.startswith("http://") or t.startswith("https://")

def is_vk_link(text: str) -> bool:
    return bool(re.match(r"^(https?://)?(www\.)?vk\.com/[\w\d_.]+$", text))


def normalize_vk(text: str) -> str | None:
    """
    Возвращает username / id123 / public123
    """
    m = re.match(r"^(https?://)?(www\.)?vk\.com/([\w\d_.]+)$", text)
    if not m:
        return None
    return m.group(3).lower()


def format_rating(score):
    if score > 0: return f"👍 {score}"
    if score < 0: return f"👎 {score}"
    return f"➖ {score}"

def normalize_phone(text: str) -> str | None:
    # убираем всё кроме цифр
    digits = re.sub(r"\D", "", text)

    # 11 цифр, начинается с 8 → меняем на 7
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]

    # 11 цифр и начинается с 7
    if len(digits) == 11 and digits.startswith("7"):
        return f"+{digits}"

    # 10 цифр (без кода страны)
    if len(digits) == 10:
        return f"+7{digits}"

    return None


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
        "• ссылку VK\n"
        "• номер телефона +79998887766\n\n"

        "Голосуй 👍👎, добавляй теги и комментарии"
    )

# ================= ТЕКСТ =================

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    # ===== РЕЖИМ КОММЕНТАРИЯ =====
    if context.user_data.get("comment_mode"):
        obj_id = context.user_data.get("obj_id")

        if obj_id:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO comments (object_id, text) VALUES (%s,%s)",
                    (obj_id, text)
                )
                conn.commit()

        context.user_data.clear()
        await update.message.reply_text("✅ Комментарий добавлен")
        return

   # ===== ОПРЕДЕЛЕНИЕ ОБЪЕКТА =====
    normalized_phone = normalize_phone(text)
    vk_username = normalize_vk(text)

    link_type = None
    link_value = None
    title = None

    if is_username(text):
        link_type = "tg"
        link_value = text.lower()
        title = text

    elif vk_username:
        link_type = "vk"
        link_value = vk_username   # ← ТОЛЬКО username
        title = f"https://vk.com/{vk_username}"

    elif normalized_phone:
        link_type = "phone"
        link_value = normalized_phone
        title = normalized_phone

    elif "t.me" in text:
        link_type = "tg"
        link_value = text.lower()
        title = text


    else:
        await update.message.reply_text(
            "❌ Я могу работать только с:\n"
            "• @username\n"
            "• ссылками t.me\n"
            "• ссылками vk.com\n"
            "• номерами телефонов РФ"
        )
        return


    # ===== СОЗДАНИЕ / ПОЛУЧЕНИЕ ОБЪЕКТА =====
    with get_conn() as conn, conn.cursor() as cur:

        # 1. ищем существующую связь
        cur.execute(
            "SELECT object_id FROM object_links WHERE type=%s AND value=%s",
            (link_type, link_value)
        )
        row = cur.fetchone()

        if row:
            obj_id = row[0]
        else:
            # 2. создаём объект
            cur.execute(
                "INSERT INTO objects (key, title) VALUES (%s,%s) RETURNING id",
                (f"{link_type}:{link_value}", title)
            )
            obj_id = cur.fetchone()[0]

            # 3. добавляем связь
            cur.execute(
                "INSERT INTO object_links (object_id, type, value) VALUES (%s,%s,%s)",
                (obj_id, link_type, link_value)
            )

        # 4. получаем данные объекта
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

        cur.execute(
            "UPDATE objects SET score = score + %s WHERE id=%s",
            (value, obj_id)
        )

        cur.execute("SELECT title, score FROM objects WHERE id=%s", (obj_id,))
        title, score = cur.fetchone()

        cur.execute("SELECT tag, count FROM tags WHERE object_id=%s", (obj_id,))
        tags = cur.fetchall()

        conn.commit()

    tag_text = "\n".join(
        f"{TAG_EMOJIS.get(t,'🏷')} {t} — {c}" for t, c in tags
    ) or "—"

    await q.edit_message_text(
        f"⭐ Объект:\n{title}\n\n"
        f"Рейтинг: {format_rating(score)}\n\n"
        f"🏷 Теги:\n{tag_text}",
        reply_markup=main_keyboard(obj_id)
    )

    await q.answer("✅ Голос учтён")



async def open_tags(update, context):
    q = update.callback_query
    _, obj_id = q.data.split("|")

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT title, score FROM objects WHERE id=%s", (obj_id,))
        title, score = cur.fetchone()
        cur.execute("SELECT tag, count FROM tags WHERE object_id=%s", (obj_id,))
        tags = cur.fetchall()

    tag_text = "\n".join(
        f"{TAG_EMOJIS.get(t,'🏷')} {t} — {c}" for t, c in tags
    ) or "—"

    await q.edit_message_text(
        f"⭐ Объект:\n{title}\n\n"
        f"Рейтинг: {format_rating(score)}\n\n"
        f"🏷 Теги:\n{tag_text}",
        reply_markup=tags_keyboard(obj_id)
    )


async def add_tag(update, context):
    q = update.callback_query
    _, obj_id, tag = q.data.split("|")
    user_id = q.from_user.id

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            INSERT INTO tag_voters (user_id, object_id)
            VALUES (%s,%s)
            ON CONFLICT DO NOTHING
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

        cur.execute("SELECT title, score FROM objects WHERE id=%s", (obj_id,))
        title, score = cur.fetchone()

        cur.execute("SELECT tag, count FROM tags WHERE object_id=%s", (obj_id,))
        tags = cur.fetchall()

        conn.commit()

    tag_text = "\n".join(
        f"{TAG_EMOJIS.get(t,'🏷')} {t} — {c}" for t, c in tags
    ) or "—"

    await q.edit_message_text(
        f"⭐ Объект:\n{title}\n\n"
        f"Рейтинг: {format_rating(score)}\n\n"
        f"🏷 Теги:\n{tag_text}",
        reply_markup=main_keyboard(obj_id)
    )

    await q.answer("✅ Тег добавлен")


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



















