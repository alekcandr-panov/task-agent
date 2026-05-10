import logging
from datetime import datetime, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiogram import Bot
from database import get_due_soon, get_overdue, mark_reminded, get_tasks, get_task, close_task

logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler(timezone="Europe/Moscow")

# Pending verification callbacks: task_id -> True
_pending_verification: dict[int, bool] = {}


def _priority_emoji(p: str) -> str:
    return {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(p, "⚪")


def get_pending_verifications() -> dict:
    return _pending_verification


def resolve_verification(task_id: int):
    _pending_verification.pop(task_id, None)


async def check_deadlines(bot: Bot, owner_id: int):
    due_soon = get_due_soon(hours=24)
    for task in due_soon:
        try:
            text = (
                f"⏰ <b>Дедлайн через 24 часа</b>\n\n"
                f"{_priority_emoji(task['priority'])} <b>{task['title']}</b>\n"
            )
            if task.get("assignee"):
                text += f"👤 {task['assignee']}\n"
            if task.get("deadline"):
                text += f"📅 {task['deadline']}\n"
            text += f"\n/done_{task['id']} — выполнено"
            await bot.send_message(owner_id, text, parse_mode="HTML")
            mark_reminded(task["id"], "due_soon")
        except Exception as e:
            logger.error(f"due_soon reminder failed: {e}")

    overdue = get_overdue()
    for task in overdue:
        try:
            text = (
                f"🚨 <b>Просрочена задача!</b>\n\n"
                f"{_priority_emoji(task['priority'])} <b>{task['title']}</b>\n"
            )
            if task.get("assignee"):
                text += f"👤 {task['assignee']}\n"
            if task.get("deadline"):
                text += f"📅 Дедлайн был: {task['deadline']}\n"
            text += f"\n/done_{task['id']} — закрыть"
            await bot.send_message(owner_id, text, parse_mode="HTML")
            mark_reminded(task["id"], "overdue")
        except Exception as e:
            logger.error(f"overdue reminder failed: {e}")


async def morning_digest(bot: Bot, owner_id: int):
    tasks = get_tasks(owner_id, status="open")
    if not tasks:
        return

    today = datetime.now().strftime("%Y-%m-%d")
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    today_tasks = [t for t in tasks if t.get("deadline", "").startswith(today)]
    overdue_tasks = [t for t in tasks if t.get("deadline") and t["deadline"] < now_str]
    upcoming = [t for t in tasks if t not in today_tasks and t not in overdue_tasks][:5]

    text = "☀️ <b>Утренний дайджест</b>\n\n"

    if overdue_tasks:
        text += f"🚨 <b>Просрочено ({len(overdue_tasks)}):</b>\n"
        for t in overdue_tasks[:3]:
            text += f"  • {t['title']}"
            if t.get("deadline"):
                text += f" <i>({t['deadline']})</i>"
            text += "\n"
        text += "\n"

    if today_tasks:
        text += f"📅 <b>На сегодня ({len(today_tasks)}):</b>\n"
        for t in today_tasks:
            text += f"  {_priority_emoji(t['priority'])} {t['title']}"
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

    text += f"\n<i>Всего открытых: {len(tasks)}</i> | /tasks"
    try:
        await bot.send_message(owner_id, text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"morning digest failed: {e}")


async def weekly_verification(bot: Bot, owner_id: int):
    """Ask owner to verify completion of open tasks — sent Sunday evening."""
    tasks = get_tasks(owner_id, status="open")
    if not tasks:
        return

    text = "🔍 <b>Еженедельная проверка задач</b>\n\nОтметь выполненные:\n\n"
    for t in tasks[:15]:
        em = _priority_emoji(t["priority"])
        text += f"{em} <b>{t['title']}</b>"
        if t.get("assignee"):
            text += f" — {t['assignee']}"
        text += f"\n/done_{t['id']} — выполнено\n\n"
        _pending_verification[t["id"]] = True

    try:
        await bot.send_message(owner_id, text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"weekly verification failed: {e}")


async def weekly_report(bot: Bot, owner_id: int):
    """Send weekly table report every Monday 9:00."""
    tasks_open = get_tasks(owner_id, status="open")
    tasks_done = get_tasks(owner_id, status="done")

    done_this_week = [
        t for t in tasks_done
        if t.get("done_at") and t["done_at"] >= (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    ]

    now = datetime.now().strftime("%d.%m.%Y")
    text = f"📊 <b>Еженедельный отчёт — {now}</b>\n\n"

    if done_this_week:
        text += "✅ <b>Выполнено за неделю:</b>\n"
        text += "<pre>"
        text += f"{'Задача':<30} {'Кто':<15} {'Дата':<12} {'Закрыто':<12}\n"
        text += "─" * 70 + "\n"
        for t in done_this_week:
            title = t["title"][:28]
            assignee = (t.get("assignee") or "—")[:13]
            created = t.get("created_at", "")[:10]
            done = t.get("done_at", "")[:10]
            text += f"{title:<30} {assignee:<15} {created:<12} {done:<12}\n"
        text += "</pre>\n\n"

    if tasks_open:
        text += f"📋 <b>Открытые задачи ({len(tasks_open)}):</b>\n"
        text += "<pre>"
        text += f"{'Задача':<30} {'Кто':<15} {'Создана':<12} {'Дедлайн':<12}\n"
        text += "─" * 70 + "\n"
        for t in tasks_open[:20]:
            title = t["title"][:28]
            assignee = (t.get("assignee") or "—")[:13]
            created = t.get("created_at", "")[:10]
            deadline = t.get("deadline", "—")[:10] if t.get("deadline") else "—"
            text += f"{title:<30} {assignee:<15} {created:<12} {deadline:<12}\n"
        text += "</pre>"

    try:
        await bot.send_message(owner_id, text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"weekly report failed: {e}")


def setup_scheduler(bot: Bot, owner_id: int):
    scheduler.add_job(check_deadlines, "interval", hours=1,
                      args=[bot, owner_id], id="deadline_check")
    scheduler.add_job(morning_digest, "cron", hour=9, minute=0,
                      args=[bot, owner_id], id="morning_digest")
    scheduler.add_job(weekly_verification, "cron", day_of_week="sun", hour=19, minute=0,
                      args=[bot, owner_id], id="weekly_verification")
    scheduler.add_job(weekly_report, "cron", day_of_week="mon", hour=9, minute=0,
                      args=[bot, owner_id], id="weekly_report")
    scheduler.start()
    logger.info("Scheduler started")
