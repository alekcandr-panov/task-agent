from datetime import datetime


def _priority_emoji(p: str) -> str:
    return {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(p, "⚪")


def _deadline_str(d: str | None) -> str:
    if not d:
        return "без срока"
    try:
        dt = datetime.fromisoformat(d)
        now = datetime.now()
        diff = (dt - now).days
        if diff < 0:
            return f"просрочено ({d})"
        elif diff == 0:
            return f"сегодня {dt.strftime('%H:%M')}"
        elif diff == 1:
            return f"завтра {dt.strftime('%H:%M')}"
        else:
            return dt.strftime("%d.%m %H:%M") if " " in d else dt.strftime("%d.%m.%Y")
    except Exception:
        return d


def format_task_short(task: dict) -> str:
    em = _priority_emoji(task["priority"])
    deadline = _deadline_str(task.get("deadline"))
    assignee = f" → {task['assignee']}" if task.get("assignee") else ""
    return f"{em} [#{task['id']}] {task['title']}{assignee} | {deadline}"


def format_task_full(task: dict) -> str:
    em = _priority_emoji(task["priority"])
    lines = [
        f"{em} <b>{task['title']}</b>  <code>#{task['id']}</code>",
    ]
    if task.get("description"):
        lines.append(f"📝 {task['description']}")
    if task.get("assignee"):
        lines.append(f"👤 Ответственный: {task['assignee']}")
    lines.append(f"📅 Срок: {_deadline_str(task.get('deadline'))}")
    if task.get("source_msg"):
        lines.append(f"💬 <i>{task['source_msg'][:80]}…</i>" if len(task["source_msg"]) > 80 else f"💬 <i>{task['source_msg']}</i>")
    lines.append(f"\n/done_{task['id']} — выполнено")
    return "\n".join(lines)


def format_task_list(tasks: list, title: str = "Открытые задачи") -> str:
    if not tasks:
        return f"✅ Нет задач в категории «{title}»"

    # Group by priority
    high = [t for t in tasks if t["priority"] == "high"]
    medium = [t for t in tasks if t["priority"] == "medium"]
    low = [t for t in tasks if t["priority"] == "low"]

    lines = [f"📋 <b>{title}</b> ({len(tasks)} шт.)\n"]

    if high:
        lines.append("🔴 <b>Высокий приоритет</b>")
        for t in high:
            lines.append(f"  • {format_task_short(t)}")
        lines.append("")

    if medium:
        lines.append("🟡 <b>Средний приоритет</b>")
        for t in medium:
            lines.append(f"  • {format_task_short(t)}")
        lines.append("")

    if low:
        lines.append("🟢 <b>Низкий приоритет</b>")
        for t in low:
            lines.append(f"  • {format_task_short(t)}")

    lines.append("\n<i>Нажми /done_ID чтобы закрыть задачу</i>")
    return "\n".join(lines)
