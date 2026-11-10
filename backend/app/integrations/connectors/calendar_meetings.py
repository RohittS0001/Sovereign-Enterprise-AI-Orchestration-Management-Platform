"""Calendars (Google, Outlook) and meeting transcripts (Zoom, Teams, Google Meet).

Calendar services return busy slots for people and events in a window; meeting services
list recent meetings with transcripts. Transcript downloads are restricted to the vendor's
own hosts (no fetching arbitrary URLs from API responses).
"""

import re
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from urllib.parse import quote, urlsplit

from backend.app.core.config import Settings
from backend.app.integrations.connectors.auth import (
    GoogleServiceAccountToken,
    OAuthToken,
    microsoft_token,
)
from backend.app.integrations.connectors.base import (
    BusySlot,
    CalendarEvent,
    ConnectorClient,
    ConnectorService,
    Transcript,
    safe_id,
    to_date,
)
from backend.app.integrations.connectors.spec import ConnectorSpec

EMAIL = re.compile(r"^[^@\s'\"<>]{1,64}@[A-Za-z0-9.-]{1,253}$")


def email(value: str) -> str:
    if not EMAIL.fullmatch(value.strip()):
        raise ValueError(f"Invalid email address: {value!r}")
    return value.strip()


def parse_dt(value: Any) -> datetime | None:
    """ISO datetime (Z or offset, Graph's 7-digit fractions) or a bare date → aware UTC."""
    if not value:
        return None
    if isinstance(value, dict):  # Google {"dateTime"|"date"}, Graph {"dateTime", "timeZone"}
        value = value.get("dateTime") or value.get("date")
        if not value:
            return None
    text = str(value).replace("Z", "+00:00")
    text = re.sub(r"(\.\d{6})\d+", r"\1", text)  # Graph: 0000000 fractional seconds
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def vtt_to_text(vtt: str) -> str:
    """WebVTT transcript → 'Speaker: words' lines (timestamps and cue numbers dropped)."""
    lines = []
    for raw in vtt.splitlines():
        line = raw.strip()
        if (
            not line
            or line == "WEBVTT"
            or "-->" in line
            or line.isdigit()
            or line.startswith("NOTE")
        ):
            continue
        line = re.sub(r"<v\s+([^>]+)>(.*?)(</v>)?$", r"\1: \2", line)
        lines.append(re.sub(r"<[^>]+>", "", line))
    return "\n".join(lines)


def free_slots(busy: list[BusySlot], start: datetime, end: datetime, minutes: int,
               day_start: int = 9, day_end: int = 18, limit: int = 10) -> list[dict[str, str]]:  # fmt: skip
    """Common free windows (weekdays, working hours UTC) of at least ``minutes``."""
    slots: list[dict[str, str]] = []
    intervals = sorted((b.start, b.end) for b in busy)
    day = start.date()
    while day <= end.date() and len(slots) < limit:
        if day.weekday() < 5:
            cursor = max(start, datetime.combine(day, time(day_start), UTC))
            stop = min(end, datetime.combine(day, time(day_end), UTC))
            for b_start, b_end in intervals:
                if b_end <= cursor or b_start >= stop:
                    continue
                if (b_start - cursor) >= timedelta(minutes=minutes):
                    slots.append({"start": cursor.isoformat(), "end": b_start.isoformat()})
                cursor = max(cursor, b_end)
            if stop - cursor >= timedelta(minutes=minutes):
                slots.append({"start": cursor.isoformat(), "end": stop.isoformat()})
        day += timedelta(days=1)
    return slots[:limit]


# ------------------------------------------------------------------ Google Calendar


class GoogleCalendarService(ConnectorService):
    source = "google_calendar"

    def __init__(self, client_factory: Any, calendar_id: str = "primary") -> None:
        super().__init__(client_factory)
        self.calendar_id = calendar_id

    async def free_busy(self, people: list[str], start: datetime, end: datetime) -> list[BusySlot]:
        body = await self.client.post("/calendar/v3/freeBusy", {
            "timeMin": start.isoformat(), "timeMax": end.isoformat(),
            "items": [{"id": email(p)} for p in people]})  # fmt: skip
        slots = []
        for person, calendar in (body.get("calendars") or {}).items():
            if calendar.get("errors"):
                raise ValueError(f"Google Calendar: no access to {person}")
            slots += [BusySlot(person=person, start=parse_dt(b["start"]), end=parse_dt(b["end"]))
                      for b in calendar.get("busy") or []]  # fmt: skip
        return slots

    async def list_events(
        self, start: datetime, end: datetime, limit: int = 50
    ) -> list[CalendarEvent]:
        body = await self.client.get(
            f"/calendar/v3/calendars/{quote(safe_id(self.calendar_id, 'calendar id'), safe='@')}/events",
            {"timeMin": start.isoformat(), "timeMax": end.isoformat(), "singleEvents": "true",
             "orderBy": "startTime", "maxResults": limit})  # fmt: skip
        return [CalendarEvent(source=self.source, id=e.get("id", ""), title=e.get("summary", "(no title)"),
                              start=parse_dt(e.get("start")), end=parse_dt(e.get("end")),
                              attendees=len(e.get("attendees") or []),
                              organizer=(e.get("organizer") or {}).get("displayName"))
                for e in body.get("items") or [] if parse_dt(e.get("start"))]  # fmt: skip

    async def health_check(self) -> dict[str, Any]:
        now = datetime.now(UTC)
        return {"events_next_day": len(await self.list_events(now, now + timedelta(days=1), 5))}


# ------------------------------------------------------------------ Outlook (Graph)


class OutlookCalendarService(ConnectorService):
    source = "outlook_calendar"

    def __init__(self, client_factory: Any, mailbox: str) -> None:
        super().__init__(client_factory)
        self.mailbox = email(mailbox)

    async def free_busy(self, people: list[str], start: datetime, end: datetime) -> list[BusySlot]:
        def utc(d: datetime) -> dict[str, str]:  # Graph wants a naive time plus a zone name
            return {
                "dateTime": d.astimezone(UTC).replace(tzinfo=None).isoformat(),
                "timeZone": "UTC",
            }

        body = await self.client.post(f"/users/{self.mailbox}/calendar/getSchedule", {
            "schedules": [email(p) for p in people], "startTime": utc(start), "endTime": utc(end),
            "availabilityViewInterval": 30})  # fmt: skip
        return [BusySlot(person=s.get("scheduleId", ""), start=parse_dt(i["start"]), end=parse_dt(i["end"]))
                for s in body.get("value") or [] for i in s.get("scheduleItems") or []
                if i.get("status") in {"busy", "tentative", "oof", "workingElsewhere"}]  # fmt: skip

    async def list_events(
        self, start: datetime, end: datetime, limit: int = 50
    ) -> list[CalendarEvent]:
        body = await self.client.get(f"/users/{self.mailbox}/calendarView", {
            "startDateTime": start.isoformat(), "endDateTime": end.isoformat(), "$top": limit,
            "$select": "id,subject,start,end,attendees,organizer", "$orderby": "start/dateTime"})  # fmt: skip
        return [CalendarEvent(source=self.source, id=e.get("id", ""), title=e.get("subject") or "(no title)",
                              start=parse_dt(e.get("start")), end=parse_dt(e.get("end")),
                              attendees=len(e.get("attendees") or []),
                              organizer=((e.get("organizer") or {}).get("emailAddress") or {}).get("name"))
                for e in body.get("value") or []]  # fmt: skip

    async def health_check(self) -> dict[str, Any]:
        now = datetime.now(UTC)
        return {"events_next_day": len(await self.list_events(now, now + timedelta(days=1), 5))}


# ------------------------------------------------------------------ Zoom


def _allowed_host(url: str, suffixes: tuple[str, ...]) -> str:
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or not parts.hostname.endswith(suffixes):
        raise ValueError("Refusing to download a transcript from an unexpected host")
    return url


class ZoomService(ConnectorService):
    source = "zoom"

    async def list_transcripts(self, start: date, end: date, limit: int = 20,
                               with_text: bool = True) -> list[Transcript]:  # fmt: skip
        body = await self.client.get("/v2/users/me/recordings",
                                     {"from": start.isoformat(), "to": end.isoformat(), "page_size": 30})  # fmt: skip
        results = []
        for meeting in body.get("meetings") or []:
            files = [
                f
                for f in meeting.get("recording_files") or []
                if f.get("file_type") == "TRANSCRIPT"
            ]
            if not files:
                continue
            text = ""
            if with_text:
                text = vtt_to_text(
                    await self.client.get_text(
                        _allowed_host(files[0]["download_url"], ("zoom.us",))
                    )
                )
            results.append(Transcript(source=self.source, meeting_id=str(meeting.get("uuid") or meeting.get("id")),
                                      title=meeting.get("topic", "Zoom meeting"),
                                      held_on=to_date(meeting.get("start_time")), text=text))  # fmt: skip
            if len(results) >= limit:
                break
        return results

    async def health_check(self) -> dict[str, Any]:
        body = await self.client.get("/v2/users/me")
        return {"user": body.get("display_name")}


# ------------------------------------------------------------------ Teams transcripts (Graph)


class TeamsMeetingsService(ConnectorService):
    source = "teams_meetings"

    def __init__(self, client_factory: Any, organizer: str) -> None:
        super().__init__(client_factory)
        self.organizer = safe_id(organizer, "Teams organizer")

    async def list_transcripts(self, start: date, end: date, limit: int = 20,
                               with_text: bool = True) -> list[Transcript]:  # fmt: skip
        user = quote(self.organizer)
        body = await self.client.get(
            f"/users/{user}/onlineMeetings/getAllTranscripts(meetingOrganizerUserId='{user}')",
            {"$filter": f"createdDateTime ge {start.isoformat()}T00:00:00Z"})  # fmt: skip
        results = []
        for item in body.get("value") or []:
            created = to_date(item.get("createdDateTime"))
            if created and created > end:
                continue
            meeting = await self.client.get(f"/users/{user}/onlineMeetings/{quote(item['meetingId'])}",
                                            {"$select": "subject"})  # fmt: skip
            text = ""
            if with_text:
                url = _allowed_host(item["transcriptContentUrl"], ("graph.microsoft.com",))
                text = vtt_to_text(await self.client.get_text(f"{url}?$format=text/vtt"))
            results.append(Transcript(source=self.source, meeting_id=f"{item['meetingId']}/{item['id']}",
                                      title=meeting.get("subject") or "Teams meeting", held_on=created,
                                      text=text))  # fmt: skip
            if len(results) >= limit:
                break
        return results

    async def health_check(self) -> dict[str, Any]:
        body = await self.client.get(f"/users/{quote(self.organizer)}", {"$select": "displayName"})
        return {"organizer": body.get("displayName")}


# ------------------------------------------------------------------ Google Meet


class GoogleMeetService(ConnectorService):
    source = "google_meet"

    async def list_transcripts(self, start: date, end: date, limit: int = 20,
                               with_text: bool = True) -> list[Transcript]:  # fmt: skip
        body = await self.client.get("/v2/conferenceRecords", {
            "filter": f'start_time>="{start.isoformat()}T00:00:00Z" AND start_time<="{end.isoformat()}T23:59:59Z"',
            "pageSize": 25})  # fmt: skip
        results = []
        for record in body.get("conferenceRecords") or []:
            name = safe_id(record.get("name", ""), "conference record")
            transcripts = (await self.client.get(f"/v2/{name}/transcripts")).get(
                "transcripts"
            ) or []
            if not transcripts:
                continue
            text = ""
            if with_text:
                entries = await self.client.get(f"/v2/{safe_id(transcripts[0]['name'], 'transcript')}/entries",
                                                {"pageSize": 100})  # fmt: skip
                text = "\n".join(e.get("text", "") for e in entries.get("transcriptEntries") or [])
            results.append(Transcript(source=self.source, meeting_id=name,
                                      title=f"Google Meet {record.get('startTime', '')[:16]}",
                                      held_on=to_date(record.get("startTime")), text=text))  # fmt: skip
            if len(results) >= limit:
                break
        return results

    async def health_check(self) -> dict[str, Any]:
        body = await self.client.get("/v2/conferenceRecords", {"pageSize": 1})
        return {"records_visible": len(body.get("conferenceRecords") or [])}


# ------------------------------------------------------------------ demo twins


class DemoCalendar:
    def __init__(self, source: str, data: dict[str, Any], today: date) -> None:
        self.source = source
        base = datetime.combine(today, time(0), UTC)
        self.busy = [BusySlot(person=b["person"], start=base + timedelta(hours=b["start_hour"]),
                              end=base + timedelta(hours=b["end_hour"]))
                     for b in data.get("busy", [])]  # fmt: skip
        self.events = [CalendarEvent(source=source, id=e["id"], title=e["title"],
                                     start=base + timedelta(hours=e["start_hour"]),
                                     end=base + timedelta(hours=e["end_hour"]), attendees=e.get("attendees", 0))
                       for e in data.get("events", [])]  # fmt: skip

    async def free_busy(self, people: list[str], start: datetime, end: datetime) -> list[BusySlot]:
        wanted = {email(p).lower() for p in people}
        return [
            b for b in self.busy if b.person.lower() in wanted and b.end > start and b.start < end
        ]

    async def list_events(
        self, start: datetime, end: datetime, limit: int = 50
    ) -> list[CalendarEvent]:
        return [e for e in self.events if e.end > start and e.start < end][:limit]

    async def health_check(self) -> dict[str, str]:
        return {"mode": "demo"}

    async def close(self) -> None:
        return None


class DemoMeetings:
    def __init__(self, source: str, data: dict[str, Any]) -> None:
        self.source = source
        self.transcripts = [Transcript(source=source, **t) for t in data.get("transcripts", [])]

    async def list_transcripts(self, start: date, end: date, limit: int = 20,
                               with_text: bool = True) -> list[Transcript]:  # fmt: skip
        rows = [t for t in self.transcripts if not t.held_on or start <= t.held_on <= end]
        return [t if with_text else t.model_copy(update={"text": ""}) for t in rows][:limit]

    async def health_check(self) -> dict[str, str]:
        return {"mode": "demo"}

    async def close(self) -> None:
        return None


def _client(s: Settings, name: str, base_url: str, **kwargs: Any) -> Any:
    return lambda: ConnectorClient(
        name, base_url=base_url, timeout=s.connector_timeout_seconds, **kwargs
    )


def _google(s: Settings, scope: str) -> GoogleServiceAccountToken:
    return GoogleServiceAccountToken(s.google_service_account_file,  # type: ignore[arg-type]
                                     [scope], s.google_delegated_user)  # fmt: skip


def _zoom(s: Settings) -> ZoomService:
    token = OAuthToken("Zoom", "https://zoom.us/oauth/token", s.zoom_client_id,
                       s.zoom_client_secret.get_secret_value(),
                       extra={"grant_type": "account_credentials", "account_id": s.zoom_account_id},
                       auth_style="basic", timeout=s.connector_timeout_seconds)  # fmt: skip
    return ZoomService(_client(s, "Zoom", "https://api.zoom.us", token_provider=token))


MS = ("ms_tenant_id", "ms_client_id", "ms_client_secret")

CALENDAR_SPECS = (
    ConnectorSpec(
        "google_calendar",
        "Google Calendar",
        "calendar",
        required=("google_service_account_file", "google_delegated_user"),
        optional=("google_calendar_id",),
        setup="Service account with domain-wide delegation (scope calendar.readonly) impersonating "
        "GOOGLE_DELEGATED_USER; free/busy works for colleagues in the same Workspace",
        docs_url="https://developers.google.com/calendar/api/v3/reference",
        build=lambda s: GoogleCalendarService(
            _client(
                s,
                "Google Calendar",
                "https://www.googleapis.com",
                token_provider=_google(s, "https://www.googleapis.com/auth/calendar.readonly"),
            ),
            s.google_calendar_id,
        ),
        demo=lambda data, today: DemoCalendar(
            "google_calendar", data.get("calendar", {}).get("google_calendar", {}), today
        ),
    ),
    ConnectorSpec(
        "outlook_calendar",
        "Outlook / Microsoft 365 calendar",
        "calendar",
        required=(*MS, "outlook_mailbox"),
        setup="Entra ID app with application permission Calendars.ReadBasic.All (or Calendars.Read); "
        "OUTLOOK_MAILBOX is the calendar used for events and getSchedule",
        docs_url="https://learn.microsoft.com/graph/api/calendar-getschedule",
        build=lambda s: OutlookCalendarService(
            _client(
                s, "Outlook", "https://graph.microsoft.com/v1.0", token_provider=microsoft_token(s)
            ),
            s.outlook_mailbox,
        ),
        demo=lambda data, today: DemoCalendar(
            "outlook_calendar", data.get("calendar", {}).get("outlook_calendar", {}), today
        ),
    ),
)

MEETING_SPECS = (
    ConnectorSpec(
        "zoom", "Zoom", "meetings", required=("zoom_account_id", "zoom_client_id", "zoom_client_secret"),
        setup="Zoom Marketplace → Server-to-Server OAuth app (scope cloud_recording:read:list_user_recordings); "
              "enable cloud recording audio transcripts",
        docs_url="https://developers.zoom.us/docs/api/",
        build=_zoom,
        demo=lambda data, today: DemoMeetings("zoom", data.get("meetings", {}).get("zoom", {})),
    ),
    ConnectorSpec(
        "teams_meetings", "Microsoft Teams transcripts", "meetings", required=(*MS, "teams_organizer_user"),
        setup="Entra ID app with OnlineMeetingTranscript.Read.All plus an application access policy "
              "for the organizer; transcription must be on in the meetings",
        docs_url="https://learn.microsoft.com/graph/api/resources/calltranscript",
        build=lambda s: TeamsMeetingsService(_client(s, "Teams", "https://graph.microsoft.com/v1.0",
                                                     token_provider=microsoft_token(s)), s.teams_organizer_user),
        demo=lambda data, today: DemoMeetings("teams_meetings", data.get("meetings", {}).get("teams_meetings", {})),
    ),
    ConnectorSpec(
        "google_meet", "Google Meet transcripts", "meetings",
        required=("google_service_account_file", "google_delegated_user"),
        setup="Service account with domain-wide delegation (scope meetings.space.readonly) "
              "impersonating the meeting organizer; Meet transcripts must be on",
        docs_url="https://developers.google.com/meet/api/reference/rest",
        build=lambda s: GoogleMeetService(_client(s, "Google Meet", "https://meet.googleapis.com",
                                                  token_provider=_google(s, "https://www.googleapis.com/auth/meetings.space.readonly"))),
        demo=lambda data, today: DemoMeetings("google_meet", data.get("meetings", {}).get("google_meet", {})),
    ),
)  # fmt: skip
