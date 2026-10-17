"""Feedback / bug-report intake.

A deliberately tiny, dependency-light endpoint: the "Report a bug" widget in
the frontend POSTs here. We validate the payload, stamp it with server-side
context (timestamp, best-effort user id), append it to a JSONL log, and emit a
structured log line. No DB table — bug reports are low-volume and append-only,
so a JSONL file (env-overridable) is the right weight for now.

Auth is best-effort: a bug report must never fail because a token is missing
or expired (that's often *what* the user is reporting). We read the user id
when a valid token is present and otherwise record the report anonymously.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Header
from pydantic import BaseModel, Field

from backend.auth.jwt_handler import get_user_id_from_token

router = APIRouter(prefix="/feedback", tags=["Feedback"])
log = logging.getLogger("pivot.feedback")

# Append-only sink. Defaults to <repo>/var/bug_reports.jsonl; override with
# PIVOT_FEEDBACK_LOG for a mounted volume in deployment.
_DEFAULT_LOG = Path(__file__).resolve().parents[2] / "var" / "bug_reports.jsonl"
_LOG_PATH = Path(os.getenv("PIVOT_FEEDBACK_LOG", str(_DEFAULT_LOG)))

# Allowed enums — kept in sync with the frontend ReportBugDialog selects.
_CATEGORIES = {"bug", "data", "ui", "performance", "other"}
_SEVERITIES = {"low", "normal", "high", "critical"}


class BugReportContext(BaseModel):
    """Auto-captured client context. All optional — the report is the point."""

    page: Optional[str] = Field(default=None, max_length=2048)
    tab: Optional[str] = Field(default=None, max_length=80)
    user_agent: Optional[str] = Field(default=None, max_length=1024)
    app_version: Optional[str] = Field(default=None, max_length=80)
    viewport: Optional[str] = Field(default=None, max_length=40)


class BugReport(BaseModel):
    category: str = Field(default="bug", max_length=40)
    severity: str = Field(default="normal", max_length=20)
    title: str = Field(..., min_length=3, max_length=160)
    description: str = Field(..., min_length=1, max_length=4000)
    email: Optional[str] = Field(default=None, max_length=160)
    context: BugReportContext = Field(default_factory=BugReportContext)


class BugReportAck(BaseModel):
    ok: bool
    id: str


def _best_effort_user_id(authorization: Optional[str]) -> Optional[int]:
    if not authorization:
        return None
    try:
        return get_user_id_from_token(authorization.replace("Bearer ", ""))
    except Exception:  # noqa: BLE001 — never let a bad token block a report
        return None


@router.post("", response_model=BugReportAck)
async def submit_bug_report(
    report: BugReport,
    authorization: str = Header(None),
) -> BugReportAck:
    """Accept a bug report. Always returns 200 on a well-formed payload —
    persistence failures are logged but never surfaced to the reporter, since
    losing the report to an error dialog is the worst outcome here."""
    report_id = uuid.uuid4().hex[:12]
    # Normalise enums to known values so the log stays queryable.
    category = report.category if report.category in _CATEGORIES else "other"
    severity = report.severity if report.severity in _SEVERITIES else "normal"

    record: dict[str, Any] = {
        "id": report_id,
        "ts": datetime.now(timezone.utc).isoformat(),
        "user_id": _best_effort_user_id(authorization),
        "category": category,
        "severity": severity,
        "title": report.title.strip(),
        "description": report.description.strip(),
        "email": (report.email or "").strip() or None,
        "context": report.context.model_dump(exclude_none=True),
    }

    try:
        _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        # Disk/permission issue — keep the report in the structured log at
        # least, and still ack the user.
        log.exception("feedback.persist_failed", extra={"report_id": report_id})

    log.info(
        "feedback.bug_report id=%s category=%s severity=%s user=%s",
        report_id,
        category,
        severity,
        record["user_id"],
    )
    return BugReportAck(ok=True, id=report_id)
