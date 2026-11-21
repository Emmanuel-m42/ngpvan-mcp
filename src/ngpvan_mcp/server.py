"""The MCP server: tool registration and the guard every call goes through.

`guarded()` is the one path from the model to VAN. For each call it:
checks the tool is allowed, runs it, turns failures into short errors the
model can act on, scrubs personal data from the result, caps its size and
writes one audit line.
"""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import Field

from . import tools as t
from .audit import AuditLog
from .client import VanApiError, VanClient
from .config import Config
from .privacy import mask_text, scrub

INSTRUCTIONS = """\
Tools for reading one organization's NGP VAN data: people, contact history, events and shifts,
saved lists and turf door counts, survey questions and activist codes.

- This server never returns phone numbers, emails, street addresses or birth dates. If asked for
  them, say they aren't available here and suggest the person look in VAN directly.
- Names may be shortened to a first name and last initial; use what the tool returns.
- Prefer the summary tools. Quote numbers exactly as the tool returned them and name the tool
  you got them from, e.g. "(turf_door_counts)".
- IDs (VAN IDs, event IDs, survey question and response IDs, code IDs) come from tool results.
  Look them up first (search_people, list_events, list_survey_questions, canvass_codes); never make
  one up.
- If no tool can answer, say so rather than estimating.
- Writes are off unless the operator turned one on. When one is on, always run it with
  dry_run=true first and get the person's OK before sending it.
"""

READ = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False)


def _cap(result: dict[str, Any], max_items: int, max_chars: int) -> tuple[dict[str, Any], bool]:
    """Trim long lists so a result stays within the configured size."""
    truncated = False

    def trim(value: Any) -> Any:
        nonlocal truncated
        if isinstance(value, list):
            if len(value) > max_items:
                truncated = True
                value = value[:max_items]
            return [trim(v) for v in value]
        if isinstance(value, dict):
            return {k: trim(v) for k, v in value.items()}
        return value

    result = trim(result)
    while len(json.dumps(result, default=str)) > max_chars:
        lists = _lists(result)
        if not lists:
            break
        longest = max(lists, key=len)
        if len(longest) <= 1:
            break
        del longest[len(longest) // 2 :]
        truncated = True
    if truncated:
        result["truncated"] = True
        result["truncated_note"] = "Some rows were left out to keep the result small; narrow the request to see them."
    return result, truncated


def _lists(value: Any) -> list[list[Any]]:
    found: list[list[Any]] = []
    if isinstance(value, list):
        found.append(value)
        for v in value:
            found.extend(_lists(v))
    elif isinstance(value, dict):
        for v in value.values():
            found.extend(_lists(v))
    return found


def _count_items(result: dict[str, Any]) -> int:
    return sum(len(v) for v in result.values() if isinstance(v, list))


class Guard:
    def __init__(self, toolbox: t.Toolbox, audit: AuditLog) -> None:
        self.tb = toolbox
        self.audit = audit

    async def __call__(
        self, name: str, args: dict[str, Any], fn: Callable[..., Awaitable[dict[str, Any]]]
    ) -> dict[str, Any]:
        started = time.monotonic()
        stats = self.tb.van.stats
        req0, retry0 = stats.requests, stats.retries
        limits = self.tb.config.limits

        async def log(status: str, **extra: Any) -> None:
            await self.audit.record(tool=name, args=args, status=status, started=started,
                                    van_requests=stats.requests - req0, retries=stats.retries - retry0, **extra)

        try:
            raw = await fn(self.tb, **args)
        except t.WriteDisabledError as exc:
            await log("denied", error_kind="write_disabled", error=str(exc))
            raise ToolError(str(exc)) from None
        except t.ToolInputError as exc:
            await log("error", error_kind="bad_input", error=str(exc))
            raise ToolError(str(exc)) from None
        except VanApiError as exc:
            await log("error", error_kind=exc.kind, error=exc.text)
            raise ToolError(_friendly(exc)) from None
        except Exception as exc:
            await log("error", error_kind="internal", error=type(exc).__name__)
            raise ToolError("the server hit an unexpected error; the audit log has the details") from None
        result, truncated = _cap(scrub(raw), limits.max_items, limits.max_result_chars)
        await log("ok", items=_count_items(result), truncated=truncated)
        return result


def _friendly(exc: VanApiError) -> str:
    hints = {
        "not_found": "VAN has no record with that id in this committee and database.",
        "forbidden": "This API key isn't allowed to do that in VAN.",
        "unauthorized": "VAN rejected the credentials. Check VAN_APP_NAME, VAN_API_KEY and the database mode.",
        "rate_limited": "VAN is rate limiting this API key. Try again in a minute.",
        "server_error": "VAN returned a server error. Try again later.",
    }
    detail = mask_text(exc.text)[:200]
    return f"{hints.get(exc.kind, 'VAN refused the request.')} ({exc.status} {exc.code}: {detail})"


VanId = Annotated[int, Field(description="The person's VAN ID, from search_people. Never guess one.", gt=0)]
Date = Annotated[str, Field(description="A date like 2026-10-19")]


def build_server(config: Config, van: VanClient, audit: AuditLog | None = None) -> MCPServer:
    tb = t.Toolbox(van=van, config=config)
    guard = Guard(tb, audit or AuditLog(config.audit_log))
    mcp = MCPServer(name="ngpvan-mcp", instructions=INSTRUCTIONS, version="0.1.0")

    @mcp.tool(annotations=READ)
    async def check_connection() -> dict[str, Any]:
        """Which VAN committee and database this server is connected to, and which write tools are on."""
        return await guard("check_connection", {}, t.check_connection)

    @mcp.tool(annotations=READ)
    async def search_people(
        first_name: Annotated[str | None, Field(description="First name or its start")] = None,
        last_name: Annotated[str | None, Field(description="Last name or its start")] = None,
        city: Annotated[str | None, Field(description="City of the home address")] = None,
        zip_code: Annotated[str | None, Field(description="5-digit ZIP of the home address")] = None,
        limit: Annotated[int, Field(ge=1, le=25)] = 10,
    ) -> dict[str, Any]:
        """Find people by name, city or ZIP. Returns VAN IDs, names and cities, plus the total match count.
        Searching by phone number or email is not offered."""
        args = {"first_name": first_name, "last_name": last_name, "city": city, "zip_code": zip_code, "limit": limit}
        return await guard("search_people", args, t.search_people)

    @mcp.tool(annotations=READ)
    async def get_person(van_id: VanId) -> dict[str, Any]:
        """One person's name, city and what contact info is on file (yes/no and SMS opt-in), never the values."""
        return await guard("get_person", {"van_id": van_id}, t.get_person)

    @mcp.tool(annotations=READ)
    async def person_activist_codes(van_id: VanId) -> dict[str, Any]:
        """The activist codes applied to one person, with dates."""
        return await guard("person_activist_codes", {"van_id": van_id}, t.person_activist_codes)

    @mcp.tool(annotations=READ)
    async def person_signups(van_id: VanId) -> dict[str, Any]:
        """Every event shift one person has signed up for, with role and status."""
        return await guard("person_signups", {"van_id": van_id}, t.person_signups)

    @mcp.tool(annotations=READ)
    async def contact_history_summary(
        van_ids: Annotated[list[int], Field(description="VAN IDs from search_people, up to 25", min_length=1)],
        since: Annotated[str | None, Field(description="Only count attempts on or after this date")] = None,
    ) -> dict[str, Any]:
        """Counts of contact attempts for up to 25 people: by contact type (phone, walk, text), by result
        (canvassed, not home, refused...) and survey responses recorded."""
        return await guard("contact_history_summary", {"van_ids": van_ids, "since": since}, t.contact_history_summary)

    @mcp.tool(annotations=READ)
    async def list_events(
        start_date: Date,
        end_date: Annotated[str | None, Field(description="Last day to include. Leave it out and only start_date "
                                                          "is searched, so set it for a week or a weekend")] = None,
        event_type: Annotated[str | None, Field(description="Canvass, Phone Bank, Training...")] = None,
    ) -> dict[str, Any]:
        """Events starting between two dates (inclusive), with their shifts and locations."""
        args = {"start_date": start_date, "end_date": end_date, "event_type": event_type}
        return await guard("list_events", args, t.list_events)

    @mcp.tool(annotations=READ)
    async def event_signups_summary(
        event_id: Annotated[int, Field(description="The event's ID from list_events", gt=0)],
        shift: Annotated[str | None, Field(description="Shift name (e.g. Morning) or shift ID")] = None,
        include_roster: bool = True,
    ) -> dict[str, Any]:
        """Who is signed up for an event or one of its shifts: counts by status, how many are committed,
        and the roster (names as the privacy policy allows, role, status)."""
        args = {"event_id": event_id, "shift": shift, "include_roster": include_roster}
        return await guard("event_signups_summary", args, t.event_signups_summary)

    @mcp.tool(annotations=READ)
    async def list_folders() -> dict[str, Any]:
        """The saved-list folders this API key can see."""
        return await guard("list_folders", {}, t.list_folders)

    @mcp.tool(annotations=READ)
    async def turf_door_counts(
        folder_id: Annotated[int | None, Field(description="Folder ID from list_folders")] = None,
        folder_name: Annotated[str | None, Field(description="Part of a folder name, e.g. Northside")] = None,
    ) -> dict[str, Any]:
        """Doors and people per saved list (turf) in a folder, with folder totals. With no folder, every folder."""
        return await guard("turf_door_counts", {"folder_id": folder_id, "folder_name": folder_name}, t.turf_door_counts)

    @mcp.tool(annotations=READ)
    async def list_survey_questions(
        include_archived: bool = False,
        name: Annotated[str | None, Field(description="Start of the question name")] = None,
    ) -> dict[str, Any]:
        """Survey questions with their script and possible responses."""
        return await guard("list_survey_questions", {"include_archived": include_archived, "name": name},
                           t.list_survey_questions)

    @mcp.tool(annotations=READ)
    async def list_activist_codes(
        include_archived: bool = False,
        code_type: Annotated[str | None, Field(description="e.g. Volunteer")] = None,
        name: Annotated[str | None, Field(description="Start of the code name")] = None,
    ) -> dict[str, Any]:
        """Activist codes available in this committee."""
        args = {"include_archived": include_archived, "code_type": code_type, "name": name}
        return await guard("list_activist_codes", args, t.list_activist_codes)

    @mcp.tool(annotations=READ)
    async def canvass_codes() -> dict[str, Any]:
        """Result codes (Not Home, Refused, Wrong Number...) and contact types (Phone, Walk, SMS)."""
        return await guard("canvass_codes", {}, t.canvass_codes)

    if config.write_enabled("apply_canvass_response"):

        @mcp.tool(annotations=WRITE)
        async def apply_canvass_response(
            van_id: VanId,
            contact_type_id: Annotated[int, Field(description="From canvass_codes, e.g. 2 for Walk")],
            survey_question_id: Annotated[int | None, Field(description="From list_survey_questions")] = None,
            survey_response_id: Annotated[int | None, Field(description="One of that question's responses")] = None,
            activist_code_id: Annotated[int | None, Field(description="From list_activist_codes")] = None,
            result_code_id: Annotated[int | None, Field(description="For a non-contact, e.g. Not Home")] = None,
            date_canvassed: Annotated[str | None, Field(description="Defaults to today in VAN")] = None,
            dry_run: Annotated[bool, Field(description="Preview only. Must be true the first time.")] = True,
            confirm_token: Annotated[str | None, Field(description="From the dry run, to send it for real")] = None,
        ) -> dict[str, Any]:
            """Record one canvass result for a person: a survey response, an activist code, or a result code.
            Always call with dry_run=true first, show the preview, and only send with the person's OK."""
            args = {
                "van_id": van_id, "contact_type_id": contact_type_id, "survey_question_id": survey_question_id,
                "survey_response_id": survey_response_id, "activist_code_id": activist_code_id,
                "result_code_id": result_code_id, "date_canvassed": date_canvassed, "dry_run": dry_run,
                "confirm_token": confirm_token,
            }
            return await guard("apply_canvass_response", args, t.apply_canvass_response)

    return mcp
