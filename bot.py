import asyncio
import logging
import os
import re
from collections import defaultdict

from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from dotenv import load_dotenv

from agent import extract_tasks, classify_message
from database import (
    init_db, add_task, get_tasks, get_task,
    close_task, get_summary
)
from formatting import format_task_list, format_task_full
from scheduler import setup_scheduler

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ["BOT_TOKEN"]
OWNER_ID = int(os.environ["OWNER_ID"])
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
router = Router()

# Rolling context window per chat: last 15 messages
chat_context: dict[int, list[str]] = defaultdict(list)


def _add_context(chat_id: int, sender: str, text: str):
    chat_context[chat_id].append(f"{sender}: {text}")
    if len(chat_context[chat_id]) > 15:
        chat_context[chat_id].pop(0)


# ── /start ──────────────────────────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(msg: Message):
    if msg.from_user.id != OWNER_ID:
        return
    await msg.answer(
        "👋 <b>Task Agent запущен!</b>\n\n"
        "Я автоматически слежу за твоими сообщениями и фиксирую задачи.\n\n"
        "Команды:\n"
        "/tasks — все открытые задачи\n"
        "/done_ID — закрыть задачу\n"
        "/summary — статистика\n"
        "/help — справка",
        parse_mode="HTML"
    )


# ── /tasks ───────────────────────────────────────────────────────────────────

@router.message(Command("tasks"))
async def cmd_tasks(msg: Message):
    if msg.from_user.id != OWNER_ID:
        return
    tasks = get_tasks(OWNER_ID)
    await msg.answer(format_task_list(tasks), parse_mode="HTML")


# ── /done_ID ─────────────────────────────────────────────────────────────────

@router.message(F.text.regexp(r"^/done_(\d+)$"))
async def cmd_done(msg: Message):
    if msg.from_user.id != OWNER_ID:
        return
    match = re.match(r"^/done_(\d+)$", msg.text)
    task_id = int(match.group(1))
    task = get_task(task_id)
    if not task:
        await msg.answer(f"❌ Задача #{task_id} не найдена.")
        return
    close_task(task_id)
    await msg.answer(f"✅ Задача закрыта:\n<b>{task['title']}</b>", parse_mode="HTML")


# ── /task_ID (detail) ────────────────────────────────────────────────────────

@router.message(F.text.regexp(r"^/task_(\d+)$"))
async def cmd_task_detail(msg: Message):
    if msg.from_user.id != OWNER_ID:
        return
    match = re.match(r"^/task_(\d+)$", msg.text)
    task_id = int(match.group(1))
    task = get_task(task_id)
    if not task:
        await msg.answer(f"❌ Задача #{task_id} не найдена.")
        return
    await msg.answer(format_task_full(task), parse_mode="HTML")


# ── /summary ─────────────────────────────────────────────────────────────────

@router.message(Command("summary"))
async def cmd_summary(msg: Message):
    if msg.from_user.id != OWNER_ID:
        return
    s = get_summary(OWNER_ID)
    text = (
        "📊 <b>Статистика задач</b>\n\n"
        f"📋 Открытых задач: <b>{s['total_open']}</b>\n"
        f"🚨 Просрочено: <b>{s['overdue']}</b>\n"
        f"📅 Срок сегодня: <b>{s['due_today']}</b>\n"
        f"✅ Закрыто за неделю: <b>{s['done_this_week']}</b>"
    )
    await msg.answer(text, parse_mode="HTML")


# ── /help ─────────────────────────────────────────────────────────────────────

@router.message(Command("help"))
async def cmd_help(msg: Message):
    if msg.from_user.id != OWNER_ID:
        return
    await msg.answer(
        "📖 <b>Справка</b>\n\n"
        "/tasks — список всех открытых задач\n"
        "/task_ID — детали задачи\n"
        "/done_ID — отметить задачу выполненной\n"
        "/summary — общая статистика\n\n"
        "<b>Как работает автосбор:</b>\n"
        "Я слежу за твоими сообщениями во всех чатах, где добавлен. "
        "Когда ты ставишь кому-то задачу — автоматически её фиксирую, "
        "извлекаю дедлайн и приоритет, и напоминаю за 24 часа до срока.",
        parse_mode="HTML"
    )


# ── Main message handler ──────────────────────────────────────────────────────

@router.message(F.text)
async def handle_message(msg: Message):
    if not msg.text or not msg.from_user:
        return

    sender_id = msg.from_user.id
    sender_name = msg.from_user.full_name or str(sender_id)
    chat_id = msg.chat.id
    text = msg.text.strip()

    # Update rolling context for this chat
    _add_context(chat_id, sender_name, text)

    # Only analyze messages from the owner
    if sender_id != OWNER_ID:
        return

    # Quick keyword pre-filter to avoid unnecessary API calls
    if not await classify_message(text):
        return

    try:
        tasks = await extract_tasks(
            message_text=text,
            owner_id=OWNER_ID,
            sender_id=sender_id,
            sender_name=sender_name,
            chat_context=chat_context[chat_id][:-1]  # context without current msg
        )
    except Exception as e:
        logger.error(f"Task extraction failed: {e}")
        return

    if not tasks:
        return

    saved = []
    for t in tasks:
        task_id = add_task(
            chat_id=OWNER_ID,
            title=t.get("title", "Без названия"),
            description=t.get("description"),
            assignee=t.get("assignee"),
            deadline=t.get("deadline"),
            priority=t.get("priority", "medium"),
            source_msg=text[:200]
        )
        saved.append((task_id, t))

    # Confirm to owner
    if len(saved) == 1:
        task_id, t = saved[0]
        em = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(t.get("priority", "medium"), "⚪")
        reply = f"✅ Зафиксирована задача {em}\n<b>{t['title']}</b>"
        if t.get("deadline"):
            reply += f"\n📅 Дедлайн: {t['deadline']}"
        if t.get("assignee"):
            reply += f"\n👤 {t['assignee']}"
        reply += f"\n\n/task_{task_id} — посмотреть"
    else:
        reply = f"✅ Зафиксировано {len(saved)} задачи:\n"
        for task_id, t in saved:
            reply += f"  • #{task_id} {t['title']}\n"

    await bot.send_message(OWNER_ID, reply, parse_mode="HTML")


# ── Startup ───────────────────────────────────────────────────────────────────

async def main():
    init_db()
    dp.include_router(router)
    setup_scheduler(bot, OWNER_ID)
    logger.info(f"Bot started. Owner ID: {OWNER_ID}")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
