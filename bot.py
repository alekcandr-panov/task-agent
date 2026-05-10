import asyncio
import logging
import os
import re
from collections import defaultdict
from datetime import datetime

from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from dotenv import load_dotenv

from agent import extract_tasks, classify_message
from meeting_agent import (
    classify_message as classify_meeting,
    extract_meeting, get_missing_fields,
    FIELD_QUESTIONS, suggest_alternatives
)
from calendar_client import create_event, get_free_slots, format_event_card
from completion_detector import detect_completion
from database import (
    init_db, add_task, get_tasks, get_task, close_task,
    get_summary, get_due_soon, get_overdue, mark_reminded,
    get_open_tasks_for_chat,
    save_meeting, update_meeting, get_meeting,
    get_upcoming_meetings, get_meetings_today
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

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
router = Router()

chat_context: dict[int, list[str]] = defaultdict(list)
meeting_state: dict[int, dict] = {}


def _add_context(chat_id, sender, text):
    chat_context[chat_id].append(f"{sender}: {text}")
    if len(chat_context[chat_id]) > 15:
        chat_context[chat_id].pop(0)


def _priority_emoji(p):
    return {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(p, "⚪")


# ── Keyboards ─────────────────────────────────────────────────────────────────

def task_keyboard(task_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Выполнено", callback_data=f"done_{task_id}"),
        InlineKeyboardButton(text="⏰ Завтра", callback_data=f"snooze_{task_id}_1d"),
        InlineKeyboardButton(text="📋 Детали", callback_data=f"detail_{task_id}"),
    ]])


def meeting_confirm_keyboard(meeting_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Создать встречу", callback_data=f"meet_confirm_{meeting_id}"),
            InlineKeyboardButton(text="❌ Отмена", callback_data=f"meet_cancel_{meeting_id}"),
        ],
        [
            InlineKeyboardButton(text="✏️ Изменить время", callback_data=f"meet_edittime_{meeting_id}"),
            InlineKeyboardButton(text="🔄 Альт. слоты", callback_data=f"meet_alts_{meeting_id}"),
        ]
    ])


def meeting_alt_keyboard(meeting_id: int, alts: list) -> InlineKeyboardMarkup:
    rows = []
    for i, alt in enumerate(alts[:3]):
        rows.append([InlineKeyboardButton(
            text=f"🕐 {alt['label']}",
            callback_data=f"meet_alt_{meeting_id}_{i}"
        )])
    rows.append([InlineKeyboardButton(
        text="◀️ Назад", callback_data=f"meet_back_{meeting_id}"
    )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ── Commands ──────────────────────────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(msg: Message):
    if msg.from_user.id != OWNER_ID:
        return
    await msg.answer(
        "👋 <b>Добрыня AI — запущен!</b>\n\n"
        "• 📋 Фиксирую задачи автоматически\n"
        "• 📅 Создаю встречи в Яндекс Календаре\n"
        "• ✅ Слежу за выполнением задач в чатах\n"
        "• 🔔 Напоминаю о дедлайнах\n\n"
        "/tasks — задачи | /meetings — встречи | /today — сегодня",
        parse_mode="HTML"
    )


@router.message(Command("tasks"))
async def cmd_tasks(msg: Message):
    if msg.from_user.id != OWNER_ID:
        return
    tasks = get_tasks(OWNER_ID)
    if not tasks:
        await msg.answer("✅ Нет открытых задач!")
        return
    await msg.answer(format_task_list(tasks), parse_mode="HTML")


@router.message(Command("meetings"))
async def cmd_meetings(msg: Message):
    if msg.from_user.id != OWNER_ID:
        return
    meetings = get_upcoming_meetings(OWNER_ID)
    if not meetings:
        await msg.answer("📅 Нет предстоящих встреч.")
        return
    text = "📅 <b>Предстоящие встречи:</b>\n\n"
    for m in meetings:
        text += f"🗓 <b>{m['title']}</b>\n"
        if m.get("date") and m.get("time"):
            try:
                dt = datetime.strptime(f"{m['date']} {m['time']}", "%Y-%m-%d %H:%M")
                text += f"   {dt.strftime('%d %B, %H:%M')}"
            except Exception:
                text += f"   {m['date']} {m['time']}"
        if m.get("location"):
            text += f" · {m['location']}"
        text += "\n\n"
    await msg.answer(text, parse_mode="HTML")


@router.message(Command("today"))
async def cmd_today(msg: Message):
    if msg.from_user.id != OWNER_ID:
        return
    tasks = get_tasks(OWNER_ID)
    meetings = get_meetings_today(OWNER_ID)
    today = datetime.now().strftime("%Y-%m-%d")
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    today_tasks = [t for t in tasks if t.get("deadline", "").startswith(today)]
    overdue = [t for t in tasks
               if t.get("deadline") and t["deadline"] < now_str]

    text = f"☀️ <b>Сегодня, {datetime.now().strftime('%d %B')}</b>\n\n"

    if meetings:
        text += f"📅 <b>Встречи ({len(meetings)}):</b>\n"
        for m in meetings:
            text += f"  🕐 {m.get('time', '?')} — {m['title']}\n"
            if m.get("location"):
                text += f"     📍 {m['location']}\n"
        text += "\n"

    if overdue:
        text += f"🚨 <b>Просрочено ({len(overdue)}):</b>\n"
        for t in overdue[:3]:
            text += f"  • {t['title']}\n"
        text += "\n"

    if today_tasks:
        text += f"📋 <b>На сегодня ({len(today_tasks)}):</b>\n"
        for t in today_tasks:
            em = _priority_emoji(t["priority"])
            text += f"  {em} {t['title']}"
            if t.get("assignee"):
                text += f" → {t['assignee']}"
            text += "\n"
    elif not meetings and not overdue:
        text += "🎉 Свободный день!"

    await msg.answer(text, parse_mode="HTML")


@router.message(Command("summary"))
async def cmd_summary(msg: Message):
    if msg.from_user.id != OWNER_ID:
        return
    s = get_summary(OWNER_ID)
    await msg.answer(
        "📊 <b>Статистика</b>\n\n"
        f"📋 Открытых: <b>{s['total_open']}</b>\n"
        f"🚨 Просрочено: <b>{s['overdue']}</b>\n"
        f"📅 На сегодня: <b>{s['due_today']}</b>\n"
        f"✅ Закрыто за неделю: <b>{s['done_this_week']}</b>",
        parse_mode="HTML"
    )


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
        "<b>Задачи:</b>\n"
        "/tasks — открытые задачи\n"
        "/done_ID — закрыть задачу\n"
        "/summary — статистика\n"
        "/report — еженедельный отчёт\n\n"
        "<b>Встречи:</b>\n"
        "/meetings — предстоящие встречи\n"
        "/today — сегодняшний день\n\n"
        "<b>Автоматически:</b>\n"
        "• Фиксирую задачи из твоих сообщений\n"
        "• Слежу за выполнением задач в чатах\n"
        "• Создаю встречи в Яндекс Календаре\n"
        "• После подтверждения встречи — оповещаю участника в чате\n"
        "• Напоминаю за 24ч до дедлайна",
        parse_mode="HTML"
    )


# ── Task callbacks ────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("done_"))
async def cb_done(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return
    task_id = int(call.data.split("_")[1])
    task = get_task(task_id)
    if not task:
        await call.answer("Задача не найдена")
        return
    close_task(task_id)
    await call.message.edit_text(
        f"✅ <b>Выполнено:</b> {task['title']}",
        parse_mode="HTML"
    )
    await call.answer("Задача закрыта!")


@router.callback_query(F.data.startswith("snooze_"))
async def cb_snooze(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return
    parts = call.data.split("_")
    task_id = int(parts[1])
    task = get_task(task_id)
    if not task:
        await call.answer("Задача не найдена")
        return
    from datetime import timedelta
    new_deadline = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d %H:%M")
    from database import get_conn
    with get_conn() as conn:
        conn.execute("UPDATE tasks SET deadline=? WHERE id=?", (new_deadline, task_id))
        conn.commit()
    await call.answer("⏰ Отложено на завтра")


@router.callback_query(F.data.startswith("detail_"))
async def cb_detail(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return
    task_id = int(call.data.split("_")[1])
    task = get_task(task_id)
    if not task:
        await call.answer("Задача не найдена")
        return
    await call.message.answer(format_task_full(task), parse_mode="HTML")
    await call.answer()


@router.message(F.text.regexp(r"^/done_(\d+)$"))
async def cmd_done(msg: Message):
    if msg.from_user.id != OWNER_ID:
        return
    task_id = int(re.match(r"^/done_(\d+)$", msg.text).group(1))
    task = get_task(task_id)
    if not task:
        await msg.answer(f"❌ Задача #{task_id} не найдена.")
        return
    close_task(task_id)
    await msg.answer(f"✅ Закрыта: <b>{task['title']}</b>", parse_mode="HTML")


# ── Meeting callbacks ─────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("meet_confirm_"))
async def cb_meet_confirm(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return
    meeting_id = int(call.data.split("_")[2])
    meeting = get_meeting(meeting_id)
    if not meeting:
        await call.answer("Встреча не найдена")
        return

    await call.answer("⏳ Создаю событие...")

    participants = meeting.get("participants", "").split(", ") if meeting.get("participants") else []

    result = await create_event(
        title=meeting["title"],
        date=meeting["date"],
        time=meeting["time"],
        duration_min=meeting.get("duration_min", 60),
        description=meeting.get("description", ""),
        location=meeting.get("location", ""),
        participants=participants
    )

    if result["success"]:
        update_meeting(meeting_id, status="confirmed", calendar_uid=result.get("uid"))

        dt = datetime.strptime(f"{meeting['date']} {meeting['time']}", "%Y-%m-%d %H:%M")
        await call.message.edit_text(
            f"✅ <b>Встреча создана в Яндекс Календаре!</b>\n\n"
            f"📝 {meeting['title']}\n"
            f"🗓 {dt.strftime('%d %B %Y, %H:%M')}\n"
            + (f"📍 {meeting['location']}\n" if meeting.get("location") else ""),
            parse_mode="HTML"
        )

        # ── НОВОЕ: Уведомить участника в исходном чате ──
        source_chat_id = meeting.get("source_chat_id")
        proposed_by = meeting.get("proposed_by", "")
        proposed_by_id = meeting.get("proposed_by_id")

        if source_chat_id and source_chat_id != OWNER_ID:
            try:
                confirm_text = (
                    f"✅ @{proposed_by} встреча подтверждена!\n\n"
                    f"📝 <b>{meeting['title']}</b>\n"
                    f"🗓 {dt.strftime('%d %B %Y, %H:%M')}"
                )
                if meeting.get("location"):
                    confirm_text += f"\n📍 {meeting['location']}"

                await bot.send_message(
                    source_chat_id,
                    confirm_text,
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.error(f"Failed to notify source chat: {e}")

    else:
        await call.message.edit_text(
            f"❌ Не удалось создать событие:\n<code>{result['error']}</code>",
            parse_mode="HTML"
        )


@router.callback_query(F.data.startswith("meet_cancel_"))
async def cb_meet_cancel(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return
    meeting_id = int(call.data.split("_")[2])
    meeting = get_meeting(meeting_id)
    update_meeting(meeting_id, status="cancelled")
    await call.message.edit_text("❌ Встреча отменена.")

    # Уведомить участника об отмене
    source_chat_id = meeting.get("source_chat_id") if meeting else None
    proposed_by = meeting.get("proposed_by", "") if meeting else ""
    if source_chat_id and source_chat_id != OWNER_ID:
        try:
            await bot.send_message(
                source_chat_id,
                f"❌ @{proposed_by}, к сожалению, в это время не получится. Предложи другое время."
            )
        except Exception as e:
            logger.error(f"Failed to notify cancel: {e}")
    await call.answer()


@router.callback_query(F.data.startswith("meet_alts_"))
async def cb_meet_alts(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return
    meeting_id = int(call.data.split("_")[2])
    meeting = get_meeting(meeting_id)
    if not meeting or not meeting.get("date"):
        await call.answer("Нет даты для поиска слотов")
        return

    await call.answer("🔍 Ищу свободные слоты...")
    free_slots = await get_free_slots(meeting["date"], meeting.get("duration_min", 60))
    proposed = f"{meeting['date']} {meeting.get('time', '')}".strip()
    alts = await suggest_alternatives(free_slots, proposed)

    if not alts:
        # Generate fallback alternatives without calendar
        from datetime import datetime, timedelta
        alts = []
        base = datetime.now()
        for i in range(1, 5):
            dt = base + timedelta(days=i)
            if dt.weekday() < 5:
                alts.append({
                    "date": dt.strftime("%Y-%m-%d"),
                    "time": "14:00",
                    "label": dt.strftime("%d %B в 14:00")
                })
            if len(alts) == 3:
                break

    meeting_state[call.from_user.id] = {"meeting_id": meeting_id, "alternatives": alts}
    await call.message.edit_text(
        "🔄 <b>Альтернативные слоты:</b>\n\nВыбери удобное время:",
        parse_mode="HTML",
        reply_markup=meeting_alt_keyboard(meeting_id, alts)
    )


@router.callback_query(F.data.startswith("meet_alt_"))
async def cb_meet_alt_select(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return
    parts = call.data.split("_")
    meeting_id = int(parts[2])
    alt_idx = int(parts[3])
    state = meeting_state.get(call.from_user.id, {})
    alts = state.get("alternatives", [])
    if alt_idx >= len(alts):
        await call.answer("Слот не найден")
        return
    alt = alts[alt_idx]
    update_meeting(meeting_id, date=alt["date"], time=alt["time"])
    meeting = get_meeting(meeting_id)
    await call.message.edit_text(
        format_event_card(dict(meeting)),
        parse_mode="HTML",
        reply_markup=meeting_confirm_keyboard(meeting_id)
    )
    await call.answer(f"Выбрано: {alt['label']}")


@router.callback_query(F.data.startswith("meet_edittime_"))
async def cb_meet_edittime(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return
    meeting_id = int(call.data.split("_")[2])
    meeting_state[call.from_user.id] = {
        "meeting_id": meeting_id,
        "missing": [],
        "current_field": "time"
    }
    await call.message.answer("🕐 Введи новое время в формате ЧЧ:ММ (например: 15:30)")
    await call.answer()


@router.callback_query(F.data.startswith("meet_back_"))
async def cb_meet_back(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return
    meeting_id = int(call.data.split("_")[2])
    meeting = get_meeting(meeting_id)
    if not meeting:
        await call.answer()
        return
    await call.message.edit_text(
        format_event_card(dict(meeting)),
        parse_mode="HTML",
        reply_markup=meeting_confirm_keyboard(meeting_id)
    )
    await call.answer()


# ── Meeting dialog ────────────────────────────────────────────────────────────

async def _ask_next_field(user_id, meeting_id, missing):
    if not missing:
        meeting = get_meeting(meeting_id)
        await bot.send_message(
            user_id,
            format_event_card(dict(meeting)),
            parse_mode="HTML",
            reply_markup=meeting_confirm_keyboard(meeting_id)
        )
        meeting_state.pop(user_id, None)
        return
    field = missing[0]
    question = FIELD_QUESTIONS.get(field, f"Уточни: {field}")
    meeting_state[user_id] = {
        "meeting_id": meeting_id,
        "missing": missing[1:],
        "current_field": field
    }
    await bot.send_message(user_id, question)


# ── Main message handler ──────────────────────────────────────────────────────

@router.message(F.text)
async def handle_message(msg: Message):
    if not msg.text or not msg.from_user:
        return

    sender_id = msg.from_user.id
    sender_name = msg.from_user.full_name or str(sender_id)
    chat_id = msg.chat.id
    text = msg.text.strip()

    _add_context(chat_id, sender_name, text)

    # ── Meeting dialog (clarifying questions) ──
    if sender_id == OWNER_ID and sender_id in meeting_state:
        state = meeting_state[sender_id]
        if "current_field" in state:
            field = state["current_field"]
            meeting_id = state["meeting_id"]
            missing = state["missing"]
            update_meeting(meeting_id, **{field: text})
            await _ask_next_field(sender_id, meeting_id, missing)
            return

    # ── Messages from other users in group chats ──
    if sender_id != OWNER_ID:
        if msg.chat.type in ("group", "supergroup"):

            # НОВОЕ: Проверяем выполнение задач из этого чата
            open_tasks = get_open_tasks_for_chat(chat_id)
            if open_tasks:
                result = await detect_completion(text, sender_name, open_tasks)
                if result.get("is_completion") and result.get("confidence", 0) >= 0.8:
                    task_id = result.get("task_id")
                    if task_id:
                        task = get_task(task_id)
                        if task and task["status"] == "open":
                            close_task(task_id)
                            logger.info(f"Auto-closed task #{task_id} based on message from {sender_name}")
                            await bot.send_message(
                                OWNER_ID,
                                f"✅ <b>Задача автоматически закрыта</b>\n\n"
                                f"📋 {task['title']}\n"
                                f"👤 {sender_name} написал в чате:\n"
                                f"<i>«{text[:150]}»</i>",
                                parse_mode="HTML"
                            )
                            return

            # Проверяем предложение встречи
            meet_result = await classify_meeting(text)
            if meet_result.get("is_meeting") and meet_result.get("confidence", 0) > 0.7:
                meeting = await extract_meeting(text)
                meeting["proposed_by"] = sender_name

                free_slots = []
                if meeting.get("date"):
                    free_slots = await get_free_slots(meeting["date"], meeting.get("duration_min", 60))

                proposed_time = f"{meeting.get('date', '')} {meeting.get('time', '')}".strip()
                alts = await suggest_alternatives(free_slots, proposed_time) if free_slots else []

                # Сохраняем встречу с источником
                meeting_id = save_meeting(
                    owner_id=OWNER_ID,
                    title=meeting.get("title", "Встреча"),
                    date=meeting.get("date"),
                    time=meeting.get("time"),
                    duration_min=meeting.get("duration_min", 60),
                    location=meeting.get("location"),
                    participants=meeting.get("participants", []),
                    description=meeting.get("description"),
                    proposed_by=sender_name,
                    source_msg=text[:200],
                    source_chat_id=chat_id,        # ← сохраняем чат
                    source_msg_id=msg.message_id,  # ← сохраняем ID сообщения
                    proposed_by_id=sender_id        # ← сохраняем ID участника
                )

                notify_text = (
                    f"📅 <b>{sender_name}</b> предлагает встречу:\n\n"
                    f"💬 <i>{text[:200]}</i>\n\n"
                    f"📝 <b>{meeting.get('title', 'Встреча')}</b>\n"
                )
                if proposed_time.strip():
                    notify_text += f"🕐 Предложенное время: {proposed_time}\n"

                await bot.send_message(OWNER_ID, notify_text, parse_mode="HTML")

                if alts:
                    alts_text = "🔄 <b>Свободные слоты из твоего календаря:</b>\n\n"
                    for alt in alts[:3]:
                        alts_text += f"  • {alt['label']}\n"
                    await bot.send_message(
                        OWNER_ID, alts_text,
                        parse_mode="HTML",
                        reply_markup=meeting_alt_keyboard(meeting_id, alts)
                    )
                else:
                    await bot.send_message(
                        OWNER_ID,
                        format_event_card(meeting),
                        parse_mode="HTML",
                        reply_markup=meeting_confirm_keyboard(meeting_id)
                    )
        return

    # ── Owner messages ──

    # Проверка встречи
    # Lower threshold for forwarded messages
    is_forwarded = bool(msg.forward_date or msg.forward_from or msg.forward_sender_name)
    meet_threshold = 0.55 if is_forwarded else 0.75
    meet_result = await classify_meeting(text)
    if meet_result.get("is_meeting") and meet_result.get("confidence", 0) > meet_threshold:
        meeting = await extract_meeting(text)
        meeting_id = save_meeting(
            owner_id=OWNER_ID,
            title=meeting.get("title", "Встреча"),
            date=meeting.get("date"),
            time=meeting.get("time"),
            duration_min=meeting.get("duration_min", 60),
            location=meeting.get("location"),
            participants=meeting.get("participants", []),
            description=meeting.get("description"),
            proposed_by=meeting.get("proposed_by"),
            source_msg=text[:200],
            source_chat_id=chat_id
        )
        missing = get_missing_fields(meeting)
        if missing:
            first_q = FIELD_QUESTIONS.get(missing[0], f"Уточни: {missing[0]}")
            meeting_state[sender_id] = {
                "meeting_id": meeting_id,
                "missing": missing[1:],
                "current_field": missing[0]
            }
            await msg.answer(
                f"📅 Вижу встречу: <b>{meeting.get('title', 'Встреча')}</b>\n\n{first_q}",
                parse_mode="HTML"
            )
        else:
            await bot.send_message(
                OWNER_ID,
                format_event_card(meeting),
                parse_mode="HTML",
                reply_markup=meeting_confirm_keyboard(meeting_id)
            )
        return

    # Проверка задачи
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

    for t in tasks:
        task_id = add_task(
            chat_id=OWNER_ID,
            title=t.get("title", "Без названия"),
            description=t.get("description"),
            assignee=t.get("assignee"),
            deadline=t.get("deadline"),
            priority=t.get("priority", "medium"),
            source_msg=text[:200],
            source_chat_id=chat_id
        )
        em = _priority_emoji(t.get("priority", "medium"))
        reply = f"✅ Задача зафиксирована {em}\n<b>{t['title']}</b>"
        if t.get("deadline"):
            reply += f"\n📅 {t['deadline']}"
        if t.get("assignee"):
            reply += f"\n👤 {t['assignee']}"

        await bot.send_message(
            OWNER_ID, reply,
            parse_mode="HTML",
            reply_markup=task_keyboard(task_id)
        )


async def main():
    init_db()
    dp.include_router(router)
    setup_scheduler(bot, OWNER_ID)
    logger.info(f"Dobrynya AI v3 started. Owner: {OWNER_ID}")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
