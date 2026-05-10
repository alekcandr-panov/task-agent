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
from database import init_db, add_task, get_tasks, get_task, close_task, get_summary
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

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
router = Router()

chat_context: dict[int, list[str]] = defaultdict(list)


def _add_context(chat_id: int, sender: str, text: str):
    chat_context[chat_id].append(f"{sender}: {text}")
    if len(chat_context[chat_id]) > 15:
        chat_context[chat_id].pop(0)


def _priority_emoji(p: str) -> str:
    return {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(p, "⚪")


@router.message(CommandStart())
async def cmd_start(msg: Message):
    if msg.from_user.id != OWNER_ID:
        return
    await msg.answer(
        "👋 <b>Task Agent запущен!</b>\n\n"
        "Слежу за твоими сообщениями и фиксирую задачи автоматически.\n\n"
        "/tasks — открытые задачи\n"
        "/done_ID — закрыть задачу\n"
        "/summary — статистика\n"
        "/report — отчёт прямо сейчас\n"
        "/help — справка",
        parse_mode="HTML"
    )


@router.message(Command("tasks"))
async def cmd_tasks(msg: Message):
    if msg.from_user.id != OWNER_ID:
        return
    tasks = get_tasks(OWNER_ID)
    await msg.answer(format_task_list(tasks), parse_mode="HTML")


@router.message(Command("summary"))
async def cmd_summary(msg: Message):
    if msg.from_user.id != OWNER_ID:
        return
    s = get_summary(OWNER_ID)
    text = (
        "📊 <b>Статистика</b>\n\n"
        f"📋 Открытых: <b>{s['total_open']}</b>\n"
        f"🚨 Просрочено: <b>{s['overdue']}</b>\n"
        f"📅 На сегодня: <b>{s['due_today']}</b>\n"
        f"✅ Закрыто за неделю: <b>{s['done_this_week']}</b>"
    )
    await msg.answer(text, parse_mode="HTML")


@router.message(Command("report"))
async def cmd_report(msg: Message):
    if msg.from_user.id != OWNER_ID:
        return
    from scheduler import weekly_report
    await weekly_report(bot, OWNER_ID)


@router.message(Command("help"))
async def cmd_help(msg: Message):
    if msg.from_user.id != OWNER_ID:
        return
    await msg.answer(
        "📖 <b>Справка</b>\n\n"
        "/tasks — все открытые задачи\n"
        "/task_ID — детали задачи\n"
        "/done_ID — отметить выполненной\n"
        "/summary — статистика\n"
        "/report — еженедельный отчёт прямо сейчас\n\n"
        "<b>Автоматически:</b>\n"
        "• Фиксирую задачи из твоих сообщений\n"
        "• Напоминаю за 24ч до дедлайна\n"
        "• Воскресенье 19:00 — проверка задач\n"
        "• Понедельник 9:00 — еженедельный отчёт",
        parse_mode="HTML"
    )


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
    await msg.answer(f"✅ Закрыта: <b>{task['title']}</b>", parse_mode="HTML")


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


@router.message(F.text)
async def handle_message(msg: Message):
    if not msg.text or not msg.from_user:
        return

    sender_id = msg.from_user.id
    sender_name = msg.from_user.full_name or str(sender_id)
    chat_id = msg.chat.id
    text = msg.text.strip()

    # Always update context (for all users in chat)
    _add_context(chat_id, sender_name, text)

    # Only analyze messages from the owner
    if sender_id != OWNER_ID:
        return

    # Quick pre-filter
    if not await classify_message(text):
        return

    try:
        tasks = await extract_tasks(
            message_text=text,
            owner_id=OWNER_ID,
            sender_id=sender_id,
            sender_name=sender_name,
            chat_context=chat_context[chat_id][:-1]
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

    # Notify owner in private — silently in group chats
    if len(saved) == 1:
        task_id, t = saved[0]
        em = _priority_emoji(t.get("priority", "medium"))
        reply = f"✅ Зафиксирована задача {em}\n<b>{t['title']}</b>"
        if t.get("deadline"):
            reply += f"\n📅 {t['deadline']}"
        if t.get("assignee"):
            reply += f"\n👤 {t['assignee']}"
        reply += f"\n/task_{task_id}"
    else:
        reply = f"✅ Зафиксировано задач: {len(saved)}\n"
        for task_id, t in saved:
            reply += f"  • #{task_id} {t['title']}\n"

    # Always send confirmation to owner's private chat, not to the group
    await bot.send_message(OWNER_ID, reply, parse_mode="HTML")


async def main():
    init_db()
    dp.include_router(router)
    setup_scheduler(bot, OWNER_ID)
    logger.info(f"Bot started. Owner ID: {OWNER_ID}")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
