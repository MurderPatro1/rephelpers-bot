import os
import re
import logging
import psycopg2
import uuid
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
        conn.commit()

def migrate_old_objects():
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT id, key FROM objects
            WHERE key LIKE '%:%'
        """)
        rows = cur.fetchall()

        for obj_id, key in rows:
            try:
                ltype, lval = key.split(":", 1)
            except ValueError:
                continue

            cur.execute("""
                INSERT INTO object_links (object_id, type, value)
                VALUES (%s,%s,%s)
                ON CONFLICT DO NOTHING
            """, (obj_id, ltype, lval))

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

    m = re.match(
        r"^(https?://)?(www\.|m\.)?(vk\.com|vk\.ru)/([\w\d_.]+)$",
        text,
        re.IGNORECASE
    )

    if not m:
        return None

    return m.group(4).lower()


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
            InlineKeyboardButton("🏷 Теги", callback_data=f"tags|{obj_id}")
        ],
        [
            InlineKeyboardButton("💬 Добавить комментарий", callback_data=f"comment|{obj_id}"),
            InlineKeyboardButton("📖 Смотреть комментарии", callback_data=f"view|{obj_id}")
        ],
        [
            InlineKeyboardButton("➕ Связать объект", callback_data=f"link|{obj_id}")
        ]
    ])


def tags_keyboard(obj_id):
    rows, row = [], []
    for t, e in TAG_EMOJIS.items():
        row.append(InlineKeyboardButton(f"{e} {t}", callback_data=f"tag|{obj_id}|{t}"))
        if len(row) == 2:
            rows.append(row); row = []
    if row: rows.append(row)
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=f"back|{obj_id}")])
    return InlineKeyboardMarkup(rows)

# ================= START =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO users VALUES (%s) ON CONFLICT DO NOTHING",
                    (update.effective_user.id,))
        conn.commit()

    await update.message.reply_text(
        "👋 Добро пожаловать\n\n"
        "Отправь:\n"
        "• номер телефона\n"
        "• @username или t.me\n"
        "• ссылку VK\n\n"
        "Можно голосовать, добавлять теги, комментарии и связывать объекты."
    )

# ================= TEXT =================

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    # ===== COMMENT MODE =====
    if context.user_data.get("comment_mode"):
        obj_id = context.user_data["obj_id"]
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO comments (object_id, text) VALUES (%s,%s)",
                (obj_id, text)
            )
            conn.commit()

        context.user_data.clear()
        await update.message.reply_text("✅ Комментарий добавлен")
        return

    # ===== LINK MODE =====
    if context.user_data.get("link_mode"):
        obj_id = context.user_data["obj_id"]
        context.user_data.clear()
        await link_object(obj_id, text, update)
        return

    # ===== NORMALIZE INPUT =====
    phone = normalize_phone(text)
    vk = normalize_vk(text)
    tg = normalize_tg(text)

    if phone:
        ltype, lval, title = "phone", phone, phone
    elif vk:
        ltype, lval, title = "vk", vk, f"https://vk.ru/{vk}"
    elif tg:
        ltype, lval, title = "tg", tg, f"@{tg}"
    else:
        await update.message.reply_text("❌ Неподдерживаемый формат")
        return

    # ===== DB LOGIC =====
    with get_conn() as conn, conn.cursor() as cur:

        cur.execute("""
            SELECT object_id
            FROM object_links
            WHERE type = %s AND value = %s
            LIMIT 1
        """, (ltype, lval))

        row = cur.fetchone()

        if row:
            obj_id = row[0]
        else:
            cur.execute(
                "INSERT INTO objects (key, title) VALUES (%s,%s) RETURNING id",
                (str(uuid.uuid4()), title)
            )
            obj_id = cur.fetchone()[0]

            cur.execute("""
                INSERT INTO object_links (object_id, type, value)
                VALUES (%s,%s,%s)
            """, (obj_id, ltype, lval))

        conn.commit()

        cur.execute("SELECT title, score FROM objects WHERE id=%s", (obj_id,))
        title, score = cur.fetchone()

        cur.execute(
            "SELECT type, value FROM object_links WHERE object_id=%s",
            (obj_id,)
        )
        links = cur.fetchall()

        cur.execute(
            "SELECT tag, count FROM tags WHERE object_id=%s",
            (obj_id,)
        )
        tags = cur.fetchall()


    # ===== RENDER =====
    links_text = "\n".join(
        f"• {t}: {v}" for t, v in links
    ) or "—"

    tags_text = "\n".join(
        f"{TAG_EMOJIS.get(t,'🏷')} {t} — {c}" for t, c in tags
    ) or "—"

    await update.message.reply_text(
        f"⭐ Объект:\n{title}\n\n"
        f"Рейтинг: {format_rating(score)}\n\n"
        f"🔗 Связанные данные:\n{links_text}\n\n"
        f"🏷 Теги:\n{tags_text}",
        reply_markup=main_keyboard(obj_id)
    )


# ================= LINK =================

async def open_tags(update, context):
    q = update.callback_query
    _, obj_id = q.data.split("|")

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT title, score FROM objects WHERE id=%s", (obj_id,))
        title, score = cur.fetchone()

        cur.execute(
            "SELECT tag, count FROM tags WHERE object_id=%s",
            (obj_id,)
        )
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


async def link_object(obj_id, text, update):
    phone = normalize_phone(text)
    vk = normalize_vk(text)
    tg = normalize_tg(text)

    if phone:
        ltype, lval = "phone", phone
    elif vk:
        ltype, lval = "vk", vk
    elif tg:
        ltype, lval = "tg", tg
    else:
        await update.message.reply_text("❌ Неверный формат")
        return

    with get_conn() as conn, conn.cursor() as cur:
        # Проверяем, не привязано ли уже к другому объекту
        cur.execute("""
            SELECT object_id
            FROM object_links
            WHERE type=%s AND value=%s
        """, (ltype, lval))

        row = cur.fetchone()

        if row and row[0] != obj_id:
            # просто считаем, что это один и тот же объект
            old_obj_id = row[0]

            cur.execute("""
                UPDATE object_links
                SET object_id = %s
                WHERE object_id = %s
            """, (obj_id, old_obj_id))

            cur.execute("""
                UPDATE votes SET object_id = %s WHERE object_id = %s
            """, (obj_id, old_obj_id))

            cur.execute("""
                UPDATE tags SET object_id = %s WHERE object_id = %s
            """, (obj_id, old_obj_id))

            cur.execute("""
                UPDATE comments SET object_id = %s WHERE object_id = %s
            """, (obj_id, old_obj_id))

            cur.execute("DELETE FROM objects WHERE id = %s", (old_obj_id,))


        cur.execute("""
            INSERT INTO object_links (object_id, type, value)
            VALUES (%s,%s,%s)
            ON CONFLICT DO NOTHING
        """, (obj_id, ltype, lval))

        conn.commit()

    await update.message.reply_text("✅ Объект связан")


async def add_tag(update, context):
    q = update.callback_query
    _, obj_id, tag = q.data.split("|")
    user_id = q.from_user.id

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO tag_voters (user_id, object_id) VALUES (%s,%s) "
            "ON CONFLICT DO NOTHING",
            (user_id, obj_id)
        )
        if cur.rowcount == 0:
            await q.answer("❌ Вы уже добавляли тег", show_alert=True)
            return

        cur.execute(
            "INSERT INTO tags (object_id, tag, count) VALUES (%s,%s,1) "
            "ON CONFLICT (object_id, tag) DO UPDATE SET count = tags.count + 1",
            (obj_id, tag)
        )

        cur.execute("SELECT title, score FROM objects WHERE id=%s", (obj_id,))
        title, score = cur.fetchone()
        cur.execute("SELECT tag, count FROM tags WHERE object_id=%s", (obj_id,))
        tags = cur.fetchall()

        conn.commit()

    tag_text = "\n".join(
        f"{TAG_EMOJIS.get(t,'🏷')} {t} — {c}" for t, c in tags
    )

    await q.edit_message_text(
        f"⭐ Объект:\n{title}\n\n"
        f"Рейтинг: {format_rating(score)}\n\n"
        f"🏷 Теги:\n{tag_text}",
        reply_markup=main_keyboard(obj_id)
    )

    await q.answer("✅ Тег добавлен")


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
        cur.execute(
            "SELECT text FROM comments WHERE object_id=%s ORDER BY id DESC LIMIT 10",
            (obj_id,)
        )
        comments = cur.fetchall()

    text = (
        "💬 Комментарии:\n\n" +
        "\n\n".join(f"• {c[0]}" for c in comments)
        if comments else "💬 Комментариев нет"
    )

    await q.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Назад", callback_data=f"back|{obj_id}")]
        ])
    )


async def back_handler(update, context):
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
        reply_markup=main_keyboard(obj_id)
    )


async def vote_handler(update, context):
    q = update.callback_query
    _, obj_id, val = q.data.split("|")
    obj_id = int(obj_id)
    val = int(val)

    with get_conn() as conn, conn.cursor() as cur:
        # защита от повторного голосования
        cur.execute(
            "INSERT INTO votes (user_id, object_id, value) VALUES (%s,%s,%s) "
            "ON CONFLICT DO NOTHING",
            (q.from_user.id, obj_id, val)
        )
        if cur.rowcount == 0:
            await q.answer("❌ Вы уже голосовали", show_alert=True)
            return

        # обновляем рейтинг
        cur.execute(
            "UPDATE objects SET score = score + %s WHERE id = %s",
            (val, obj_id)
        )

        # читаем обновлённые данные
        cur.execute(
            "SELECT title, score FROM objects WHERE id = %s",
            (obj_id,)
        )
        title, score = cur.fetchone()

        cur.execute(
            "SELECT type, value FROM object_links WHERE object_id = %s",
            (obj_id,)
        )
        links = cur.fetchall()

        cur.execute(
            "SELECT tag, count FROM tags WHERE object_id = %s",
            (obj_id,)
        )
        tags = cur.fetchall()

        conn.commit()

    links_text = "\n".join(
        f"• {t}: {v}" for t, v in links
    ) or "—"

    tags_text = "\n".join(
        f"{TAG_EMOJIS.get(t,'🏷')} {t} — {c}" for t, c in tags
    ) or "—"

    await q.edit_message_text(
        f"⭐ Объект:\n{title}\n\n"
        f"Рейтинг: {format_rating(score)}\n\n"
        f"🔗 Связанные данные:\n{links_text}\n\n"
        f"🏷 Теги:\n{tags_text}",
        reply_markup=main_keyboard(obj_id)
    )

    await q.answer("✅")



async def link_button(update, context):
    q = update.callback_query
    _, obj_id = q.data.split("|")
    context.user_data["link_mode"] = True
    context.user_data["obj_id"] = obj_id
    await q.edit_message_text("➕ Отправьте данные для связи")

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM users")
        users = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM objects")
        objects = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM object_links")
        links = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM votes")
        votes = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM comments")
        comments = cur.fetchone()[0]

        cur.execute("SELECT COALESCE(SUM(count),0) FROM tags")
        tags = cur.fetchone()[0]

    await update.message.reply_text(
        f"📊 Статистика\n\n"
        f"👤 Пользователей: {users}\n"
        f"⭐ Объектов: {objects}\n"
        f"🔗 Связей: {links}\n"
        f"👍 Голосов: {votes}\n"
        f"🏷 Тегов: {tags}\n"
        f"💬 Комментариев: {comments}"
    )


# ================= MAIN =================

def main():
    init_db()
    migrate_old_objects()
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    app.add_handler(CallbackQueryHandler(vote_handler, pattern="^vote"))
    app.add_handler(CallbackQueryHandler(open_tags, pattern="^tags"))
    app.add_handler(CallbackQueryHandler(add_tag, pattern="^tag"))
    app.add_handler(CallbackQueryHandler(comment_button, pattern="^comment"))
    app.add_handler(CallbackQueryHandler(view_comments, pattern="^view"))
    app.add_handler(CallbackQueryHandler(back_handler, pattern="^back"))
    app.add_handler(CallbackQueryHandler(link_button, pattern="^link"))
    app.add_handler(CommandHandler("stats", stats_cmd))



    app.run_polling()

if __name__ == "__main__":
    main()

















