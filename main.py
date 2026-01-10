import json
import os
import re
import hashlib
import logging
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
FILE_NAME = "ratings.json"

TAG_EMOJIS = {
    "Бизнес": "💼",
    "Криминал": "🔫",
    "Полиция": "👮‍♂️",
    "Легкодоступная": "👱‍♀️",
    "Мошенник": "⚠️",
}

logging.basicConfig(level=logging.INFO)

# ================= ВСПОМОГАТЕЛЬНЫЕ =================

def is_username(text: str) -> bool:
    return text.startswith("@") and len(text) > 1

def is_telegram_link(text: str) -> bool:
    return text.startswith("https://t.me/") or text.startswith("http://t.me/")

def is_phone_number(text: str) -> bool:
    return bool(re.fullmatch(r"\+\d{10,15}", text))

def make_key(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()

# ================= ХРАНЕНИЕ =================

def load_ratings():
    if not os.path.exists(FILE_NAME):
        return {}
    with open(FILE_NAME, "r", encoding="utf-8") as f:
        return json.load(f)

def save_ratings(data):
    with open(FILE_NAME, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

ratings = load_ratings()

def ensure_object(key: str, title: str):
    ratings.setdefault(key, {
        "title": title,
        "score": 0,
        "votes": {},
        "tags": {},
        "tag_voters": [],
        "comments": []
    })
    if "title" not in ratings[key]:
        ratings[key]["title"] = title

# ================= ФОРМАТ =================

def format_tags(tags: dict) -> str:
    if not tags:
        return "—"
    lines = []
    for tag, count in sorted(tags.items(), key=lambda x: -x[1]):
        emoji = TAG_EMOJIS.get(tag, "🏷")
        lines.append(f"{emoji} {tag} — {count}")
    return "\n".join(lines)

def format_rating(score: int) -> str:
    if score > 0:
        return f"👍 {score}"
    if score < 0:
        return f"👎 {score}"
    return f"➖ {score}"

# ================= КЛАВИАТУРЫ =================

def main_keyboard(key: str):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("👍 +1", callback_data=f"vote|{key}|1"),
            InlineKeyboardButton("👎 -1", callback_data=f"vote|{key}|-1"),
        ],
        [
            InlineKeyboardButton("💬 Добавить комментарий", callback_data=f"comment|{key}"),
            InlineKeyboardButton("📖 Смотреть комментарии", callback_data=f"view|{key}"),
        ],
        [
            InlineKeyboardButton("🏷 Теги", callback_data=f"tags|{key}")
        ]
    ])

def tags_keyboard(key: str):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💼 Бизнес", callback_data=f"tag|{key}|Бизнес"),
            InlineKeyboardButton("🔫 Криминал", callback_data=f"tag|{key}|Криминал"),
        ],
        [
            InlineKeyboardButton("👮‍♂️ Полиция", callback_data=f"tag|{key}|Полиция"),
            InlineKeyboardButton("👱‍♀️ Легкодоступная", callback_data=f"tag|{key}|Легкодоступная"),
        ],
        [
            InlineKeyboardButton("⚠️ Мошенник", callback_data=f"tag|{key}|Мошенник"),
        ],
        [
            InlineKeyboardButton("⬅️ Назад", callback_data=f"back|{key}")
        ]
    ])

# ================= СТАРТ =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "👋 Добро пожаловать в бот социального рейтинга\n\n"
        "Отправь:\n"
        "• @username\n"
        "• ссылку t.me\n"
        "• номер телефона +79998887766\n\n"
        "Голосуй 👍👎, добавляй теги и анонимные комментарии."
    )

# ================= ТЕКСТ =================

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    # режим добавления комментария
    if context.user_data.get("comment_mode"):
        key = context.user_data.get("comment_key")
        if key and key in ratings:
            ratings[key]["comments"].append(text)
            save_ratings(ratings)
        context.user_data.clear()
        await update.message.reply_text("✅ Комментарий добавлен")
        return

    if not (is_username(text) or is_telegram_link(text) or is_phone_number(text)):
        return

    if is_username(text):
        key = f"user:{text}"
    elif is_phone_number(text):
        key = f"phone:{text}"
    else:
        key = f"link:{make_key(text)}"

    ensure_object(key, text)
    save_ratings(ratings)

    obj = ratings[key]

    await update.message.reply_text(
        f"⭐ Объект:\n{obj['title']}\n\n"
        f"Рейтинг: {format_rating(obj['score'])}\n\n"
        f"🏷 Теги:\n{format_tags(obj['tags'])}",
        reply_markup=main_keyboard(key)
    )

# ================= CALLBACKS =================

async def vote_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    _, key, value = q.data.split("|")
    user_id = str(q.from_user.id)
    value = int(value)

    if user_id in ratings[key]["votes"]:
        await q.answer("❌ Вы уже голосовали", show_alert=True)
        return

    ratings[key]["votes"][user_id] = value
    ratings[key]["score"] += value
    save_ratings(ratings)

    obj = ratings[key]
    await q.edit_message_text(
        f"⭐ Объект:\n{obj['title']}\n\n"
        f"Рейтинг: {format_rating(obj['score'])}\n\n"
        f"🏷 Теги:\n{format_tags(obj['tags'])}",
        reply_markup=main_keyboard(key)
    )

async def open_tags(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    _, key = q.data.split("|")
    obj = ratings[key]
    await q.edit_message_text(
        f"⭐ Объект:\n{obj['title']}\n\n"
        f"🏷 Теги:\n{format_tags(obj['tags'])}",
        reply_markup=tags_keyboard(key)
    )

async def add_tag(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    _, key, tag = q.data.split("|")
    user_id = str(q.from_user.id)

    if user_id in ratings[key]["tag_voters"]:
        await q.answer("❌ Вы уже добавляли тег", show_alert=True)
        return

    ratings[key]["tags"][tag] = ratings[key]["tags"].get(tag, 0) + 1
    ratings[key]["tag_voters"].append(user_id)
    save_ratings(ratings)

    await q.answer("✅ Тег добавлен")
    await open_tags(update, context)

async def back_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    _, key = q.data.split("|")
    obj = ratings[key]
    await q.edit_message_text(
        f"⭐ Объект:\n{obj['title']}\n\n"
        f"Рейтинг: {format_rating(obj['score'])}\n\n"
        f"🏷 Теги:\n{format_tags(obj['tags'])}",
        reply_markup=main_keyboard(key)
    )

async def comment_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    _, key = q.data.split("|")
    context.user_data["comment_mode"] = True
    context.user_data["comment_key"] = key
    await q.edit_message_text(
        "💬 Напишите комментарий одним сообщением\n\n"
        "⚠️ Комментарий будет анонимным",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Отмена", callback_data=f"back|{key}")]
        ])
    )

async def view_comments(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    _, key = q.data.split("|")
    comments = ratings[key]["comments"]

    if not comments:
        text = "💬 Комментариев пока нет"
    else:
        text = "💬 Комментарии:\n\n"
        for c in comments[-10:]:
            text += f"• {c}\n\n"

    await q.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Назад", callback_data=f"back|{key}")]
        ])
    )

# ================= MAIN =================

def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    app.add_handler(CallbackQueryHandler(vote_handler, pattern="^vote\\|"))
    app.add_handler(CallbackQueryHandler(open_tags, pattern="^tags\\|"))
    app.add_handler(CallbackQueryHandler(add_tag, pattern="^tag\\|"))
    app.add_handler(CallbackQueryHandler(back_handler, pattern="^back\\|"))
    app.add_handler(CallbackQueryHandler(comment_button, pattern="^comment\\|"))
    app.add_handler(CallbackQueryHandler(view_comments, pattern="^view\\|"))

    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
