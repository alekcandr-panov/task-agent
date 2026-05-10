import json
import re
import os
from datetime import datetime
from anthropic import Anthropic

client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

CLASSIFY_PROMPT = """Ты анализируешь сообщения из Telegram.

Определи: содержит ли сообщение предложение встречи/созвона/звонка/митинга?

Признаки встречи:
- Предложение встретиться, созвониться, поговорить
- Упоминание времени + участников
- Слова: встреча, созвон, звонок, митинг, переговоры, обсудим, meet, call, zoom, teams

Отвечай ТОЛЬКО JSON:
{"is_meeting": true/false, "confidence": 0.0-1.0}
"""

EXTRACT_PROMPT = """Ты извлекаешь параметры встречи из сообщения.

Сегодня: {today}

Извлеки:
- title: тема встречи (краткое название)
- date: дата в формате YYYY-MM-DD или null
- time: время начала HH:MM или null  
- duration_min: длительность в минутах (по умолчанию 60) или null
- participants: список участников (имена или @username) или []
- location: место или ссылка (Zoom/Teams/адрес) или null
- description: дополнительные детали или null
- proposed_by: кто предложил встречу (имя/username) или null

Отвечай ТОЛЬКО валидным JSON без пояснений.
"""

ALTERNATIVES_PROMPT = """Тебе предложили встречу в определённое время.
Твои свободные слоты: {free_slots}
Предложенное время: {proposed_time}

Предложи 3 альтернативных времени которые:
1. Близки к предложенному
2. Входят в свободные слоты
3. Логичны для рабочего расписания

Отвечай ТОЛЬКО JSON-массивом:
[
  {"date": "YYYY-MM-DD", "time": "HH:MM", "label": "понедельник, 15 мая в 14:00"},
  ...
]
"""


async def classify_message(text: str) -> dict:
    """Determine if message contains a meeting proposal."""
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


def get_missing_fields(meeting: dict) -> list[str]:
    """Return list of missing required fields."""
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
    """Suggest alternative meeting times based on free slots."""
    if not free_slots:
        return []

    slots_str = "\n".join([f"- {s}" for s in free_slots[:10]])
    system = ALTERNATIVES_PROMPT.format(
        free_slots=slots_str,
        proposed_time=proposed_time
    )

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=300,
        system="Отвечай только JSON без пояснений.",
        messages=[{"role": "user", "content": system}]
    )
    raw = response.content[0].text.strip()
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
    try:
        return json.loads(raw)
    except Exception:
        return []
