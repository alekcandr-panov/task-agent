import os
import uuid
from datetime import datetime, timedelta
from typing import Optional
import httpx

CALDAV_URL = os.getenv("YANDEX_CALDAV_URL", "https://caldav.yandex.ru")
YANDEX_LOGIN = os.getenv("YANDEX_LOGIN", "")
YANDEX_PASSWORD = os.getenv("YANDEX_APP_PASSWORD", "")


def _auth():
    return (YANDEX_LOGIN, YANDEX_PASSWORD)


def _headers():
    return {
        "Content-Type": "application/xml; charset=utf-8",
        "Depth": "1",
    }


def _make_vcal(title: str, date: str, time: str,
               duration_min: int = 60,
               description: str = "",
               location: str = "",
               participants: list = None) -> tuple:
    uid = str(uuid.uuid4())
    start_dt = datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M")
    end_dt = start_dt + timedelta(minutes=duration_min)

    dtstart = start_dt.strftime("%Y%m%dT%H%M%S")
    dtend = end_dt.strftime("%Y%m%dT%H%M%S")
    dtstamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")

    attendees = ""
    if participants:
        for p in participants:
            clean = p.lstrip("@")
            attendees += f"ATTENDEE;CN={clean}:mailto:{clean}@example.com\n"

    vcal = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//DobryniaBot//RU
BEGIN:VEVENT
UID:{uid}
DTSTAMP:{dtstamp}
DTSTART:{dtstart}
DTEND:{dtend}
SUMMARY:{title}
DESCRIPTION:{description}
LOCATION:{location}
{attendees}END:VEVENT
END:VCALENDAR""".strip()

    return vcal, uid


async def create_event(title: str, date: str, time: str,
                       duration_min: int = 60,
                       description: str = "",
                       location: str = "",
                       participants: list = None) -> dict:
    """Create event in Yandex Calendar via CalDAV."""
    if not YANDEX_LOGIN or not YANDEX_PASSWORD:
        return {"success": False, "error": "Яндекс Календарь не настроен. Добавь YANDEX_LOGIN и YANDEX_APP_PASSWORD в .env"}

    vcal_str, uid = _make_vcal(
        title, date, time, duration_min,
        description, location, participants or []
    )

    calendar_url = f"{CALDAV_URL}/calendars/{YANDEX_LOGIN}/events-default/{uid}.ics"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.put(
                calendar_url,
                content=vcal_str.encode("utf-8"),
                headers={"Content-Type": "text/calendar; charset=utf-8"},
                auth=_auth()
            )
        if resp.status_code in (201, 204):
            return {"success": True, "uid": uid, "url": calendar_url}
        else:
            return {"success": False, "error": f"Ошибка CalDAV: {resp.status_code} {resp.text[:200]}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def get_free_slots(date: str, duration_min: int = 60) -> list:
    """Get free time slots for a given date from Yandex Calendar."""
    if not YANDEX_LOGIN or not YANDEX_PASSWORD:
        return []

    try:
        start_dt = datetime.strptime(date, "%Y-%m-%d").replace(hour=0, minute=0)
        end_dt = start_dt.replace(hour=23, minute=59)

        dtstart = start_dt.strftime("%Y%m%dT%H%M%SZ")
        dtend = end_dt.strftime("%Y%m%dT%H%M%SZ")

        report_xml = f"""<?xml version="1.0" encoding="utf-8"?>
<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:prop><d:getetag/><c:calendar-data/></d:prop>
  <c:filter>
    <c:comp-filter name="VCALENDAR">
      <c:comp-filter name="VEVENT">
        <c:time-range start="{dtstart}" end="{dtend}"/>
      </c:comp-filter>
    </c:comp-filter>
  </c:filter>
</c:calendar-query>"""

        calendar_url = f"{CALDAV_URL}/calendars/{YANDEX_LOGIN}/events-default/"

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.request(
                "REPORT",
                calendar_url,
                content=report_xml.encode("utf-8"),
                headers=_headers(),
                auth=_auth()
            )

        busy_times = _parse_busy_times(resp.text)
        free = _calculate_free_slots(busy_times, date, duration_min)
        return free

    except Exception as e:
        return []


def _parse_busy_times(caldav_response: str) -> list:
    import re
    busy = []
    events = re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", caldav_response, re.DOTALL)
    for event in events:
        start = re.search(r"DTSTART[^:]*:(\d{8}T\d{6})", event)
        end = re.search(r"DTEND[^:]*:(\d{8}T\d{6})", event)
        if start and end:
            try:
                s = datetime.strptime(start.group(1), "%Y%m%dT%H%M%S")
                e = datetime.strptime(end.group(1), "%Y%m%dT%H%M%S")
                busy.append((s, e))
            except Exception:
                pass
    return busy


def _calculate_free_slots(busy: list, date: str, duration_min: int) -> list:
    base = datetime.strptime(date, "%Y-%m-%d")
    work_start = base.replace(hour=9, minute=0)
    work_end = base.replace(hour=19, minute=0)

    slots = []
    current = work_start

    while current + timedelta(minutes=duration_min) <= work_end:
        slot_end = current + timedelta(minutes=duration_min)
        is_busy = any(
            not (slot_end <= b[0] or current >= b[1])
            for b in busy
        )
        if not is_busy:
            slots.append(current.strftime("%H:%M"))
        current += timedelta(minutes=30)

    return slots


def format_event_card(meeting: dict) -> str:
    """Format meeting confirmation card."""
    lines = ["📅 <b>Карточка встречи</b>\n"]
    lines.append(f"📝 <b>{meeting.get('title', 'Без названия')}</b>")

    date = meeting.get("date", "")
    time = meeting.get("time", "")
    if date and time:
        try:
            dt = datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M")
            lines.append(f"🗓 {dt.strftime('%d %B %Y, %H:%M')}")
        except Exception:
            lines.append(f"🗓 {date} {time}")

    duration = meeting.get("duration_min", 60)
    lines.append(f"⏱ {duration} минут")

    if meeting.get("location"):
        lines.append(f"📍 {meeting['location']}")

    if meeting.get("participants"):
        p = ", ".join(meeting["participants"]) if isinstance(meeting["participants"], list) else meeting["participants"]
        lines.append(f"👥 {p}")

    if meeting.get("description"):
        lines.append(f"💬 {meeting['description']}")

    if meeting.get("proposed_by"):
        lines.append(f"\n✉️ Предложил: {meeting['proposed_by']}")

    return "\n".join(lines)
