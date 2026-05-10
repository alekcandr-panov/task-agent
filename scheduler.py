import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiogram import Bot
from database import get_due_soon, get_overdue, mark_reminded, get_tasks
from formatting import format_task_short

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone="Europe/Moscow")


def _priority_emoji(p: str) -> str:
    return {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(p, "⚪")


async def check_deadlines(bot: Bot, owner_id: int):
    """Run every hour. Alert owner about due-soon and overdue tasks."""

    # Tasks due within 24 hours
    due_soon = get_due_soon(hours=24)
    for task in due_soon:
        try:
            text = (
                f"⏰ <b>Дедлайн через 24 часа</b>\n\n"
                f"{_priority_emoji(task['priority'])} <b>{task['title']}</b>\n"
            )
            if task.get("assignee"):
                text += f"👤 Ответственный: {task['assignee']}\n"
            if task.get("deadline"):
                text += f"📅 Дедлайн: {task['deadline']}\n"
            if task.get("description"):
                text += f"\n{task['description']}\n"
            text += f"\n/done_{task['id']} — отметить выполненной"

            await bot.send_message(owner_id, text, parse_mode="HTML")
            mark_reminded(task["id"], "due_soon")
        except Exception as e:
            logger.error(f"Failed to send due_soon reminder: {e}")

    # Overdue tasks
    overdue = get_overdue()
    for task in overdue:
        try:
            text = (
                f"🚨 <b>Просрочена задача!</b>\n\n"
                f"{_priority_emoji(task['priority'])} <b>{task['title']}</b>\n"
            )
            if task.get("assignee"):
                text += f"👤 Ответственный: {task['assignee']}\n"
            if task.get("deadline"):
                text += f"📅 Дедлайн был: {task['deadline']}\n"
            text += f"\n/done_{task['id']} — закрыть задачу"

            await bot.send_message(owner_id, text, parse_mode="HTML")
            mark_reminded(task["id"], "overdue")
        except Exception as e:
            logger.error(f"Failed to send overdue reminder: {e}")


async def morning_digest(bot: Bot, owner_id: int):
    """Send a morning digest at 9:00 AM."""
    tasks = get_tasks(owner_id, status="open")
    if not tasks:
        return

    import datetime
    today = datetime.date.today().isoformat()
    today_tasks = [t for t in tasks if t.get("deadline", "").startswith(today)]
    overdue_tasks = [
        t for t in tasks
        if t.get("deadline") and t["deadline"] < datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    ]
    upcoming = [t for t in tasks if t not in today_tasks and t not in overdue_tasks][:5]

    text = "☀️ <b>Утренний дайджест задач</b>\n\n"

    if overdue_tasks:
        text += f"🚨 <b>Просрочено ({len(overdue_tasks)}):</b>\n"
        for t in overdue_tasks[:3]:
            text += f"  • {t['title']}"
            if t.get("deadline"):
                text += f" — <i>{t['deadline']}</i>"
            text += "\n"
        text += "\n"

    if today_tasks:
        text += f"📅 <b>На сегодня ({len(today_tasks)}):</b>\n"
        for t in today_tasks:
            em = _priority_emoji(t["priority"])
            text += f"  {em} {t['title']}"
            if t.get("assignee"):
                text += f" → {t['assignee']}"
            text += "\n"
        text += "\n"

    if upcoming:
        text += f"📋 <b>Предстоящие ({len(upcoming)}):</b>\n"
        for t in upcoming:
            text += f"  • {t['title']}"
            if t.get("deadline"):
                text += f" — {t['deadline']}"
            text += "\n"

    total = len(tasks)
    text += f"\n<i>Всего открытых задач: {total}</i>\n"
    text += "Полный список: /tasks"

    try:
        await bot.send_message(owner_id, text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Morning digest failed: {e}")


def setup_scheduler(bot: Bot, owner_id: int):
    scheduler.add_job(
        check_deadlines, "interval", hours=1,
        args=[bot, owner_id], id="deadline_check"
    )
    scheduler.add_job(
        morning_digest, "cron", hour=9, minute=0,
        args=[bot, owner_id], id="morning_digest"
    )
    scheduler.start()
    logger.info("Scheduler started")
