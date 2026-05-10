import re
import os
from anthropic import Anthropic

client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

COMPLETION_PROMPT = """Ты анализируешь сообщения в рабочем Telegram-чате.

Определи: сообщает ли автор о выполнении какой-либо задачи?

Признаки выполнения:
- "сделал", "готово", "выполнено", "отправил", "подготовил", "завершил"
- "всё готово", "сделано", "закончил", "успел", "выслал", "загрузил"
- "готов отчёт", "презентация готова", "договорился"
- "done", "completed", "finished", "sent", "ready"

Не считать выполнением:
- вопросы ("когда сделаешь?")
- обещания ("сделаю завтра")
- обсуждения без факта выполнения

Список открытых задач из этого чата:
{tasks_list}

Если сообщение говорит о выполнении — найди соответствующую задачу из списка.

Отвечай ТОЛЬКО JSON:
{{
  "is_completion": true/false,
  "task_id": 123 или null,
  "confidence": 0.0-1.0,
  "reason": "краткое объяснение"
}}
"""


async def detect_completion(message_text: str, sender_name: str,
                            open_tasks: list) -> dict:
    """
    Detect if a message indicates task completion.
    Returns dict with is_completion, task_id, confidence.
    """
    if not open_tasks:
        return {"is_completion": False, "task_id": None, "confidence": 0.0}

    # Quick keyword pre-filter
    completion_keywords = [
        "сделал", "готово", "выполнено", "отправил", "подготовил",
        "завершил", "закончил", "успел", "выслал", "загрузил",
        "готов", "готова", "сделано", "договорился", "согласовал",
        "done", "completed", "finished", "sent", "ready", "ok", "ок",
        "✅", "☑️", "✓"
    ]
    text_lower = message_text.lower()
    if not any(kw in text_lower for kw in completion_keywords):
        return {"is_completion": False, "task_id": None, "confidence": 0.0}

    # Build task list for prompt
    tasks_list = "\n".join([
        f"ID {t['id']}: {t['title']}"
        + (f" (исполнитель: {t['assignee']})" if t.get('assignee') else "")
        for t in open_tasks
    ])

    user_msg = f"Автор сообщения: {sender_name}\nСообщение: {message_text}"
    system = COMPLETION_PROMPT.format(tasks_list=tasks_list)

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            system=system,
            messages=[{"role": "user", "content": user_msg}]
        )
        import json, re as re_mod
        raw = response.content[0].text.strip()
        raw = re_mod.sub(r"^```(?:json)?|```$", "", raw, flags=re_mod.MULTILINE).strip()
        return json.loads(raw)
    except Exception:
        return {"is_completion": False, "task_id": None, "confidence": 0.0}
