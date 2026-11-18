import os
import sqlite3
import logging
from datetime import datetime, timezone

from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ContextTypes, filters
)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "715454218"))
DB_PATH = os.getenv("DB_PATH", "kharform_money_ai.sqlite3")

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("kharform-money-ai")


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            message TEXT,
            created_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS drafts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT,
            status TEXT DEFAULT 'pending',
            created_at TEXT
        )
    """)
    conn.commit()
    return conn


def is_admin(update: Update) -> bool:
    return bool(update.effective_user and update.effective_user.id == ADMIN_ID)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Это KHARFORM MONEY AI.\n\n"
        "Напиши, что тебя интересует: товар, сотрудничество или заказ. "
        "Сообщение попадёт менеджеру KHARFORM."
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_admin(update):
        text = (
            "Команды администратора:\n"
            "/newpost Текст публикации — создать черновик\n"
            "/pending — показать ожидающие черновики\n"
            "/approve ID — одобрить черновик (пока без публикации)\n"
            "/reject ID — отклонить черновик\n"
            "/stats — статистика заявок\n"
        )
    else:
        text = "Отправь сообщение, и менеджер KHARFORM получит твою заявку."
    await update.message.reply_text(text)


async def receive_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    message = update.message
    if not user or not message or not message.text:
        return

    if user.id == ADMIN_ID:
        await message.reply_text(
            "Ты администратор. Используй /help для списка команд. "
            "Для теста заявки напиши боту с другого Telegram-аккаунта."
        )
        return

    conn = db()
    cur = conn.execute(
        "INSERT INTO leads(user_id, username, message, created_at) VALUES (?, ?, ?, ?)",
        (user.id, user.username or "", message.text,
         datetime.now(timezone.utc).isoformat())
    )
    lead_id = cur.lastrowid
    conn.commit()
    conn.close()

    await message.reply_text("Заявка принята. Менеджер KHARFORM свяжется с тобой.")
    try:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                f"Новая заявка #{lead_id}\n"
                f"От: @{user.username or 'без username'} (ID {user.id})\n\n"
                f"{message.text}"
            )
        )
    except Exception:
        log.exception("Не удалось переслать заявку администратору")


async def newpost(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    text = " ".join(context.args).strip()
    if not text:
        await update.message.reply_text("Формат: /newpost Текст публикации")
        return
    conn = db()
    cur = conn.execute(
        "INSERT INTO drafts(text, status, created_at) VALUES (?, 'pending', ?)",
        (text, datetime.now(timezone.utc).isoformat())
    )
    draft_id = cur.lastrowid
    conn.commit()
    conn.close()
    await update.message.reply_text(f"Черновик #{draft_id} сохранён. /pending")


async def pending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    conn = db()
    rows = conn.execute(
        "SELECT id, text, created_at FROM drafts WHERE status='pending' ORDER BY id"
    ).fetchall()
    conn.close()
    if not rows:
        await update.message.reply_text("Ожидающих черновиков нет.")
        return
    parts = []
    for draft_id, text, created_at in rows:
        parts.append(f"#{draft_id}: {text[:500]}\nСоздан: {created_at}")
    await update.message.reply_text("\n\n".join(parts))


async def set_draft_status(update: Update, context: ContextTypes.DEFAULT_TYPE, status: str):
    if not is_admin(update):
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Укажи ID. Например: /approve 1")
        return
    draft_id = int(context.args[0])
    conn = db()
    cur = conn.execute(
        "UPDATE drafts SET status=? WHERE id=? AND status='pending'",
        (status, draft_id)
    )
    conn.commit()
    changed = cur.rowcount
    conn.close()
    if not changed:
        await update.message.reply_text("Черновик не найден или уже обработан.")
    elif status == "approved":
        await update.message.reply_text(
            f"Черновик #{draft_id} одобрен. Автопубликация пока не подключена."
        )
    else:
        await update.message.reply_text(f"Черновик #{draft_id} отклонён.")


async def approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await set_draft_status(update, context, "approved")


async def reject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await set_draft_status(update, context, "rejected")


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    conn = db()
    leads = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
    pending_count = conn.execute(
        "SELECT COUNT(*) FROM drafts WHERE status='pending'"
    ).fetchone()[0]
    approved = conn.execute(
        "SELECT COUNT(*) FROM drafts WHERE status='approved'"
    ).fetchone()[0]
    conn.close()
    await update.message.reply_text(
        f"Заявок: {leads}\nОжидающих черновиков: {pending_count}\n"
        f"Одобрено черновиков: {approved}"
    )


def main():
    if not BOT_TOKEN:
        raise RuntimeError("Не задан BOT_TOKEN в переменных окружения")
    db().close()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("newpost", newpost))
    app.add_handler(CommandHandler("pending", pending))
    app.add_handler(CommandHandler("approve", approve))
    app.add_handler(CommandHandler("reject", reject))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receive_message))
    log.info("KHARFORM MONEY AI запущен")
    app.run_polling()


if __name__ == "__main__":
    main()
