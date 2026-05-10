import json
import re
import os
from datetime import datetime
from anthropic import Anthropic

client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

CLASSIFY_PROMPT = """Ты анализируешь сообщения из Telegram, включая пересланные переписки.

Определи: содержит ли сообщение или переписка информацию о встрече/созвоне/звонке/митинге?

Признаки встречи:
- Договорённость о встрече или созвоне
- Упоминание конкретного времени встречи
- Слова: встреча, созвон, звонок, митинг, переговоры, обсудим, в 13:30, в 14:00
- Утверждение времени ("в 13:30 нормально", "отлично утвердили")
- Пересланная переписка где стороны договорились о времени

Отвечай ТОЛЬКО JSON:
{"is_meeting": true/false, "confidence": 0.0-1.0}
"""

EXTRACT_PROMPT = """Ты извлекаешь параметры встречи из сообщения или пересланной переписки.

Сегодня: {today}

Извлеки:
- title: тема встречи (краткое название, если не указано — придумай по контексту)
- date: дата в формате YYYY-MM-DD или null
- time: время начала HH:MM или null
- duration_min: длительность в минутах (по умолчанию 60)
- participants: список участников (имена или @username) или []
- location: место или ссылка (Zoom/Teams/очно/адрес) или null
- description: дополнительные детали или null
- proposed_by: кто предложил встречу или null

Если дата указана как "13 мая", "13.05" — переведи в YYYY-MM-DD используя текущий год.
Если время указано как "13:30", "13.30" — переведи в HH:MM.

Отвечай ТОЛЬКО валидным JSON без пояснений.
"""

ALTERNATIVES_PROMPT = """Предложи 3 альтернативных времени для встречи.
Свободные слоты: {free_slots}
Предложенное время: {proposed_time}

Выбери слоты близкие к предложенному времени.

Отвечай ТОЛЬКО JSON-массивом:
[
  {{"date": "YYYY-MM-DD", "time": "HH:MM", "label": "среда, 13 мая в 14:00"}},
  {{"date": "YYYY-MM-DD", "time": "HH:MM", "label": "среда, 13 мая в 15:00"}},
  {{"date": "YYYY-MM-DD", "time": "HH:MM", "label": "четверг, 14 мая в 13:00"}}
]
"""


async def classify_message(text: str) -> dict:
    """Determine if message contains a meeting."""
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=100,
        system=CLASSIFY_PROMPT,
        messages=[{"role": "user", "content": text}]
    )
    raw = response.content[0].text.strip()
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
    try:
        return json.loads(raw)
    except Exception:
        return {"is_meeting": False, "confidence": 0.0}


async def extract_meeting(text: str) -> dict:
    """Extract meeting parameters from message."""
    today = datetime.now().strftime("%Y-%m-%d (%A, %d %B %Y)")
    system = EXTRACT_PROMPT.format(today=today)

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=500,
        system=system,
        messages=[{"role": "user", "content": text}]
    )
    raw = response.content[0].text.strip()
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
    try:
        return json.loads(raw)
    except Exception:
        return {}


def get_missing_fields(meeting: dict) -> list:
    missing = []
    if not meeting.get("date"):
        missing.append("date")
    if not meeting.get("time"):
        missing.append("time")
    if not meeting.get("title"):
        missing.append("title")
    return missing


FIELD_QUESTIONS = {
    "date": "📅 На какую дату назначить встречу?",
    "time": "🕐 В какое время?",
    "title": "📝 Как назвать встречу?",
    "duration_min": "⏱ Сколько времени займёт? (в минутах)",
    "location": "📍 Где встретимся? (адрес, Zoom, Teams или другое)",
}


async def suggest_alternatives(free_slots: list, proposed_time: str) -> list:
    """Suggest alternative meeting times."""
    if not free_slots:
        # If no free slots from calendar — generate reasonable alternatives
        try:
            from datetime import datetime, timedelta
            base = datetime.now()
            alts = []
            for i in range(1, 4):
                dt = base + timedelta(days=i)
                if dt.weekday() < 5:  # Skip weekends
                    alts.append({
                        "date": dt.strftime("%Y-%m-%d"),
                        "time": "14:00",
                        "label": dt.strftime("%A, %d %B в 14:00")
                    })
            return alts[:3]
        except Exception:
            return []

    slots_str = "\n".join([f"- {s}" for s in free_slots[:10]])
    prompt = ALTERNATIVES_PROMPT.format(
        free_slots=slots_str,
        proposed_time=proposed_time
    )

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=400,
        system="Отвечай только JSON без пояснений.",
        messages=[{"role": "user", "content": prompt}]
    )
    raw = response.content[0].text.strip()
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
    try:
        return json.loads(raw)
    except Exception:
        return []
