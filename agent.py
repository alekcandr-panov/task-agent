import json
import re
import os
from datetime import datetime
from anthropic import Anthropic

client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

SYSTEM_PROMPT = """Ты — агент-помощник, который анализирует сообщения в рабочих Telegram-чатах.

Твоя задача: найти задачи, которые ПОСТАВИЛ владелец бота (его Telegram ID передаётся в запросе).

Правила:
- Фиксируй ВСЕ задачи владельца, даже если нет дедлайна, исполнителя или деталей.
- Фиксируй задачи которые владелец ставит другим людям И задачи которые он берёт на себя.
- Задача — это любое конкретное действие, поручение, просьба, договорённость.
- Если дедлайна нет — оставь deadline null. НЕ придумывай дедлайн.
- Дедлайн возвращай в ISO формате YYYY-MM-DD HH:MM или YYYY-MM-DD.
- Сегодняшняя дата передаётся в запросе — используй её для расчёта относительных дат.
- Приоритет: high (срочно/важно), medium (обычная), low (когда будет время).
- assignee — кому поставлена задача. Если владелец берёт на себя — пиши "Я (владелец)".
- title — конкретный глагол + объект: "Подготовить X", "Отправить Y", "Созвониться с Z".
- description — любые важные детали из сообщения.

НЕ фиксируй: общие разговоры, вопросы без действия, обсуждения без поручений.

Отвечай ТОЛЬКО валидным JSON-массивом. Без пояснений, без markdown.

Формат:
{
  "title": "Подготовить презентацию для клиента",
  "description": "детали или null",
  "assignee": "Иван или @username или Я (владелец)",
  "deadline": "2025-05-16 18:00" или null,
  "priority": "high|medium|low"
}

Если задач нет — верни: []
"""


async def extract_tasks(message_text: str, owner_id: int,
                        sender_id: int, sender_name: str,
                        chat_context: list[str] | None = None) -> list[dict]:
    today = datetime.now().strftime("%Y-%m-%d (%A)")
    context_block = ""
    if chat_context:
        context_block = "Контекст предыдущих сообщений:\n" + "\n".join(chat_context[-10:]) + "\n\n"

    user_prompt = (
        f"Сегодня: {today}\n"
        f"ID владельца бота: {owner_id}\n"
        f"Автор текущего сообщения: {sender_name} (ID: {sender_id})\n\n"
        f"{context_block}"
        f"Текущее сообщение:\n{message_text}"
    )

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}]
    )

    raw = response.content[0].text.strip()
    raw = re.sub(r"^```(?:json)?", "", raw, flags=re.MULTILINE).strip()
    raw = re.sub(r"```$", "", raw, flags=re.MULTILINE).strip()

    try:
        tasks = json.loads(raw)
        if not isinstance(tasks, list):
            return []
        return tasks
    except json.JSONDecodeError:
        return []


async def classify_message(text: str) -> bool:
    keywords = [
        "сделай", "сделать", "нужно", "надо", "подготовь", "подготовить",
        "отправь", "отправить", "проверь", "проверить", "созвонись", "созвониться",
        "напиши", "написать", "договорись", "договориться", "обнови", "обновить",
        "исправь", "исправить", "организуй", "организовать", "пришли", "прислать",
        "до ", "дедлайн", "deadline", "к пятнице", "к понедельнику", "к среде",
        "к четвергу", "к вторнику", "к завтра", "завтра до", "срочно", "asap",
        "не забудь", "задача", "поручаю", "прошу", "подготовьте", "сделайте",
        "к концу", "возьми", "займись", "разберись", "уточни", "согласуй",
        "на этой неделе", "сегодня до", "до конца дня", "можешь", "можете",
    ]
    text_lower = text.lower()
    return any(kw in text_lower for kw in keywords)
