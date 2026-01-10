print("BOT STARTING...")

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
import json
import os
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler
from telegram.ext import MessageHandler, filters
import re

import os


TOKEN = os.getenv("BOT_TOKEN")

import logging

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)



FILE_NAME = "ratings.json"
TAG_EMOJIS = {
    "Бизнес": "💼",
    "Криминал": "🔫",
    "Полиция": "👮‍♂️",
    "Легкодоступная": "👱‍♀️",
    "Мошенник": "⚠️",
}


# ===== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ (ПРОВЕРКИ) =====

def is_username(text: str) -> bool:
    return text.startswith("@") and len(text) > 1

def is_telegram_link(text: str) -> bool:
    return text.startswith("https://t.me/") or text.startswith("http://t.me/")

def is_phone_number(text: str) -> bool:
    # ТОЛЬКО формат +79998887766
    return bool(re.fullmatch(r"\+\d{10,15}", text))



def load_ratings():
    if not os.path.exists(FILE_NAME):
        return {}
    with open(FILE_NAME, "r", encoding="utf-8") as f:
        return json.load(f)

def save_ratings(data):
    with open(FILE_NAME, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def rating_keyboard(key):
    keyboard = [
        [
            InlineKeyboardButton("👍 +1", callback_data=f"vote|{key}|1"),
            InlineKeyboardButton("👎 -1", callback_data=f"vote|{key}|-1"),
        ],
        [
            InlineKeyboardButton("🏷 Теги", callback_data=f"open_tags|{key}")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def tags_keyboard(key):
    keyboard = [
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
    ]
    return InlineKeyboardMarkup(keyboard)



def format_tags(tags):
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
    elif score < 0:
        return f"👎 {score}"
    else:
        return f"➖ {score}"

def is_phone_number(text: str) -> bool:
    return bool(re.fullmatch(r"\+\d{10,15}", text))

def full_keyboard(key):
    keyboard = [
        [
            InlineKeyboardButton("👍 +1", callback_data=f"vote|{key}|1"),
            InlineKeyboardButton("👎 -1", callback_data=f"vote|{key}|-1"),
        ],
        [
            InlineKeyboardButton("💬 Добавить комментарий", callback_data=f"comment|{key}"),
            InlineKeyboardButton("📖 Смотреть комментарии", callback_data=f"view_comments|{key}"),
        ],
        [
            InlineKeyboardButton("🏷 Теги", callback_data=f"open_tags|{key}")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)







TOKEN = "8186874294:AAHlIidQsjqfLPw0MCdGMuuCUKCmWq-rFYE"

ratings = load_ratings()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()

    await update.message.reply_text(
        "👋 Добро пожаловать в бот социального рейтинга\n\n"
        "📌 Что здесь можно делать:\n"
        "• Проверять людей, аккаунты и номера телефонов\n"
        "• Ставить 👍 или 👎\n"
        "• Добавлять теги и комментарии\n\n"
        "🧭 Как пользоваться:\n"
        "1️⃣ Отправь @username, ссылку или номер телефона\n"
        "2️⃣ Поставь оценку 👍 / 👎\n"
        "3️⃣ При желании добавь теги и комментарии\n\n"
        "⚠️ Правила:\n"
        "• Один человек — один голос\n"
        "• Нельзя голосовать за себя\n"
        "• Комментарии анонимные\n\n"
        "👇 Просто отправь объект для проверки"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📘 Команды:\n\n"
        "/start — приветствие\n"
        "/help — помощь\n"
        "/rate — добавить репутацию\n"
        "/check — посмотреть рейтинг\n\n"
        "Пример:\n"
        "/rate @username +1"
    )

async def rate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 2:
        await update.message.reply_text(
            "❌ Формат:\n"
            "/rate user:@username +1\n"
            "/rate channel:@channel +1\n"
            "/rate shop:название +1"
        )
        return

    target = context.args[0]
    value_str = context.args[1]

    if ":" not in target:
        await update.message.reply_text("❌ Укажи тип: user, channel или shop")
        return

    target_type, target_name = target.split(":", 1)

    if target_type not in ("user", "channel", "shop"):
        await update.message.reply_text("❌ Тип должен быть user, channel или shop")
        return

    if value_str not in ("+1", "-1"):
        await update.message.reply_text("❌ Только +1 или -1")
        return

    voter_id = str(update.effective_user.id)
    value = int(value_str)

    full_key = f"{target_type}:{target_name}"

    # ❌ запрет голосовать за себя (только для user)
    if target_type == "user":
        if target_name == f"@{update.effective_user.username}":
            await update.message.reply_text("❌ Нельзя голосовать за себя")
            return

    if full_key not in ratings:
        ratings[full_key] = {
            "score": 0,
            "votes": {}
        }

    if voter_id in ratings[full_key]["votes"]:
        await update.message.reply_text("❌ Ты уже голосовал за этот объект")
        return

    ratings[full_key]["votes"][voter_id] = value
    ratings[full_key]["score"] += value

    save_ratings(ratings)

    await update.message.reply_text(
        f"✅ Голос учтён\n"
        f"⭐ Рейтинг {full_key}: {ratings[full_key]['score']}"
    )



async def check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1:
        await update.message.reply_text(
            "❌ Формат:\n"
            "/check user:@username\n"
            "/check channel:@channel\n"
            "/check shop:название"
        )
        return

    key = context.args[0]

    if key not in ratings:
        await update.message.reply_text("⭐ Рейтинг: 0")
        return

    await update.message.reply_text(
        f"⭐ Рейтинг {key}: {ratings[key]['score']}"
    )

async def show_with_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1:
        await update.message.reply_text(
            "❌ Формат:\n"
            "/show user:@username\n"
            "/show channel:@channel\n"
            "/show shop:название"
        )
        return

    key = context.args[0]

    if key not in ratings:
        ratings[key] = {
            "score": 0,
            "votes": {}
        }
        save_ratings(ratings)

    keyboard = [
        [
            InlineKeyboardButton("👍 +1", callback_data=f"vote|{key}|1"),
            InlineKeyboardButton("👎 -1", callback_data=f"vote|{key}|-1"),
        ]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        f"⭐ Рейтинг {key}: {ratings[key]['score']}",
        reply_markup=reply_markup
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # обрабатываем ТОЛЬКО голосование
    if not query.data.startswith("vote|"):
        return

    _, key, value_str = query.data.split("|")
    voter_id = str(query.from_user.id)
    value = int(value_str)

    # запрет голосовать за себя (только для user)
    if key.startswith("user:"):
        username = key.split(":", 1)[1]
        if query.from_user.username and username == f"@{query.from_user.username}":
            await query.answer("❌ Нельзя голосовать за себя", show_alert=True)
            return

    # защита от повторного голосования
    if voter_id in ratings[key]["votes"]:
        await query.answer("❌ Ты уже голосовал", show_alert=True)
        return

    ratings[key]["votes"][voter_id] = value
    ratings[key]["score"] += value
    save_ratings(ratings)

    # обновляем сообщение, ТЕГИ НЕ ПРОПАДАЮТ
    await query.edit_message_text(
        f"⭐ Объект:\n{key.split(':', 1)[1]}\n\n"
        f"Рейтинг: {format_rating(ratings[key]['score'])}\n\n"
        f"🏷 Теги:\n{format_tags(ratings[key]['tags'])}",
        reply_markup=rating_keyboard(key)
    )



async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    # ❌ команды не трогаем
    if text.startswith("/"):
        return

    # ✅ режим комментариев
    if context.user_data.get("comment_mode"):
        key = context.user_data.get("comment_key")
        if not key:
            context.user_data.clear()
            return

        ratings[key].setdefault("comments", [])
        ratings[key]["comments"].append(text)
        save_ratings(ratings)

        context.user_data.clear()
        await update.message.reply_text("✅ Комментарий добавлен анонимно")
        return

    # ⛔ НЕ объект — ничего не делаем
    if not (
        is_username(text)
        or is_telegram_link(text)
        or is_phone_number(text)
    ):
        await update.message.reply_text(
            "❌ Я могу работать только с:\n"
            "• @username\n"
            "• ссылками t.me\n"
            "• номерами телефонов в формате +79998887766"
        )
        return

    # ✅ ОПРЕДЕЛЯЕМ КЛЮЧ ОБЪЕКТА
    if is_phone_number(text):
        key = f"phone:{text}"
    elif is_username(text):
        key = f"user:{text}"
    else:
        key = f"link:{text}"

    context.user_data["current_key"] = key

    # создаём объект, если его нет
    ratings.setdefault(key, {
        "score": 0,
        "votes": {},
        "tags": {},
        "tag_voters": [],
        "comments": []
    })

    save_ratings(ratings)

    await update.message.reply_text(
        f"⭐ Объект:\n{text}\n\n"
        f"Рейтинг: {ratings[key]['score']}\n\n"
        f"🏷 Теги:\n{format_tags(ratings[key]['tags'])}",
        reply_markup=full_keyboard(key)
    )





async def handle_tag(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    if not query.data.startswith("tag|"):
        return

    _, key, tag = query.data.split("|")
    user_id = str(query.from_user.id)

    if key not in ratings:
        await query.answer("❌ Объект не найден", show_alert=True)
        return

    ratings[key].setdefault("tags", {})
    ratings[key].setdefault("tag_voters", [])

    if user_id in ratings[key]["tag_voters"]:
        await query.answer("❌ Вы уже добавляли тег", show_alert=True)
        return

    ratings[key]["tags"][tag] = ratings[key]["tags"].get(tag, 0) + 1
    ratings[key]["tag_voters"].append(user_id)

    save_ratings(ratings)

    await query.answer("✅ Тег добавлен")

    await query.edit_message_text(
        f"⭐ Объект:\n{key.split(":", 1)[1]}\n\n"
        f"Рейтинг: {format_rating(ratings[key]['score'])}\n\n"
        f"🏷 Теги:\n{format_tags(ratings[key]['tags'])}",
        reply_markup=full_keyboard(key)
    )
   
    await query.edit_message_text(
        text,  # твой текст с тегами
        reply_markup=full_keyboard(key)
    )


async def open_tags(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    _, key = query.data.split("|", 1)

    await query.edit_message_text(
        f"⭐ Объект:\n{key.split(':', 1)[1]}\n\n"
        f"Рейтинг: {format_rating(ratings[key]['score'])}\n\n"
        f"🏷 Теги:\n{format_tags(ratings[key]['tags'])}",
        reply_markup=tags_keyboard(key)
    )
    
    await query.edit_message_text(
    text,
    reply_markup=full_keyboard(key)
)


async def back_to_rating(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    _, key = query.data.split("|", 1)

    await query.edit_message_text(
        f"⭐ Объект:\n{key.split(':', 1)[1]}\n\n"
        f"Рейтинг: {format_rating(ratings[key]['score'])}\n\n"
        f"🏷 Теги:\n{format_tags(ratings[key]['tags'])}",
        reply_markup=rating_keyboard(key)
    )




async def open_comments(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    _, key = query.data.split("|")

    context.user_data["comment_mode"] = True
    context.user_data["comment_key"] = key

    comments = ratings[key].get("comments", [])

    if comments:
        text = "💬 Комментарии:\n\n"
        for c in comments:
            text += f"💬 Аноним:\n{c['text']}\n\n"

    else:
        text = "💬 Комментариев пока нет."

    text += "\n✍️ Напиши комментарий одним сообщением:"

    await query.edit_message_text(
        text,
        reply_markup=full_keyboard(key)
    )

async def handle_comment_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not query.data.startswith("comment|"):
        return

    _, key = query.data.split("|")

    context.user_data["comment_mode"] = True
    context.user_data["comment_key"] = key

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Отмена", callback_data="cancel_comment")]
    ])

    await query.edit_message_text(
        "💬 Напишите комментарий к объекту\n\n"
        "⚠️ Комментарий будет анонимным",
        reply_markup=keyboard
    )

async def handle_cancel_comment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data != "cancel_comment":
        return

    key = context.user_data.get("comment_key")

    context.user_data.clear()

    if not key or key not in ratings:
        await query.edit_message_text("❌ Действие отменено")
        return

    await query.edit_message_text(
        f"⭐ Объект:\n{key.replace('custom:', '')}\n\n"
        f"Рейтинг: {ratings[key]['score']}\n\n"
        f"🏷 Теги:\n{format_tags(ratings[key].get('tags', {}))}",
        reply_markup=full_keyboard(key)
    )

async def handle_view_comments(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not query.data.startswith("view_comments|"):
        return

    _, key = query.data.split("|")

    comments = ratings.get(key, {}).get("comments", [])

    if not comments:
        text = "💬 Комментариев пока нет"
    else:
        text = "💬 Комментарии:\n\n"
        for i, c in enumerate(comments[-10:], 1):
            text += f"{i}. {c}\n\n"

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Назад", callback_data=f"back|{key}")]
        ])
    )


async def handle_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not query.data.startswith("back|"):
        return

    _, key = query.data.split("|")

    await query.edit_message_text(
        f"⭐ Объект:\n{key.replace('custom:', '')}\n\n"
        f"Рейтинг: {ratings[key]['score']}\n\n"
        f"🏷 Теги:\n{format_tags(ratings[key].get('tags', {}))}",
        reply_markup=full_keyboard(key)
    )





def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    app.add_handler(CallbackQueryHandler(button_handler, pattern="^vote\\|"))
    app.add_handler(CallbackQueryHandler(handle_tag, pattern="^tag\\|"))
    app.add_handler(CallbackQueryHandler(open_tags, pattern="^open_tags\\|"))
    app.add_handler(CallbackQueryHandler(back_to_rating, pattern="^back\\|"))
    app.add_handler(CallbackQueryHandler(open_comments, pattern="^open_comments\\|"))
    app.add_handler(CallbackQueryHandler(handle_comment_button, pattern="^comment\\|"))
    app.add_handler(CallbackQueryHandler(handle_cancel_comment, pattern="^cancel_comment$"))
    app.add_handler(CallbackQueryHandler(button_handler, pattern="^vote\\|"))
    app.add_handler(CallbackQueryHandler(handle_comment_button, pattern="^comment\\|"))
    app.add_handler(CallbackQueryHandler(handle_view_comments, pattern="^view_comments\\|"))
    app.add_handler(CallbackQueryHandler(handle_back, pattern="^back\\|"))






    print("Бот запущен...")
    app.run_polling()
    app.run_polling(drop_pending_updates=True)



if __name__ == "__main__":
    main()




