import json
import re
import os
from anthropic import Anthropic

client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

SYSTEM_PROMPT = """Ты — агент-помощник, который анализирует сообщения в рабочих Telegram-чатах.

Твоя задача: найти задачи, которые ПОСТАВИЛ владелец бота (его Telegram ID передаётся в запросе).

Правила:
- Фиксируй только задачи, которые ставит владелец. Задачи от других людей игнорируй.
- Извлекай конкретные действия, а не общие разговоры.
- Если дедлайна нет явно — оставь deadline null.
- Дедлайн возвращай в ISO формате YYYY-MM-DD HH:MM или YYYY-MM-DD.
- Приоритет: high / medium / low. Угадывай по тональности.
- assignee — кому поставлена задача (имя или @username). Если себе — null.

Отвечай ТОЛЬКО валидным JSON-массивом задач. Без пояснений, без markdown.

Формат каждой задачи:
{
  "title": "краткое название",
  "description": "детали задачи",
  "assignee": "@username или имя или null",
  "deadline": "2025-05-15 18:00" или null,
  "priority": "high|medium|low"
}

Если задач нет — верни пустой массив: []
"""


async def extract_tasks(message_text: str, owner_id: int,
                        sender_id: int, sender_name: str,
                        chat_context: list[str] | None = None) -> list[dict]:
    """
    Extract tasks from a message. Only tasks set by owner_id are captured.
    chat_context: last N messages for context (optional).
    """
    context_block = ""
    if chat_context:
        context_block = "Контекст предыдущих сообщений:\n" + "\n".join(chat_context[-10:]) + "\n\n"

    user_prompt = (
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

    # Strip possible markdown fences
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
    """
    Quick pre-filter: does this message likely contain a task?
    Uses a small heuristic to avoid calling Claude for every message.
    """
    keywords = [
        "сделай", "сделать", "нужно", "надо", "подготовь", "подготовить",
        "отправь", "отправить", "проверь", "проверить", "созвонись",
        "напиши", "написать", "договорись", "обновить", "исправить",
        "до ", "дедлайн", "deadline", "к пятнице", "к понедельнику",
        "к завтра", "срочно", "asap", "не забудь", "задача", "поручаю",
        "прошу", "подготовьте", "сделайте", "к концу"
    ]
    text_lower = text.lower()
    return any(kw in text_lower for kw in keywords)
